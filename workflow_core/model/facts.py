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
    """The runtime's own words and values that a diagnosis quotes, given by the adapter."""

    # The card's setting that bounds the duration of a run.
    runtime_limit_key: str
    # The agent's settings of a hook's delay and of a tool call's ceiling, with the runtime's defaults (seconds).
    hook_key: str
    deadline_key: str
    hook_timeout_default: float
    hook_timeout_max: float
    tool_deadline_default: float
    # Where a workflow's repository keeps its agents' profiles when its entry does not say.
    repo_profiles_default: str
    # The agent's setting of its turns, the terminal's default delay, and the ceiling of a tool call to suggest.
    max_turns_key: str
    terminal_timeout_default: float
    tool_deadline_suggested: float
    # The terminal's tool name, and the runtime's sentence when it retries a worker that exits without a kanban call.
    terminal_tool: str
    nudge_marker: str


class CardFactsReader(Protocol):
    """Reads the facts of one card of a named board. A card absent from the board is `None`; a board that cannot
    be read raises `BoardUnavailableError`. It gives the runtime's texts the diagnosis quotes."""

    terms: RuntimeTerms
    texts: RuntimeTexts

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


class PluginHook(BaseModel):
    """A hook a plugin plugs (`register_hook("<hook>", <callback>)` in its code), with its registration line as is."""

    plugin: str
    hook: str
    callback: str
    source: str


class ProfileFacts(BaseModel):
    """An agent's configuration in service, and the repository's value of its hooks' delay; `None`: the key is
    absent, the runtime's default applies."""

    profile: str
    hook_timeout: float | None
    tool_deadline: float | None
    terminal_timeout: float | None
    max_turns: float | None
    # Unix seconds of the configuration file's last change.
    changed_at: float | None
    # False when the workflow's repository configuration cannot be read: then `repo_hook_timeout` says nothing
    # (factory-live leaves `repoHookTimeout` out).
    repo_read: bool
    repo_hook_timeout: float | None
    plugins: list[str]
    hooks: list[PluginHook]
    # Per active plugin, the longest delay written in its code (the longest command it can run).
    longest_commands: dict[str, float]


class EvidenceFile(BaseModel):
    """A proof file left by the agent in `evidence/` of its work folder; `at` in Unix seconds."""

    name: str
    bytes: int
    at: float


class ToolOutcome(BaseModel):
    """What a tool's result says: a refusal by a guard or by the runtime, a delay, an error, or nothing to report.
    Only the fields of its kind are set."""

    kind: Literal[
        "hook_timeout", "hook_raised", "guard", "pr_acceptance", "tool_deadline", "command_timeout", "error", "ok"
    ]
    callback: str | None = None
    error: str | None = None
    plugin: str | None = None
    message: str | None = None
    classification: str | None = None
    detail: str | None = None
    tool: str | None = None
    seconds: float | None = None


class DiagnosisInput(BaseModel):
    """Everything a diagnosis reads of a card, at an instant: the facts of its board and of its sources. A source
    that could not be read is `None`, never empty. Times are Unix seconds."""

    now: float
    board: str
    card: CardDetail
    runs: list[RunFact]
    events: list[EventFact]
    # The event told (the block, the give-up, the loop); `None`: the last failure.
    anchor: EventFact | None = None
    sessions: list[SessionFacts] | None = None
    logs: list[LogFact] | None = None
    profile: ProfileFacts | None = None
    evidence: list[EvidenceFile] | None = None
    branch: str | None = None
    pr_url: str | None = None
    # The agent a held card came from, for the relay command.
    owner: str | None = None


class RuntimeTexts(Protocol):
    """The runtime's texts, as the adapter knows them: the rules of the diagnosis read through this port only."""

    terms: RuntimeTerms

    def classify_tool_result(self, content: str, guard_suffixes: list[str], pr_acceptance: str | None) -> ToolOutcome:
        """What a tool's result says. A guard is recognised by the workflow's guard suffixes only, the pull request
        acceptance check by the workflow's text only."""
        ...

    def set_command(self, agent: str, key: str, value: float) -> str:
        """The command that sets an agent's setting."""
        ...

    def plugin_of_module(self, module: str | None) -> str | None:
        """A plugin's name from its Python module, as the runtime names it."""
        ...

    def is_protocol_violation(self, error: str | None) -> bool:
        """A run's error says the worker exited without a terminal kanban call."""
        ...

    def was_nudged(self, output: str | None) -> bool:
        """The runtime retried the worker so it hands the card back before it exits."""
        ...

    def budget_of(self, text: str | None) -> tuple[int, int] | None:
        """The turns used and allowed, from an exhausted budget's text."""
        ...

    def runtime_of(self, text: str | None) -> tuple[int, int] | None:
        """The elapsed and allowed seconds, from a run's maximum duration error."""
        ...

    def resume_session_of(self, text: str | None) -> str | None:
        """The session a run's output names to resume it."""
        ...

    def is_breaker_error(self, error: str) -> bool:
        """A give-up's error says the breaker tripped on protocol violations."""
        ...

    def is_workspace_error(self, error: str) -> bool:
        """A spawn failure's error is about the card's work folder."""
        ...
