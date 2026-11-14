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
  station, with a line in the journal once; open, nothing is done.
The root card itself is never handed to an agent, and only the engine finishes it (`workflow_core.signals`, in
`engine.db`):
- a run on a root card is an anomaly, written once per run to the journal and noted for the notifier; while it runs
  (no end), the item is left alone: nothing written, nothing run, its answers neither applied nor refused; once it
  has ended, the item is handled as any other;
- the engine notes each root card it completes, just before the write (taken back when the write fails); a root card
  done that is not noted, at any station, was finished by another: an anomaly written once to the journal and noted,
  and the engine never touches the item again, even after a restart.
A decision is applied this way: `advance` assigns the card to the lane of the station `to`, or of the next station
without it, and completes the root card after the last station; `fail` (a command past its delay or with an unreadable
output included) and an unknown `to` block the card with the station and the reason, its kind never the one of its
previous block; `wait` changes nothing, and the command runs again at the next pass with the next attempt. An answer
for an item without an open wait, as read at the start of the pass, is marked refused. The engine never takes a card
(`claim`): its lock is its own, held in memory.

An answer from GitHub (a gesture of the owner on the item's issue, AD-10) follows three more rules; an answer of the
test machine's tool (`local`) does not:
- a gesture the workflow's manifest does not declare (by a comment), and that is not `unblock`, is refused;
- a gesture commented before the open wait's `scheduled` event is refused (a stale screen), and the next answer of the
  item is tried;
- `unblock`, for an item whose root card is blocked, unblocks the card, is marked applied to
  `<key>:blocked:<card>:<event>` (the card's last `blocked` event), and runs the station's command again at once with
  the answer `{"gesture": "unblock", "note": ...}`, its decision applied; an unblock the board refuses to write is
  marked refused, so that the owner can give it again; for an item whose card is not blocked, it is refused.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from workflow_core.engine.answers import ENGINE_DB, UNBLOCK, Answer, AnswerBox
from workflow_core.events import state_dir
from workflow_core.model.board import BoardReader, BoardUnavailableError, Item, child_cards, child_key
from workflow_core.model.decision import Decision
from workflow_core.model.manifest import ManifestStation as Station
from workflow_core.model.state import CoreState
from workflow_core.model.writer import BoardWriteError, BoardWriter
from workflow_core.notify.notifier import journal_line
from workflow_core.plugin.commands import command_input, run_command
from workflow_core.plugin.manifest import PluggedWorkflow, is_root_card, issue_of, wait_id
from workflow_core.signals import FINISHED_ELSEWHERE, TAKEN, Signals

PASS_SECONDS = 30.0
PREFIX = "workflow-core: moteur"
# The kinds of the engine's blocks, taken in turn on a card: the board sends a card blocked again for the kind of its
# previous block to `triage`, where nothing launches it any more; a kind that never repeats keeps it `blocked`, for the
# owner to unblock.
BLOCK_KINDS = ("needs_input", "capability")


class CommandRunner(Protocol):
    def __call__(self, repo, command: list[str], payload: dict, timeout: float, log, *, label: str) -> Decision: ...


def open_wait(workflow: PluggedWorkflow, item: Item) -> str | None:
    """The id of the item's open wait, `<key>:owner:<card>:<event>` from the card's last `scheduled` event, when the
    card waits (`scheduled`) at the lane of an `owner` station; None otherwise."""
    return wait_id(workflow, item.id, item.status, item.assignee, item.events)


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
        records: Signals | None = None,
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
        # The engine's own finishes and the anomalies it found, in `engine.db` of the core's state folder.
        self.records = records if records is not None else Signals(state_dir() / ENGINE_DB)
        self._guard = threading.Lock()
        self._boards_lock = threading.Lock()
        self._known_boards: set[str] = set()
        self._locks: dict[str, threading.Lock] = {}
        # Launches of each station for each item since the start, by (item, station).
        self._launches: dict[tuple[str, str], int] = {}
        self._told: set[tuple[str, ...]] = set()
        # The runs on root cards already noted in the base, with whether they were still running then.
        self._noted: set[tuple[str, bool]] = set()
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
        unknown: list[Answer] = []
        for answer in self.answers.pending(f"{workflow.key}:") if self.answers is not None else []:
            if answer.source == "github" and answer.gesture == UNBLOCK:
                unblocks.append(answer)
            elif answer.source == "github" and answer.gesture not in workflow.gestures:
                unknown.append(answer)
            else:
                pending.append(answer)
        waiting: set[str] = set()
        blocked: set[str] = set()
        # The items whose root card has a run in progress: left alone, their answers kept for later.
        held: set[str] = set()
        for item in items:
            if self._stopped.is_set():
                return
            if not is_root_card(item, workflow.key):
                continue
            station = workflow.station_of_lane(item.assignee)
            if station is None:
                continue
            element = item.idempotency_key
            if self.records.abandoned(workflow.key, element):
                continue
            if item.status == "done":
                # Only the engine finishes a root card, and notes it just before (AD-4).
                if not self.records.finished_by_engine(workflow.board, item.id):
                    self._finished_elsewhere(workflow, item, station)
                continue
            # Only child cards are handed to agents: a run on a root card means someone took it (AD-4).
            running = [self._taken(workflow, item, station, run) for run in item.runs]
            if any(running):
                held.add(element)
                continue
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
        for answer in unknown:
            if answer.item not in held and not self._stopped.is_set():
                self._refuse(answer, "geste inconnu du workflow")
        for answer in pending:
            if answer.item not in waiting and answer.item not in held and not self._stopped.is_set():
                self._refuse(answer)
        for answer in unblocks:
            if answer.item not in blocked and answer.item not in held and not self._stopped.is_set():
                self._refuse(answer, "carte racine non bloquée")

    def _finished_elsewhere(self, workflow: PluggedWorkflow, item: Item, station: Station) -> None:
        """A root card done that the engine did not finish: noted once, and its item is never touched again."""
        element = item.idempotency_key
        noted = self.records.signal(
            f"{workflow.key}:{FINISHED_ELSEWHERE}:{item.id}",
            workflow=workflow.key,
            item=element,
            card=item.id,
            kind=FINISHED_ELSEWHERE,
            station=station.key,
            detail=f"La carte racine {item.id} a été terminée à la station {station.key} par un autre que le moteur.",
            issue=issue_of(item.body),
            # A card finished before the notifier's switch-on time is never alerted.
            opened_at=item.completed_at,
        )
        if noted:
            self.log(
                f"<4>{PREFIX}, {element} : anomalie : carte racine {item.id} terminée à la station {station.key} "
                "par un autre que le moteur, élément abandonné par le moteur"
            )

    def _taken(self, workflow: PluggedWorkflow, item: Item, station: Station, run) -> bool:
        """A run on a root card: noted once per run. Whether it is still in progress."""
        element = item.idempotency_key
        running = run.ended_at is None
        profile = run.profile or "inconnu"
        key = f"{workflow.key}:{TAKEN}:{item.id}:{run.id}"
        # A run already noted is not written again at each pass; the base's row still holds it after a restart.
        if (key, running) in self._noted:
            return running
        noted = self.records.signal(
            key,
            workflow=workflow.key,
            item=element,
            card=item.id,
            kind=TAKEN,
            station=station.key,
            detail=f"La carte racine {item.id} a été prise par {profile} (run {run.id}, "
            f"{'en cours' if running else 'terminé'}).",
            issue=issue_of(item.body),
            opened_at=run.started_at,
        )
        with self._guard:
            self._noted.add((key, running))
        if noted:
            self.log(
                f"<4>{PREFIX}, {element} : anomalie : carte racine {item.id} prise par {profile} (run {run.id})"
                + (", élément laissé tant que le run dure" if running else "")
            )
        return running

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
        """A blocked root card, and the owner's `unblock`: the card unblocked, the answer marked applied, and the
        station's command run again at once with the answer, its decision applied. An unblock the board refuses is
        marked refused: the owner can give it again."""
        element = item.idempotency_key
        if item.status != "blocked" or not self.answers.is_pending(answer.id):
            return
        event = last_event(item, "blocked")
        wait = f"{workflow.key}:blocked:{item.id}:{event.id if event is not None else 0}"
        # Written before it is marked, under the item's lock: an answer is applied only once its unblock is written.
        try:
            self.writer.unblock(workflow.board, item.id)
        except BoardWriteError:
            if self.answers.mark(answer.id, "refused"):
                self.log(
                    f"<3>{PREFIX}, {element} : réponse {answer.id} ({UNBLOCK}) refusée, la carte {item.id} n'a pas pu "
                    f"être débloquée ({wait}) : le geste peut être reposé"
                )
            raise
        if not self.answers.mark(answer.id, "applied", wait):
            return
        self.log(f"{PREFIX}, {element} : réponse {answer.id} ({UNBLOCK}) appliquée au blocage {wait}")
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
                # Noted before the write, taken back when it fails: a root card done that is not noted was finished
                # by another.
                self.records.note_finish(workflow.board, item.id, element)
                try:
                    self.writer.complete(workflow.board, item.id, result)
                except BoardWriteError:
                    self.records.forget_finish(workflow.board, item.id)
                    raise
                self.log(f"{PREFIX}, {element} : {station.key} rend advance, carte {item.id} terminée ({result})")
                return
        lane = workflow.lane(target.key)
        self.writer.assign(workflow.board, item.id, lane)
        self.log(f"{PREFIX}, {element} : {station.key} rend advance, carte {item.id} passée à {lane}")

    def _fail(self, workflow: PluggedWorkflow, item: Item, station: Station, reason: str) -> None:
        motive = f"Échec de la station {station.key} : {reason}"
        # The card's kind outlives an unblock: the next block takes the other one (see BLOCK_KINDS).
        kind = BLOCK_KINDS[1] if item.block_kind == BLOCK_KINDS[0] else BLOCK_KINDS[0]
        self.writer.block(workflow.board, item.id, motive, kind=kind)
        self.log(f"<4>{PREFIX}, {item.idempotency_key} : carte {item.id} bloquée : {motive} (sorte {kind})")


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
