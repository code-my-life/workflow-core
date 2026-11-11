#!/usr/bin/env python3
"""The watchdog of workflow-core: an outage of /healthz of 5 minutes or more is written to journald, once (ADR 0019).

A systemd timer runs it every minute, apart from the core: standard library only, nothing imported from the core's
package. Each run asks /healthz once, with the shared secret, and keeps in its state file when the outage began and
whether it was written. Lines start with the syslog priority journald reads: "<3>" (err) for the outage, "<6>" (info)
for the recovery. Opening an issue on GitHub comes with the GitHub notifier epic.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

HEALTHZ_URL = "http://127.0.0.1:8447/healthz"
TIMEOUT_SECONDS = 5
OUTAGE_SECONDS = 5 * 60
SECRET_VARIABLE = "WORKFLOW_CORE_SECRET"


def default_state_path() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return base / "workflow-core" / "watchdog.json"


def healthz_answers(secret: str | None = None) -> bool:
    """True when /healthz answers 200 within 5 seconds, with the shared secret."""
    token = os.environ.get(SECRET_VARIABLE, "") if secret is None else secret
    request = urllib.request.Request(HEALTHZ_URL, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _load(path: Path) -> dict:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(state, dict) and isinstance(state.get("reported"), bool):
            return {"first_failure_at": state.get("first_failure_at"), "reported": state["reported"]}
    except (OSError, ValueError):
        pass
    return {"first_failure_at": None, "reported": False}


def _save(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(state) + "\n", encoding="utf-8")
    temporary.replace(path)


def _minutes(since: str, now: datetime) -> int:
    return int((now - datetime.fromisoformat(since)).total_seconds() // 60)


def run_once(
    *,
    probe: Callable[[], bool] = healthz_answers,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    state_path: Path | None = None,
    out: TextIO | None = None,
) -> None:
    """One passage: ask /healthz, update the state, write the outage or the recovery when due."""
    path = state_path or default_state_path()
    out = out or sys.stderr
    state = _load(path)
    now = clock()

    if probe():
        if state["reported"]:
            since = state["first_failure_at"]
            print(
                f"<6>workflow-core /healthz répond de nouveau, après une panne depuis {since} "
                f"({_minutes(since, now)} min)",
                file=out,
                flush=True,
            )
        if state != {"first_failure_at": None, "reported": False}:
            _save(path, {"first_failure_at": None, "reported": False})
        return

    if state["first_failure_at"] is None:
        state["first_failure_at"] = now.isoformat(timespec="seconds")
    since = state["first_failure_at"]
    if not state["reported"] and (now - datetime.fromisoformat(since)).total_seconds() >= OUTAGE_SECONDS:
        print(f"<3>workflow-core /healthz en échec depuis {since} ({_minutes(since, now)} min)", file=out, flush=True)
        state["reported"] = True
    _save(path, state)


if __name__ == "__main__":
    run_once()
