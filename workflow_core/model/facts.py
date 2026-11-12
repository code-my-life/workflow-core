"""The facts of one card that the diagnosis reads, and the port through which an adapter serves them (ADR 0012).

The rules of `workflow_core.diagnosis` only see these facts: what the runtime's bases hold, as plain values. What the
diagnosis writes out of the runtime in clear (the name of a setting, later configuration keys and commands to copy)
comes from the adapter too, in `RuntimeTerms`: the rules never name the runtime (ADR 0001).
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel


class CardDetail(BaseModel):
    """A card with every field the diagnosis reads. Dates are Unix seconds, `None` when absent."""

    id: str
    title: str
    body: str | None
    status: str
    assignee: str | None
    created_at: int
    started_at: int | None
    completed_at: int | None
    block_kind: str | None
    current_run_id: int | None
    workspace_path: str | None
    skills: list[str]
    last_heartbeat_at: int | None
    branch_name: str | None
    consecutive_failures: int
    block_recurrences: int
    last_failure_error: str | None
    max_runtime_seconds: int | None
    idempotency_key: str | None
    model_override: str | None
    provider_override: str | None


class RunFact(BaseModel):
    """One run of the card, with its error text and its metadata (a JSON object, empty when unreadable)."""

    id: int
    profile: str | None
    status: str
    outcome: str | None
    started_at: int
    ended_at: int | None
    summary: str | None
    metadata: dict[str, Any]
    error: str | None


class EventFact(BaseModel):
    """One event of the card's life (created, blocked, timed out...), its payload a JSON object, empty when
    unreadable."""

    id: int
    run_id: int | None
    kind: str
    payload: dict[str, Any]
    created_at: int


class CardFacts(BaseModel):
    """A card, its runs and its events, read together in one short read transaction; runs and events by id."""

    card: CardDetail
    runs: list[RunFact]
    events: list[EventFact]


class RuntimeTerms(BaseModel):
    """The runtime's own words that a diagnosis quotes, given by the adapter."""

    # The card's setting that bounds the duration of a run.
    runtime_limit_key: str


class CardFactsReader(Protocol):
    """Reads the facts of one card of a named board. A card absent from the board is `None`; a board that cannot
    be read raises `BoardUnavailableError`."""

    terms: RuntimeTerms

    def read_card_facts(self, board: str, card: str) -> CardFacts | None: ...

    def read_board_facts(self, board: str) -> list[CardFacts]:
        """Every card of the board with its runs and its events."""
        ...


LogFactKind = Literal[
    "hook_timeout",
    "hook_skipped",
    "hook_abandoned",
    "hook_raised",
    "plugin_load_failed",
    "stop_nudge",
    "turn_ended",
    "tool_deadline",
    "api_failed",
]


class LogFact(BaseModel):
    """A catalogued line of an agent's journal, already dated: the same values as factory-live's `LogFact`."""

    # Unix seconds, decimals kept.
    at: float
    session_id: str | None
    kind: LogFactKind
    hook: str | None
    callback: str | None
    # The plugin's Python module, when the runtime names it.
    module: str | None
    seconds: int | None
    plugin: str | None
    detail: str | None
    count: int | None
    # The line's message, quoted (secrets masked, cut).
    line: str


class AgentLogPort(Protocol):
    """Reads an agent's journal: its catalogued facts between two instants (Unix seconds), sorted by time; `None`
    when the agent has no journal."""

    def facts(self, agent: str, since: float, until: float) -> list[LogFact] | None: ...


class ToolCallFact(BaseModel):
    """A tool call of a session: the terminal's command, else the path or the name given to the tool."""

    at: float
    tool: str
    input: str | None
    call_id: str | None


class ToolResultFact(BaseModel):
    """A tool's result: the content's first 1 500 and last 500 characters when it is longer, secrets masked."""

    at: float
    tool: str
    call_id: str | None
    content: str


class SessionFacts(BaseModel):
    """A session's calls and results, in the order of its messages; `run_id`: the run it was attached to."""

    id: str
    run_id: int | None
    calls: list[ToolCallFact]
    results: list[ToolResultFact]


class SessionsPort(Protocol):
    """Reads an agent's sessions. `None` when its base cannot be read: never an empty list."""

    def run_facts(self, agent: str, card: str, runs: list[RunFact]) -> list[SessionFacts] | None:
        """The sessions of these runs of a card, each found without guessing, at most once."""
        ...

    def open_sessions(self, agent: str, at: float) -> list[str] | None:
        """The agent's sessions started before this instant (Unix seconds) and not yet ended."""
        ...
