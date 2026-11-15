"""The contract test against the installed Hermès: the real bases of the declared boards, opened read-only.

It runs at the install (scripts/install.sh) and by `uv run pytest tests/contract`. On a machine without any board
base (the CI on GitHub), it is skipped with its reason, never passed empty. As the service at its start, it leaves out
the board of a workflow plugged by its manifest that does not exist yet: the engine creates it only at its first pass.
That board is said, with its reason, by the skip of a second test.
"""

import re
import shutil

import pytest
from workflow_core.adapters import runtime_adapter
from workflow_core.config import load_workflows

# Why a board is left out of the contract: the board of a workflow plugged by its manifest, not created yet.
NOT_CREATED = "board absent, créé par le moteur à sa première passe"


def contract_boards(adapter) -> tuple[list[str], list[str]]:
    """The boards the contract covers and the boards it leaves out, as the service picks them at its start: every board
    served by its sheet, and a manifest's board only once it exists."""
    writer = adapter.make_board_writer()
    workflows = load_workflows().values()
    served = {workflow.board for workflow in workflows if not workflow.plugged_by_manifest}
    plugged = {workflow.board for workflow in workflows if workflow.plugged_by_manifest}
    created = {board for board in plugged if writer.has_board(board)}
    return sorted(served | created), sorted(plugged - created - served)


def test_the_installed_boards_keep_the_contract():
    adapter = runtime_adapter()
    boards, _ = contract_boards(adapter)
    reader = adapter.make_board_reader()
    if not any(reader.base_path(board).is_file() for board in boards):
        pytest.skip(f"no installed board base under {reader.home} for {', '.join(boards)}")

    verdict = adapter.check_contract(boards)

    assert verdict.ok, "contract broken:\n" + "\n".join(verdict.problems)


def test_a_manifest_board_not_created_yet_is_left_to_the_engine():
    boards, waiting = contract_boards(runtime_adapter())
    # No board is left out but one the engine has not created: each declared board is checked or said here.
    assert set(boards) | set(waiting) == {workflow.board for workflow in load_workflows().values()}
    if waiting:
        left_out = " ; ".join(f"{board}: {NOT_CREATED}" for board in waiting)
        pytest.skip(f"{left_out} ; le contrat porte sur {', '.join(boards) or 'aucun board'}")


def test_the_installed_version_line_keeps_the_contract():
    adapter = runtime_adapter()
    if shutil.which(adapter.VERSION_COMMAND[0]) is None:
        pytest.skip(f"no installed runtime command {adapter.VERSION_COMMAND[0]!r} on the PATH")

    version, problem = adapter.read_runtime_version()

    assert problem is None, problem
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
