"""The core's configuration: the declared workflows, each with its key, its board and the path of its sheet.

Until the manifests (engine epic), `config/workflows.toml` lists them, one `[[workflow]]` table each.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, Field

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "workflows.toml"


class Workflow(BaseModel):
    key: str = Field(min_length=1)
    board: str = Field(min_length=1)
    # The path of the workflow's sheet; `~` is accepted.
    sheet: Path
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

    @property
    def repo_path(self) -> Path | None:
        return self.repo.expanduser() if self.repo is not None else None

    @property
    def sheet_path(self) -> Path:
        return self.sheet.expanduser()


def load_workflows(path: Path = DEFAULT_CONFIG) -> dict[str, Workflow]:
    """The declared workflows, by key. A key declared twice is refused."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    workflows: dict[str, Workflow] = {}
    for entry in data.get("workflow", []):
        workflow = Workflow.model_validate(entry)
        if workflow.key in workflows:
            raise ValueError(f'{path}: workflow "{workflow.key}" is declared twice')
        workflows[workflow.key] = workflow
    return workflows
