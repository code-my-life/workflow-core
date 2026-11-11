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
