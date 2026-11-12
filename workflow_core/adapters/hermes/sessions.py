"""The sessions of each Hermès agent (`profiles/<agent>/state.db`), read-only (ADR 0012).

Ported from factory-live at commit d47f235: `SessionReader.runFacts` and `sessionsActiveAt`
(apps/server/src/adapters/hermes/sessions.ts), and the session references of a run (`runRefOf`,
apps/server/src/application/diagnosis.ts). The transcript and the usage per model, which factory-live also reads,
are not ported: the diagnosis does not use them.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote as url_quote

from workflow_core.adapters.hermes.agent_log import safe_file
from workflow_core.model.facts import RunFact, SessionFacts, ToolCallFact, ToolResultFact
from workflow_core.text import js_regex, js_trim, redact_secrets, utf16_slice

_NAME = js_regex(r"^[\w-]+$")
_CARD = js_regex(r"^t_[0-9a-f]{8}$")
# The session `hermes --resume` names in a run's error or output.
RESUME = js_regex(r"hermes --resume (\d{8}_\d{6}_[0-9a-f]{6}) -p (\S+)")
# Of a tool's result, the diagnosis keeps the start (the refusal) and the end (the exit code).
RESULT_HEAD = 1_500
RESULT_TAIL = 500
INPUT_MAX = 500
# A worker's session starts a few seconds after its run.
SESSION_START_SLACK = 120

# The tables and columns read, declared for the contract test (contract.py).
SESSION_COLUMNS = ("id", "started_at", "ended_at")
MESSAGE_COLUMNS = ("id", "session_id", "role", "content", "tool_call_id", "tool_calls", "tool_name", "timestamp")
# The JSON fields read in each element of `messages.tool_calls`.
TOOL_CALL_FIELDS = ("id", "call_id", "function.name", "function.arguments")


def _valid_array(column: str) -> str:
    return f"CASE WHEN json_valid({column}) AND json_type({column}) = 'array' THEN {column} ELSE '[]' END"


# The tool calls of a session: name, id, and the telling argument (command, path, name).
CALLS_SQL = f"""
  SELECT m.timestamp AS at, json_extract(c.value, '$.id') AS id,
         json_extract(c.value, '$.call_id') AS call_id,
         json_extract(c.value, '$.function.name') AS tool,
         CASE WHEN json_valid(json_extract(c.value, '$.function.arguments')) THEN coalesce(
           json_extract(json_extract(c.value, '$.function.arguments'), '$.command'),
           json_extract(json_extract(c.value, '$.function.arguments'), '$.path'),
           json_extract(json_extract(c.value, '$.function.arguments'), '$.name')) END AS input
  FROM messages m, json_each({_valid_array("m.tool_calls")}) c
  WHERE m.session_id = ? AND m.role = 'assistant' AND m.tool_calls IS NOT NULL
  ORDER BY m.id, c.key"""

# The tool results of a session, start and end only: a content can weigh hundreds of KB.
RESULTS_SQL = f"""
  SELECT timestamp AS at, tool_call_id, tool_name, length(content) AS size,
         substr(content, 1, {RESULT_HEAD}) AS head, substr(content, -{RESULT_TAIL}) AS tail
  FROM messages WHERE session_id = ? AND role = 'tool' ORDER BY id"""

FIRST_PROMPT_SQL = """
  SELECT s.id FROM sessions s WHERE s.started_at BETWEEN ? AND ? AND substr((
    SELECT m.content FROM messages m WHERE m.session_id = s.id AND m.role = 'user'
    ORDER BY m.id LIMIT 1), 1, ?) = ?"""


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _trimmed(value: object) -> str | None:
    """factory-live's `text` of the application: a non-blank string, trimmed."""
    return js_trim(value) or None if isinstance(value, str) else None


def resume_session_of(text: str | None) -> str | None:
    match = RESUME.search(text) if text else None
    return match[1] if match else None


def _number(value: object) -> float:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


def _call_fact(row: sqlite3.Row) -> ToolCallFact:
    raw = _text(row["input"])
    trimmed = js_trim(raw) if raw is not None else ""
    return ToolCallFact(
        at=_number(row["at"]),
        tool=_text(row["tool"]) if _text(row["tool"]) is not None else "?",
        input=utf16_slice(redact_secrets(trimmed), INPUT_MAX) if trimmed else None,
        call_id=_text(row["id"]) if _text(row["id"]) is not None else _text(row["call_id"]),
    )


