"""The core's HTTP API (ADR 0011): read-only, every route behind the shared secret, errors as {"error", "detail"}."""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.exceptions import HTTPException

from workflow_core.clock import iso_ms, utc_now
from workflow_core.config import Workflow
from workflow_core.diagnosis import diagnose_board, diagnose_card
from workflow_core.diagnosis.service import run_verdicts
from workflow_core.events import BoardWatcher, EventHub
from workflow_core.interventions import interventions_of
from workflow_core.machine import MachineChecks
from workflow_core.model.agents import AgentsReader
from workflow_core.model.board import BoardReader, BoardUnavailableError, Item
from workflow_core.model.checks import CHECK_EVERY_SECONDS, DeclaredCheck, RuntimeChecks
from workflow_core.model.data import ID_FORMS, SUBJECTS, DeclaredDataReader
from workflow_core.model.diagnosis import diagnosis_json
from workflow_core.model.facts import CardFactsReader, DiagnosisSources
from workflow_core.model.interventions import Intervention
from workflow_core.model.live import LiveReader, LiveWatcher
from workflow_core.model.sheet import WorkflowSheet
from workflow_core.model.state import CoreState
from workflow_core.model.triggers import Trigger
from workflow_core.sheets import InvalidSheetError, SheetUnavailableError, read_sheet, sheet_json
from workflow_core.stations import place

# The version of the API's contract (ADR 0018): within it, responses only gain optional fields.
SCHEMA_VERSION = 1


class UnknownWorkflowError(Exception):
    def __init__(self, key: str) -> None:
        super().__init__(f'workflow "{key}" is not declared in the configuration')


class UnknownSubjectError(Exception):
    def __init__(self, subject: str) -> None:
        super().__init__(f'subject "{subject}" is not one of {", ".join(SUBJECTS)}')


