"""The notifier's pass: every 30 seconds in the service, one alert in its issue for each wait of a workflow whose
entry asks for it (`notify = true`), never twice for the same key, and nothing at all when switched off or when the
contract is broken (ADR 0009, ADR 0019).

A wait is a blocked card, or a ready card held by the active pull request guard for the workflow's
`held_after_minutes`, both recognised by the diagnosis' triggers (one rule in the repository): its key is
`<workflow>:<trigger id>`, `factory:needs_input:t_ab12cd34:5509`, `factory:held_active_pr:t_ab12cd34:1794500000`.
Before posting a key its register does not hold,
the notifier reads every comment of the issue and posts nothing when the App already wrote the wait's marker there:
a lost register or a rebuilt journal never posts an alert twice.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from workflow_core.adapters.github import APP_BOT, IssueComment
from workflow_core.config import Workflow
from workflow_core.diagnosis.triggers import ready_since, trigger_of
from workflow_core.model.board import BoardUnavailableError
from workflow_core.model.facts import CardFacts, RuntimeTexts
from workflow_core.model.state import CoreState
from workflow_core.notify.alerts import blocked_alert, held_alert, marker, parse_issue
from workflow_core.notify.register import AlertRegister
from workflow_core.notify.settings import NotifySettings

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
    ) -> None:
        self.workflows = [workflow for workflow in workflows.values() if workflow.notify]
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
        for workflow in self.workflows:
            try:
                self._workflow_pass(workflow)
            except BoardUnavailableError as error:
                self.log(f"<3>workflow-core: notificateur, {workflow.key} : {error}")
            except Exception as error:  # never an exception out of the pass
                self.log(f"<3>workflow-core: notificateur, {workflow.key} : {type(error).__name__}: {error}")

    def _workflow_pass(self, workflow: Workflow) -> None:
        now = self.clock()
        cards = self.reader.read_board_facts(workflow.board)
        issues: dict[str, str | None] | None = None
        for facts in cards:
            card = facts.card
            if card.status not in ("blocked", "ready") or card.assignee in workflow.notify_skip_assignees:
                continue
            trigger = trigger_of(facts, workflow, now)
            if trigger is None:
                continue
            if card.status == "blocked" and trigger.kind in BLOCKED_KINDS:
                opened = trigger.opened_at
            elif card.status == "ready" and trigger.kind == "held_active_pr":
                # A held card's wait opens when it became ready.
                opened = ready_since(facts)
            else:
                continue
            if opened < self.settings.since:
                continue
            key = f"{workflow.key}:{trigger.id}"
            if self.register.has(key):
                continue
            if issues is None:
                issues = self.reader.read_issue_urls(workflow.board)
            issue = issues.get(card.id)
            parsed = parse_issue(issue) if issue else None
            if parsed is None:
                if (workflow.key, card.id) not in self._told_without_issue:
                    self._told_without_issue.add((workflow.key, card.id))
                    self.log(
                        f"workflow-core: notificateur, carte {card.id} de {workflow.key} sans issue : pas d'alerte"
                    )
                continue
            owner, repo, number = parsed
            if self.settings.repos is not None and f"{owner}/{repo}" not in self.settings.repos:
                continue
            if self._already_in_issue(key, issue, f"{owner}/{repo}", number):
                continue
            if trigger.kind == "held_active_pr":
                body = self._held_body(workflow, facts, key, issue, now)
            else:
                body = self._blocked_body(workflow, facts, trigger, key, issue)
            try:
                url = self.post_comment(f"{owner}/{repo}", number, body)
            except Exception as error:
                self.log(f"<3>workflow-core: notificateur, alerte {key} non postée sur {issue} : {error}")
                continue
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
