"""The agents of a workflow and their configuration in service, read-only (issue #129).

The agents are those the workflow's manifest declares (`profiles.<agent>`), else those of its board's runs. Each one's
`config.yaml` under Hermès' home is read for the keys of `CONFIG_KEYS` only (profiles.py): the model and its
fallbacks, the effort, the CLI's toolsets, the active plugins, the names of the MCP servers and the delays the
diagnosis reads. A model's address, a key, a token or the settings of an MCP server are never read into the answer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from workflow_core.adapters.hermes.paths import safe_file
from workflow_core.adapters.hermes.profiles import (
    MAX_CONFIG_BYTES,
    ProfileReader,
    _parts,
    config_path,
    parse_capped,
    profile_facts_of,
)
from workflow_core.adapters.hermes.texts import TERMS
from workflow_core.config import Workflow
from workflow_core.diagnosis.guards import guard_risks, hook_timeouts_by_plugin
from workflow_core.model.agents import Agent, AgentLimit, AgentModel, AgentPlugin, GuardRisk, KanbanSettings
from workflow_core.model.facts import ProfileFacts


def _record(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _strings(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _model(entry: Any, name_key: str) -> AgentModel | None:
    record = _record(entry)
    name = _text(record.get(name_key))
    return AgentModel(name=name, provider=_text(record.get("provider")) or "?") if name else None


def _limits(facts: ProfileFacts | None) -> list[AgentLimit]:
    """The runtime's delays that cut an agent's work, set in its configuration, with the runtime's defaults (as
    factory-live's `limitsOf`): zero or less means no limit set."""

    def set_(value: float | None) -> float | None:
        return value if value is not None and value > 0 else None

    present = facts is not None
    return [
        AgentLimit(
            key="hook_callback_timeout",
            value=set_(facts.hook_timeout) if present else None,
            default=TERMS.hook_timeout_default,
            unit="seconds",
        ),
        AgentLimit(
            key="tool_deadline",
            value=set_(facts.tool_deadline) if present else None,
            default=TERMS.tool_deadline_default,
            unit="seconds",
        ),
        AgentLimit(
            key="terminal_timeout",
            value=set_(facts.terminal_timeout) if present else None,
            default=TERMS.terminal_timeout_default,
            unit="seconds",
        ),
        AgentLimit(key="max_turns", value=set_(facts.max_turns) if present else None, default=None, unit="turns"),
    ]


class HermesAgentsReader:
    """Reads the agents of a workflow under Hermès' home (`AgentsReader`)."""

    def __init__(self, home: Path, boards, journals) -> None:
        self.home = home
        self.boards = boards
        self.journals = journals
        self.profiles = ProfileReader(home)

    def _manifest(self, workflow: Workflow) -> dict | None:
        repo = workflow.repo_path
        parts = _parts(workflow.manifest) if workflow.manifest else None
        if repo is None or parts is None:
            return None
        path = safe_file(repo, *parts)
        return parse_capped(path, MAX_CONFIG_BYTES) if path is not None else None

    def _names(self, workflow: Workflow, manifest: dict | None) -> list[str]:
        declared = _record(manifest.get("profiles")) if manifest is not None else {}
        if declared:
            return sorted(name for name in declared if isinstance(name, str))
        found: set[str] = set()
        for facts in self.boards.read_board_facts(workflow.board):
            found |= {run.profile for run in facts.runs if run.profile}
        return sorted(found)

    def kanban(self) -> KanbanSettings | None:
        path = safe_file(self.home, "config.yaml")
        config = parse_capped(path, MAX_CONFIG_BYTES) if path is not None else None
        if config is None:
            return None
        kanban = _record(config.get("kanban"))
        return KanbanSettings(
            max_in_progress=_int(kanban.get("max_in_progress")),
            max_in_progress_per_profile=_int(kanban.get("max_in_progress_per_profile")),
        )

    def agents(
        self, workflow: Workflow, days: int, now: float
    ) -> tuple[KanbanSettings | None, list[Agent], list[GuardRisk]]:
        manifest = self._manifest(workflow)
        declared = _record(manifest.get("profiles")) if manifest is not None else {}
        read = [
            self._agent(workflow, name, _strings(_record(declared.get(name)).get("skills")), days, now)
            for name in self._names(workflow, manifest)
        ]
        # The guards at risk, from the facts of the agents served by this very answer (issue #137).
        facts = [agent_facts for _, agent_facts in read if agent_facts is not None]
        return self.kanban(), [agent for agent, _ in read], guard_risks(facts, TERMS)

    def _agent(
        self, workflow: Workflow, name: str, skills: list[str], days: int, now: float
    ) -> tuple[Agent, ProfileFacts | None]:
        path = config_path(self.home, name)
        config = parse_capped(path, MAX_CONFIG_BYTES) if path is not None else None
        if config is None:
            return Agent(
                name=name,
                skills=skills,
                model=None,
                fallbacks=[],
                effort=None,
                toolsets=[],
                plugins=[],
                mcp=[],
                limits=_limits(None),
            ), None
        facts = self.profiles.facts(name, workflow) or profile_facts_of(name, config, None, None, [])
        logs = self.journals.facts(name, now - days * 86_400, now) or []
        timeouts = hook_timeouts_by_plugin(logs, facts)
        fallbacks = config.get("fallback_providers")
        return Agent(
            name=name,
            skills=skills,
            model=_model(config.get("model"), "default"),
            fallbacks=[
                model
                for entry in (fallbacks if isinstance(fallbacks, list) else [])
                if (model := _model(entry, "model"))
            ],
            effort=_text(_record(config.get("agent")).get("reasoning_effort")),
            toolsets=_strings(_record(config.get("platform_toolsets")).get("cli")),
            plugins=[
                AgentPlugin(
                    name=plugin,
                    longest_seconds=facts.longest_commands.get(plugin),
                    hook_timeouts=timeouts.get(plugin, 0),
                )
                for plugin in facts.plugins
            ],
            mcp=sorted(key for key in _record(config.get("mcp_servers")) if isinstance(key, str)),
            limits=_limits(facts),
        ), facts