class InvalidIdError(Exception):
    def __init__(self, subject: str, item: str) -> None:
        super().__init__(f'"{item}" is not an id of a {subject}')


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
    sources: DiagnosisSources | None = None,
    state: CoreState | None = None,
    watcher: BoardWatcher | None = None,
    journal_path: Path | None = None,
    clock: Callable[[], float] = time.time,
    spy: Callable[[str], Any] | None = None,
    live: LiveReader | None = None,
    live_watcher: LiveWatcher | None = None,
    runtime_checks: RuntimeChecks | None = None,
    checks: list[DeclaredCheck] | None = None,
    checks_every: float = CHECK_EVERY_SECONDS,
    data: DeclaredDataReader | None = None,
    agents: AgentsReader | None = None,
    background: list[Any] = (),
) -> FastAPI:
    """The API. With a board watcher and a journal path, it also probes the boards and serves GET /api/events. The
    facts of a card, for its diagnosis, are read by `facts`, by default the board reader itself, and the other
    sources of the diagnosis by `sources`, by default the board reader's. `background` holds tasks started and
    stopped with the service (each with `async start()` and `async stop()`), such as the notifier."""
    if not secret:
        raise ValueError("the shared secret is empty")
    card_facts: CardFactsReader = facts if facts is not None else boards
    diagnosis_sources: DiagnosisSources = sources if sources is not None else card_facts.diagnosis_sources()
    # The agents' live state, activity and usage: by default, the board reader's.
    live_reader: LiveReader | None = live if live is not None else getattr(boards, "live_reader", lambda: None)()
    # The data the workflows declare: by default, the board reader's.
    data_reader: DeclaredDataReader | None = (
        data if data is not None else getattr(boards, "data_reader", lambda: None)()
    )
    # The agents of the workflows and their configuration (#129): by default, the board reader's.
    agents_reader: AgentsReader | None = (
        agents if agents is not None else getattr(boards, "agents_reader", lambda: None)()
    )
    expected = secret.encode("utf-8")
    hub: EventHub | None = None
    # The machine's checks run apart from the routes: /api/machine serves their last results (ADR 0008).
    machine_checks = (
        MachineChecks(runtime=runtime_checks, declared=list(checks or []), clock=clock, every_seconds=checks_every)
        if runtime_checks is not None or checks
        else None
    )

    async def watch_machine() -> None:
        # The first round is done at startup; then a check whose status changes is announced once.
        while True:
            await asyncio.sleep(min(1.0, checks_every / 2))
            if await asyncio.to_thread(machine_checks.run_due) and hub is not None and hub.journal is not None:
                hub.publish("machine", [check.model_dump() for check in machine_checks.current()])

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if hub is not None:
            await hub.start()
        for task in background:
            await task.start()
        watching = None
        if machine_checks is not None:
            await asyncio.to_thread(machine_checks.run_due)
            watching = asyncio.create_task(watch_machine(), name="workflow-core machine checks")
        try:
            yield
        finally:
            if watching is not None:
                watching.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watching
                machine_checks.close()
            for task in reversed(background):
                await task.stop()
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

    def sheet_of(workflow: Workflow) -> WorkflowSheet | None:
        try:
            return read_sheet(workflow)
        except (InvalidSheetError, SheetUnavailableError):
            return None

    def read_items(board: str) -> list[Item]:
        items = boards.read_items(board)
        # Only a successful read moves the time: a failed one keeps the last good time.
        core.last_read_at[board] = utc_now()
        # Each item says where it stands, from the sheet of the workflow of its board (#140).
        workflow = next((workflow for workflow in workflows.values() if workflow.board == board), None)
        return place(items, sheet_of(workflow), workflow.key) if workflow is not None else items

    def open_interventions(workflow: Workflow) -> list[Intervention]:
        # The interventions of the workflow's board, computed from its cards' facts (ADR 0012).
        cards = card_facts.read_board_facts(workflow.board)
        return interventions_of(cards, workflow, clock(), card_facts.texts)

    if watcher is not None and journal_path is not None:
        hub = EventHub(
            workflows=workflows,
            watcher=watcher,
            read_items=read_items,
            journal_path=journal_path,
            clock=clock,
            spy=spy,
            live=live_reader,
            live_watcher=live_watcher,
            interventions=open_interventions,
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

    @app.exception_handler(UnknownSubjectError)
    async def unknown_subject(request: Request, exc: UnknownSubjectError) -> JSONResponse:
        return error(422, "unknown_subject", str(exc))

    @app.exception_handler(InvalidIdError)
    async def invalid_id(request: Request, exc: InvalidIdError) -> JSONResponse:
        return error(422, "invalid_id", str(exc))

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
            listed.append(
                {
                    "key": workflow.key,
                    "board": workflow.board,
                    "sheet_valid": valid,
                    "gestures": [gesture.model_dump(exclude_none=True) for gesture in workflow.gestures],
                }
            )
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

    def trigger_json(trigger: Trigger) -> dict:
        # The core's keys, as the rest of its API; only the diagnosis follows factory-live's contract.
        return {
            "id": trigger.id,
            "kind": trigger.kind,
            "event": trigger.event,
            "opened_at": iso_ms(trigger.opened_at),
            "agent": trigger.agent,
        }

    @app.get("/api/workflows/{key}/items/{item}/diagnosis")
    def diagnosis(key: str, item: str) -> dict:
        # Computed on demand, for a card that is a trigger (ADR 0012); another card has no diagnosis.
        workflow = declared(key)
        found = card_facts.read_card_facts(workflow.board, item)
        if found is None:
            raise UnknownItemError(workflow.board, item)
        computed = diagnose_card(found, workflow, clock(), diagnosis_sources)
        trigger, result = computed if computed is not None else (None, None)
        return {
            "schema_version": SCHEMA_VERSION,
            "workflow": workflow.key,
            "item": item,
            "trigger": trigger_json(trigger) if trigger is not None else None,
            "diagnosis": diagnosis_json(result) if result is not None else None,
        }

    @app.get("/api/workflows/{key}/items/{item}/run-verdicts")
    def verdicts(key: str, item: str) -> dict:
        # The verdict of each failed run of the card, finished or not, newest run first; computed on demand.
        workflow = declared(key)
        found = card_facts.read_card_facts(workflow.board, item)
        if found is None:
            raise UnknownItemError(workflow.board, item)
        return {
            "schema_version": SCHEMA_VERSION,
            "verdicts": [
                verdict.model_dump(mode="json", by_alias=True)
                for verdict in run_verdicts(found, workflow, clock(), diagnosis_sources)
            ],
        }

    @app.get("/api/workflows/{key}/diagnoses")
    def diagnoses(key: str) -> dict:
        # Every trigger of the board with its diagnosis, by opening time.
        workflow = declared(key)
        cards = card_facts.read_board_facts(workflow.board)
        return {
            "schema_version": SCHEMA_VERSION,
            "workflow": workflow.key,
            "diagnoses": [
                {"item": trigger.card, "trigger": trigger_json(trigger), "diagnosis": diagnosis_json(result)}
                for trigger, result in diagnose_board(cards, workflow, clock(), diagnosis_sources)
            ],
        }

    def reading_live() -> LiveReader:
        if live_reader is None:
            raise HTTPException(404, "the live state is not read")
        return live_reader

    @app.get("/api/workflows/{key}/live")
    def live_state(key: str) -> dict:
        # What each agent holding a card in progress does now; the stream's `live` events carry the same state.
        workflow = declared(key)
        agents = reading_live().live(workflow)
        return {"schema_version": SCHEMA_VERSION, "workflow": workflow.key, "agents": [a.model_dump() for a in agents]}

    @app.get("/api/workflows/{key}/activity")
    def activity(key: str, limit: int = Query(50, ge=1, le=200)) -> dict:
        workflow = declared(key)
        facts = reading_live().activity(workflow, limit, clock())
        return {"schema_version": SCHEMA_VERSION, "workflow": workflow.key, "facts": [f.model_dump() for f in facts]}

    @app.get("/api/workflows/{key}/usage")
    def usage(key: str, days: int = Query(7, ge=1, le=90)) -> dict:
        workflow = declared(key)
        report = reading_live().usage(workflow, days, clock())
        return {"schema_version": SCHEMA_VERSION, "workflow": workflow.key, **report.model_dump()}

    @app.get("/api/workflows/{key}/interventions")
    def interventions(key: str) -> dict:
        # The open interventions of the workflow's board, by opening time; the core posts no gesture (ADR 0011).
        workflow = declared(key)
        return {
            "schema_version": SCHEMA_VERSION,
            "workflow": workflow.key,
            "interventions": [found.model_dump(mode="json") for found in open_interventions(workflow)],
        }

    @app.get("/api/workflows/{key}/data")
    def declared_data(key: str, subject: str, id: str) -> dict:
        # The data the workflow declares for this subject, read at the request (ADR 0011): an absent one has no
        # value, an unreadable one a status that says so; the answer never fails for one of them.
        workflow = declared(key)
        if subject not in SUBJECTS:
            raise UnknownSubjectError(subject)
        if not ID_FORMS[subject].fullmatch(id):
            raise InvalidIdError(subject, id)
        found = data_reader.read(workflow, subject, id) if data_reader is not None and workflow.data else []
        return {
            "schema_version": SCHEMA_VERSION,
            "workflow": workflow.key,
            "subject": subject,
            "id": id,
            "data": [entry.model_dump() for entry in found],
        }

    @app.get("/api/workflows/{key}/agents")
    def workflow_agents(key: str, days: int = Query(30, ge=1, le=90)) -> dict:
        # The workflow's agents and their configuration in service, without a secret (#129); the hook timeouts of
        # their plugins are counted over the last `days` days.
        workflow = declared(key)
        kanban, found, risks = (
            agents_reader.agents(workflow, days, clock()) if agents_reader is not None else (None, [], [])
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "workflow": workflow.key,
            "kanban": kanban.model_dump() if kanban is not None else None,
            "agents": [agent.model_dump() for agent in found],
            "guard_risks": [risk.model_dump() for risk in risks],
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
        # The machine's checks, when the core runs any (ADR 0018: an optional field).
        if machine_checks is not None:
            payload["checks"] = [check.model_dump() for check in machine_checks.current()]
        return with_cursor(payload, cursor)

    return app
