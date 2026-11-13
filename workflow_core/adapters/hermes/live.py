"""The live state of the agents, their activity and their usage, read from Hermès' bases and journals (ADR 0011).

factory-live received the live part from Hermès' outbound webhooks (`pre_tool_call`, `post_api_request`...) and
derived it in `applyHook` (packages/domain/src/activity.ts at d47f235). The core reads the same facts on disk,
read-only: the session of the run in progress (`profiles/<agent>/state.db`: its model, its last messages, its tool
calls and results), `session_model_usage` for the usage, and the agent's journal (`logs/agent.log`) for the model
actually served and the failed calls. Hermès writes the served model in no base: only a journal line that names a
model other than the one asked for (`auto`) gives it.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from workflow_core.adapters.hermes.agent_log import (
    journal_paths,
    parse_log_line,
    zoned_epoch,
)
from workflow_core.adapters.hermes.board import HermesBoardReader, hermes_home, make_board_reader
from workflow_core.adapters.hermes.sessions import (
    SessionReader,
    open_session_base,
    run_session,
    session_base,
    session_db,
)
from workflow_core.adapters.hermes.texts import TEXTS
from workflow_core.adapters.hermes.values import as_number
from workflow_core.clock import iso_ms
from workflow_core.config import Workflow
from workflow_core.model.facts import CardFacts, RunFact
from workflow_core.model.live import (
    ActivityFact,
    AgentLive,
    AgentUsage,
    DayUsage,
    LiveAction,
    LiveModel,
    ModelUsage,
    Usage,
    UsageRow,
)
from workflow_core.text import js_regex, js_trim, redact_secrets, utf16_slice

# The model a session notes when the router chooses: the one asked for, not the one served.
REQUESTED_MODEL = "auto"
DETAIL_MAX = 2_000
TEXT_MAX = 300
# The runs whose sessions feed the activity: those in progress, or started or ended within this window.
ACTIVITY_WINDOW_SECONDS = 24 * 3600
# At the first read of a journal, only its end counts; then only what was appended is read.
FIRST_READ_BYTES = 4 * 1024 * 1024
ENTRIES_KEPT = 20_000

# The columns of `session_model_usage` read, declared for the contract test (contract.py).
USAGE_COLUMNS = (
    "session_id",
    "model",
    "billing_provider",
    "api_call_count",
    "input_tokens",
    "output_tokens",
    "estimated_cost_usd",
    "actual_cost_usd",
    "last_seen",
)

# The journal lines read here, each with its kind, declared for the contract test (contract.py).
LIVE_LOG_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("api_call", js_regex(r"^API call #\d+: model=(\S+) provider=(\S+)")),
    ("fallback", js_regex(r"^Fallback activated: (\S+) → (\S+) \(([^)]+)\)")),
    ("api_failed", js_regex(r"^API call failed \(attempt \d+\/\d+(?:, not retryable)?\) error_type=(\w+)(.*)$")),
)
_PROVIDER = js_regex(r"\bprovider=(\S+)")
_MODEL = js_regex(r"\bmodel=(\S+)")
_RATE_LIMITED = js_regex(r"\bHTTP 429\b")

# The command lines an agent delegates to, as factory-live recognises them (`describeToolCall`).
_CLAUDE = js_regex(r"(?:^|[\s;&|(])claude\s+-p\b")
_OPENCODE = js_regex(r"(?:^|[\s;&|(])opencode\s+run\b")
_LANE_MODEL = js_regex(r"--model[=\s]+(\"?)([^\s\"]+)\1")
_DETAIL_KEYS = ("path", "file_path", "name", "query", "task_id", "url")
# The card events that are failures; `blocked` is its own status; the rest is ok. Heartbeats are left out.
_FAILED_EVENTS = frozenset({"crashed", "timed_out", "gave_up", "spawn_failed", "reclaimed", "stale"})
_BLOCKING_OUTCOMES = frozenset({"guard", "hook_timeout", "hook_raised"})
_FAILED_OUTCOMES = frozenset({"error", "tool_deadline", "command_timeout", "pr_acceptance"})

LAST_CALL_SQL = """
  SELECT id, tool_calls, timestamp FROM messages
  WHERE session_id = ? AND role = 'assistant' AND tool_calls IS NOT NULL ORDER BY id DESC LIMIT 1"""
ANSWERED_SQL = "SELECT tool_call_id FROM messages WHERE session_id = ? AND role = 'tool' AND id > ?"
LAST_MESSAGE_SQL = "SELECT role, timestamp FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT 1"
USAGE_SQL = """
  SELECT model, billing_provider AS provider, date(last_seen, 'unixepoch') AS day,
         SUM(api_call_count) AS calls, SUM(input_tokens) AS input, SUM(output_tokens) AS output,
         SUM(CASE WHEN actual_cost_usd > 0 THEN actual_cost_usd ELSE estimated_cost_usd END) AS cost
  FROM session_model_usage WHERE last_seen >= ? GROUP BY model, billing_provider, day ORDER BY model, day"""


def _clean(value: object, units: int) -> str | None:
    if not isinstance(value, str):
        return None
    trimmed = js_trim(value)
    return utf16_slice(redact_secrets(trimmed), units) if trimmed else None


@dataclass(frozen=True)
class ToolCall:
    kind: str
    label: str
    detail: str | None
    lane_model: str | None


def describe_call(call: object) -> ToolCall:
    """A tool call of `messages.tool_calls`, as factory-live's `describeToolCall` reads it: a delegated command line
    (`claude -p`, `opencode run`) is `cli`, any other call `tool` with its telling argument."""
    function = call.get("function") if isinstance(call, dict) else None
    name = function.get("name") if isinstance(function, dict) else None
    name = name if isinstance(name, str) and name else "?"
    raw = function.get("arguments") if isinstance(function, dict) else None
    try:
        args = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        args = None
    args = args if isinstance(args, dict) else {}
    command = _clean(args.get("command"), DETAIL_MAX)
    if name == TEXTS.terms.terminal_tool and command:
        claude, opencode = _CLAUDE.search(command), _OPENCODE.search(command)
        if claude or opencode:
            model = _LANE_MODEL.search(command)
            return ToolCall("cli", "claude -p" if claude else "opencode run", command, model[2] if model else None)
        return ToolCall("tool", name, command, None)
    detail = next((found for key in _DETAIL_KEYS if (found := _clean(args.get(key), DETAIL_MAX))), None)
    return ToolCall("tool", name, detail, None)


@dataclass(frozen=True)
class LogEntry:
    at: float
    session: str | None
    kind: str
    model: str | None
    provider: str | None
    # For a failed call: its error type, and whether it was refused for quota.
    error: str | None = None
    rate_limited: bool = False


def log_entry_of(message: str, at: float, session: str | None) -> LogEntry | None:
    for kind, pattern in LIVE_LOG_PATTERNS:
        match = pattern.search(message)
        if match is None:
            continue
        if kind == "api_call":
            return LogEntry(at, session, kind, match[1], match[2])
        if kind == "fallback":
            return LogEntry(at, session, kind, match[2], match[3])
        rest = match[2] or ""
        model, provider = _MODEL.search(rest), _PROVIDER.search(rest)
        return LogEntry(
            at,
            session,
            kind,
            model[1] if model else None,
            provider[1] if provider else None,
            error=match[1],
            rate_limited=match[1] == "RateLimitError" or _RATE_LIMITED.search(rest) is not None,
        )
    return None


@dataclass
class _JournalCache:
    inode: int
    offset: int
    pending: str = ""
    last_at: float | None = None
    entries: deque = field(default_factory=lambda: deque(maxlen=ENTRIES_KEPT))


class LiveJournal:
    """The lines of the agents' journals that name a model or a failed call, read incrementally: a reread only parses
    what was appended; a replaced or shortened file is read again from its end."""

    def __init__(self, home: Path, time_zone: str) -> None:
        self.home = home
        self.time_zone = time_zone
        self._files: dict[Path, _JournalCache] = {}

    def entries(self, agent: str) -> list[LogEntry]:
        found: list[LogEntry] = []
        for path in journal_paths(self.home, agent):
            cache = self._read(path)
            if cache is not None:
                found += cache.entries
        return sorted(found, key=lambda entry: entry.at)

    def _read(self, path: Path) -> _JournalCache | None:
        try:
            handle = path.open("rb")
        except OSError:
            self._files.pop(path, None)
            return None
        with handle:
            stat = os.fstat(handle.fileno())
            cache = self._files.get(path)
            if cache is None or cache.inode != stat.st_ino or stat.st_size < cache.offset:
                start = max(0, stat.st_size - FIRST_READ_BYTES)
                cache = _JournalCache(inode=stat.st_ino, offset=start, pending="\0" if start > 0 else "")
                self._files[path] = cache
            if cache.offset < stat.st_size:
                handle.seek(cache.offset)
                chunk = handle.read(stat.st_size - cache.offset)
                cache.offset += len(chunk)
                lines = (cache.pending + chunk.decode("utf-8", errors="replace")).split("\n")
                cache.pending = lines.pop()
                if lines and lines[0].startswith("\0"):
                    lines.pop(0)
                for raw in lines:
                    parsed = parse_log_line(raw.removesuffix("\r"))
                    if parsed is None:
                        continue
                    at = zoned_epoch(parsed.wall_clock, parsed.millis, self.time_zone, cache.last_at)
                    if at is None:
                        continue
                    cache.last_at = at
                    entry = log_entry_of(parsed.message, at, parsed.session_id)
                    if entry is not None:
                        cache.entries.append(entry)
            return cache


def _iso(seconds: float | None) -> str | None:
    return iso_ms(seconds) if isinstance(seconds, int | float) and not isinstance(seconds, bool) else None


def _day(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%d")


def _zero_usage(model: str, provider: str, cost: float | None) -> ModelUsage:
    """A model's usage before any call is counted."""
    return ModelUsage(
        model=model,
        provider=provider,
        calls=0,
        input_tokens=0,
        output_tokens=0,
        cost_usd=cost,
        rate_limited=0,
        errors=0,
    )


