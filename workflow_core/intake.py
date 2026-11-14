"""The reading of GitHub: where an item is born from an open issue (ADR 0016, ADR 0017).

1. At the start, then every 10 minutes (`GITHUB_POLL_SECONDS`), the core reads, for each workflow plugged by its
   manifest, the open issues of the manifest's `issues_repo`, page by page, without the pull requests GitHub lists
   among them. No other repository is read, and no webhook is received.
2. Only the issues opened by the owner's account (`OWNER_LOGIN`, also the one factory-live opens an issue for) are
   taken; the others are left without a word, the repository being public. With `issue_label` in the workflow's entry
   of the configuration, only the issues carrying that label are taken; without it, those labelled `test` are left
   out, so that a production core never takes an issue of the test machine.
3. An issue already handled is never handed to the intake command again: it is noted in the table `intake` of
   `engine.db`, and, should that register be lost, a comment of the App carries its hidden marker
   `<!-- workflow-core:intake <repo>#<n> accept -->` (or `refuse`). A marker written by another account does not
   count.
4. A new issue is handed to the workflow's intake command (`uv run --directory <repo> ...`, within its delay, without
   the core's secrets), `{"schema_version": 1, "issue": {...}}` on its input. A command that cannot be launched, runs
   past its delay or gives an unreadable output has made no decision: the journal says so, nothing is posted, and the
   next reading tries again.
5. Accepted with the id `<id>`, the issue becomes the item `<key>:<id>`: its root card is created, assigned to the
   lane of the first station, with the item as its idempotency key and the issue's address as its body (the runtime
   gives back the existing card for a key already taken). Then the App comments the issue: the item, its first
   station, its link in factory-live (left out without `WORKFLOW_CORE_LIVE_URL`), the reason, and the marker. The
   engine takes the item at its next pass.
6. Refused, the issue gets a comment with the reason and the marker; no card is created.
7. A card created whose comment could not be posted leaves the issue without a marker: at the next reading, the
   command runs again, the creation gives back the same card, and the comment is posted. The core alone writes these
   comments (ADR 0009): the intake command never speaks to GitHub.
8. In observer mode, or while the runtime's version is not confirmed, the issues are read but no command is run,
   nothing is created and nothing is posted.
"""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from workflow_core.adapters.github import APP_BOT, OWNER_LOGIN, OpenIssue
from workflow_core.clock import utc_now
from workflow_core.engine.answers import LOCK_TIMEOUT_SECONDS
from workflow_core.gestures import Gestures
from workflow_core.model.decision import IntakeDecision
from workflow_core.model.state import CoreState
from workflow_core.model.writer import BoardWriter
from workflow_core.notify.alerts import item_link
from workflow_core.notify.notifier import journal_line
from workflow_core.plugin.commands import intake_input, run_intake
from workflow_core.plugin.manifest import PluggedWorkflow

# Seconds between two readings of GitHub, until the webhooks reach the core.
GITHUB_POLL_SECONDS = 600
# The label of the test machine's issues, left out by an entry without `issue_label`.
TEST_LABEL = "test"
PREFIX = "workflow-core: relecture"
SCHEMA = """
CREATE TABLE IF NOT EXISTS intake (
    repo TEXT NOT NULL,
    number INTEGER NOT NULL,
    item TEXT,
    decision TEXT NOT NULL CHECK (decision IN ('accept', 'refuse')),
    handled_at TEXT NOT NULL,
    PRIMARY KEY (repo, number)
)
"""


class IntakeCommand(Protocol):
    def __call__(
        self, repo, command: list[str], payload: dict, timeout: float, log, *, label: str
    ) -> IntakeDecision | None: ...


def intake_marker(repo: str, number: int, decision: str) -> str:
    """The hidden marker of an issue's decision, exactly."""
    return f"<!-- workflow-core:intake {repo}#{number} {decision} -->"


def accept_comment(*, key: str, item: str, station: str, reason: str, link: str | None, marker: str) -> str:
    parts = [
        f"✅ Cette issue devient l'élément `{item}` du workflow `{key}` ; il commence à la station `{station}`.",
    ]
    if link:
        parts.append(f"[Voir l'élément dans factory-live]({link})")
    if reason.strip():
        parts.append(f"Motif : {reason.strip()}")
    parts.append(marker)
    return "\n\n".join(parts)


