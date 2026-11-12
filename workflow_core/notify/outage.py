"""The outage issue: run by the watchdog's unit after its probe, every minute, without the service.

It reads the watchdog's state file (`watchdog.json`, never written here) and keeps its own, `outage-issue.json`, in
the core's state folder: the number of the current outage's issue, and when the outage began. When the watchdog has
written an outage and no issue is noted, it comments the App's open outage issue, else reopens and comments one closed
less than 24 hours ago, else opens one; then notes its number. When the watchdog is back to no failure, it comments the
noted issue, closes it and forgets it. A GitHub failure is written to journald (`<3>`), nothing is noted, and the next
passage tries again: the command always exits with 0 (ADR 0019).
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from workflow_core.events import state_dir as core_state_dir
from workflow_core.notify.issues import open_or_comment, read_state, write_state

REPO = "code-my-life/workflow-core"
MARKER = "<!-- workflow-core:panne -->"
TITLE = "Le socle workflow-core ne répond plus"
WATCHDOG_FILE = "watchdog.json"
OUTAGE_FILE = "outage-issue.json"
FOOTER = "<sub>Chien de garde du socle workflow-core</sub>"


def _minutes(since: str, now: datetime) -> int:
    return int((now - datetime.fromisoformat(since)).total_seconds() // 60)


def _opening_body(since: str, now: datetime) -> str:
    return "\n".join(
        [
            f"`/healthz` du socle échoue depuis {since} ({_minutes(since, now)} min) : le chien de garde ne le joint "
            "plus.",
            "",
            "**À regarder sur la machine**",
            "```",
            "systemctl --user status workflow-core.service",
            'journalctl --user -u workflow-core.service --since "-15 min"',
            "```",
            "",
            "Cette issue reçoit un commentaire et se ferme quand le socle répond de nouveau ; une panne qui revient "
            "dans les 24 heures la rouvre.",
            "",
            FOOTER,
            "",
            MARKER,
        ]
    )


def _still_down(since: str, now: datetime) -> str:
    return f"Le socle ne répond toujours pas depuis {since} ({_minutes(since, now)} min).\n\n{FOOTER}"


def _default_client():
    from workflow_core.adapters.github import AppTokens, GitHubClient

    return GitHubClient(AppTokens())


def run_once(
    *,
    state_dir: Path | None = None,
    client=None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    out: TextIO | None = None,
) -> int:
    """One passage; always 0."""
    folder = state_dir if state_dir is not None else core_state_dir()
    out = out or sys.stderr
    watchdog = read_state(folder / WATCHDOG_FILE)
    outage_path = folder / OUTAGE_FILE
    noted = read_state(outage_path)
    number = noted.get("issue")
    since = watchdog.get("first_failure_at")
    now = clock()
    try:
        if watchdog.get("reported") is True and isinstance(since, str) and number is None:
            client = client if client is not None else _default_client()
            number = open_or_comment(
                client, REPO, MARKER, now, TITLE, _opening_body(since, now), _still_down(since, now)
            )
            write_state(outage_path, {"issue": number, "since": since})
        elif since is None and number is not None:
            client = client if client is not None else _default_client()
            began = noted.get("since")
            lasted = f"après {_minutes(began, now)} min de panne" if isinstance(began, str) else "après une panne"
            client.post_comment(REPO, number, f"Le socle répond de nouveau, {lasted}.\n\n{FOOTER}")
            client.set_issue_state(REPO, number, "closed")
            write_state(outage_path, {"issue": None})
    except Exception as error:  # never a failed unit because of GitHub: the next passage tries again
        print(f"<3>workflow-core: issue de panne : {error}", file=out, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(run_once())
