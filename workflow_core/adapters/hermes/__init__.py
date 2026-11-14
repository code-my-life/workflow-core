"""The adapter to the agent runtime: the only package allowed to name it (ADR 0001)."""

from workflow_core.adapters.hermes.board import make_board_reader, make_board_watcher
from workflow_core.adapters.hermes.contract import check_contract
from workflow_core.adapters.hermes.live import make_live_reader, make_live_watcher
from workflow_core.adapters.hermes.machine import make_machine_checks
from workflow_core.adapters.hermes.version import (
    VERSION_CHECK_SECONDS,
    VERSION_COMMAND,
    read_runtime_version,
    version_change,
)
from workflow_core.adapters.hermes.write_cycle import WriteCycleError, run_write_cycle
from workflow_core.adapters.hermes.writer import make_board_writer

# The runtime's name, as the core writes it to the owner.
RUNTIME_NAME = "Hermès"

__all__ = [
    "RUNTIME_NAME",
    "VERSION_CHECK_SECONDS",
    "VERSION_COMMAND",
    "WriteCycleError",
    "check_contract",
    "make_board_reader",
    "make_board_watcher",
    "make_board_writer",
    "make_live_reader",
    "make_live_watcher",
    "make_machine_checks",
    "read_runtime_version",
    "run_write_cycle",
    "version_change",
]
