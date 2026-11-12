"""The agents of a workflow and their configuration in service, without a secret (issue #129).

The agents are those the workflow's manifest declares, else those of its board's runs. Their configuration is read in
the runtime's home, read-only, for the keys listed only: never a model's address, a key, a token nor the settings of
an MCP server beyond its name.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from workflow_core.adapters import runtime_adapter
from workflow_core.adapters.hermes.texts import TERMS
from workflow_core.api.app import create_app
from workflow_core.config import load_workflows

from conftest import RUNTIME_HOME_VAR, make_runtime_board

ROOT = Path(__file__).resolve().parent.parent
SECRET = "test-secret-not-a-real-one"
GOOD = {"Authorization": f"Bearer {SECRET}"}
DASH = "\u2014"
PARIS = ZoneInfo("Europe/Paris")
# The test's now: 2026-11-12 10:00 in Paris.
NOW = datetime(2026, 11, 12, 10, 0, tzinfo=PARIS).timestamp()

# Fictive secrets: none of them may leave the core.
BASE_URL = "http://secret-host.invalid:8000/v1"
API_KEY = "sk-test-not-a-real-key"
TOKEN = "tok-test-not-a-real-token"

CODER_CONFIG = f"""
model:
  default: glm-5.3
  provider: freellmapi
  base_url: {BASE_URL}
  api_key: {API_KEY}
fallback_providers:
  - {{model: qwen-3.6, provider: nvidia}}
  - {{model: devstral-2, provider: custom}}
agent:
  reasoning_effort: high
  max_turns: 60
platform_toolsets:
  cli: [terminal, file]
plugins:
  enabled: [coder-guard, factory-notify]
  hook_callback_timeout: 30
timeouts:
  tools:
    sequential_call: 600
terminal:
  timeout: 300
mcp_servers:
  graphify:
    url: http://127.0.0.1:8081/mcp
    env:
      TOKEN: {TOKEN}
"""

GUARD = """
def pre_tool(**kwargs):
    run(["npm", "test"], timeout=900)

def register(ctx):
    ctx.register_hook("pre_tool_call", pre_tool)
"""
NOTIFY = """
def notify(**kwargs):
    pass

def register(ctx):
    ctx.register_hook("post_tool_call", notify)
"""
MANIFEST = """
profiles:
  coder: {skills: [factory-author-proof], plugins: [coder-guard, factory-notify]}
  gate: {skills: [factory-gate], plugins: [gate-readonly]}
"""


def timeout_line(when: datetime) -> str:
    """A hook timeout of coder-guard's callback, as the runtime writes it in the agent's journal (Paris time)."""
    stamp = when.astimezone(PARIS).strftime("%Y-%m-%d %H:%M:%S")
    return (
        f"{stamp},000 WARNING [20261112_090000_abcdef] hermes_cli.plugins: Hook 'pre_tool_call' callback pre_tool "
        f"timed out after 30s {DASH} skipping"
    )


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def factory(tmp_path, monkeypatch) -> dict:
    """A runtime home with the factory's board (runs of `coder` and `gate`), the agents' configurations, plugins and
    journals, the root configuration and the factory's repository with its manifest."""
    home = tmp_path / "runtime"
    make_runtime_board(
        home,
        "factory",
        tasks=[{"id": "t_1", "title": "impl factory-live#24", "status": "review", "created_at": 1}],
        runs=[
            {"id": 1, "task_id": "t_1", "profile": "gate", "status": "done", "started_at": 2},
            {"id": 2, "task_id": "t_1", "profile": "coder", "status": "done", "started_at": 3},
        ],
    )
    write(home / "config.yaml", "kanban:\n  max_in_progress: 2\n  max_in_progress_per_profile: 1\n")
    coder = home / "profiles" / "coder"
    write(coder / "config.yaml", CODER_CONFIG)
    write(coder / "plugins" / "coder-guard" / "__init__.py", GUARD)
    write(coder / "plugins" / "factory-notify" / "__init__.py", NOTIFY)
    (home / "profiles" / "gate").mkdir(parents=True)
    repo = tmp_path / "factory-repo"
    write(repo / "factory.yaml", MANIFEST)
    monkeypatch.setenv(RUNTIME_HOME_VAR, str(home))
    workflows = load_workflows(ROOT / "config" / "workflows.toml")
    workflows["factory"] = workflows["factory"].model_copy(update={"repo": repo})
    return {"home": home, "repo": repo, "workflows": workflows}


def agents(factory: dict, days: int | None = None, workflows=None) -> dict:
    client = TestClient(
        create_app(
            secret=SECRET,
            workflows=workflows or factory["workflows"],
            boards=runtime_adapter().make_board_reader(),
            clock=lambda: NOW,
        )
    )
    params = {"days": days} if days is not None else {}
    response = client.get("/api/workflows/factory/agents", params=params, headers=GOOD)
    assert response.status_code == 200, response.text
    return response.json()


def limits(hook=None, tool=None, terminal=None, turns=None) -> list[dict]:
    return [
        {"key": "hook_callback_timeout", "value": hook, "default": TERMS.hook_timeout_default, "unit": "seconds"},
        {"key": "tool_deadline", "value": tool, "default": TERMS.tool_deadline_default, "unit": "seconds"},
        {"key": "terminal_timeout", "value": terminal, "default": TERMS.terminal_timeout_default, "unit": "seconds"},
        {"key": "max_turns", "value": turns, "default": None, "unit": "turns"},
    ]


