"""The owner's gestures on GitHub: a comment `/<key> <gesture>[ <note>]` on the issue of an item (AD-10, ADR 0017).

1. At each reading of GitHub, for each workflow plugged by its manifest, the core reads the comments of the issue of
   each item (the address the root card's body carries, in one of the manifest's `issues_repos`): the items whose root
   card is
   not finished, and the finished ones whose issue is still open, so that a gesture given after the end gets its
   answer. Only the comments from the previous reading of that issue on are read (two minutes earlier, for the clocks),
   page by page; every comment at the first reading of an issue since the start.
2. A comment is a gesture when its author is the owner's account (`WORKFLOW_CORE_OWNER_LOGIN`, also the one
   factory-live comments for, compared without case as GitHub's logins; without it, no comment is) and its first line
   is exactly `/<key> <gesture>`, then an optional note (the rest of the line); the case of the line counts.
   Any other comment is left without a word.
3. A gesture enters the answer box of `engine.db` once, its id `github:<comment id>`, its time the comment's creation
   time. A comment that already carries a reaction of the App is never taken again, even when `engine.db` is lost.
4. The engine applies or refuses it at its pass (`workflow_core.engine.engine`).
5. Once it is handled, the App reacts to the comment: 👍 (`+1`) when applied, 😕 (`confused`) when refused. A
   reaction GitHub refuses is tried again after the next reading, not at every check; one refused because the comment
   is gone (404) is given up, with one line in the journal.
6. In observer mode, the gestures read still enter the box, but nothing is reacted: the engine applies nothing, and
   the reactions wait for the normal mode.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from workflow_core.adapters.github import GitHubError, IssueComment
from workflow_core.engine.answers import AnswerBox
from workflow_core.engine.engine import issue_of
from workflow_core.model.board import BoardReader, BoardUnavailableError, Item
from workflow_core.model.state import CoreState
from workflow_core.notify.notifier import journal_line
from workflow_core.plugin.manifest import FINISHED, PluggedWorkflow, is_root_card

PREFIX = "workflow-core: gestes"
# How far back before the previous reading of an issue its comments are read again, in seconds.
OVERLAP_SECONDS = 120
# The App's reaction to a handled answer, by its outcome.
REACTIONS = {"applied": "+1", "refused": "confused"}
# GitHub's answer to a reaction on a comment that no longer exists.
NOT_FOUND = 404
ISSUE_PATH = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/issues/(\d+)", re.ASCII)


def gesture_of(key: str, body: str) -> tuple[str, str] | None:
    """The gesture and its note when the comment's first line is `/<key> <gesture>[ <note>]`; None otherwise."""
    line = body.split("\n", 1)[0].rstrip("\r").rstrip()
    found = re.fullmatch(rf"/{re.escape(key)} (\S+)(?: (.*))?", line)
    if found is None:
        return None
    return found[1], (found[2] or "").strip()


