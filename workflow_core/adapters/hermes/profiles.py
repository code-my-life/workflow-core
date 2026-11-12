"""The configuration of each Hermès agent and the code of its active plugins, read-only (ADR 0012).

Ported from factory-live at commit d47f235: `ProfileConfigReader.facts` and its helpers
(apps/server/src/adapters/hermes/profiles.ts), and the safe paths of the configurations and plugins
(apps/server/src/adapters/hermes/paths.ts). The workflow's repository, and the folders of its profiles and plugins
there, come from the workflow's entry in config/workflows.toml: nothing proper to a workflow is written here.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

import yaml

from workflow_core.adapters.hermes.paths import NAME, safe_file
from workflow_core.config import Workflow
from workflow_core.model.facts import PluginHook, ProfileFacts
from workflow_core.text import js_regex, js_trim, whole

# The ceilings of a read: a bigger file is not a file the diagnosis expects.
MAX_CONFIG_BYTES = 256 * 1024
MAX_PLUGIN_BYTES = 512 * 1024
# Where a workflow's repository keeps its agents' profiles when its entry does not say (ADR 0001).
DEFAULT_REPO_PROFILES = "hermes/profiles"

# A hook a plugin plugs, read in its code: `ctx.register_hook("<hook>", <function>)`.
REGISTER_HOOK = js_regex(r"""register_hook\(\s*["'](\w+)["']\s*,\s*(\w+)""")
# A delay written in a plugin's Python code: `timeout=120`, `timeout: int = 900`.
TIMEOUT_LITERAL = js_regex(r"\btimeout\s*(?::\s*(?:int|float)\s*)?=\s*(\d{1,6}(?:\.\d{1,3})?)\b")
# The YAML keys read in config.yaml, declared for the contract test: each must stay a table when present.
CONFIG_TABLES = ("plugins", "timeouts", "timeouts.tools", "terminal", "agent")
CONFIG_KEYS = (
    "plugins.hook_callback_timeout",
    "plugins.enabled",
    "timeouts.tools.sequential_call",
    "timeouts.tools.concurrent_batch",
    "terminal.timeout",
    "agent.max_turns",
)

# JavaScript's `Number(text)` for a trimmed text: a decimal, `0x`, `0o` or `0b` literal, or `Infinity`.
_JS_DECIMAL = re.compile(r"[+-]?(?:\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?|Infinity)", re.ASCII)
_JS_RADIX = re.compile(r"0(?:[xX][0-9a-fA-F]+|[oO][0-7]+|[bB][01]+)", re.ASCII)


def _js_number(text: str) -> float:
    trimmed = js_trim(text)
    if trimmed == "":
        return 0.0
    if _JS_DECIMAL.fullmatch(trimmed):
        return float(trimmed.replace("Infinity", "inf"))
    if _JS_RADIX.fullmatch(trimmed):
        return float(int(trimmed, 0))
    return math.nan


def amount(value: Any) -> float | None:
    """A delay or a number of turns, as Hermès reads it (`float(...)`): anything else counts as absent."""
    if isinstance(value, bool):
        number = math.nan
    elif isinstance(value, int | float):
        number = float(value)
    elif isinstance(value, str):
        number = _js_number(js_trim(value))
    else:
        number = math.nan
    return whole(number) if value != "" and math.isfinite(number) else None


def _record(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _strings(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def plugin_hooks_of(plugin: str, source: str) -> list[PluginHook]:
    """The hooks a plugin plugs, read in its code, each with its registration line, trimmed."""
    hooks = []
    for match in REGISTER_HOOK.finditer(source):
        start = source.rfind("\n", 0, match.start()) + 1
        end = source.find("\n", match.start())
        line = source[start : end if end != -1 else len(source)]
        hooks.append(PluginHook(plugin=plugin, hook=match[1], callback=match[2], source=js_trim(line)))
    return hooks


def longest_timeout_of(source: str) -> float | None:
    """The longest command a plugin's code can run: the greatest literal delay it passes or gives by default."""
    values = [float(match[1]) for match in TIMEOUT_LITERAL.finditer(source)]
    return whole(max(values)) if values else None


def profile_facts_of(
    profile: str,
    config: dict,
    repo: dict | None,
    changed_at: float | None,
    hooks: list[PluginHook],
    longest_commands: dict[str, float] | None = None,
) -> ProfileFacts:
    """What the diagnosis keeps of an agent's configuration: its delays and its active plugins."""
    plugins = _record(config.get("plugins"))
    tools = _record(_record(config.get("timeouts")).get("tools"))
    deadline = amount(tools.get("sequential_call"))
    return ProfileFacts(
        profile=profile,
        hook_timeout=amount(plugins.get("hook_callback_timeout")),
        # Hermès falls back on a batch of tools' delay when the delay of one call is not set.
        tool_deadline=deadline if deadline is not None else amount(tools.get("concurrent_batch")),
        terminal_timeout=amount(_record(config.get("terminal")).get("timeout")),
        max_turns=amount(_record(config.get("agent")).get("max_turns")),
        changed_at=changed_at,
        repo_read=repo is not None,
        repo_hook_timeout=amount(_record(repo.get("plugins")).get("hook_callback_timeout"))
        if repo is not None
        else None,
        plugins=_strings(plugins.get("enabled")),
        hooks=hooks,
        longest_commands=longest_commands or {},
    )


def _parts(folder: str) -> list[str] | None:
    """A folder of the workflow's entry, relative to its repository, as path parts; `None` if it would leave it."""
    parts = [part for part in folder.split("/") if part not in ("", ".")]
    return parts if parts and ".." not in parts else None


def config_path(home: Path, agent: str) -> Path | None:
    """`profiles/<agent>/config.yaml` of Hermès' home: the configuration in service."""
    return safe_file(home / "profiles", agent, "config.yaml") if NAME.fullmatch(agent) else None


def repo_config_path(workflow: Workflow, agent: str) -> Path | None:
    """`<repo>/<profiles>/<agent>/config.yaml`: the configuration the workflow's repository installs."""
    repo = workflow.repo_path
    parts = _parts(workflow.profiles or DEFAULT_REPO_PROFILES)
    if repo is None or parts is None or not NAME.fullmatch(agent):
        return None
    return safe_file(repo, *parts, agent, "config.yaml")


def plugin_source(home: Path, workflow: Workflow, agent: str, plugin: str) -> Path | None:
    """The code of a plugin installed for the agent, else the one of the workflow's repository."""
    if not NAME.fullmatch(agent) or not NAME.fullmatch(plugin):
        return None
    installed = safe_file(home / "profiles", agent, "plugins", plugin, "__init__.py")
    if installed is not None:
        return installed
    repo = workflow.repo_path
    parts = _parts(workflow.plugins) if workflow.plugins else None
    return safe_file(repo, *parts, plugin, "__init__.py") if repo is not None and parts is not None else None


def read_capped(path: Path, maximum: int) -> str | None:
    """The content of a file of the allowed list, if it does not pass its ceiling."""
    try:
        return path.read_bytes().decode("utf-8", errors="replace") if path.stat().st_size <= maximum else None
    except OSError:
        return None


def parse_capped(path: Path, maximum: int) -> dict | None:
    """An allowed YAML; `None` when it is absent, too big or unreadable."""
    content = read_capped(path, maximum)
    if content is None:
        return None
    try:
        return _record(yaml.safe_load(content))
    except yaml.YAMLError:
        return None


def _mtime_seconds(path: Path) -> float | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    # As Node's `mtimeMs / 1000`: milliseconds with their fraction, then seconds.
    return (stat.st_mtime_ns // 1_000_000_000 * 1000 + (stat.st_mtime_ns % 1_000_000_000) / 1e6) / 1000


class ProfileReader:
    """Reads the agents' configurations under Hermès' home, and their workflow's repository."""

    def __init__(self, home: Path) -> None:
        self.home = home

    def facts(self, agent: str, workflow: Workflow) -> ProfileFacts | None:
        path = config_path(self.home, agent)
        config = parse_capped(path, MAX_CONFIG_BYTES) if path is not None else None
        if path is None or config is None:
            return None
        repo_path = repo_config_path(workflow, agent)
        longest: dict[str, float] = {}
        hooks: list[PluginHook] = []
        for plugin in _strings(_record(config.get("plugins")).get("enabled")):
            source = plugin_source(self.home, workflow, agent, plugin)
            code = read_capped(source, MAX_PLUGIN_BYTES) if source is not None else None
            if code is None:
                continue
            seconds = longest_timeout_of(code)
            if seconds is not None:
                longest[plugin] = seconds
            hooks += plugin_hooks_of(plugin, code)
        return profile_facts_of(
            agent,
            config,
            parse_capped(repo_path, MAX_CONFIG_BYTES) if repo_path is not None else None,
            _mtime_seconds(path),
            hooks,
            longest,
        )


def make_profile_reader(environ: dict[str, str] | None = None) -> ProfileReader:
    """The configuration reader of the Hermès home named by `HERMES_HOME`."""
    from workflow_core.adapters.hermes.board import hermes_home

    return ProfileReader(hermes_home(environ))