def _result_fact(row: sqlite3.Row) -> ToolResultFact:
    head = _text(row["head"]) or ""
    size = row["size"] if isinstance(row["size"], int) else len(head.encode("utf-16-le", "surrogatepass")) // 2
    content = f"{head}\n…\n{_text(row['tail']) or ''}" if size > RESULT_HEAD + RESULT_TAIL else head
    return ToolResultFact(
        at=_number(row["at"]),
        tool=_text(row["tool_name"]) if _text(row["tool_name"]) is not None else "?",
        call_id=_text(row["tool_call_id"]),
        content=redact_secrets(content),
    )


def session_base(home: Path, agent: str) -> Path | None:
    """The agent's session base, for a safe name and without a symbolic link on the way."""
    return safe_file(home / "profiles", agent, "state.db") if _NAME.fullmatch(agent) else None


def open_session_base(path: Path) -> sqlite3.Connection:
    """A read-only connection (SQLite URI `mode=ro`): SQLite refuses every write, the base is never modified."""
    db = sqlite3.connect(f"file:{url_quote(str(path))}?mode=ro", uri=True, isolation_level=None)
    db.row_factory = sqlite3.Row
    return db


class SessionReader:
    """Reads the agents' session bases under Hermès' home, read-only. Each reading opens its own connection and
    closes it: the API serves its requests from several threads, and a SQLite connection belongs to its thread."""

    def __init__(self, home: Path) -> None:
        self.home = home

    @contextmanager
    def _db(self, agent: str) -> Iterator[sqlite3.Connection | None]:
        path = session_base(self.home, agent)
        if path is None:
            yield None
            return
        try:
            db = open_session_base(path)
        except sqlite3.Error:
            yield None
            return
        try:
            yield db
        finally:
            db.close()

    def _session_of(self, db: sqlite3.Connection, card: str, run: RunFact) -> str | None:
        """A run's session, never guessed: the one Hermès noted, else the one of `hermes --resume`, else the only
        session launched with the run whose first message is « work kanban task <card> »."""
        noted = _trimmed(run.metadata.get("worker_session_id"))
        resumed = resume_session_of(run.error) or resume_session_of(_trimmed(run.metadata.get("worker_output")))
        for session in (noted, resumed):
            if session and db.execute("SELECT 1 FROM sessions WHERE id = ?", (session,)).fetchone():
                return session
        if not _CARD.fullmatch(card):
            return None
        prompt = f"work kanban task {card}"
        rows = db.execute(
            FIRST_PROMPT_SQL, (run.started_at - 5, run.started_at + SESSION_START_SLACK, len(prompt), prompt)
        ).fetchall()
        return str(rows[0]["id"]) if len(rows) == 1 else None

    def run_facts(self, agent: str, card: str, runs: list[RunFact]) -> list[SessionFacts] | None:
        with self._db(agent) as db:
            if db is None:
                return None
            try:
                facts: list[SessionFacts] = []
                seen: set[str] = set()
                for run in runs:
                    session = self._session_of(db, card, run)
                    if not session or session in seen:
                        continue
                    seen.add(session)
                    facts.append(
                        SessionFacts(
                            id=session,
                            run_id=run.id,
                            calls=[_call_fact(row) for row in db.execute(CALLS_SQL, (session,))],
                            results=[_result_fact(row) for row in db.execute(RESULTS_SQL, (session,))],
                        )
                    )
            except sqlite3.Error:
                return None
            return facts

    def open_sessions(self, agent: str, at: float) -> list[str] | None:
        with self._db(agent) as db:
            if db is None:
                return None
            try:
                rows = db.execute(
                    "SELECT id FROM sessions WHERE started_at <= ? AND (ended_at IS NULL OR ended_at >= ?)", (at, at)
                ).fetchall()
            except sqlite3.Error:
                return None
            return [str(row["id"]) for row in rows]

    def close(self) -> None:
        """Nothing to close: no connection is kept between readings."""


def make_session_reader(environ: dict[str, str] | None = None) -> SessionReader:
    """The session reader of the Hermès home named by `HERMES_HOME`."""
    from workflow_core.adapters.hermes.board import hermes_home

    return SessionReader(hermes_home(environ))
