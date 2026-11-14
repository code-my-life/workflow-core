"""Run the command of a workflow's station in its repository and read its decision (ADR 0005).

The command runs as `uv run --directory <repo> <command...>`, launched with a list of arguments, never through a shell,
in its own process group. Its input is a JSON object on its standard input:
`{"schema_version": 1, "item": {"id", "issue", "title"}, "station", "attempt", "answer"}`; its output, on its standard
output, is a decision `{"decision", "reason", "to"}`. A command past its delay is killed with its whole group and
becomes `fail`; an output that is not a decision becomes `fail` too. Its error output goes to the journal, 2,000
characters at most.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError

from workflow_core.model.decision import Decision

SCHEMA_VERSION = 1
# The most characters of a command's error output written to the journal.
STDERR_LIMIT = 2000
UNREADABLE = "sortie illisible"
# The core's variables a command does not inherit: its shared secret, its settings and its GitHub App.
PRIVATE_PREFIXES = ("WORKFLOW_CORE_", "GITHUB_APP_")


def command_input(
    *, item: str, issue: str | None, title: str, station: str, attempt: int, answer: dict | None = None
) -> dict:
    """The input of a station's command; `answer` is the owner's answer at an `owner` station
    (`{"gesture", "note"}`), None otherwise."""
    return {
        "schema_version": SCHEMA_VERSION,
        "item": {"id": item, "issue": issue, "title": title},
        "station": station,
        "attempt": attempt,
        "answer": answer,
    }


def _kill_group(process: subprocess.Popen) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)


def run_command(
    repo: Path,
    command: list[str],
    payload: dict,
    timeout: float,
    log: Callable[[str], None],
    *,
    label: str = "commande",
) -> Decision:
    """Run the command and return its decision; never raises. `label` prefixes its journal lines."""
    arguments = ["uv", "run", "--directory", str(repo), *command]
    # The core's own secrets never reach a workflow's command (ADR 0002).
    environ = {name: value for name, value in os.environ.items() if not name.startswith(PRIVATE_PREFIXES)}
    try:
        process = subprocess.Popen(
            arguments,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            env=environ,
        )
    except OSError as error:
        log(f"<3>workflow-core: {label} : lancement impossible : {error}")
        return Decision.fail(f"lancement impossible : {error}")
    try:
        stdout, stderr = process.communicate(json.dumps(payload), timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(process)
        stdout, stderr = process.communicate()
        _log_stderr(stderr, log, label)
        return Decision.fail(f"délai de {timeout:g} s dépassé")
    finally:
        if process.poll() is None:
            _kill_group(process)
            process.wait()
    _log_stderr(stderr, log, label)
    try:
        return Decision.model_validate(json.loads(stdout))
    except (ValueError, ValidationError):
        log(f"<3>workflow-core: {label} : {UNREADABLE} (code {process.returncode}) : {stdout[:STDERR_LIMIT]!r}")
        return Decision.fail(UNREADABLE)


def _log_stderr(stderr: str | None, log: Callable[[str], None], label: str) -> None:
    if stderr and stderr.strip():
        log(f"workflow-core: {label} : sortie d'erreur : {stderr.strip()[:STDERR_LIMIT]}")
