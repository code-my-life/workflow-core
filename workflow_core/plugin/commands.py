"""Run the command of a workflow's station in its repository and read its decision (ADR 0005).

The command runs as `uv run --directory <repo> <command...>`, launched with a list of arguments, never through a shell,
in its own process group. Its input is a JSON object on its standard input:
`{"schema_version": 1, "item": {"id", "issue", "title"}, "station", "attempt", "answer"}`; its output, on its standard
output, is a decision `{"decision", "reason", "to"}`. A command past its delay is killed with its whole group and
becomes `fail`; an output that is not a decision becomes `fail` too. Its error output goes to the journal, 2,000
characters at most.

A workflow's intake command runs the same way, with `{"schema_version": 1, "issue": {"repo", "number", "url",
"title", "body", "labels"}}` on its input; its output is `{"decision": "accept", "id"}` or `{"decision": "refuse",
"reason"}`. A launch that fails, a delay passed or an unreadable output is no decision at all.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from workflow_core.model.decision import Decision, IntakeDecision

SCHEMA_VERSION = 1
# The most characters of a command's error output written to the journal.
STDERR_LIMIT = 2000
UNREADABLE = "sortie illisible"
# The core's variables a command does not inherit: its shared secret, its settings and its GitHub App.
PRIVATE_PREFIXES = ("WORKFLOW_CORE_", "GITHUB_APP_")
# The core's own virtual environment: `uv run --directory` ignores it for the workflow's project, with a warning on
# every launch.
DROPPED_NAMES = ("VIRTUAL_ENV",)


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


@dataclass(frozen=True)
class _Ran:
    """A command's run: its standard output and exit code, or the problem that left it without an output."""

    stdout: str = ""
    code: int | None = None
    problem: str | None = None
    # Whether the problem, if any, is already in the journal (a launch that failed).
    told: bool = False


def _launch(
    repo: Path, command: list[str], payload: dict, timeout: float, log: Callable[[str], None], label: str
) -> _Ran:
    """Run `uv run --directory <repo> <command...>` with the payload on its input, in its own process group, without
    the core's secrets; its error output goes to the journal. Never raises."""
    arguments = ["uv", "run", "--directory", str(repo), *command]
    # The core's own secrets never reach a workflow's command (ADR 0002), nor its virtual environment.
    environ = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(PRIVATE_PREFIXES) and name not in DROPPED_NAMES
    }
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
        return _Ran(problem=f"lancement impossible : {error}", told=True)
    try:
        stdout, stderr = process.communicate(json.dumps(payload), timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(process)
        stdout, stderr = process.communicate()
        _log_stderr(stderr, log, label)
        return _Ran(problem=f"délai de {timeout:g} s dépassé")
    finally:
        if process.poll() is None:
            _kill_group(process)
            process.wait()
    _log_stderr(stderr, log, label)
    return _Ran(stdout=stdout, code=process.returncode)


def _unreadable(ran: _Ran, log: Callable[[str], None], label: str) -> None:
    log(f"<3>workflow-core: {label} : {UNREADABLE} (code {ran.code}) : {ran.stdout[:STDERR_LIMIT]!r}")


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
    ran = _launch(repo, command, payload, timeout, log, label)
    if ran.problem is not None:
        return Decision.fail(ran.problem)
    try:
        return Decision.model_validate(json.loads(ran.stdout))
    except (ValueError, ValidationError):
        _unreadable(ran, log, label)
        return Decision.fail(UNREADABLE)


def intake_input(*, repo: str, number: int, url: str, title: str, body: str, labels: list[str]) -> dict:
    """The input of a workflow's intake command: the issue it decides on."""
    return {
        "schema_version": SCHEMA_VERSION,
        "issue": {"repo": repo, "number": number, "url": url, "title": title, "body": body, "labels": list(labels)},
    }


def run_intake(
    repo: Path,
    command: list[str],
    payload: dict,
    timeout: float,
    log: Callable[[str], None],
    *,
    label: str = "intake",
) -> IntakeDecision | None:
    """Run a workflow's intake command on an issue and return its decision; None when it could not be launched, ran
    past its delay or gave an unreadable output, each written to the journal: none of them is a decision. Never
    raises."""
    ran = _launch(repo, command, payload, timeout, log, label)
    if ran.problem is not None:
        if not ran.told:
            log(f"<3>workflow-core: {label} : {ran.problem}")
        return None
    try:
        return IntakeDecision.model_validate(json.loads(ran.stdout))
    except (ValueError, ValidationError):
        _unreadable(ran, log, label)
        return None


def _log_stderr(stderr: str | None, log: Callable[[str], None], label: str) -> None:
    if stderr and stderr.strip():
        log(f"workflow-core: {label} : sortie d'erreur : {stderr.strip()[:STDERR_LIMIT]}")
