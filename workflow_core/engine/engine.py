"""The engine's pass: every 30 seconds in the service, in normal mode only, for each workflow plugged by its manifest
(ADR 0004, amended; ADR 0005; ADR 0019).

A pass creates the workflow's board when its base does not exist yet (once), reads its cards again, and knows the root
cards by their idempotency key, `<key>:<local id>` without `/` (a child card's key is `<item>/<station>/<passage>`).
Under the item's own lock, with the card read again:
- a ready root card at the lane `<key>-<station>` of a `code` station has the station's command run, and its decision
  applied;
- a ready root card at the lane of an `owner` station is parked (`schedule`) with its reason: the item waits for its
  owner. The wait's id is `<key>:owner:<card>:<event>`, the event being the card's last `scheduled` event;
- a root card waiting at an `owner` station, with an answer of the owner in the answer box, has the station's command
  run with that answer. The answer is marked applied to the wait once the command is back, before any write, so it is
  never handed to a command twice; `wait` leaves the card waiting, any other decision unblocks it and is applied;
- a ready root card at the lane of an `agent` station gets a child card at each passage, assigned to the station's
  profile, with the idempotency key `<item>/<station>/<passage>` and the item's id on the first line of its body
  (its issue's address on the second). The child is tied to its item by this key only, never by a parent or a link
  of the runtime. The passage in progress began at the root card's last `assigned` event (the root card's creation
  without one): its child is the latest of the station, when its `created` event (or its first event) came after.
  Without one, a new child is created, its passage one more than the station's children. The child done, the root
  card is advanced as by an `advance` without `to`; blocked (or in triage) or archived, the root card stays at its
  station, with a line in the journal once; open, nothing is done. The root card itself is never handed to an agent:
  a root card with a run is an anomaly, written once per run to the journal, and the item is handled as any other;
  a root card done at a station that is not the last one was finished by another than the engine: an anomaly,
  written once to the journal, without any write.
A decision is applied this way: `advance` assigns the card to the lane of the station `to`, or of the next station
without it, and completes the root card after the last station; `fail` (a command past its delay or with an unreadable
output included) and an unknown `to` block the card with the station and the reason; `wait` changes nothing, and the
command runs again at the next pass with the next attempt. An answer for an item without an open wait, as read at the
start of the pass, is marked refused. The engine never takes a card (`claim`): its lock is its own, held in memory.

An answer from GitHub (a gesture of the owner on the item's issue, AD-10) follows three more rules; an answer of the
test machine's tool (`local`) does not:
- a gesture the workflow's manifest does not declare (by a comment), and that is not `unblock`, is refused;
- a gesture commented before the open wait's `scheduled` event is refused (a stale screen), and the next answer of the
  item is tried;
- `unblock`, for an item whose root card is blocked, is marked applied to `<key>:blocked:<card>:<event>` (the card's
  last `blocked` event), unblocks the card, and runs the station's command again at once with the answer
  `{"gesture": "unblock", "note": ...}`, its decision applied; for an item whose card is not blocked, it is refused.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import threading
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from workflow_core.engine.answers import UNBLOCK, Answer, AnswerBox
from workflow_core.model.board import BoardReader, BoardUnavailableError, Item, child_cards, child_key
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


def open_wait(workflow: PluggedWorkflow, item: Item) -> str | None:
    """The id of the item's open wait, `<key>:owner:<card>:<event>` from the card's last `scheduled` event, when the
    card waits (`scheduled`) at the lane of an `owner` station; None otherwise."""
    station = workflow.station_of_lane(item.assignee)
    if item.status != "scheduled" or station is None or station.nature != "owner":
        return None
    event = next((event for event in reversed(item.events) if event.kind == "scheduled"), None)
    return None if event is None else f"{workflow.key}:owner:{item.id}:{event.id}"


def last_event(item: Item, kind: str):
    """The card's last event of this kind, or None."""
    return next((event for event in reversed(item.events) if event.kind == kind), None)


def stale(answer: Answer, item: Item) -> bool:
    """Whether a GitHub answer was given before the item's open wait was opened (the same second is not)."""
    if answer.source != "github":
        return False
    opened = last_event(item, "scheduled")
    if opened is None:
        return False
    try:
        given = datetime.fromisoformat(answer.received_at).timestamp()
    except ValueError:
        return False
    return int(given) < opened.created_at


