"""The agents' configuration and their active plugins' code, read by the runtime's adapter (ADR 0012).

The cases are those of factory-live's `profiles.test.ts` at commit d47f235, with their expected values copied out by
hand. The runtime's home and the workflow's repository are built in a temporary folder.
"""

from pathlib import Path

import pytest
from workflow_core.adapters.hermes.profiles import (
    ProfileReader,
    longest_timeout_of,
    plugin_hooks_of,
    profile_facts_of,
)
from workflow_core.config import Workflow

# coder-guard's code, cut down to its delays and its `register()` block, as it is in the factory.
CODER_GUARD = """def _git(root: Path, *args, timeout: int = 60):
    return subprocess.run(["git", *args], cwd=root, timeout=timeout)
def _run(cmd: str, cwd: Path, timeout: int = 900) -> tuple[int, str]:
    r = subprocess.run(["bash", "-lc", cmd], cwd=cwd, timeout=timeout)
f = _git(root, "fetch", "--quiet", "origin", timeout=120)
r = subprocess.run(["python3", str(script)], input=diff, timeout=600)
def register(ctx):
    ctx.register_hook("pre_tool_call", pre_tool)
    ctx.register_hook("post_tool_call", post_tool)
    ctx.register_hook("pre_llm_call", inject)
"""


def write_config(root: Path, yaml: str, folder: str = "profiles") -> None:
    (root / folder / "coder").mkdir(parents=True, exist_ok=True)
    (root / folder / "coder" / "config.yaml").write_text(yaml, encoding="utf-8")


@pytest.fixture
def roots(tmp_path) -> tuple[Path, Path]:
    home = tmp_path / "runtime"
    repo = tmp_path / "factory"
    write_config(
        home, "agent:\n  max_turns: 400\nterminal:\n  timeout: 900\nplugins:\n  enabled: [coder-guard, comment-guard]\n"
    )
    write_config(repo, "plugins:\n  enabled: [coder-guard]\n  hook_callback_timeout: 600\n")
    (home / "profiles" / "coder" / "plugins" / "coder-guard").mkdir(parents=True)
    (home / "profiles" / "coder" / "plugins" / "coder-guard" / "__init__.py").write_text(CODER_GUARD)
    (repo / "plugins" / "comment-guard").mkdir(parents=True)
    (repo / "plugins" / "comment-guard" / "__init__.py").write_text(
        'def register(ctx):\n    ctx.register_hook( "pre_tool_call",guard )'
    )
    return home, repo


def factory(repo: Path, **fields) -> Workflow:
    values = {"key": "factory", "board": "factory", "sheet": Path("f.json"), "repo": repo}
    return Workflow(**{**values, "profiles": "profiles", "plugins": "plugins", **fields})


def test_the_service_delays_the_repo_value_and_the_active_plugin_hooks_are_read(roots):
    home, repo = roots

    facts = ProfileReader(home).facts("coder", factory(repo))

    assert (facts.profile, facts.hook_timeout, facts.tool_deadline, facts.terminal_timeout, facts.max_turns) == (
        "coder",
        None,
        None,
        900,
        400,
    )
    assert (facts.repo_read, facts.repo_hook_timeout) == (True, 600)
    assert facts.plugins == ["coder-guard", "comment-guard"]
    assert facts.changed_at > 0
    # coder-guard can run a 900 s command (`_run`); comment-guard writes no delay.
    assert facts.longest_commands == {"coder-guard": 900}
    assert [(hook.plugin, hook.hook, hook.callback, hook.source) for hook in facts.hooks] == [
        ("coder-guard", "pre_tool_call", "pre_tool", 'ctx.register_hook("pre_tool_call", pre_tool)'),
        ("coder-guard", "post_tool_call", "post_tool", 'ctx.register_hook("post_tool_call", post_tool)'),
        ("coder-guard", "pre_llm_call", "inject", 'ctx.register_hook("pre_llm_call", inject)'),
        # Absent from the profile: the code of the workflow's repository, last line without a newline.
        ("comment-guard", "pre_tool_call", "guard", 'ctx.register_hook( "pre_tool_call",guard )'),
    ]