def digests(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_an_agent_is_served_with_its_configuration(factory):
    before = digests(factory["home"]) | digests(factory["repo"])

    answer = agents(factory)

    assert answer["schema_version"] == 1
    assert answer["workflow"] == "factory"
    assert [agent["name"] for agent in answer["agents"]] == ["coder", "gate"]
    assert answer["agents"][0] == {
        "name": "coder",
        "skills": ["factory-author-proof"],
        "model": {"name": "glm-5.3", "provider": "freellmapi"},
        "fallbacks": [{"name": "qwen-3.6", "provider": "nvidia"}, {"name": "devstral-2", "provider": "custom"}],
        "effort": "high",
        "toolsets": ["terminal", "file"],
        "plugins": [
            {"name": "coder-guard", "longest_seconds": 900, "hook_timeouts": 0},
            {"name": "factory-notify", "longest_seconds": None, "hook_timeouts": 0},
        ],
        "mcp": ["graphify"],
        "limits": limits(hook=30, tool=600, terminal=300, turns=60),
    }
    # The files read stay as they were, byte for byte (C7).
    assert digests(factory["home"]) | digests(factory["repo"]) == before


def test_no_secret_of_a_config_leaves_the_core(factory):
    text = json.dumps(agents(factory))

    for secret in (BASE_URL, "secret-host", API_KEY, TOKEN, "base_url", "api_key", "8081"):
        assert secret not in text, secret
    assert '"mcp": ["graphify"]' in text


def test_plugin_hook_timeouts_and_longest_command(factory):
    journal = factory["home"] / "profiles" / "coder" / "logs" / "agent.log"
    lines = [
        timeout_line(datetime.fromtimestamp(NOW - 3600, PARIS)),
        timeout_line(datetime.fromtimestamp(NOW - 2 * 86_400, PARIS)),
        # Forty days ago: outside a period of 30 days, inside one of 90.
        timeout_line(datetime.fromtimestamp(NOW - 40 * 86_400, PARIS)),
    ]
    write(journal, "".join(f"{line}\n" for line in sorted(lines)))

    guard = agents(factory, days=30)["agents"][0]["plugins"][0]
    assert guard == {"name": "coder-guard", "longest_seconds": 900, "hook_timeouts": 2}
    assert agents(factory, days=90)["agents"][0]["plugins"][0]["hook_timeouts"] == 3


def test_an_unreadable_config_gives_an_empty_agent(factory):
    write(factory["home"] / "profiles" / "gate" / "config.yaml", "model: [unclosed\n")

    gate = agents(factory)["agents"][1]
    assert gate == {
        "name": "gate",
        "skills": ["factory-gate"],
        "model": None,
        "fallbacks": [],
        "effort": None,
        "toolsets": [],
        "plugins": [],
        "mcp": [],
        "limits": limits(),
    }

    # Without a manifest, the agents are those of the board's runs, with no skill.
    workflows = dict(factory["workflows"])
    workflows["factory"] = workflows["factory"].model_copy(update={"manifest": None})
    listed = agents(factory, workflows=workflows)["agents"]
    assert [(agent["name"], agent["skills"]) for agent in listed] == [("coder", []), ("gate", [])]


def test_the_kanban_settings_are_served(factory):
    assert agents(factory)["kanban"] == {"max_in_progress": 2, "max_in_progress_per_profile": 1}

    write(factory["home"] / "config.yaml", "kanban: [broken\n")
    assert agents(factory)["kanban"] is None


def guard_config(hook: str = "", tools: str = "") -> str:
    """An agent's configuration with only `coder-guard` active, its hook's delay and its tool ceiling as given."""
    return f"plugins:\n  enabled: [coder-guard]\n{hook}{tools}"


def guard_code(command: int, hooked: bool = True) -> str:
    """The code of a guard that can run a command of `command` seconds, plugged to a hook or not."""
    register = '    ctx.register_hook("pre_tool_call", pre_tool)\n' if hooked else "    pass\n"
    return f'def pre_tool(**kwargs):\n    run(["npm", "test"], timeout={command})\n\ndef register(ctx):\n{register}'


def guard_agents(factory: dict, agents_setup: dict[str, tuple[str, str]]) -> list[dict]:
    """Writes each agent's configuration and its guard's code, declares them in the manifest, and reads the risks."""
    home, repo = factory["home"], factory["repo"]
    for name, (config, code) in agents_setup.items():
        write(home / "profiles" / name / "config.yaml", config)
        write(home / "profiles" / name / "plugins" / "coder-guard" / "__init__.py", code)
    profiles = "".join(f"  {name}: {{skills: [], plugins: [coder-guard]}}\n" for name in agents_setup)
    write(repo / "factory.yaml", f"profiles:\n{profiles}")
    return agents(factory)["guard_risks"]


def test_guard_risks_are_published_with_the_agents(factory):
    hook = "  hook_callback_timeout: 600\n"
    risks = guard_agents(
        factory,
        {"coder": (guard_config(hook), guard_code(900)), "scribe": (guard_config(hook), guard_code(900))},
    )

    # 600 s asked for the hook, but a tool call's ceiling (420 s by default) holds it.
    assert risks == [
        {"plugin": "coder-guard", "agents": ["coder", "scribe"], "longest_seconds": 900.0, "limit_seconds": 420.0}
    ]


def test_a_short_command_or_a_plugin_without_hook_is_no_risk(factory):
    no_delay = guard_config("  hook_callback_timeout: 0\n", "timeouts:\n  tools:\n    sequential_call: 0\n")
    risks = guard_agents(
        factory,
        {
            # 20 s under the default delay of 30 s.
            "coder": (guard_config(), guard_code(20)),
            # 900 s, but plugged to no hook.
            "scribe": (guard_config(), guard_code(900, hooked=False)),
            # 900 s plugged to a hook, but no delay cuts it.
            "writer": (no_delay, guard_code(900)),
        },
    )

    assert risks == []
