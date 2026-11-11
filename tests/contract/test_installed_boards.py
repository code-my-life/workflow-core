"""The contract test against the installed Hermès: the real bases of the declared boards, opened read-only.

It runs at the install (scripts/install.sh) and by `uv run pytest tests/contract`. On a machine without any board
base (the CI on GitHub), it is skipped with its reason, never passed empty.
"""

import pytest
from workflow_core.adapters import runtime_adapter
from workflow_core.config import load_workflows


def test_the_installed_boards_keep_the_contract():
    adapter = runtime_adapter()
    boards = sorted({workflow.board for workflow in load_workflows().values()})
    reader = adapter.make_board_reader()
    if not any(reader.base_path(board).is_file() for board in boards):
        pytest.skip(f"no installed board base under {reader.home} for {', '.join(boards)}")

    verdict = adapter.check_contract(boards)

    assert verdict.ok, "contract broken:\n" + "\n".join(verdict.problems)
