"""The live state of a workflow's agents, its activity and its usage, and the ports through which an adapter serves
them (ADR 0011). factory-live received them from the runtime's webhooks; the core reads them from the runtime's bases
and journals, read-only.
"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel

from workflow_core.config import Workflow

LiveState = Literal["thinking", "tool", "cli", "blocked"]


class LiveAction(BaseModel):
    """What the agent is doing: a tool call (`tool`), a delegated command line (`cli`, its label the command's name),
    or waiting for the model (`llm`). `detail`: the command, the path or the name given to the tool, secrets masked,
    2 000 characters at most; for `llm`, the model."""

    kind: Literal["llm", "tool", "cli"]
    label: str
    detail: str | None
    # ISO 8601 UTC, to the millisecond.
    started_at: str


class LiveModel(BaseModel):
    """The agent's model. `source`: `live` for a served model read in the agent's journal, `session` for the model
    its session notes (which may be the one asked for, not the one served)."""

    name: str
    provider: str | None
    source: Literal["live", "session"]


class AgentLive(BaseModel):
    """The live state of an agent holding a card in progress."""

    agent: str
    item: str
    run: int | None
    state: LiveState
    action: LiveAction | None
    model: LiveModel | None
    # The model given to a delegated command line, when its command names one.
    lane_model: str | None
    # The card's last heartbeat, ISO 8601 UTC; None when absent.
    heartbeat_at: str | None


class ActivityFact(BaseModel):
    """One recent fact of a board: a tool call and its outcome, a model call, a card's event, a guard's refusal."""

    id: str
    at: str
    kind: Literal["tool", "llm", "kanban", "guard", "system"]
    agent: str | None
    item: str | None
    text: str
    status: Literal["ok", "running", "error", "blocked"] | None


class ModelUsage(BaseModel):
    model: str
    provider: str
    calls: int
    input_tokens: int
    output_tokens: int
    # The actual cost when known, else the estimated one (USD).
    cost_usd: float | None
    # Calls refused for quota (429), and other failed calls, counted in the agents' journals.
    rate_limited: int
    errors: int


class AgentUsage(BaseModel):
    agent: str
    tokens: int


class DayUsage(BaseModel):
    # YYYY-MM-DD, UTC.
    day: str
    tokens: int
    rate_limited: int


class Usage(BaseModel):
    """Usage and costs over `days` days, per model, per agent and per day."""

    days: int
    models: list[ModelUsage]
    agents: list[AgentUsage]
    daily: list[DayUsage]


class LiveReader(Protocol):
    """Reads the live state, the activity and the usage of a workflow's agents. `now`: Unix seconds."""

    def live(self, workflow: Workflow) -> list[AgentLive]: ...

    def activity(self, workflow: Workflow, limit: int, now: float) -> list[ActivityFact]: ...

    def usage(self, workflow: Workflow, days: int, now: float) -> Usage: ...


class LiveWatcher(Protocol):
    """Says, cheaply enough to be asked every 300 ms, whether the agents' sessions or journals were written to since
    the last call for this board."""

    def changed(self, board: str) -> bool: ...

    def close(self) -> None: ...
