"""Read a workflow's manifest: `<repo>/workflow-manifest.json`, `schema_version` 1 (ADR 0005, amended).

Only the key and the stations (their key, nature, command and delay) are read at this stage; the manifest's judge comes
later. A manifest that is absent, unreadable, or of another key leaves its workflow unplugged, with the reason.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

from workflow_core.config import Workflow

MANIFEST_FILE = "workflow-manifest.json"


class Station(BaseModel):
    """A station of the manifest. A `code` station names its command, a list of arguments run in the workflow's
    repository, and its delay in seconds."""

    key: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9-]*$")
    nature: Literal["code", "owner", "agent"]
    command: list[str] | None = None
    timeout_seconds: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _a_code_station_has_its_command(self) -> Station:
        if self.nature == "code" and (not self.command or self.timeout_seconds is None):
            raise ValueError(f'station "{self.key}": a code station needs its command and its timeout_seconds')
        return self


class Manifest(BaseModel):
    schema_version: Literal[1]
    key: str = Field(min_length=1)
    stations: list[Station] = Field(min_length=1)


class ManifestRefusedError(Exception):
    """A workflow left unplugged, with its reason in French."""

    def __init__(self, key: str, reason: str) -> None:
        super().__init__(f"workflow {key} non branché : {reason}")
        self.key = key
        self.reason = reason


@dataclass(frozen=True)
class PluggedWorkflow:
    """A workflow plugged by its manifest: its key, its board, its repository, its stations in order."""

    key: str
    board: str
    repo: Path
    stations: tuple[Station, ...]

    def lane(self, station: str) -> str:
        """The lane of a station, `<key>-<station>`: the assignee of a card that stands there, never a profile."""
        return f"{self.key}-{station}"

    def station_of_lane(self, assignee: str | None) -> Station | None:
        return next((station for station in self.stations if self.lane(station.key) == assignee), None)

    def station(self, key: str) -> Station | None:
        return next((station for station in self.stations if station.key == key), None)

    def next_station(self, key: str) -> Station | None:
        """The station after this one in the manifest; None after the last."""
        keys = [station.key for station in self.stations]
        index = keys.index(key)
        return self.stations[index + 1] if index + 1 < len(self.stations) else None


def _why(error: Exception) -> str:
    if isinstance(error, ValidationError) and error.errors():
        first = error.errors()[0]
        return f"{'.'.join(str(part) for part in first['loc']) or 'racine'} : {first['msg']}"
    return str(error) or type(error).__name__


def plug(workflow: Workflow) -> PluggedWorkflow:
    """The workflow plugged by the manifest of its repository; `ManifestRefusedError` with the reason otherwise."""
    repo = workflow.repo_path
    if repo is None:
        raise ManifestRefusedError(workflow.key, "aucun dépôt déclaré")
    path = repo / MANIFEST_FILE
    if not path.is_file():
        raise ManifestRefusedError(workflow.key, f"manifeste absent : {path}")
    try:
        manifest = Manifest.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as error:
        raise ManifestRefusedError(workflow.key, f"manifeste illisible : {path} ({_why(error)})") from error
    if manifest.key != workflow.key:
        reason = f"clé du manifeste « {manifest.key} » différente de « {workflow.key} »"
        raise ManifestRefusedError(workflow.key, reason)
    keys = [station.key for station in manifest.stations]
    if len(set(keys)) != len(keys):
        raise ManifestRefusedError(workflow.key, f"manifeste illisible : {path} (station déclarée deux fois)")
    return PluggedWorkflow(key=workflow.key, board=workflow.board, repo=repo, stations=tuple(manifest.stations))
