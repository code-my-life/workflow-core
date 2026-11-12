"""The form of each response of the API, published as JSON Schema in schema/ for the clients (ADR 0006, ADR 0011).

The routes build their answers as plain values; these models describe them, and a test checks every answer against
the published schema. Within schema_version 1, an answer only gains optional fields (ADR 0018).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel

from workflow_core.model.board import Item
from workflow_core.model.checks import MachineCheck
from workflow_core.model.contract import ContractVerdict, Mode
from workflow_core.model.live import ActivityFact, AgentLive, Usage
from workflow_core.model.sheet import WorkflowSheet


class _Response(BaseModel):
    schema_version: Literal[1]


class _AtCursor(_Response):
    # The journal's cursor taken before the read, when the event stream runs: a client opening the stream there
    # misses nothing.
    cursor: str | None = None


class ListedWorkflow(BaseModel):
    key: str
    board: str
    # Whether the declared sheet is readable and passes the judge.
    sheet_valid: bool


class WorkflowsResponse(_AtCursor):
    """GET /api/workflows: the declared workflows."""

    workflows: list[ListedWorkflow]


class SheetResponse(_Response):
    """GET /api/workflows/<key>/sheet: the workflow's sheet, judged."""

    workflow: str
    sheet: WorkflowSheet


class ItemsResponse(_AtCursor):
    """GET /api/workflows/<key>/items: every item of the workflow with its full state."""

    workflow: str
    items: list[Item]


class BoardRead(BaseModel):
    # The time of the last successful read of the board (ISO 8601 UTC), None before the first.
    last_read_at: str | None


class MachineResponse(_AtCursor):
    """GET /api/machine: what the core holds about itself."""

    runtime_version: str | None
    contract: ContractVerdict | None
    mode: Mode
    boards: dict[str, BoardRead]
    # The state of the machine: one check per service, as the former probe reported them (ADR 0008). Empty before
    # the first round of checks.
    checks: list[MachineCheck] = []


class LiveResponse(_Response):
    """GET /api/workflows/<key>/live: the live state of each agent holding a card in progress."""

    workflow: str
    agents: list[AgentLive]


class ActivityResponse(_Response):
    """GET /api/workflows/<key>/activity?limit=<n>: the last facts of the board, oldest first (200 at most)."""

    workflow: str
    facts: list[ActivityFact]


class UsageResponse(_Response, Usage):
    """GET /api/workflows/<key>/usage?days=<n>: usage and costs over n days (90 at most)."""

    workflow: str


class StreamEvent(BaseModel):
    """One event of GET /api/events. `reset` opens an epoch: the client reads the full state again; it names no
    workflow, no item and carries no state. `changed` carries the full state of the item, `live` the live state of an
    agent (its item: the card it holds), `activity` one new fact of the board, `machine` the full list of the machine's
    checks when a status changed (no workflow, no item)."""

    cursor: str
    workflow: str | None
    item: str | None
    kind: Literal["changed", "reset", "live", "activity", "machine"]
    # ISO 8601, UTC.
    at: str
    state: Item | AgentLive | ActivityFact | list[MachineCheck] | None


class ErrorResponse(BaseModel):
    """Every error of the API: a stable code and a sentence."""

    error: str
    detail: str


def _schema_text(model: type[BaseModel]) -> Callable[[], str]:
    def text() -> str:
        return json.dumps(model.model_json_schema(by_alias=True), indent=2, ensure_ascii=False) + "\n"

    return text


# Each file of schema/ written by scripts/write_api_schemas.py, and the text it holds.
API_SCHEMAS: dict[str, Callable[[], str]] = {
    "workflows.schema.json": _schema_text(WorkflowsResponse),
    "sheet-response.schema.json": _schema_text(SheetResponse),
    "items.schema.json": _schema_text(ItemsResponse),
    "machine.schema.json": _schema_text(MachineResponse),
    "event.schema.json": _schema_text(StreamEvent),
    "error.schema.json": _schema_text(ErrorResponse),
    "live.schema.json": _schema_text(LiveResponse),
    "activity.schema.json": _schema_text(ActivityResponse),
    "usage.schema.json": _schema_text(UsageResponse),
}
