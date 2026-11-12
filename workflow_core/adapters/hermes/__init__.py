"""The adapter to the agent runtime: the only package allowed to name it (ADR 0001)."""

from workflow_core.adapters.hermes.board import make_board_reader, make_board_watcher
from workflow_core.adapters.hermes.contract import check_contract
from workflow_core.adapters.hermes.version import VERSION_COMMAND, read_runtime_version

# The runtime's name, as the core writes it to the owner.
RUNTIME_NAME = "Hermès"

__all__ = [
    "RUNTIME_NAME",
    "VERSION_COMMAND",
    "check_contract",
    "make_board_reader",
    "make_board_watcher",
    "read_runtime_version",
]