def refuse_comment(*, key: str, reason: str, marker: str) -> str:
    return f"🚫 Cette issue n'est pas prise par le workflow `{key}` : {reason.strip().rstrip('.')}.\n\n{marker}"


def taken(workflow: PluggedWorkflow, issue: OpenIssue) -> bool:
    """Whether the workflow takes this issue in: opened by the owner, with the workflow's label, or without the test
    label when it has none. GitHub's label names ignore case, and so does the comparison."""
    if issue.author != OWNER_LOGIN:
        return False
    labels = {label.casefold() for label in issue.labels}
    if workflow.issue_label is not None:
        return workflow.issue_label.casefold() in labels
    return TEST_LABEL.casefold() not in labels


class IntakeRegister:
    """The issues already handled, in the table `intake` of `engine.db` at `path`; the folder and the table are
    created when missing. One connection per call, as the answer box."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=LOCK_TIMEOUT_SECONDS, isolation_level=None)
        db.execute(SCHEMA)
        return db

    def has(self, repo: str, number: int) -> bool:
        db = self._connect()
        try:
            return (
                db.execute("SELECT 1 FROM intake WHERE repo = ? AND number = ?", (repo, number)).fetchone() is not None
            )
        finally:
            db.close()

    def note(self, repo: str, number: int, item: str | None, decision: str) -> None:
        """Note an issue handled; one already noted is kept as it was."""
        db = self._connect()
        try:
            db.execute(
                "INSERT OR IGNORE INTO intake (repo, number, item, decision, handled_at) VALUES (?, ?, ?, ?, ?)",
                (repo, number, item, decision, utc_now()),
            )
        finally:
            db.close()


class Intake:
    def __init__(
        self,
        *,
        workflows: list[PluggedWorkflow],
        github: Any,
        writer: BoardWriter,
        state: CoreState,
        register: IntakeRegister,
        ensure_board: Callable[[PluggedWorkflow], None],
        confirm_version: Callable[[], bool] | None = None,
        live_url: str | None = None,
        run: IntakeCommand = run_intake,
        log: Callable[[str], None] = journal_line,
    ) -> None:
        self.workflows = workflows
        # The core's GitHub client: `list_open_issues`, `list_comments`, `post_comment`.
        self.github = github
        self.writer = writer
        self.state = state
        self.register = register
        # Creates the workflow's board when its base does not exist yet: the engine's own.
        self.ensure_board = ensure_board
        # Whether the runtime's version is confirmed since the start: the service's version watch.
        self.confirm_version = confirm_version
        self.live_url = live_url
        self.run = run
        self.log = log
        self._told: set[tuple] = set()
        self._guard = threading.Lock()

    def _tell_once(self, key: tuple, line: str) -> None:
        with self._guard:
            if key in self._told:
                return
            self._told.add(key)
        self.log(line)

    def read_once(self) -> None:
        """One reading of every plugged workflow's issues. Never raises: a failure of a workflow or of an issue is
        written to the journal, and the others go on."""
        for workflow in self.workflows:
            try:
                self._read(workflow)
            except Exception as error:  # never an exception out of the reading
                self.log(f"<3>{PREFIX}, {workflow.key} : {type(error).__name__}: {error}")

    def _read(self, workflow: PluggedWorkflow) -> None:
        if workflow.intake is None or not workflow.issues_repo:
            return
        repo = workflow.issues_repo
        try:
            issues = self.github.list_open_issues(repo)
        except Exception as error:
            self.log(f"<3>{PREFIX}, {workflow.key} : issues de {repo} illisibles : {error}")
            return
        kept = [issue for issue in issues if taken(workflow, issue)]
        if self.state.mode != "normal":
            for issue in kept:
                self._tell_once(
                    (repo, issue.number, "observer"),
                    f"{PREFIX}, {repo}#{issue.number} : en attente du mode normal, intake non lancée",
                )
            return
        if self.confirm_version is not None and not self.confirm_version():
            return
        for issue in kept:
            try:
                self._take(workflow, issue)
            except Exception as error:  # an issue that fails leaves the others to go on
                self.log(f"<3>{PREFIX}, {repo}#{issue.number} : {type(error).__name__}: {error}")

    def _marked(self, repo: str, issue: OpenIssue) -> str | None:
        """The decision an App's comment of the issue already carries, or None; raises when the comments cannot be
        read in full."""
        comments = self.github.list_comments(repo, issue.number)
        for decision in ("accept", "refuse"):
            marker = intake_marker(repo, issue.number, decision)
            if any(comment.author == APP_BOT and marker in comment.body for comment in comments):
                return decision
        return None

    def _take(self, workflow: PluggedWorkflow, issue: OpenIssue) -> None:
        repo, number = workflow.issues_repo, issue.number
        if self.register.has(repo, number):
            return
        try:
            marked = self._marked(repo, issue)
        except Exception as error:
            self.log(f"<3>{PREFIX}, {repo}#{number} : commentaires illisibles, issue remise : {error}")
            return
        if marked is not None:
            self.register.note(repo, number, None, marked)
            self.log(f"{PREFIX}, {repo}#{number} : déjà traitée ({marked})")
            return
        payload = intake_input(
            repo=repo, number=number, url=issue.url, title=issue.title, body=issue.body, labels=list(issue.labels)
        )
        intake = workflow.intake
        label = f"relecture, {repo}#{number} intake"
        decision = self.run(workflow.repo, intake.command, payload, intake.timeout_seconds, self.log, label=label)
        if decision is None:
            self.log(f"<4>{PREFIX}, {repo}#{number} : intake sans décision, réessai à la prochaine relecture")
            return
        marker = intake_marker(repo, number, decision.decision)
        item = None
        if decision.decision == "accept":
            item = f"{workflow.key}:{decision.id}"
            first = workflow.stations[0].key
            self.ensure_board(workflow)
            card = self.writer.create_root_card(
                workflow.board, title=f"{item} · {issue.title}", lane=workflow.lane(first), item=item, issue=issue.url
            )
            self.log(f"{PREFIX}, {repo}#{number} : acceptée, élément {item}, carte {card} à la station {first}")
            body = accept_comment(
                key=workflow.key,
                item=item,
                station=first,
                reason=decision.reason,
                link=item_link(self.live_url, workflow.key),
                marker=marker,
            )
        else:
            self.log(f"{PREFIX}, {repo}#{number} : refusée : {decision.reason}")
            body = refuse_comment(key=workflow.key, reason=decision.reason, marker=marker)
        try:
            url = self.github.post_comment(repo, number, body)
        except Exception as error:
            self.log(
                f"<3>{PREFIX}, {repo}#{number} : commentaire non posté, réessai à la prochaine relecture : {error}"
            )
            return
        self.register.note(repo, number, item, decision.decision)
        self.log(f"{PREFIX}, {repo}#{number} : décision {decision.decision} dite dans l'issue : {url}")


class IntakeRunner:
    """Reads GitHub in the service: at the start, then every `every` seconds by `clock`, off the event loop. It runs
    only when a workflow is plugged by its manifest. With the owner's gestures, each reading of the issues is followed
    by a reading of the items' comments, and every check (`check_every`) reacts to the gestures handled meanwhile."""

    def __init__(
        self,
        intake: Intake,
        gestures: Gestures | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        every: float = GITHUB_POLL_SECONDS,
        check_every: float = 5.0,
    ) -> None:
        self.intake = intake
        self.gestures = gestures
        self.clock = clock
        self.every = every
        self.check_every = check_every
        self._last: float | None = None
        self._task: asyncio.Task | None = None

    def tick(self) -> bool:
        """Read GitHub when no reading was made yet, or when the last one is `every` seconds old; whether it read.
        The gestures handled meanwhile get their reactions either way."""
        now = self.clock()
        read = self._last is None or now - self._last >= self.every
        if read:
            self._last = now
            self.intake.read_once()
            if self.gestures is not None:
                self.gestures.read_once()
        if self.gestures is not None:
            self.gestures.react()
        return read

    async def _loop(self) -> None:
        while True:
            await asyncio.to_thread(self.tick)
            await asyncio.sleep(self.check_every)

    async def start(self) -> None:
        if self.intake.workflows and self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
