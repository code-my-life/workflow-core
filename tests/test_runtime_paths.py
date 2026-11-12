"""The only files of the runtime and of a workflow's repository the diagnosis reads (ADR 0012).

The cases are those of factory-live's `paths.test.ts` at commit d47f235: paths built from checked names, never
leaving their root, never following a symbolic link.
"""

from pathlib import Path

import pytest
from workflow_core.adapters.hermes.agent_log import journal_paths
from workflow_core.adapters.hermes.evidence import evidence_dir, evidence_entry
from workflow_core.adapters.hermes.profiles import config_path, plugin_source, repo_config_path
from workflow_core.config import Workflow


@pytest.fixture
def home(tmp_path) -> Path:
    home = tmp_path / "runtime"
    profiles = home / "profiles"
    repo = tmp_path / "factory"
    board = home / "kanban" / "boards" / "factory"
    (profiles / "coder" / "logs").mkdir(parents=True)
    (profiles / "coder" / "plugins" / "coder-guard").mkdir(parents=True)
    (repo / "plugins" / "merge-guard").mkdir(parents=True)
    (repo / "profiles" / "coder").mkdir(parents=True)
    (board / "workspaces" / "t_665ba7d7" / "evidence").mkdir(parents=True)
    (board / "evidence" / "t_03d9d40d").mkdir(parents=True)
    (profiles / "coder" / "config.yaml").write_text("plugins: {}\n")
    (profiles / "coder" / ".env").write_text("OPENAI_API_KEY=sk-secret\n")
    (profiles / "coder" / "auth.json").write_text("{}")
    (profiles / "coder" / "logs" / "agent.log").write_text("")
    (profiles / "coder" / "plugins" / "coder-guard" / "__init__.py").write_text("")
    (repo / "plugins" / "merge-guard" / "__init__.py").write_text("")
    (repo / "profiles" / "coder" / "config.yaml").write_text("")
    return home


def workflow(home: Path) -> Workflow:
    repo = home.parent / "factory"
    return Workflow(
        key="factory", board="factory", sheet=Path("f.json"), repo=repo, profiles="profiles", plugins="plugins"
    )


def test_no_name_leaves_its_root_and_no_other_profile_file_is_read(home):
    profiles = home / "profiles"
    entry = workflow(home)
    repo = entry.repo
    assert config_path(home, "coder") == profiles / "coder" / "config.yaml"
    assert repo_config_path(entry, "coder") == repo / "profiles" / "coder" / "config.yaml"
    assert journal_paths(home, "coder") == [profiles / "coder" / "logs" / "agent.log"]
    assert (
        plugin_source(home, entry, "coder", "coder-guard")
        == profiles / "coder" / "plugins" / "coder-guard" / "__init__.py"
    )
    # A plugin absent from the profile: the one of the workflow's repository.
    assert plugin_source(home, entry, "coder", "merge-guard") == repo / "plugins" / "merge-guard" / "__init__.py"

    for name in ("..", "../coder", "coder/..", ".env", "auth.json", "coder/.env", ""):
        assert config_path(home, name) is None, name
        assert repo_config_path(entry, name) is None, name
        assert journal_paths(home, name) == [], name
        assert plugin_source(home, entry, "coder", name) is None, name
        assert plugin_source(home, entry, name, "coder-guard") is None, name
    assert evidence_dir(home, "factory", "../t_665ba7d7") is None
    assert evidence_dir(home, "factory", "t_665BA7D7") is None


def test_no_symlink_is_followed_even_inside_a_profile(home):
    profiles = home / "profiles"
    (profiles / "scribe" / "logs").mkdir(parents=True)
    (profiles / "scribe" / "config.yaml").symlink_to(profiles / "coder" / ".env")
    (profiles / "linked").symlink_to(profiles / "coder" / "logs")
    (profiles / "scribe" / "logs" / "agent.log").symlink_to(profiles / "coder" / "auth.json")
    assert config_path(home, "scribe") is None
    assert journal_paths(home, "scribe") == []
    assert journal_paths(home, "linked") == []

    board = home / "kanban" / "boards" / "factory"
    assert evidence_dir(home, "factory", "t_665ba7d7") == board / "workspaces" / "t_665ba7d7" / "evidence"
    assert evidence_dir(home, "factory", "t_03d9d40d") == board / "evidence" / "t_03d9d40d"
    assert evidence_dir(home, "factory", "t_00000000") is None
    evidence = board / "workspaces" / "t_665ba7d7" / "evidence"
    (evidence / "01-test.txt").write_text("ok")
    (evidence / "02-env.txt").symlink_to(profiles / "coder" / ".env")
    assert evidence_entry(evidence, ["01-test.txt"], "file") == evidence / "01-test.txt"
    assert evidence_entry(evidence, ["02-env.txt"], "file") is None
    assert evidence_entry(evidence, ["..", "..", "evidence"], "dir") is None