def _add_calls(total: ModelUsage | UsageRow, row: sqlite3.Row, tokens_in: int, tokens_out: int) -> None:
    """Add a row of `USAGE_SQL` to a model's usage or to a line of the detail."""
    total.calls += int(as_number(row["calls"]))
    total.input_tokens += tokens_in
    total.output_tokens += tokens_out
    total.cost_usd = round((total.cost_usd or 0.0) + as_number(row["cost"]), 6)


class HermesLiveReader:
    """Reads the live state, the activity and the usage of a workflow's agents under one Hermès home (`LiveReader`).
    Each reading opens its own connections and closes them."""

    def __init__(self, home: Path, time_zone: str, boards: HermesBoardReader) -> None:
        self.home = home
        self.boards = boards
        self.sessions = SessionReader(home)
        self.journal = LiveJournal(home, time_zone)

    # --- live -----------------------------------------------------------------------------------------------------

    def live(self, workflow: Workflow) -> list[AgentLive]:
        agents: list[AgentLive] = []
        for facts in self.boards.read_board_facts(workflow.board):
            card = facts.card
            if card.current_run_id is None:
                continue
            run = next((run for run in facts.runs if run.id == card.current_run_id), None)
            agent = (run.profile if run is not None else None) or card.assignee
            if not agent:
                continue
            agents.append(self._agent_live(agent, facts, run))
        return sorted(agents, key=lambda live: (live.item, live.agent))

    def _agent_live(self, agent: str, facts: CardFacts, run: RunFact | None) -> AgentLive:
        card = facts.card
        state, action, model, lane_model = "thinking", None, None, None
        with session_db(self.home, agent) as db:
            session = None
            if db is not None and run is not None:
                with suppress(sqlite3.Error):
                    session = run_session(db, card.id, run)
            if session is not None:
                with suppress(sqlite3.Error):
                    model = self._model(agent, db, session)
                    state, action, lane_model = self._action(db, session, model)
        if card.status == "blocked":
            state, action = "blocked", None
        return AgentLive(
            agent=agent,
            item=card.id,
            run=card.current_run_id,
            state=state,
            action=action,
            model=model,
            lane_model=lane_model,
            heartbeat_at=_iso(card.last_heartbeat_at),
        )

    def _model(self, agent: str, db: sqlite3.Connection, session: str) -> LiveModel | None:
        row = db.execute("SELECT model FROM sessions WHERE id = ?", (session,)).fetchone()
        noted = row["model"] if row is not None and isinstance(row["model"], str) and row["model"] else None
        if noted is None:
            return None
        if noted == REQUESTED_MODEL:
            served = [
                entry
                for entry in self.journal.entries(agent)
                if entry.session == session
                and entry.kind in ("api_call", "fallback")
                and entry.model
                and entry.model != REQUESTED_MODEL
            ]
            if served:
                return LiveModel(name=served[-1].model, provider=served[-1].provider, source="live")
        return LiveModel(name=noted, provider=None, source="session")

    def _action(
        self, db: sqlite3.Connection, session: str, model: LiveModel | None
    ) -> tuple[str, LiveAction | None, str | None]:
        last_call = db.execute(LAST_CALL_SQL, (session,)).fetchone()
        if last_call is not None:
            answered = {row["tool_call_id"] for row in db.execute(ANSWERED_SQL, (session, last_call["id"]))}
            try:
                calls = json.loads(last_call["tool_calls"])
            except (TypeError, ValueError):
                calls = []
            for call in calls if isinstance(calls, list) else []:
                call_id = call.get("id") or call.get("call_id") if isinstance(call, dict) else None
                if call_id in answered:
                    continue
                described = describe_call(call)
                action = LiveAction(
                    kind=described.kind,
                    label=described.label,
                    detail=described.detail,
                    started_at=iso_ms(as_number(last_call["timestamp"])),
                )
                return ("cli" if described.kind == "cli" else "tool"), action, described.lane_model
        last = db.execute(LAST_MESSAGE_SQL, (session,)).fetchone()
        if last is not None and last["role"] in ("tool", "user"):
            action = LiveAction(
                kind="llm",
                label="llm",
                detail=model.name if model is not None else None,
                started_at=iso_ms(as_number(last["timestamp"])),
            )
            return "thinking", action, None
        return "thinking", None, None

    # --- activity -------------------------------------------------------------------------------------------------

    def activity(self, workflow: Workflow, limit: int, now: float) -> list[ActivityFact]:
        found: list[tuple[float, ActivityFact]] = []
        since = now - ACTIVITY_WINDOW_SECONDS
        for facts in self.boards.read_board_facts(workflow.board):
            card = facts.card
            profile_of = {run.id: run.profile for run in facts.runs}
            for event in facts.events:
                if event.kind == "heartbeat":
                    continue
                reason = event.payload.get("reason") or event.payload.get("summary")
                text = f"{event.kind} : {reason}" if isinstance(reason, str) and reason.strip() else event.kind
                status = "blocked" if event.kind == "blocked" else "error" if event.kind in _FAILED_EVENTS else "ok"
                found.append(
                    (
                        event.created_at,
                        ActivityFact(
                            id=f"event-{event.id}",
                            at=iso_ms(event.created_at),
                            kind="kanban",
                            agent=profile_of.get(event.run_id) or card.assignee,
                            item=card.id,
                            text=_clean(text, TEXT_MAX) or event.kind,
                            status=status,
                        ),
                    )
                )
            for run in facts.runs:
                recent = run.ended_at is None or run.started_at >= since or run.ended_at >= since
                agent = run.profile or card.assignee
                if recent and agent:
                    found += self._session_activity(workflow, agent, card.id, run)
        found.sort(key=lambda pair: (pair[0], pair[1].id))
        return [fact for _, fact in found[-limit:]]

    def _session_activity(
        self, workflow: Workflow, agent: str, card: str, run: RunFact
    ) -> list[tuple[float, ActivityFact]]:
        found: list[tuple[float, ActivityFact]] = []
        for session in self.sessions.run_facts(agent, card, [run]) or []:
            results = {result.call_id: result for result in session.results if result.call_id}
            for index, call in enumerate(session.calls):
                result = results.get(call.call_id)
                kind, status = "tool", "running"
                if result is not None:
                    outcome = TEXTS.classify_tool_result(
                        result.content, workflow.guard_suffixes, workflow.pr_acceptance
                    ).kind
                    if outcome in _BLOCKING_OUTCOMES:
                        kind, status = "guard", "blocked"
                    else:
                        status = "error" if outcome in _FAILED_OUTCOMES else "ok"
                text = f"{call.tool} : {call.input}" if call.input else call.tool
                found.append(
                    (
                        call.at,
                        ActivityFact(
                            id=f"tool-{session.id}-{call.call_id or index}",
                            at=iso_ms(call.at),
                            kind=kind,
                            agent=agent,
                            item=card,
                            text=_clean(text, TEXT_MAX) or call.tool,
                            status=status,
                        ),
                    )
                )
            for entry in self.journal.entries(agent):
                if entry.session != session.id or entry.kind == "fallback":
                    continue
                failed = entry.kind == "api_failed"
                text = f"{entry.model or '?'} ({entry.provider or '?'})"
                found.append(
                    (
                        entry.at,
                        ActivityFact(
                            id=f"llm-{session.id}-{entry.at:.3f}",
                            at=iso_ms(entry.at),
                            kind="llm",
                            agent=agent,
                            item=card,
                            text=f"{entry.error} : {text}" if failed else text,
                            status="error" if failed else "ok",
                        ),
                    )
                )
        return found

    # --- usage ----------------------------------------------------------------------------------------------------

    def usage(self, workflow: Workflow, days: int, now: float) -> Usage:
        since = now - days * 86_400
        agents: set[str] = set()
        for facts in self.boards.read_board_facts(workflow.board):
            agents |= {run.profile for run in facts.runs if run.profile}
            if facts.card.assignee:
                agents.add(facts.card.assignee)
        models: dict[tuple[str, str], ModelUsage] = {}
        by_agent: dict[str, int] = {}
        by_day: dict[str, list[int]] = {}
        # The detail, by (day, agent, model, provider): the same figures as the three sums, never others.
        detail: dict[tuple[str, str, str, str], UsageRow] = {}

        def row_of(day: str, agent: str, model: str, provider: str, cost: float | None) -> UsageRow:
            return detail.setdefault(
                (day, agent, model, provider),
                UsageRow(
                    day=day,
                    agent=agent,
                    model=model,
                    provider=provider,
                    calls=0,
                    input_tokens=0,
                    output_tokens=0,
                    cost_usd=cost,
                    rate_limited=0,
                    errors=0,
                ),
            )

        for agent in sorted(agents):
            with session_db(self.home, agent) as db:
                rows = []
                if db is not None:
                    with suppress(sqlite3.Error):
                        rows = db.execute(USAGE_SQL, (since,)).fetchall()
            for row in rows:
                key = (str(row["model"]), str(row["provider"] or "?"))
                tokens_in, tokens_out = int(as_number(row["input"])), int(as_number(row["output"]))
                usage = models.setdefault(key, _zero_usage(key[0], key[1], 0.0))
                _add_calls(usage, row, tokens_in, tokens_out)
                by_agent[agent] = by_agent.get(agent, 0) + tokens_in + tokens_out
                by_day.setdefault(str(row["day"]), [0, 0])[0] += tokens_in + tokens_out
                line = row_of(str(row["day"]), agent, key[0], key[1], 0.0)
                _add_calls(line, row, tokens_in, tokens_out)
            for entry in self.journal.entries(agent):
                if entry.kind != "api_failed" or entry.at < since:
                    continue
                name = entry.model or "?"
                usage = next((usage for (model, _), usage in models.items() if model == name), None)
                if usage is None:
                    usage = models.setdefault(
                        (name, entry.provider or "?"), _zero_usage(name, entry.provider or "?", None)
                    )
                # The line of the model as `models` counts it: its provider is that of the model's usage.
                line = row_of(_day(entry.at), agent, usage.model, usage.provider, None)
                if entry.rate_limited:
                    usage.rate_limited += 1
                    line.rate_limited += 1
                    by_day.setdefault(_day(entry.at), [0, 0])[1] += 1
                else:
                    usage.errors += 1
                    line.errors += 1
        return Usage(
            days=days,
            models=sorted(
                models.values(), key=lambda usage: (-(usage.input_tokens + usage.output_tokens), usage.model)
            ),
            agents=[AgentUsage(agent=agent, tokens=tokens) for agent, tokens in sorted(by_agent.items())],
            daily=[DayUsage(day=day, tokens=t, rate_limited=r) for day, (t, r) in sorted(by_day.items())],
            rows=[detail[key] for key in sorted(detail)],
        )


