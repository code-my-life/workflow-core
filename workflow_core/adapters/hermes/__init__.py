"""The adapter to the agent runtime: the only package allowed to name it (ADR 0001)."""

from workflow_core.adapters.hermes.board import make_board_reader

__all__ = ["make_board_reader"]
