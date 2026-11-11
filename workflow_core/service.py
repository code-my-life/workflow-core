"""Build the core's service: check the contract with the runtime, then serve, whatever the verdict (ADR 0019)."""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from types import ModuleType
from typing import Any

import uvicorn
from fastapi import FastAPI

from workflow_core.api.app import create_app
from workflow_core.config import Workflow
from workflow_core.events import JOURNAL_FILE, state_dir
from workflow_core.model.contract import ContractVerdict, CoreState

# journald reads a leading "<n>" on a service's output as the syslog priority of the line.
ERROR = "<3>"
INFO = "<6>"


def journal(priority: str, message: str) -> None:
    print(f"{priority}workflow-core: {message}", file=sys.stderr, flush=True)


def build_service(
    *,
    secret: str,
    workflows: dict[str, Workflow],
    adapter: ModuleType,
    clock: Callable[[], float] = time.time,
    spy: Callable[[str], Any] | None = None,
) -> FastAPI:
    """The API, with the core's state after the contract check. A broken contract keeps the observer mode and is
    written to the journal; the service still answers."""
    boards = sorted({workflow.board for workflow in workflows.values()})
    try:
        verdict = adapter.check_contract(boards)
    except Exception as error:  # a check that cannot run is a broken contract, never a crash at startup
        verdict = ContractVerdict(
            ok=False, checked_at=datetime.now(UTC).isoformat(timespec="seconds"), problems=[f"vérification : {error}"]
        )
    version, _ = adapter.read_runtime_version()
    state = CoreState(contract=verdict, runtime_version=version)
    if verdict.ok:
        journal(INFO, f"contrat tenu pour {', '.join(boards)} ; mode {state.mode}")
    else:
        journal(ERROR, f"contrat cassé, mode {state.mode} : " + " ; ".join(verdict.problems))

    return create_app(
        secret=secret,
        workflows=workflows,
        boards=adapter.make_board_reader(),
        state=state,
        watcher=adapter.make_board_watcher(),
        journal_path=state_dir() / JOURNAL_FILE,
        clock=clock,
        spy=spy,
    )


# Seconds uvicorn waits for open connections at a stop: an SSE client never closes its own, so the core ends them.
GRACEFUL_STOP_SECONDS = 2


def server_config(app: FastAPI, host: str, port: int, log_level: str = "info") -> uvicorn.Config:
    """How the core is served, by `python -m workflow_core` and by the tests alike."""
    return uvicorn.Config(
        app, host=host, port=port, log_level=log_level, timeout_graceful_shutdown=GRACEFUL_STOP_SECONDS
    )
