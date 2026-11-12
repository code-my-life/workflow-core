"""The issues the core keeps for its own incidents (an outage, a broken contract), each recognised by its hidden
marker: one issue per incident, taken again rather than duplicated (ADR 0019)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from workflow_core.adapters.github import APP_BOT

# An incident issue closed less than this ago is reopened rather than a new one opened.
REOPEN_WITHIN = timedelta(hours=24)


def marked_issue(client, repo: str, marker: str, now: datetime) -> dict | None:
    """The App's issue with this marker to take again: an open one, else one closed less than 24 hours ago. An issue
    written by another account never counts, whatever its body."""
    marked = [
        issue
        for issue in client.list_issues(repo)
        if (issue.get("user") or {}).get("login") == APP_BOT and marker in (issue.get("body") or "")
    ]
    opened = [issue for issue in marked if issue.get("state") == "open"]
    if opened:
        return opened[0]
    recent = [
        issue
        for issue in marked
        if issue.get("closed_at")
        and now - datetime.fromisoformat(issue["closed_at"].replace("Z", "+00:00")) < REOPEN_WITHIN
    ]
    return recent[0] if recent else None


def open_or_comment(client, repo: str, marker: str, now: datetime, title: str, body: str, comment: str) -> int:
    """Comment the incident's issue, reopened if needed, else open one; its number."""
    found = marked_issue(client, repo, marker, now)
    if found is None:
        return client.create_issue(repo, title, body)
    number = int(found["number"])
    if found.get("state") != "open":
        client.set_issue_state(repo, number, "open")
    client.post_comment(repo, number, comment)
    return number


def read_state(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def write_state(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value) + "\n", encoding="utf-8")
    temporary.replace(path)
