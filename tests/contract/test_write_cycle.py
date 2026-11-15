"""The write cycle against the installed Hermès: every command the writer runs, done on a throwaway board
`workflow-core-contrat-<Unix time>` and read back, then the board removed (ADR 0019).

It runs at the install (scripts/install.sh) and by `uv run pytest tests/contract`. It writes to no board in service.
On a machine without the runtime's command (the CI on GitHub), it is skipped with its reason.
"""

import shutil

import pytest
from workflow_core.adapters import runtime_adapter


def test_a_full_write_cycle_on_a_throwaway_board():
    adapter = runtime_adapter()
    if shutil.which(adapter.VERSION_COMMAND[0]) is None:
        pytest.skip(f"no installed runtime command {adapter.VERSION_COMMAND[0]!r} on the PATH")
    reader = adapter.make_board_reader()

    try:
        board = adapter.run_write_cycle(adapter.make_board_writer(), reader)
        assert board.startswith("workflow-core-contrat-")
    finally:
        folder = reader.home / "kanban" / "boards"
        # Neither a board of the reserved prefix nor one of the former `contrat-` prefix is left.
        left = (
            sorted(path.name for pattern in ("workflow-core-contrat-*", "contrat-*") for path in folder.glob(pattern))
            if folder.is_dir()
            else []
        )
        assert left == [], f"throwaway boards left: {left}"
