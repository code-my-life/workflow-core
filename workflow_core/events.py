"""The event journal, the probe of the boards and the SSE stream of their changes (ADR 0011, ADR 0013).

The journal is a SQLite file of the core's own (`events.db` in its state folder), rebuilt from the boards when it is
missing or unreadable. Each rebuild opens an epoch, the Unix second of the rebuild, whose first event is a `reset`.
An event's cursor is `<epoch>:<n>`, its rank in the epoch. The probe asks each declared board every 300 ms whether
its base was written to, and only then rereads its items: each item whose full state differs from the last one the
journal knows (or which appears) becomes a `changed` event.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sqlite3
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, Protocol

from workflow_core.clock import utc_now
from workflow_core.config import Workflow
from workflow_core.model.board import BoardUnavailableError, Item
from workflow_core.model.live import LiveReader, LiveWatcher

STATE_DIR_VARIABLE = "WORKFLOW_CORE_STATE_DIR"
JOURNAL_FILE = "events.db"
PROBE_SECONDS = 0.3
KEEPALIVE_SECONDS = 15.0
# The facts of a board's activity compared at each probe: the most recent.
ACTIVITY_KEPT = 200

SCHEMA = (
    "CREATE TABLE meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL)",
    "CREATE TABLE events (n INTEGER PRIMARY KEY, body TEXT NOT NULL)",
    "CREATE TABLE items (workflow TEXT NOT NULL, item TEXT NOT NULL, state TEXT NOT NULL,"
    " PRIMARY KEY (workflow, item))",
)


def state_dir(environ: dict[str, str] | None = None) -> Path:
    """The core's state folder: WORKFLOW_CORE_STATE_DIR, else $XDG_STATE_HOME/workflow-core, else
    ~/.local/state/workflow-core."""
    env = os.environ if environ is None else environ
    if env.get(STATE_DIR_VARIABLE):
        return Path(env[STATE_DIR_VARIABLE]).expanduser()
    base = (
        Path(env["XDG_STATE_HOME"]).expanduser() if env.get("XDG_STATE_HOME") else Path("~/.local/state").expanduser()
    )
    return base / "workflow-core"


def _canonical(state: dict) -> str:
    return json.dumps(state, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class JournalUnreadableError(Exception):
    """The journal file is missing, or is not a journal: the core rebuilds it."""


class EventJournal:
    """The numbered events of the current epoch and the last known full state of each item."""

    def __init__(self, db: sqlite3.Connection, epoch: int, last: int) -> None:
        self._db = db
        self.epoch = epoch
        self.last = last

    @property
    def cursor(self) -> str:
        return f"{self.epoch}:{self.last}"

    @classmethod
    def open_existing(cls, path: Path) -> EventJournal:
        if not path.is_file():
            raise JournalUnreadableError(f"no journal at {path}")
        db = None
        try:
            db = sqlite3.connect(path, isolation_level=None)
            meta = dict(db.execute("SELECT key, value FROM meta").fetchall())
            db.execute("SELECT count(*) FROM items").fetchone()
            (last,) = db.execute("SELECT coalesce(max(n), 0) FROM events").fetchone()
            first = db.execute("SELECT body FROM events WHERE n = 1").fetchone()
            if "epoch" not in meta or first is None or json.loads(first[0])["kind"] != "reset":
                raise JournalUnreadableError(f"{path} is not a journal of the core")
        except (sqlite3.Error, ValueError, KeyError, TypeError) as error:
            if db is not None:
                db.close()
            raise JournalUnreadableError(f"{path}: {error}") from error
        except JournalUnreadableError:
            db.close()
            raise
        return cls(db, int(meta["epoch"]), int(last))

    @classmethod
    def rebuild(cls, path: Path, epoch: int, current: dict[str, list[dict]]) -> EventJournal:
        """A new journal at `path`, holding the current state of every item, whose first event is a `reset`."""
        path.parent.mkdir(parents=True, exist_ok=True)
        for leftover in (path, *(path.with_name(path.name + suffix) for suffix in ("-journal", "-wal", "-shm"))):
            leftover.unlink(missing_ok=True)
        db = sqlite3.connect(path, isolation_level=None)
        db.execute("BEGIN")
        for statement in SCHEMA:
            db.execute(statement)
        db.execute("INSERT INTO meta (key, value) VALUES ('epoch', ?)", (epoch,))
        db.executemany(
            "INSERT INTO items (workflow, item, state) VALUES (?, ?, ?)",
            [(workflow, item["id"], _canonical(item)) for workflow, items in current.items() for item in items],
        )
        journal = cls(db, epoch, 0)
        journal._append("reset", None, None, None)
        db.execute("COMMIT")
        return journal

    def _append(self, kind: str, workflow: str | None, item: str | None, state: dict | None) -> dict:
        self.last += 1
        event = {
            "cursor": f"{self.epoch}:{self.last}",
            "workflow": workflow,
            "item": item,
            "kind": kind,
            "at": utc_now(),
            "state": state,
        }
        self._db.execute(
            "INSERT INTO events (n, body) VALUES (?, ?)", (self.last, json.dumps(event, ensure_ascii=False))
        )
        return event

    def note(self, kind: str, workflow: str, item: str | None, state: dict) -> dict:
        """One `live` or `activity` event: the live state of an agent, or a new fact of the board."""
        return self._append(kind, workflow, item, state)

    def record(self, workflow: str, items: list[dict]) -> list[dict]:
        """Compare a fresh read of a workflow's items with the last known states; one `changed` event per item that
        differs or appears, in the order read."""
        known = dict(self._db.execute("SELECT item, state FROM items WHERE workflow = ?", (workflow,)).fetchall())
        changed = [item for item in items if known.get(item["id"]) != _canonical(item)]
        if not changed:
            return []
        events = []
        self._db.execute("BEGIN")
        try:
            for item in changed:
                self._db.execute(
                    "INSERT OR REPLACE INTO items (workflow, item, state) VALUES (?, ?, ?)",
                    (workflow, item["id"], _canonical(item)),
                )
                events.append(self._append("changed", workflow, item["id"], item))
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            self.last -= len(events)
            raise
        return events

    def events_after(self, n: int) -> list[dict]:
        rows = self._db.execute("SELECT body FROM events WHERE n > ? ORDER BY n", (n,)).fetchall()
        return [json.loads(body) for (body,) in rows]

    def reset_event(self) -> dict:
        (body,) = self._db.execute("SELECT body FROM events WHERE n = 1").fetchone()
        return json.loads(body)

    def close(self) -> None:
        self._db.close()


class BoardWatcher(Protocol):
    def changed(self, board: str) -> bool: ...

    def close(self) -> None: ...


def frame(event: dict) -> str:
    """One SSE frame: the cursor as its id, the kind as its event, the event as its JSON data."""
    return f"id: {event['cursor']}\nevent: {event['kind']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


class EventHub:
    """Runs the probe, keeps the journal, and feeds every open stream."""

    def __init__(
        self,
        *,
        workflows: dict[str, Workflow],
        watcher: BoardWatcher,
        read_items: Callable[[str], list[Item]],
        journal_path: Path,
        clock: Callable[[], float],
        probe_seconds: float = PROBE_SECONDS,
        keepalive_seconds: float = KEEPALIVE_SECONDS,
        spy: Callable[[str], Any] | None = None,
        live: LiveReader | None = None,
        live_watcher: LiveWatcher | None = None,
    ) -> None:
        self.workflows = workflows
        self.live = live
        self.live_watcher = live_watcher
        # The last live state of each agent and the last facts sent, per workflow: only what differs goes out.
        self._live_known: dict[str, dict[tuple[str, str], dict]] = {}
        self._activity_known: dict[str, dict[str, dict]] = {}
        self.watcher = watcher
        self.read_items = read_items
        self.journal_path = journal_path
        self.clock = clock
        self.probe_seconds = probe_seconds
        self.keepalive_seconds = keepalive_seconds
        self.spy = spy
        self.journal: EventJournal | None = None
        self._subscribers: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None

    def _current_items(self) -> dict[str, list[dict]]:
        current: dict[str, list[dict]] = {}
        for key, workflow in self.workflows.items():
            # A board unreadable at the rebuild is left out: its items come later, as `changed` events.
            with contextlib.suppress(BoardUnavailableError):
                current[key] = [item.model_dump() for item in self.read_items(workflow.board)]
        return current

    def open_journal(self) -> bool:
        """Open the journal, or rebuild it from the boards; True when a new epoch was opened."""
        try:
            self.journal = EventJournal.open_existing(self.journal_path)
            return False
        except JournalUnreadableError:
            self.journal = EventJournal.rebuild(self.journal_path, int(self.clock()), self._current_items())
            return True

    def _read_live(self, key: str, workflow: Workflow) -> tuple[dict, dict] | None:
        try:
            agents = self.live.live(workflow)
            facts = self.live.activity(workflow, ACTIVITY_KEPT, self.clock())
        except Exception:  # a source that cannot be read leaves the live state as it was, never stops the probe
            return None
        return (
            {(agent.agent, agent.item): agent.model_dump() for agent in agents},
            {fact.id: fact.model_dump() for fact in facts},
        )

    def seed_live(self) -> None:
        """What the agents do now, known without an event: a client reads it by the routes."""
        if self.live is None:
            return
        for key, workflow in self.workflows.items():
            read = self._read_live(key, workflow)
            if read is not None:
                self._live_known[key], self._activity_known[key] = read

    def _live_events(self, key: str, workflow: Workflow) -> list[dict]:
        read = self._read_live(key, workflow)
        if read is None:
            return []
        agents, facts = read
        known_agents = self._live_known.get(key, {})
        known_facts = self._activity_known.get(key, {})
        events = [
            self.journal.note("live", key, agent_key[1], state)
            for agent_key, state in agents.items()
            if known_agents.get(agent_key) != state
        ]
        events += [
            self.journal.note("activity", key, fact["item"], fact)
            for fact_id, fact in facts.items()
            if known_facts.get(fact_id) != fact
        ]
        self._live_known[key], self._activity_known[key] = agents, facts
        return events

    async def start(self) -> None:
        self.open_journal()
        self.seed_live()
        self._task = asyncio.create_task(self._probe(), name="workflow-core probe")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        for queue in list(self._subscribers):
            queue.put_nowait(None)
        self.watcher.close()
        if self.live_watcher is not None:
            self.live_watcher.close()
        if self.journal is not None:
            self.journal.close()

    def probe_once(self) -> list[dict]:
        events: list[dict] = []
        for key, workflow in self.workflows.items():
            board_changed = False
            try:
                if self.watcher.changed(workflow.board):
                    items = self.read_items(workflow.board)
                    board_changed = True
                    if self.spy is not None:
                        self.spy(workflow.board)
                    events += self.journal.record(key, [item.model_dump() for item in items])
            except BoardUnavailableError:
                pass
            if self.live is None:
                continue
            try:
                live_changed = self.live_watcher is not None and self.live_watcher.changed(workflow.board)
            except Exception:  # an unreadable session base is no change
                live_changed = False
            if board_changed or live_changed:
                events += self._live_events(key, workflow)
        for event in events:
            for queue in self._subscribers:
                queue.put_nowait(event)
        return events

    async def _probe(self) -> None:
        while True:
            self.probe_once()
            await asyncio.sleep(self.probe_seconds)

    def _backlog(self, cursor: str | None) -> list[dict]:
        """The events a client resuming at `cursor` must get before the live ones."""
        journal = self.journal
        if cursor is None:
            return []
        epoch, _, n = cursor.partition(":")
        if epoch == str(journal.epoch) and n.isdigit() and int(n) <= journal.last:
            return journal.events_after(int(n))
        # Another epoch, or beyond the last event: the client must reread the full state.
        return [journal.reset_event()]

    async def stream(self, cursor: str | None) -> AsyncIterator[str]:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            # Subscribed before the backlog is read: nothing falls between the two, and nothing is sent twice.
            sent = self.journal.last
            for event in self._backlog(cursor):
                yield frame(event)
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), self.keepalive_seconds)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if event is None:
                    return
                if int(event["cursor"].split(":")[1]) <= sent:
                    continue
                yield frame(event)
        finally:
            self._subscribers.discard(queue)
