"""The contract test against the installed Hermès: the session base of each installed agent, opened read-only.

On a machine without any agent session base (the CI on GitHub), it is skipped with its reason, never passed empty.
"""

import pytest
from workflow_core.adapters.hermes.board import make_board_reader
from workflow_core.adapters.hermes.contract import session_bases, session_problems


def test_the_installed_session_bases_keep_the_contract():
    home = make_board_reader().home
    if not session_bases(home):
        pytest.skip(f"no agent session base under {home / 'profiles'}")

    problems = session_problems(home)

    assert problems == [], "contract broken:\n" + "\n".join(problems)