def current_child(item: Item, children: list[Item]) -> Item | None:
    """The child card of the passage in progress at the root card's station, among the station's children by passage:
    the latest one, when it was created after the root card arrived at the station (its last `assigned` event, 0
    without one; event ids grow within a board). A child without any event is taken as current: a new passage is
    never created on a guess."""
    if not children:
        return None
    latest = children[-1]
    if not latest.events:
        return latest
    arrived = last_event(item, "assigned")
    created = last_event(latest, "created") or latest.events[0]
    return latest if created.id > (arrived.id if arrived is not None else 0) else None


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
        answers: AnswerBox | None = None,
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
        # The owner's answers; without a box, no answer is ever read.
        self.answers = answers
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

    def ensure_board(self, workflow: PluggedWorkflow) -> None:
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
        self.ensure_board(workflow)
        items = self.reader.read_items(workflow.board)
        # The answers are read after the cards: an answer applied by another pass meanwhile is no longer pending.
        pending: list[Answer] = []
        unblocks: list[Answer] = []
        for answer in self.answers.pending(f"{workflow.key}:") if self.answers is not None else []:
            if answer.source == "github" and answer.gesture == UNBLOCK:
                unblocks.append(answer)
            elif answer.source == "github" and answer.gesture not in workflow.gestures:
                if not self._stopped.is_set():
                    self._refuse(answer, "geste inconnu du workflow")
            else:
                pending.append(answer)
        waiting: set[str] = set()
        blocked: set[str] = set()
        for item in items:
            if self._stopped.is_set():
                return
            if not is_root_card(item, workflow.key):
                continue
            station = workflow.station_of_lane(item.assignee)
            if station is None:
                continue
            element = item.idempotency_key
            if item.runs:
                # Only child cards are handed to agents: a run on a root card means someone took it (AD-4).
                run = item.runs[-1]
                self._tell_once(
                    (element, "taken", str(run.id)),
                    f"<4>{PREFIX}, {element} : anomalie : carte racine {item.id} prise par {run.profile or 'inconnu'} "
                    f"(run {run.id})",
                )
            if item.status == "done" and workflow.next_station(station.key) is not None:
                # The engine completes a root card only after the last station (AD-4).
                self._tell_once(
                    (element, "finished", station.key),
                    f"<4>{PREFIX}, {element} : anomalie : carte racine {item.id} terminée à la station {station.key} "
                    "par un autre que le moteur",
                )
            if open_wait(workflow, item) is not None:
                waiting.add(element)
                answers = [answer for answer in pending if answer.item == element]
                if answers:
                    self._under_lock(
                        workflow, item, lambda fresh, s=station, a=answers: self._resume(workflow, fresh, s, a)
                    )
            elif item.status == "blocked":
                blocked.add(element)
                answer = next((answer for answer in unblocks if answer.item == element), None)
                if answer is not None:
                    self._under_lock(
                        workflow, item, lambda fresh, s=station, a=answer: self._unblock(workflow, fresh, s, a)
                    )
            elif item.status == "ready" and station.nature == "owner":
                self._under_lock(workflow, item, lambda fresh, s=station: self._park(workflow, fresh, s))
            elif item.status == "ready" and station.nature == "agent":
                self._under_lock(workflow, item, lambda fresh, s=station: self._agent(workflow, fresh, s))
            elif item.status == "ready":
                self._under_lock(workflow, item, lambda fresh, s=station: self._step(workflow, fresh, s))
        for answer in pending:
            if answer.item not in waiting and not self._stopped.is_set():
                self._refuse(answer)
        for answer in unblocks:
            if answer.item not in blocked and not self._stopped.is_set():
                self._refuse(answer, "carte racine non bloquée")

    def _under_lock(self, workflow: PluggedWorkflow, item: Item, action: Callable[[Item], None]) -> None:
        """Run the action on the card read again under the item's lock; an item another pass holds is skipped."""
        lock = self._lock_of(item.idempotency_key)
        if not lock.acquire(blocking=False):
            return
        try:
            # The cards were read before a long command of this pass may have run: under the item's lock, the card is
            # read again, and left alone if another pass moved it meanwhile.
            fresh = next((card for card in self.reader.read_items(workflow.board) if card.id == item.id), None)
            if fresh is None or fresh.status != item.status or fresh.assignee != item.assignee:
                return
            action(fresh)
        except (BoardWriteError, BoardUnavailableError) as error:
            self.log(f"<3>{PREFIX}, {item.idempotency_key} : {error}")
        finally:
            lock.release()

    def _attempt(self, element: str, station: Station) -> int:
        with self._guard:
            attempt = self._launches.get((element, station.key), 0) + 1
            self._launches[(element, station.key)] = attempt
        return attempt

    def _launch(self, workflow: PluggedWorkflow, item: Item, station: Station, answer: dict | None = None) -> Decision:
        element = item.idempotency_key
        attempt = self._attempt(element, station)
        payload = command_input(
            item=element,
            issue=issue_of(item.body),
            title=item.title,
            station=station.key,
            attempt=attempt,
            answer=answer,
        )
        self.log(f"{PREFIX}, {element} : commande de {station.key} lancée (essai {attempt})")
        label = f"moteur, {element} {station.key}"
        return self.run(workflow.repo, station.command, payload, station.timeout_seconds, self.log, label=label)

    def _step(self, workflow: PluggedWorkflow, item: Item, station: Station) -> None:
        """A ready card at a `code` station: run its command and apply its decision."""
        if station.nature != "code":
            self._tell_once(
                (item.idempotency_key, station.key, "nature"),
                f"{PREFIX}, {item.idempotency_key} à la station {station.key} ({station.nature}) : "
                "rien à faire à cette étape",
            )
            return
        self._apply(workflow, item, station, self._launch(workflow, item, station))

    def _park(self, workflow: PluggedWorkflow, item: Item, station: Station) -> None:
        """A ready card at an `owner` station: it waits for its owner."""
        element = item.idempotency_key
        self.writer.schedule(workflow.board, item.id, f"En attente du propriétaire à la station {station.key}")
        fresh = next((card for card in self.reader.read_items(workflow.board) if card.id == item.id), None)
        wait = open_wait(workflow, fresh) if fresh is not None else None
        self.log(f"{PREFIX}, {element} : carte {item.id} en attente du propriétaire, attente {wait} ouverte")

    def _agent(self, workflow: PluggedWorkflow, item: Item, station: Station) -> None:
        """A ready root card at an `agent` station: its child card of the passage in progress is created when it has
        none, and its end moves the root card on."""
        element = item.idempotency_key
        children = child_cards(self.reader.read_items(workflow.board), item, station.key)
        child = current_child(item, children)
        if child is None:
            passage = len(children) + 1
            issue = issue_of(item.body)
            card = self.writer.create_child_card(
                workflow.board,
                title=f"{element} · {station.key} (passage {passage})",
                profile=station.profile,
                key=child_key(item, station.key, passage),
                body="\n".join([element] + ([issue] if issue else [])),
            )
            self.log(
                f"{PREFIX}, {element} : carte fille {card} demandée à la station {station.key} (créée, ou rendue par "
                f"sa clé si elle existait déjà) (passage {passage}), confiée à {station.profile}"
            )
            return
        if child.status == "done":
            reason = f"carte fille {child.id} terminée"
            self._apply(workflow, item, station, Decision(decision="advance", reason=reason))
        elif child.status == "archived":
            self._tell_once(
                (element, child.id, "archived"),
                f"<4>{PREFIX}, {element} : carte fille {child.id} archivée à la station {station.key}, carte racine "
                f"{item.id} laissée à la station, aucun nouveau passage",
            )
        elif child.status in ("blocked", "triage"):
            event = next(
                (event for event in reversed(child.events) if event.kind in ("blocked", "block_loop_detected")), None
            )
            reason = event.payload.get("reason") if event is not None else None
            self._tell_once(
                (element, child.id, "blocked", str(event.id if event is not None else 0)),
                f"<4>{PREFIX}, {element} : carte fille {child.id} bloquée à la station {station.key} "
                f"({reason or 'sans raison'}), carte racine {item.id} laissée à la station",
            )

    def _resume(self, workflow: PluggedWorkflow, item: Item, station: Station, answers: list[Answer]) -> None:
        """A card waiting at an `owner` station, and the answers for its item: the command decides on the first one
        that answers this wait, once; a GitHub answer given before the wait opened is refused on the way."""
        element = item.idempotency_key
        wait = open_wait(workflow, item)
        if wait is None:
            return
        for answer in answers:
            # The pass's snapshot of the answers may be stale: another pass may have applied this one meanwhile, with
            # a `wait` that left the card where it was.
            if not self.answers.is_pending(answer.id):
                continue
            if stale(answer, item):
                if self.answers.mark(answer.id, "refused"):
                    self.log(
                        f"<4>{PREFIX}, {element} : réponse {answer.id} ({answer.gesture}) refusée, donnée avant "
                        f"l'ouverture de l'attente {wait}"
                    )
                continue
            self._answer_wait(workflow, item, station, answer, wait)
            return

    def _answer_wait(self, workflow: PluggedWorkflow, item: Item, station: Station, answer: Answer, wait: str) -> None:
        element = item.idempotency_key
        decision = self._launch(workflow, item, station, {"gesture": answer.gesture, "note": answer.note})
        # Marked before any write: whatever happens next, the answer is never handed to a command again.
        if not self.answers.mark(answer.id, "applied", wait):
            self.log(f"{PREFIX}, {element} : réponse {answer.id} déjà traitée, décision de {station.key} ignorée")
            return
        self.log(f"{PREFIX}, {element} : réponse {answer.id} ({answer.gesture}) appliquée à l'attente {wait}")
        if decision.decision == "wait":
            self.log(f"{PREFIX}, {element} : {station.key} rend wait ({decision.reason}), carte laissée en attente")
            return
        try:
            self.writer.unblock(workflow.board, item.id)
            self._apply(workflow, item, station, decision)
        except BoardWriteError:
            self.log(
                f"<3>{PREFIX}, {element} : réponse {answer.id} ({answer.gesture}) appliquée à l'attente {wait}, "
                f"mais la décision de {station.key} n'a pas pu être écrite"
            )
            raise

    def _unblock(self, workflow: PluggedWorkflow, item: Item, station: Station, answer: Answer) -> None:
        """A blocked root card, and the owner's `unblock`: marked applied, the card unblocked, and the station's
        command run again at once with the answer, its decision applied."""
        element = item.idempotency_key
        if item.status != "blocked" or not self.answers.is_pending(answer.id):
            return
        event = last_event(item, "blocked")
        wait = f"{workflow.key}:blocked:{item.id}:{event.id if event is not None else 0}"
        # Marked before any write: whatever happens next, the answer is never applied again.
        if not self.answers.mark(answer.id, "applied", wait):
            return
        self.log(f"{PREFIX}, {element} : réponse {answer.id} ({UNBLOCK}) appliquée au blocage {wait}")
        try:
            self.writer.unblock(workflow.board, item.id)
        except BoardWriteError:
            self.log(
                f"<3>{PREFIX}, {element} : réponse {answer.id} ({UNBLOCK}) appliquée au blocage {wait}, "
                f"mais la carte {item.id} n'a pas pu être débloquée"
            )
            raise
        if station.command is None or self._stopped.is_set():
            return
        decision = self._launch(workflow, item, station, {"gesture": UNBLOCK, "note": answer.note})
        self._apply(workflow, item, station, decision)

    def _refuse(self, answer: Answer, why: str = "aucune attente ouverte") -> None:
        """An answer that answers nothing: refused, under the item's lock, without any write."""
        lock = self._lock_of(answer.item)
        if not lock.acquire(blocking=False):
            return
        try:
            if self.answers.mark(answer.id, "refused"):
                self.log(f"<4>{PREFIX}, {answer.item} : réponse {answer.id} ({answer.gesture}) refusée, {why}")
        finally:
            lock.release()

    def _apply(self, workflow: PluggedWorkflow, item: Item, station: Station, decision: Decision) -> None:
        element = item.idempotency_key
        if decision.decision == "wait":
            self.log(f"{PREFIX}, {element} : {station.key} rend wait ({decision.reason}), carte inchangée")
            return
        if decision.decision == "fail":
            self._fail(workflow, item, station, decision.reason)
            return
        if decision.to is not None:
            target = workflow.station(decision.to)
            if target is None:
                self._fail(workflow, item, station, f"station « {decision.to} » inconnue")
                return
        else:
            target = workflow.next_station(station.key)
            if target is None:
                # Only here does the engine finish a root card (AD-4). The runtime refuses an empty result.
                result = decision.reason or f"élément terminé à la station {station.key}"
                self.writer.complete(workflow.board, item.id, result)
                self.log(f"{PREFIX}, {element} : {station.key} rend advance, carte {item.id} terminée ({result})")
                return
        lane = workflow.lane(target.key)
        self.writer.assign(workflow.board, item.id, lane)
        self.log(f"{PREFIX}, {element} : {station.key} rend advance, carte {item.id} passée à {lane}")

    def _fail(self, workflow: PluggedWorkflow, item: Item, station: Station, reason: str) -> None:
        motive = f"Échec de la station {station.key} : {reason}"
        self.writer.block(workflow.board, item.id, motive)
        self.log(f"<4>{PREFIX}, {item.idempotency_key} : carte {item.id} bloquée : {motive}")


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
