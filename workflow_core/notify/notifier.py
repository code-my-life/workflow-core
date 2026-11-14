"""The notifier's pass: every 30 seconds in the service, one alert in its issue for each wait of a workflow whose
entry asks for it (`notify = true`) or that is plugged by its manifest, never twice for the same key, and nothing at
all when switched off or when the contract is broken (ADR 0009, ADR 0019). It only reads and posts: it writes neither
to the runtime nor to the engine's base.

A wait is a blocked card, or a ready card held by the active pull request guard for the workflow's
`held_after_minutes`, both recognised by the diagnosis' triggers (one rule in the repository): its key is
`<workflow>:<trigger id>`, `factory:needs_input:t_ab12cd34:5509`, `factory:held_active_pr:t_ab12cd34:1794500000`.
Before posting a key its register does not hold,
the notifier reads every comment of the issue and posts nothing when the App already wrote the wait's marker there:
a lost register or a rebuilt journal never posts an alert twice.

A workflow plugged by its manifest has three more kinds of waits, under the same switch, switch-on time, list of
repositories, register and marker, each alerted in the issue its card's body cites:
- a root card waiting (`scheduled`) at the lane of an `owner` station: its key is the wait's id,
  `essai:owner:<card>:<event>`, opened at the card's last `scheduled` event;
- a root card, or a child card of an `agent` station (`<item>/<station>/<passage>`), blocked: the key of a factory's
  blocked card, `essai:<block kind>:<card>:<event>`, under the title of a failure at its station;
- an anomaly the engine noted in `engine.db` (`workflow_core.signals`): a root card finished by another than the
  engine, or taken by an agent, its key the anomaly's.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol

from workflow_core.adapters.github import APP_BOT, IssueComment
from workflow_core.config import Workflow
from workflow_core.diagnosis.triggers import ready_since, trigger_of
from workflow_core.model.board import BoardUnavailableError
from workflow_core.model.facts import CardFacts, RuntimeTexts
from workflow_core.model.state import CoreState
from workflow_core.notify.alerts import (
    blocked_alert,
    failure_alert,
    held_alert,
    marker,
    owner_wait_alert,
    parse_issue,
    signal_alert,
)
from workflow_core.notify.register import AlertRegister
from workflow_core.notify.settings import NotifySettings
from workflow_core.plugin.manifest import PluggedWorkflow, child_of, is_root_key, issue_of, lanes_of, wait_id
from workflow_core.signals import Signals

PASS_SECONDS = 30.0
# The triggers of a blocked card that are alerted.
BLOCKED_KINDS = frozenset({"needs_input", "capability", "transient", "gave_up"})


class WaitReader(Protocol):
    """What the notifier reads of a board, through the runtime's adapter."""

    def read_board_facts(self, board: str) -> list[CardFacts]: ...

    def read_issue_urls(self, board: str) -> dict[str, str | None]: ...

    # The runtime's own words: `relay_command` writes the relaunch of a held card.
    texts: RuntimeTexts


def journal_line(line: str) -> None:
    # journald reads a leading "<n>" as the priority of the line; lines without one are information.
    print(line if line.startswith("<") else f"<6>{line}", file=sys.stderr, flush=True)


