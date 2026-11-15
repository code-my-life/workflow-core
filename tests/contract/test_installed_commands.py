"""The commands the writer runs, checked against the installed Hermès: each declared command and option is still in
its `--help` (ADR 0019). Nothing is written: only `--help` is run.

On a machine without the runtime's command (the CI on GitHub), it is skipped with its reason.
"""

import shutil

import pytest
from workflow_core.adapters import runtime_adapter


def test_the_installed_commands_keep_their_declared_options():
    adapter = runtime_adapter()
    if shutil.which(adapter.VERSION_COMMAND[0]) is None:
        pytest.skip(f"no installed runtime command {adapter.VERSION_COMMAND[0]!r} on the PATH")

    assert adapter.contract.command_problems() == []
