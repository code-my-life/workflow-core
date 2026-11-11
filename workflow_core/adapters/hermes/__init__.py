"""The adapter to the agent runtime: the only package allowed to name it (ADR 0001)."""

from workflow_core.adapters.hermes.board import make_board_reader
from workflow_core.adapters.hermes.contract import check_contract
from workflow_core.adapters.hermes.version import VERSION_COMMAND, read_runtime_version

__all__ = ["VERSION_COMMAND", "check_contract", "make_board_reader", "read_runtime_version"]
