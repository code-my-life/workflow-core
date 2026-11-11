"""Build the core's service: check the contract with the runtime, then serve, whatever the verdict (ADR 0019)."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from types import ModuleType

from fastapi import FastAPI

from workflow_core.api.app import create_app
from workflow_core.config import Workflow
from workflow_core.model.contract import ContractVerdict, CoreState

# journald reads a leading "<n>" on a service's output as the syslog priority of the line.
ERROR = "<3>"
INFO = "<6>"


def journal(priority: str, message: str) -> None:
    print(f"{priority}workflow-core: {message}", file=sys.stderr, flush=True)


def build_service(*, secret: str, workflows: dict[str, Workflow], adapter: ModuleType) -> FastAPI:
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

    return create_app(secret=secret, workflows=workflows, boards=adapter.make_board_reader(), state=state)
