"""The agents of a workflow and their configuration in service, as the core serves them (issue #129), and the port
through which an adapter reads them. Nothing here is a secret: no model address, key, token nor MCP server setting."""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel

from workflow_core.config import Workflow


class AgentModel(BaseModel):
    name: str
    # "?" when the configuration does not name it.
    provider: str


class AgentPlugin(BaseModel):
    name: str
    # The longest command the plugin's code can run (seconds), as the diagnosis reads it; None when none is written.
    longest_seconds: float | None
    # The timeouts of its hook noted in the agent's journal over the period.
    hook_timeouts: int


class AgentLimit(BaseModel):
    key: Literal["hook_callback_timeout", "tool_deadline", "terminal_timeout", "max_turns"]
    # Set in the agent's configuration; None when absent (or zero or less: no limit), the runtime's default applies.
    value: float | None
    default: float | None
    unit: Literal["seconds", "turns"]


class Agent(BaseModel):
    name: str
    # The skills the workflow's manifest declares for the agent; empty without a manifest.
    skills: list[str]
    model: AgentModel | None
    fallbacks: list[AgentModel]
    effort: str | None
    toolsets: list[str]
    plugins: list[AgentPlugin]
    # The names of the MCP servers only.
    mcp: list[str]
    limits: list[AgentLimit]


class KanbanSettings(BaseModel):
    """The runtime's settings of the kanban that bound the cards in progress."""

    max_in_progress: int | None
    max_in_progress_per_profile: int | None


class AgentsReader(Protocol):
    """Reads the agents of a workflow over the last `days` days. `now`: Unix seconds."""

    def agents(self, workflow: Workflow, days: int, now: float) -> tuple[KanbanSettings | None, list[Agent]]: ...
