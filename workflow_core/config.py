"""The core's configuration: the declared workflows, each with its key, its board, the path of its sheet, and the
optional values its diagnosis needs (its triggers' settings, its sources, its own words).

`config/workflows.toml` lists them, one `[[workflow]]` table each; `WORKFLOW_CORE_CONFIG` names another file (the test
machine's). A workflow plugged by its manifest (ADR 0005, amended) declares only its key and its repository, and
optionally its board; the entries with a sheet keep their form until their migration.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from workflow_core.model.checks import DeclaredCheck
from workflow_core.model.data import DataDeclaration

__all__ = [
    "CONFIG_VARIABLE",
    "DEFAULT_CONFIG",
    "DataDeclaration",
    "Gesture",
    "Workflow",
    "config_path",
    "load_checks",
    "load_workflows",
]

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "workflows.toml"
# The variable that names the configuration file; without it, DEFAULT_CONFIG.
CONFIG_VARIABLE = "WORKFLOW_CORE_CONFIG"


def config_path(environ: dict[str, str] | None = None) -> Path:
    """The configuration file: the one named by WORKFLOW_CORE_CONFIG (`~` accepted), else the repository's."""
    env = os.environ if environ is None else environ
    named = env.get(CONFIG_VARIABLE)
    return Path(named).expanduser() if named else DEFAULT_CONFIG


class Gesture(BaseModel):
    """A gesture the workflow declares (`[[workflow.gestures]]`, ADR 0010): `comment` posts `/<key> <name> [note]` on
    the issue of an item, `issue` opens an issue in the declared `repo`. The core publishes them; it never posts one."""

    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9-]*$")
    kind: Literal["comment", "issue"]
    repo: str | None = None


class Workflow(BaseModel):
    key: str = Field(min_length=1)
    # The workflow's board; by default, its key (ADR 0003).
    board: str = Field(min_length=1)
    # The path of the workflow's sheet; `~` is accepted. Without it, the workflow is plugged by the manifest of its
    # repository, and only the engine serves it.
    sheet: Path | None = None
    # The diagnosis' triggers (optional). Minutes a ready card held by the active pull request guard waits before it
    # is a trigger.
    held_after_minutes: int = Field(default=30, ge=0)
    # The workflow's conventions for the two kinds of interventions that are not diagnosed, as regular expressions,
    # case-insensitive: the title of a card whose `needs_input` block is a pull request to approve, and, on such a
    # card, the block reason of a red main. Without them, every blocked card is diagnosed.
    approval_card_title: str | None = None
    main_red_reason: str | None = None
    # The diagnosis' sources and words of the workflow (optional). A value left out leaves the rule that needs it
    # without it, never with another workflow's. `repo`: the workflow's repository (`~` accepted); `profiles` and
    # `plugins`: the folders of its agents' profiles and of its plugins in that repository (the runtime's adapter
    # knows the default folder of the profiles).
    repo: Path | None = None
    profiles: str | None = None
    plugins: str | None = None
    # The name endings of the workflow's guard plugins, its default agent, its relay agent, and the text of its pull
    # request acceptance check.
    guard_suffixes: list[str] = Field(default_factory=list)
    default_agent: str | None = None
    relay_agent: str | None = None
    pr_acceptance: str | None = None
    # How a card's body names its branch, as a regular expression whose first group is the branch (multiline).
    body_branch: str | None = None
    # The data the workflow shows besides its cards (`[[workflow.data]]`, ADR 0011): read by the runtime's adapter at
    # the declared paths, served as generic values. None by default.
    data: list[DataDeclaration] = Field(default_factory=list)
    # The gestures the workflow declares (`[[workflow.gestures]]`), until the manifests. None by default.
    gestures: list[Gesture] = Field(default_factory=list)
    # The workflow's own manifest of its agents, relative to its repository (`profiles.<agent>.skills`): the agents it
    # declares and their skills (#129). Without it, its agents are those of its board's runs.
    manifest: str | None = None
    # The notifier (optional): whether the workflow's waits are alerted in their issues, and the assignees whose
    # blocked cards are not (lanes run by scripts that publish their own message).
    notify: bool = False
    notify_skip_assignees: list[str] = Field(default_factory=list)
    # The agents a held card's relaunch command relays through, the first one that is not its assignee; without any,
    # the alert of a held card leaves the command out.
    notify_relay_agents: list[str] = Field(default_factory=list)
    # For a workflow plugged by its manifest: only the issues that carry this label are taken in; without it, the
    # issues labelled `test` are left out, so that a production core never takes an issue of the test machine.
    issue_label: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _board_defaults_to_the_key(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("board") is None and isinstance(data.get("key"), str):
            return {**data, "board": data["key"]}
        return data

    @model_validator(mode="after")
    def _sheet_or_repository(self) -> Workflow:
        if self.sheet is None and self.repo is None:
            raise ValueError(f'workflow "{self.key}": a sheet or a repository is required')
        return self

    @property
    def plugged_by_manifest(self) -> bool:
        """Whether the workflow is plugged by its manifest (no sheet): the engine serves it, the API does not yet."""
        return self.sheet is None

    @property
    def repo_path(self) -> Path | None:
        return self.repo.expanduser() if self.repo is not None else None

    @property
    def sheet_path(self) -> Path:
        if self.sheet is None:
            raise ValueError(f'workflow "{self.key}" has no sheet: it is plugged by its manifest')
        return self.sheet.expanduser()


def load_workflows(path: Path | None = None) -> dict[str, Workflow]:
    """The declared workflows, by key, read from `path`, by default the configuration file (`config_path`). A key
    declared twice is refused."""
    path = config_path() if path is None else path
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    workflows: dict[str, Workflow] = {}
    for entry in data.get("workflow", []):
        workflow = Workflow.model_validate(entry)
        if workflow.key in workflows:
            raise ValueError(f'{path}: workflow "{workflow.key}" is declared twice')
        workflows[workflow.key] = workflow
    return workflows


def load_checks(path: Path | None = None) -> list[DeclaredCheck]:
    """The declared checks of the machine (`[[check]]`), in their order, read from `path`, by default the
    configuration file (`config_path`). An id declared twice is refused."""
    path = config_path() if path is None else path
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    checks: list[DeclaredCheck] = []
    for entry in data.get("check", []):
        check = DeclaredCheck.model_validate(entry)
        if any(known.id == check.id for known in checks):
            raise ValueError(f'{path}: check "{check.id}" is declared twice')
        checks.append(check)
    return checks
