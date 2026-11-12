"""The contract test against the installed Hermès: the configuration of each installed agent, read-only.

On a machine without any agent configuration (the CI on GitHub), it is skipped with its reason, never passed empty.
"""

import pytest
from workflow_core.adapters.hermes.board import make_board_reader
from workflow_core.adapters.hermes.contract import config_problems, configs


def test_the_installed_agent_configs_keep_the_contract():
    home = make_board_reader().home
    if not configs(home):
        pytest.skip(f"no agent configuration under {home / 'profiles'}")

    problems = config_problems(home)

    assert problems == [], "contract broken:\n" + "\n".join(problems)
