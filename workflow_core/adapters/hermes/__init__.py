"""The adapter to the agent runtime: the only package allowed to name it (ADR 0001)."""

from workflow_core.adapters.hermes.board import make_board_reader
from workflow_core.adapters.hermes.contract import check_contract

__all__ = ["check_contract", "make_board_reader"]