class HermesLiveWatcher:
    """Watches the agents' session bases (`PRAGMA data_version`, one read-only connection per agent) and journals
    (inode and size), cheaply enough to be asked every 300 ms. Every call names its board."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self._connections: dict[str, sqlite3.Connection] = {}
        self._stamps: dict[str, tuple] = {}

    def _agents(self) -> list[str]:
        profiles = self.home / "profiles"
        return sorted(folder.name for folder in profiles.iterdir()) if profiles.is_dir() else []

    def _version(self, agent: str) -> int | None:
        db = self._connections.get(agent)
        try:
            if db is None:
                path = session_base(self.home, agent)
                if path is None:
                    return None
                db = open_session_base(path)
                self._connections[agent] = db
            return db.execute("PRAGMA data_version").fetchone()[0]
        except sqlite3.Error:
            stale = self._connections.pop(agent, None)
            if stale is not None:
                stale.close()
            return None

    def _stamp(self) -> tuple:
        parts = []
        for agent in self._agents():
            journals = []
            for path in journal_paths(self.home, agent):
                with suppress(OSError):
                    stat = path.stat()
                    journals.append((path.name, stat.st_ino, stat.st_size))
            parts.append((agent, self._version(agent), tuple(journals)))
        return tuple(parts)

    def changed(self, board: str) -> bool:
        stamp = self._stamp()
        if self._stamps.get(board) == stamp:
            return False
        self._stamps[board] = stamp
        return True

    def close(self) -> None:
        for db in self._connections.values():
            db.close()
        self._connections.clear()


def make_live_reader(environ: dict[str, str] | None = None) -> HermesLiveReader:
    """The live reader of the Hermès home named by `HERMES_HOME`, its journals dated in the zone of `HERMES_TZ`."""
    return make_board_reader(environ).live_reader(environ)


def make_live_watcher(environ: dict[str, str] | None = None) -> HermesLiveWatcher:
    """A watcher of the sessions and journals of the agents of the Hermès home named by `HERMES_HOME`."""
    return HermesLiveWatcher(hermes_home(environ))