class Notifier:
    def __init__(
        self,
        *,
        workflows: dict[str, Workflow],
        reader: WaitReader,
        state: CoreState,
        settings: NotifySettings,
        post_comment: Callable[[str, int, str], str],
        read_comments: Callable[[str, int], list[IssueComment]],
        register_path: Path,
        clock: Callable[[], float] = time.time,
        log: Callable[[str], None] = journal_line,
        lanes: Mapping[str, frozenset[str]] | None = None,
        plugged: Sequence[PluggedWorkflow] = (),
        records: Signals | None = None,
    ) -> None:
        self.workflows = [workflow for workflow in workflows.values() if workflow.notify]
        # The workflows plugged by their manifest: always notified, without `notify`.
        self.plugged = list(plugged)
        # The engine's notes of anomalies (`engine.db`), only read; without them, no anomaly is alerted.
        self.records = records
        # The lanes of each workflow plugged by its manifest, by key: their cards are never held (`trigger_of`).
        self.lanes: Mapping[str, frozenset[str]] = lanes or {}
        self.reader = reader
        self.state = state
        self.settings = settings
        self.post_comment = post_comment
        self.read_comments = read_comments
        self.clock = clock
        self.log = log
        self.register = AlertRegister(register_path, log)
        self._told_without_issue: set[tuple[str, str]] = set()

    def run_pass(self) -> None:
        """One pass over every notified workflow. Never raises: a failure is written to the journal, and the key is
        not noted, so the next pass tries again."""
        if not self.settings.on or self.state.mode != "normal":
            return
        passes = [(workflow.key, self._workflow_pass, workflow) for workflow in self.workflows]
        passes += [(workflow.key, self._plugged_pass, workflow) for workflow in self.plugged]
        for key, workflow_pass, workflow in passes:
            try:
                workflow_pass(workflow)
            except BoardUnavailableError as error:
                self.log(f"<3>workflow-core: notificateur, {key} : {error}")
            except Exception as error:  # never an exception out of the pass
                self.log(f"<3>workflow-core: notificateur, {key} : {type(error).__name__}: {error}")

    def _workflow_pass(self, workflow: Workflow) -> None:
        now = self.clock()
        cards = self.reader.read_board_facts(workflow.board)
        issues: list[dict[str, str | None]] = []

        def issue_of_card(card: str) -> str | None:
            # The board's issues are read once per pass, and only when an alert needs one.
            if not issues:
                issues.append(self.reader.read_issue_urls(workflow.board))
            return issues[0].get(card)

        for facts in cards:
            card = facts.card
            if card.status not in ("blocked", "ready") or card.assignee in workflow.notify_skip_assignees:
                continue
            trigger = trigger_of(facts, workflow, now, lanes=self.lanes.get(workflow.key, frozenset()))
            if trigger is None:
                continue
            if card.status == "blocked" and trigger.kind in BLOCKED_KINDS:
                opened = trigger.opened_at
            elif card.status == "ready" and trigger.kind == "held_active_pr":
                # A held card's wait opens when it became ready.
                opened = ready_since(facts)
            else:
                continue
            key = f"{workflow.key}:{trigger.id}"
            if trigger.kind == "held_active_pr":

                def body(issue: str, facts=facts, key=key) -> str:
                    return self._held_body(workflow, facts, key, issue, now)
            else:

                def body(issue: str, facts=facts, trigger=trigger, key=key) -> str:
                    return self._blocked_body(workflow, facts, trigger, key, issue)

            self._alert(workflow.key, card.id, key, opened, lambda card=card.id: issue_of_card(card), body)

    def _plugged_pass(self, workflow: PluggedWorkflow) -> None:
        """The waits of a workflow plugged by its manifest: a root card waiting at an `owner` station, a root card or
        a child card blocked, and the anomalies the engine noted; each alerted in its item's issue, the address its
        card's body cites (the engine's own note for an anomaly)."""
        now = self.clock()
        entry = Workflow(key=workflow.key, board=workflow.board, repo=workflow.repo)
        lanes = lanes_of(workflow)
        for facts in self.reader.read_board_facts(workflow.board):
            card = facts.card
            if is_root_key(card.idempotency_key, workflow.key):
                station = workflow.station_of_lane(card.assignee)
                if station is None:
                    continue
                wait = wait_id(workflow, card.id, card.status, card.assignee, facts.events)
                if wait is not None:
                    self._owner_wait(workflow, facts, station.key, wait)
                elif card.status == "blocked":
                    self._failure(workflow, entry, lanes, facts, card.idempotency_key, station.key, now, child=False)
            elif card.status == "blocked" and (found := child_of(card.idempotency_key, workflow.key)) is not None:
                element, station = found
                self._failure(workflow, entry, lanes, facts, element, station, now, child=True)
        for signal in self.records.signals_of(workflow.key) if self.records is not None else []:

            def body(issue: str, signal=signal) -> str:
                return signal_alert(
                    key=signal.key,
                    workflow=workflow.key,
                    element=signal.item,
                    kind=signal.kind,
                    station=workflow.label(signal.station),
                    card=signal.card,
                    detail=signal.detail,
                    live_url=self.settings.live_url,
                )

            self._alert(workflow.key, signal.card, signal.key, signal.opened_at, lambda s=signal: s.issue, body)

    def _owner_wait(self, workflow: PluggedWorkflow, facts: CardFacts, station: str, wait: str) -> None:
        """A root card waiting at an `owner` station: its wait opened at its last `scheduled` event."""
        card = facts.card
        opened = next(event for event in reversed(facts.events) if event.kind == "scheduled").created_at

        def body(issue: str) -> str:
            return owner_wait_alert(
                key=wait,
                workflow=workflow.key,
                element=card.idempotency_key,
                station=workflow.label(station),
                gestures=list(workflow.gestures),
                live_url=self.settings.live_url,
            )

        self._alert(workflow.key, card.id, wait, opened, lambda: issue_of(card.body), body)

    def _failure(
        self,
        workflow: PluggedWorkflow,
        entry: Workflow,
        lanes: frozenset[str],
        facts: CardFacts,
        element: str,
        station: str,
        now: float,
        *,
        child: bool,
    ) -> None:
        """A blocked card of a plugged workflow, alerted as the factory's: the same trigger, the same key."""
        trigger = trigger_of(facts, entry, now, lanes=lanes)
        if trigger is None or trigger.kind not in BLOCKED_KINDS:
            return
        card = facts.card
        event = next((item for item in facts.events if item.id == trigger.event), None)
        reason = event.payload.get("reason") if event is not None else None
        key = f"{workflow.key}:{trigger.id}"

        def body(issue: str) -> str:
            return failure_alert(
                key=key,
                workflow=workflow.key,
                element=element,
                station=workflow.label(station),
                card=card.id,
                title=card.title,
                reason=reason if isinstance(reason, str) else "",
                board=workflow.board,
                child=child,
                live_url=self.settings.live_url,
            )

        self._alert(workflow.key, card.id, key, trigger.opened_at, lambda: issue_of(card.body), body)

    def _alert(
        self,
        workflow: str,
        card: str,
        key: str,
        opened: float,
        issue_of_wait: Callable[[], str | None],
        body: Callable[[str], str],
    ) -> None:
        """One wait, the same way for every workflow: opened before the switch-on time, already in the register, without
        a readable issue, out of the listed repositories, or already in its issue, it is not posted; otherwise its
        alert is posted, then noted."""
        if opened < self.settings.since:
            return
        if self.register.has(key):
            return
        issue = issue_of_wait()
        parsed = parse_issue(issue) if issue else None
        if parsed is None:
            if (workflow, card) not in self._told_without_issue:
                self._told_without_issue.add((workflow, card))
                self.log(f"workflow-core: notificateur, carte {card} de {workflow} sans issue : pas d'alerte")
            return
        owner, repo, number = parsed
        if self.settings.repos is not None and f"{owner}/{repo}" not in self.settings.repos:
            return
        if self._already_in_issue(key, issue, f"{owner}/{repo}", number):
            return
        try:
            url = self.post_comment(f"{owner}/{repo}", number, body(issue))
        except Exception as error:
            self.log(f"<3>workflow-core: notificateur, alerte {key} non postée sur {issue} : {error}")
            return
        self.register.note(key, url)
        self.log(f"workflow-core: notificateur, alerte {key} postée : {url}")

    def _blocked_body(self, workflow: Workflow, facts: CardFacts, trigger, key: str, issue: str) -> str:
        event = next((item for item in facts.events if item.id == trigger.event), None)
        reason = event.payload.get("reason") if event is not None else None
        return blocked_alert(
            key=key,
            workflow=workflow.key,
            kind=trigger.kind,
            issue=issue,
            card=facts.card.id,
            title=facts.card.title,
            question=reason if isinstance(reason, str) else "",
            live_url=self.settings.live_url,
        )

    def _held_body(self, workflow: Workflow, facts: CardFacts, key: str, issue: str, now: float) -> str:
        card = facts.card
        relay = next((agent for agent in workflow.notify_relay_agents if agent != card.assignee), None)
        relaunch = (
            self.reader.texts.relay_command(workflow.board, card.id, relay, card.assignee)
            if relay is not None and card.assignee
            else None
        )
        return held_alert(
            key=key,
            workflow=workflow.key,
            issue=issue,
            card=card.id,
            title=card.title,
            minutes=int((now - ready_since(facts)) // 60),
            relaunch=relaunch,
            live_url=self.settings.live_url,
        )

    def _already_in_issue(self, key: str, issue: str, repo: str, number: int) -> bool:
        """Whether the issue already holds this wait's alert, by the App (a marker written by another account does
        not count); then the key is noted. An issue not read in full counts as holding it: nothing is posted before
        every page is read, and the next pass tries again."""
        try:
            comments = self.read_comments(repo, number)
        except Exception as error:
            self.log(
                f"<3>workflow-core: notificateur, commentaires de {issue} illisibles, alerte {key} remise : {error}"
            )
            return True
        found = next((c for c in comments if c.author == APP_BOT and marker(key) in c.body), None)
        if found is None:
            return False
        self.register.note(key, found.url)
        self.log(f"workflow-core: notificateur, alerte {key} existait déjà : {found.url}")
        return True


class NotifierRunner:
    """Runs the notifier's pass every 30 seconds in the service, off the event loop."""

    def __init__(self, notifier: Notifier, every: float = PASS_SECONDS) -> None:
        self.notifier = notifier
        self.every = every
        self._task: asyncio.Task | None = None

    async def _loop(self) -> None:
        while True:
            await asyncio.to_thread(self.notifier.run_pass)
            await asyncio.sleep(self.every)

    async def start(self) -> None:
        if self.notifier.settings.on and self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
