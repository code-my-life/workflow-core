"""The contract test against the installed Hermès: the last dated lines of the real agent journals, read-only.

On a machine without any agent journal (the CI on GitHub), it is skipped with its reason, never passed empty.
"""

import pytest
from workflow_core.adapters.hermes.board import make_board_reader
from workflow_core.adapters.hermes.contract import journal_problems, journals


def test_the_installed_agent_journals_keep_the_contract():
    home = make_board_reader().home
    found = journals(home)
    if not found:
        pytest.skip(f"no agent journal under {home / 'profiles'}")

    problems = journal_problems(home)

    assert problems == [], "contract broken:\n" + "\n".join(problems)
