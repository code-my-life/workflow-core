"""The engine's pass: every 30 seconds in the service, in normal mode only, for each workflow plugged by its manifest
(ADR 0004, amended; ADR 0005; ADR 0019).

A pass creates the workflow's board when its base does not exist yet (once), reads its cards again, and knows the root
cards by their idempotency key, `<key>:<local id>` without `/` (a child card's key is `<item>/<station>/<passage>`).
For each ready root card assigned to the lane `<key>-<station>` of a `code` station, under the item's own lock, it runs
the station's command and applies its decision: `advance` assigns the card to the lane of the station `to`, or of the
next station without it. The engine never takes a card (`claim`): its lock is its own, held in memory.

At this stage, `wait`, `fail`, the last station, an unknown `to` and the `owner` stations change nothing and leave a
line in the journal.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import threading
from collections.abc import Callable
from typing import Protocol

from workflow_core.model.board import BoardReader, BoardUnavailableError, Item
from workflow_core.model.decision import Decision
from workflow_core.model.manifest import ManifestStation as Station
from workflow_core.model.state import CoreState
from workflow_core.model.writer import BoardWriteError, BoardWriter
from workflow_core.notify.notifier import journal_line
from workflow_core.plugin.commands import command_input, run_command
from workflow_core.plugin.manifest import PluggedWorkflow, is_root_card

PASS_SECONDS = 30.0
# The first GitHub issue address of a card's body is its item's issue.
ISSUE_URL = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/issues/\d+", re.ASCII)
PREFIX = "workflow-core: moteur"


class CommandRunner(Protocol):
    def __call__(self, repo, command: list[str], payload: dict, timeout: float, log, *, label: str) -> Decision: ...


def issue_of(body: str | None) -> str | None:
    found = ISSUE_URL.search(body or "")
    return found[0] if found else None


class Engine:
    def __init__(
        self,
        *,
        workflows: list[PluggedWorkflow],
        reader: BoardReader,
        writer: BoardWriter,
        state: CoreState,
        run: CommandRunner = run_command,
        log: Callable[[str], None] = journal_line,
        confirm_version: Callable[[], bool] | None = None,
    ) -> None:
        self.workflows = workflows
        self.reader = reader
        self.writer = writer
        self.state = state
        self.run = run
        self.log = log
        # Whether the runtime's version is confirmed since the start (read again when it is not yet): the service's
        # version watch. False stops the pass before anything is read, run or written.
        self.confirm_version = confirm_version
        self._guard = threading.Lock()
        self._boards_lock = threading.Lock()
        self._known_boards: set[str] = set()
        self._locks: dict[str, threading.Lock] = {}
        # Launches of each station for each item since the start, by (item, station).
        self._launches: dict[tuple[str, str], int] = {}
        self._told: set[tuple[str, ...]] = set()
        self._mode: str | None = None
        # Set by `stop()`: a pass still running starts no new command after it.
        self._stopped = threading.Event()

    def stop(self) -> None:
        """No pass launches a new command once stopped; a command already running ends on its own."""
        self._stopped.set()

    def run_pass(self) -> None:
        """One pass over every plugged workflow. Never raises: a failure is written to the journal, and the next pass
        tries again. In observer mode, or while the runtime's version is not confirmed, nothing is read, run or
        written."""
        mode = self.state.mode
        if mode != self._mode:
            self._mode = mode
            if mode == "normal":
                self.log(f"{PREFIX}, mode normal : les passes lancent les commandes et écrivent")
            else:
                self.log(f"{PREFIX}, mode observateur : aucune commande lancée, rien d'écrit")
        if mode != "normal":
            return
        if self.confirm_version is not None and not self.confirm_version():
            return
        for workflow in self.workflows:
            try:
                self._workflow_pass(workflow)
            except (BoardWriteError, BoardUnavailableError) as error:
                self.log(f"<3>{PREFIX}, {workflow.key} : {error}")
            except Exception as error:  # never an exception out of the pass
                self.log(f"<3>{PREFIX}, {workflow.key} : {type(error).__name__}: {error}")

    def _ensure_board(self, workflow: PluggedWorkflow) -> None:
        """Create the workflow's board when its base does not exist; a board created or found is not checked again."""
        with self._boards_lock:
            if workflow.board in self._known_boards:
                return
            if not self.writer.has_board(workflow.board):
                self.writer.create_board(workflow.board)
                self.log(f"{PREFIX}, {workflow.key} : board {workflow.board} créé")
            self._known_boards.add(workflow.board)

    def _lock_of(self, item: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(item, threading.Lock())

    def _tell_once(self, key: tuple[str, ...], line: str) -> None:
        with self._guard:
            if key in self._told:
                return
            self._told.add(key)
        self.log(line)

    def _workflow_pass(self, workflow: PluggedWorkflow) -> None:
        self._ensure_board(workflow)
        for item in self.reader.read_items(workflow.board):
            if self._stopped.is_set():
                return
            if item.status != "ready" or not is_root_card(item, workflow.key):
                continue
            station = workflow.station_of_lane(item.assignee)
            if station is None:
                continue
            lock = self._lock_of(item.idempotency_key)
            if not lock.acquire(blocking=False):
                continue
            try:
                # The cards were read before a long command of this pass may have run: under the item's lock, the
                # card is read again, and left alone if another pass moved it meanwhile.
                fresh = next((card for card in self.reader.read_items(workflow.board) if card.id == item.id), None)
                if fresh is None or fresh.status != "ready" or fresh.assignee != item.assignee:
                    continue
                self._step(workflow, fresh, station)
            except (BoardWriteError, BoardUnavailableError) as error:
                self.log(f"<3>{PREFIX}, {item.idempotency_key} : {error}")
            finally:
                lock.release()

    def _step(self, workflow: PluggedWorkflow, item: Item, station: Station) -> None:
        element = item.idempotency_key
        if station.nature != "code":
            self._tell_once(
                (element, station.key, "nature"),
                f"{PREFIX}, {element} à la station {station.key} ({station.nature}) : rien à faire à cette étape",
            )
            return
        with self._guard:
            attempt = self._launches.get((element, station.key), 0) + 1
            self._launches[(element, station.key)] = attempt
        label = f"moteur, {element} {station.key}"
        payload = command_input(
            item=element, issue=issue_of(item.body), title=item.title, station=station.key, attempt=attempt
        )
        self.log(f"{PREFIX}, {element} : commande de {station.key} lancée (essai {attempt})")
        decision = self.run(workflow.repo, station.command, payload, station.timeout_seconds, self.log, label=label)
        if decision.decision != "advance":
            said = f"{decision.decision} ({decision.reason})"
            self.log(f"{PREFIX}, {element} : {station.key} rend {said}, carte inchangée")
            return
        target = workflow.station(decision.to) if decision.to is not None else workflow.next_station(station.key)
        if target is None:
            why = f"station « {decision.to} » inconnue" if decision.to is not None else "dernière station"
            self.log(f"{PREFIX}, {element} : {station.key} rend advance, {why} : carte inchangée")
            return
        lane = workflow.lane(target.key)
        self.writer.assign(workflow.board, item.id, lane)
        self.log(f"{PREFIX}, {element} : {station.key} rend advance, carte {item.id} passée à {lane}")


class EngineRunner:
    """Launches the engine's pass every 30 seconds in the service, off the event loop, without waiting for the
    previous one: an item a running pass still holds is skipped by its lock."""

    def __init__(self, engine: Engine, every: float = PASS_SECONDS) -> None:
        self.engine = engine
        self.every = every
        self._task: asyncio.Task | None = None
        self._passes: set[asyncio.Task] = set()

    async def _loop(self) -> None:
        while True:
            running = asyncio.create_task(asyncio.to_thread(self.engine.run_pass))
            self._passes.add(running)
            running.add_done_callback(self._passes.discard)
            await asyncio.sleep(self.every)

    async def start(self) -> None:
        if self.engine.workflows and self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self.engine.stop()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        # A pass in its thread cannot be interrupted: its current command runs to its end and its decision is still
        # applied, but the engine's stop flag keeps the pass from launching any other command. Cancelling its task only
        # stops waiting for it.
        for running in list(self._passes):
            running.cancel()
