"""The core's HTTP API (ADR 0011): read-only, every route behind the shared secret, errors as {"error", "detail"}."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from workflow_core.config import Workflow
from workflow_core.model.board import BoardReader, BoardUnavailableError, Item
from workflow_core.model.contract import CoreState
from workflow_core.sheets import InvalidSheetError, SheetUnavailableError, read_sheet, sheet_json

# The version of the API's contract (ADR 0018): within it, responses only gain optional fields.
SCHEMA_VERSION = 1


class UnknownWorkflowError(Exception):
    def __init__(self, key: str) -> None:
        super().__init__(f'workflow "{key}" is not declared in the configuration')


def error(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse({"error": code, "detail": detail}, status_code=status)


def _bearer(request: Request) -> str | None:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    return token if scheme.lower() == "bearer" and token else None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def create_app(
    *, secret: str, workflows: dict[str, Workflow], boards: BoardReader, state: CoreState | None = None
) -> FastAPI:
    if not secret:
        raise ValueError("the shared secret is empty")
    expected = secret.encode("utf-8")
    app = FastAPI(title="workflow-core", docs_url=None, redoc_url=None, openapi_url=None)
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
        core.last_read_at[board] = _now()
        return items

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
        listed = []
        for workflow in workflows.values():
            try:
                read_sheet(workflow)
                valid = True
            except (InvalidSheetError, SheetUnavailableError):
                valid = False
            listed.append({"key": workflow.key, "board": workflow.board, "sheet_valid": valid})
        return {"schema_version": SCHEMA_VERSION, "workflows": listed}

    @app.get("/api/workflows/{key}/sheet")
    def sheet(key: str) -> dict:
        workflow = declared(key)
        return {"schema_version": SCHEMA_VERSION, "workflow": workflow.key, "sheet": sheet_json(read_sheet(workflow))}

    @app.get("/api/workflows/{key}/items")
    def items(key: str) -> dict:
        workflow = declared(key)
        return {
            "schema_version": SCHEMA_VERSION,
            "workflow": workflow.key,
            "items": [item.model_dump() for item in read_items(workflow.board)],
        }

    @app.get("/api/machine")
    def machine() -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "runtime_version": core.runtime_version,
            "contract": core.contract.model_dump() if core.contract is not None else None,
            "mode": core.mode,
            "boards": {board: {"last_read_at": at} for board, at in sorted(core.last_read_at.items())},
        }

    return app
