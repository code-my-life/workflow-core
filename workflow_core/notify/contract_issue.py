"""The broken contract's issue: at each startup, a failed contract test opens, comments or reopens an issue in
code-my-life/workflow-core; a contract kept again comments it and closes it (ADR 0019).

It is the one write to GitHub allowed in observer mode, whatever the notifier's switch. Its state, the number of the
noted issue, is `contract-issue.json` of the core's state folder. A GitHub failure is written to the journal and the
core stays as it is: the next startup tries again.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from workflow_core.model.contract import ContractVerdict
from workflow_core.notify.issues import open_or_comment, read_state, write_state

REPO = "code-my-life/workflow-core"
MARKER = "<!-- workflow-core:contrat -->"
STATE_FILE = "contract-issue.json"
FOOTER = "<sub>Test de contrat du socle workflow-core</sub>"


def _problems(verdict: ContractVerdict) -> list[str]:
    return [f"- {problem}" for problem in verdict.problems] or ["- (aucun problème nommé)"]


def _opening_body(verdict: ContractVerdict, runtime: str, version: str | None) -> str:
    return "\n".join(
        [
            f"Le test de contrat du socle a échoué au démarrage du {verdict.checked_at} : ce que le socle lit de "
            f"{runtime} n'a plus la forme attendue. Le socle est en observateur : il sert encore factory-live, mais "
            "n'écrit plus rien, ni alerte ni carte.",
            "",
            f"Version de {runtime} lue : `{version or 'illisible'}`",
            "",
            "**Problèmes trouvés**, dans l'ordre :",
            *_problems(verdict),
            "",
            "**À regarder sur la machine**",
            "```",
            'journalctl --user -u workflow-core.service --since "-15 min"',
            "uv run pytest tests/contract -rs",
            "```",
            "",
            "Cette issue reçoit un commentaire à chaque démarrage tant que le contrat reste cassé, et se ferme au "
            "premier démarrage où il tient de nouveau.",
            "",
            FOOTER,
            "",
            MARKER,
        ]
    )


def _still_broken(verdict: ContractVerdict, runtime: str, version: str | None) -> str:
    return "\n".join(
        [
            f"Le contrat est toujours cassé au démarrage du {verdict.checked_at} "
            f"({runtime} `{version or 'illisible'}`) :",
            *_problems(verdict),
            "",
            FOOTER,
        ]
    )


def report_contract(
    verdict: ContractVerdict,
    *,
    runtime: str,
    version: str | None,
    client,
    state_dir: Path,
    now: datetime,
    log: Callable[[str], None],
) -> None:
    """Open, comment or close the broken contract's issue after the startup's verdict. Never raises."""
    path = state_dir / STATE_FILE
    noted = read_state(path).get("issue")
    try:
        if not verdict.ok:
            title = f"Le contrat du socle avec {runtime} est cassé"
            number = open_or_comment(
                client,
                REPO,
                MARKER,
                now,
                title,
                _opening_body(verdict, runtime, version),
                _still_broken(verdict, runtime, version),
            )
            write_state(path, {"issue": number})
            log(f"<3>workflow-core: contrat cassé, issue {REPO}#{number}")
        elif noted is not None:
            client.post_comment(REPO, int(noted), f"Le contrat avec {runtime} tient de nouveau.\n\n{FOOTER}")
            client.set_issue_state(REPO, int(noted), "closed")
            write_state(path, {"issue": None})
            log(f"<6>workflow-core: contrat tenu, issue {REPO}#{noted} fermée")
    except Exception as error:  # GitHub out of reach or the App not configured: the next startup tries again
        log(f"<3>workflow-core: issue du contrat cassé non écrite : {error}")


class ContractIssueTask:
    """Reports the startup's verdict once, off the event loop, without delaying the service's start."""

    def __init__(self, report: Callable[[], None]) -> None:
        self.report = report
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(asyncio.to_thread(self.report))

    async def stop(self) -> None:
        if self._task is not None:
            with contextlib.suppress(Exception):
                await self._task
            self._task = None
