"""The core's HTTP API (ADR 0011): read-only, every route behind the shared secret, errors as {"error", "detail"}."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.exceptions import HTTPException

from workflow_core.clock import utc_now
from workflow_core.config import Workflow
from workflow_core.diagnosis import diagnose, input_of
from workflow_core.events import BoardWatcher, EventHub
from workflow_core.model.board import BoardReader, BoardUnavailableError, Item
from workflow_core.model.diagnosis import diagnosis_json
from workflow_core.model.facts import CardFactsReader
from workflow_core.model.state import CoreState
from workflow_core.sheets import InvalidSheetError, SheetUnavailableError, read_sheet, sheet_json

# The version of the API's contract (ADR 0018): within it, responses only gain optional fields.
SCHEMA_VERSION = 1


class UnknownWorkflowError(Exception):
    def __init__(self, key: str) -> None:
        super().__init__(f'workflow "{key}" is not declared in the configuration')


class UnknownItemError(Exception):
    def __init__(self, board: str, item: str) -> None:
        super().__init__(f'card "{item}" is not on board "{board}"')


def error(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse({"error": code, "detail": detail}, status_code=status)


def _bearer(request: Request) -> str | None:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    return token if scheme.lower() == "bearer" and token else None


def create_app(
    *,
    secret: str,
    workflows: dict[str, Workflow],
    boards: BoardReader,
    facts: CardFactsReader | None = None,
    state: CoreState | None = None,
    watcher: BoardWatcher | None = None,
    journal_path: Path | None = None,
    clock: Callable[[], float] = time.time,
    spy: Callable[[str], Any] | None = None,
) -> FastAPI:
    """The API. With a board watcher and a journal path, it also probes the boards and serves GET /api/events. The
    facts of a card, for its diagnosis, are read by `facts`, by default the board reader itself."""
    if not secret:
        raise ValueError("the shared secret is empty")
    card_facts: CardFactsReader = facts if facts is not None else boards
    expected = secret.encode("utf-8")
    hub: EventHub | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if hub is not None:
            await hub.start()
        try:
            yield
        finally:
            if hub is not None:
                await hub.stop()

    app = FastAPI(title="workflow-core", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    core = state if state is not None else CoreState()
    for workflow in workflows.values():
        core.last_read_at.setdefault(workflow.board, None)
    app.state.core = core

    def declared(key: str) -> Workflow:
        workflow = workflows.get(key)
        if workflow is None:
            raise UnknownWorkflowError(key)
        return workflow

    def read_items(board: str) -> list[Item]:
        items = boards.read_items(board)
        # Only a successful read moves the time: a failed one keeps the last good time.
        core.last_read_at[board] = utc_now()
        return items

    if watcher is not None and journal_path is not None:
        hub = EventHub(
            workflows=workflows, watcher=watcher, read_items=read_items, journal_path=journal_path, clock=clock, spy=spy
        )
    app.state.events = hub

    def with_cursor(payload: dict, cursor: str | None) -> dict:
        # The journal's cursor taken before the read (ADR 0018: an optional field). A client that opens the stream at it
        # misses nothing; an event that fell between the two comes again, harmless since it carries the full state.
        if cursor is not None:
            payload["cursor"] = cursor
        return payload

    def current_cursor() -> str | None:
        return hub.journal.cursor if hub is not None and hub.journal is not None else None

    @app.middleware("http")
    async def require_the_secret(request: Request, call_next):
        # Every route, /healthz included, and every unknown path: nothing answers without the secret.
        token = _bearer(request)
        if token is None or not secrets.compare_digest(token.encode("utf-8"), expected):
            return error(401, "unauthorized", "missing or wrong shared secret (Authorization: Bearer <secret>)")
        return await call_next(request)

    @app.exception_handler(UnknownWorkflowError)
    async def unknown_workflow(request: Request, exc: UnknownWorkflowError) -> JSONResponse:
        return error(404, "unknown_workflow", str(exc))

    @app.exception_handler(UnknownItemError)
    async def unknown_item(request: Request, exc: UnknownItemError) -> JSONResponse:
        return error(404, "unknown_item", str(exc))

    @app.exception_handler(BoardUnavailableError)
    async def board_unavailable(request: Request, exc: BoardUnavailableError) -> JSONResponse:
        return error(503, "board_unavailable", str(exc))

    @app.exception_handler(InvalidSheetError)
    async def invalid_sheet(request: Request, exc: InvalidSheetError) -> JSONResponse:
        return error(422, "invalid_sheet", str(exc))

    @app.exception_handler(SheetUnavailableError)
    async def sheet_unavailable(request: Request, exc: SheetUnavailableError) -> JSONResponse:
        return error(503, "sheet_unavailable", str(exc))

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return error(exc.status_code, code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error(422, "invalid_request", str(exc.errors()[0]["msg"]) if exc.errors() else "invalid request")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/workflows")
    def list_workflows() -> dict:
        cursor = current_cursor()
        listed = []
        for workflow in workflows.values():
            try:
                read_sheet(workflow)
                valid = True
            except (InvalidSheetError, SheetUnavailableError):
                valid = False
            listed.append({"key": workflow.key, "board": workflow.board, "sheet_valid": valid})
        return with_cursor({"schema_version": SCHEMA_VERSION, "workflows": listed}, cursor)

    @app.get("/api/workflows/{key}/sheet")
    def sheet(key: str) -> dict:
        workflow = declared(key)
        return {"schema_version": SCHEMA_VERSION, "workflow": workflow.key, "sheet": sheet_json(read_sheet(workflow))}

    @app.get("/api/workflows/{key}/items")
    def items(key: str) -> dict:
        workflow = declared(key)
        cursor = current_cursor()
        payload = {
            "schema_version": SCHEMA_VERSION,
            "workflow": workflow.key,
            "items": [item.model_dump() for item in read_items(workflow.board)],
        }
        return with_cursor(payload, cursor)

    @app.get("/api/workflows/{key}/items/{item}/diagnosis")
    def diagnosis(key: str, item: str) -> dict:
        # Computed on demand, from the card's facts read in one short read transaction (ADR 0012).
        workflow = declared(key)
        found = card_facts.read_card_facts(workflow.board, item)
        if found is None:
            raise UnknownItemError(workflow.board, item)
        computed = diagnose(input_of(found, workflow.board, clock()), card_facts.texts, workflow)
        return {
            "schema_version": SCHEMA_VERSION,
            "workflow": workflow.key,
            "item": item,
            "diagnosis": diagnosis_json(computed) if computed is not None else None,
        }

    @app.get("/api/events")
    async def events(request: Request, cursor: str | None = None) -> StreamingResponse:
        if hub is None:
            raise HTTPException(404, "the event stream is not running")
        resume = request.headers.get("last-event-id") or cursor
        return StreamingResponse(
            hub.stream(resume),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/machine")
    def machine() -> dict:
        cursor = current_cursor()
        payload = {
            "schema_version": SCHEMA_VERSION,
            "runtime_version": core.runtime_version,
            "contract": core.contract.model_dump() if core.contract is not None else None,
            "mode": core.mode,
            "boards": {board: {"last_read_at": at} for board, at in sorted(core.last_read_at.items())},
        }
        return with_cursor(payload, cursor)

    return app