def test_an_unreadable_repo_differs_from_a_key_absent_from_the_repo(roots):
    home, repo = roots
    reader = ProfileReader(home)

    write_config(repo, "plugins:\n  enabled: [coder-guard]\n")
    absent = reader.facts("coder", factory(repo))
    assert (absent.repo_read, absent.repo_hook_timeout) == (True, None)

    write_config(repo, "plugins: [")
    unreadable = reader.facts("coder", factory(repo))
    assert (unreadable.repo_read, unreadable.repo_hook_timeout) == (False, None)
    # A workflow without a repository: the repository cannot be read either.
    assert reader.facts("coder", factory(None)).repo_read is False


def test_numbers_are_read_as_the_runtime_reads_them_with_the_batch_deadline_as_fallback():
    facts = profile_facts_of(
        "coder",
        {
            "plugins": {"hook_callback_timeout": "600"},
            "timeouts": {"tools": {"concurrent_batch": 300}},
            "terminal": {"timeout": True},
            "agent": {"max_turns": ""},
        },
        None,
        None,
        [],
    )
    assert (facts.hook_timeout, facts.tool_deadline, facts.terminal_timeout, facts.max_turns) == (600, 300, None, None)
    both = profile_facts_of(
        "coder", {"timeouts": {"tools": {"sequential_call": 500, "concurrent_batch": 300}}}, None, None, []
    )
    assert both.tool_deadline == 500


def test_only_hook_registrations_are_kept():
    assert plugin_hooks_of("x", "# register_hook est appelé plus bas\nregister(ctx)") == []


def test_the_longest_literal_timeout_is_read_and_nothing_else():
    assert longest_timeout_of("subprocess.run(cmd, capture_output=True, text=True, timeout=30)") == 30
    assert longest_timeout_of("def f(timeout: float = 12.5):\n  run(timeout=timeout)") == 12.5
    assert longest_timeout_of("run(timeout=TIMEOUT)\nrequests.get(url)") is None
    assert longest_timeout_of("# timeout_seconds = 9000\nmy_timeout=99") is None


def test_a_missing_or_unreadable_config_or_an_unsafe_name_gives_none(roots):
    home, repo = roots
    reader = ProfileReader(home)

    assert reader.facts("unknown", factory(repo)) is None
    assert reader.facts("../coder", factory(repo)) is None
    write_config(home, "plugins: [")
    assert reader.facts("coder", factory(repo)) is None


def test_the_repo_config_is_read_under_the_declared_profiles_folder(roots, tmp_path):
    home, _ = roots
    studio = tmp_path / "studio"
    write_config(studio, "plugins:\n  hook_callback_timeout: 120\n", folder="hermes/profiles")
    write_config(studio, "plugins:\n  hook_callback_timeout: 999\n", folder="profiles")
    entry = Workflow(key="studio", board="studio", sheet=Path("s.json"), repo=studio)

    # Without `profiles`, the repository's profiles are under `hermes/profiles`.
    assert ProfileReader(home).facts("coder", entry).repo_hook_timeout == 120
    declared = entry.model_copy(update={"profiles": "profiles"})
    assert ProfileReader(home).facts("coder", declared).repo_hook_timeout == 999
    # Without `plugins`, no plugin is read from the repository: comment-guard has no code then.
    assert [hook.plugin for hook in ProfileReader(home).facts("coder", entry).hooks] == ["coder-guard"] * 3


def test_the_read_files_are_left_unchanged(roots):
    home, repo = roots
    files = sorted(path for root in roots for path in root.rglob("*") if path.is_file())
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in files}

    ProfileReader(home).facts("coder", factory(repo))

    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in files} == before