def iso_seconds(github_time: str) -> str | None:
    """A time as GitHub writes it (`2026-11-14T09:30:00Z`), as the box keeps it (`2026-11-14T09:30:00+00:00`)."""
    try:
        moment = datetime.fromisoformat(github_time.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.astimezone(UTC).isoformat(timespec="seconds")


class Gestures:
    def __init__(
        self,
        *,
        workflows: list[PluggedWorkflow],
        github: Any,
        reader: BoardReader,
        answers: AnswerBox,
        state: CoreState,
        log: Callable[[str], None] = journal_line,
        clock: Callable[[], float] = time.time,
        owner: str | None = None,
    ) -> None:
        self.workflows = workflows
        # The owner's account: the only author whose comments are gestures; None takes none.
        self.owner = owner
        # The core's GitHub client: `list_comments_since`, `list_open_issues`, `add_reaction`.
        self.github = github
        self.reader = reader
        self.answers = answers
        self.state = state
        self.log = log
        self.clock = clock
        # When each issue's comments were last read in full, by (repo, number): Unix seconds, at the reading's start.
        self._read_at: dict[tuple[str, int], float] = {}
        # The answers whose reaction GitHub refused since the last reading: not tried again before the next one.
        self._failed: set[str] = set()
        self._guard = threading.Lock()

    def read_once(self) -> None:
        """One reading of the comments of every item's issue. Never raises: a failure is written to the journal."""
        with self._guard:
            self._failed.clear()
        for workflow in self.workflows:
            try:
                self._read(workflow)
            except Exception as error:  # never an exception out of the reading
                self.log(f"<3>{PREFIX}, {workflow.key} : {type(error).__name__}: {error}")

    def _issues(self, workflow: PluggedWorkflow) -> list[tuple[str, int, Item]]:
        """The issues to read, by repository and number, each with the root card of its item."""
        try:
            items = self.reader.read_items(workflow.board)
        except BoardUnavailableError:
            # The board's base does not exist yet: no item.
            return []
        found: dict[tuple[str, int], Item] = {}
        finished: dict[tuple[str, int], Item] = {}
        for item in items:
            if not is_root_card(item, workflow.key):
                continue
            address = ISSUE_PATH.fullmatch(issue_of(item.body) or "")
            if address is None or address[1] not in workflow.issues_repos:
                continue
            issue = (address[1], int(address[2]))
            if item.status in FINISHED:
                finished.setdefault(issue, item)
            else:
                found.setdefault(issue, item)
        for repo in sorted({repo for repo, number in finished if (repo, number) not in found}):
            try:
                still_open = {issue.number for issue in self.github.list_open_issues(repo)}
            except Exception as error:
                self.log(f"<3>{PREFIX}, {workflow.key} : issues de {repo} illisibles : {error}")
                continue
            for (finished_repo, number), item in finished.items():
                if finished_repo == repo and number in still_open:
                    found.setdefault((repo, number), item)
        return [(repo, number, item) for (repo, number), item in sorted(found.items())]

    def _read(self, workflow: PluggedWorkflow) -> None:
        if self.owner is None:
            return
        for repo, number, item in self._issues(workflow):
            self._read_issue(workflow, repo, number, item)

    def _read_issue(self, workflow: PluggedWorkflow, repo: str, number: int, item: Item) -> None:
        started = self.clock()
        previous = self._read_at.get((repo, number))
        since = None
        if previous is not None:
            since = datetime.fromtimestamp(previous - OVERLAP_SECONDS, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            comments = self.github.list_comments_since(repo, number, since)
        except Exception as error:
            self.log(
                f"<3>{PREFIX}, {repo}#{number} : commentaires illisibles, relus à la prochaine relecture : {error}"
            )
            return
        try:
            for comment in comments:
                self._take(workflow, repo, number, item, comment)
        except Exception as error:
            # The issue is read again in full from its previous reading; the next issues are still read.
            self.log(f"<3>{PREFIX}, {repo}#{number} : gestes non reçus, relus à la prochaine relecture : {error}")
            return
        self._read_at[(repo, number)] = started

    def _take(self, workflow: PluggedWorkflow, repo: str, number: int, item: Item, comment: IssueComment) -> None:
        gesture = gesture_of(workflow.key, comment.body)
        # GitHub's logins ignore case.
        if self.owner is None or comment.author.casefold() != self.owner.casefold():
            return
        if gesture is None or comment.app_reactions or not comment.id:
            return
        answer_id = f"github:{comment.id}"
        if self.answers.has(answer_id):
            return
        name, note = gesture
        self.answers.receive(
            item.idempotency_key,
            name,
            note,
            answer_id=answer_id,
            source="github",
            received_at=iso_seconds(comment.created_at),
        )
        self.log(f"{PREFIX}, {repo}#{number} : geste {name} reçu pour {item.idempotency_key} (réponse {answer_id})")

    def react(self) -> None:
        """React to the comment of each handled GitHub answer not reacted to yet, in normal mode only. Never raises."""
        if self.state.mode != "normal":
            return
        try:
            unreacted = self.answers.unreacted()
        except Exception as error:
            self.log(f"<3>{PREFIX} : boîte des réponses illisible : {type(error).__name__}: {error}")
            return
        for answer_id, item, outcome in unreacted:
            with self._guard:
                if answer_id in self._failed:
                    continue
            try:
                try:
                    self._react(answer_id, item, outcome)
                except GitHubError as error:
                    if error.status != NOT_FOUND:
                        raise
                    # The comment was deleted: no reaction can ever be posted, and none is tried again.
                    self.answers.mark_reacted(answer_id)
                    self.log(
                        f"<4>{PREFIX}, {item} : réaction à {answer_id} abandonnée, commentaire introuvable ({error})"
                    )
            except Exception as error:
                self._retry_later(answer_id, item, error)

    def _retry_later(self, answer_id: str, item: str, error: Exception) -> None:
        with self._guard:
            self._failed.add(answer_id)
        self.log(f"<3>{PREFIX}, {item} : réaction à {answer_id} non posée, réessai après la relecture : {error}")

    def _react(self, answer_id: str, item: str, outcome: str) -> None:
        workflow = next((workflow for workflow in self.workflows if item.startswith(f"{workflow.key}:")), None)
        content = REACTIONS.get(outcome)
        if workflow is None or content is None:
            return
        repo = self._issues_repo_of(workflow, item)
        if repo is None:
            return
        comment_id = int(answer_id.removeprefix("github:"))
        self.github.add_reaction(repo, comment_id, content)
        self.answers.mark_reacted(answer_id)
        self.log(f"{PREFIX}, {item} : réponse {answer_id} {outcome}, réaction {content} posée")

    def _issues_repo_of(self, workflow: PluggedWorkflow, item: str) -> str | None:
        """The repository of the item's issue, where its gestures were commented: the address its root card carries,
        if it is one of the manifest's `issues_repos`."""
        for card in self.reader.read_items(workflow.board):
            if card.idempotency_key == item and is_root_card(card, workflow.key):
                address = ISSUE_PATH.fullmatch(issue_of(card.body) or "")
                if address is not None and address[1] in workflow.issues_repos:
                    return address[1]
        return None
