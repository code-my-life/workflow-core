"""Plug a workflow by its manifest: `<repo>/workflow-manifest.json`, judged at the start (ADR 0005, amended; ADR 0006).

The manifest is judged by `judge_manifest`, with its sheet read from the repository and the lanes still in use on the
workflow's board, read through the card reader without writing. A manifest that is absent, unreadable or refused
leaves its workflow unplugged, with the reason; the other workflows run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from workflow_core.config import Workflow
from workflow_core.model.board import BoardReader, BoardUnavailableError, Item
from workflow_core.model.manifest import ManifestIntake, ManifestStation, judge_manifest

MANIFEST_FILE = "workflow-manifest.json"
# The statuses of a card that holds nothing any more.
FINISHED = ("done", "archived")


class ManifestRefusedError(Exception):
    """A workflow left unplugged, with its reason in French."""

    def __init__(self, key: str, reason: str) -> None:
        super().__init__(f"workflow {key} non branché : {reason}")
        self.key = key
        self.reason = reason


@dataclass(frozen=True)
class PluggedWorkflow:
    """A workflow plugged by its manifest: its key, its board, its repository, its stations in order, the repository
    of its issues and its intake command, and the label its issues must carry (None: any but `test`)."""

    key: str
    board: str
    repo: Path
    stations: tuple[ManifestStation, ...]
    issues_repo: str = ""
    intake: ManifestIntake | None = None
    issue_label: str | None = None

    def lane(self, station: str) -> str:
        """The lane of a station, `<key>-<station>`: the assignee of a card that stands there, never a profile."""
        return f"{self.key}-{station}"

    def station_of_lane(self, assignee: str | None) -> ManifestStation | None:
        return next((station for station in self.stations if self.lane(station.key) == assignee), None)

    def station(self, key: str) -> ManifestStation | None:
        return next((station for station in self.stations if station.key == key), None)

    def next_station(self, key: str) -> ManifestStation | None:
        """The station after this one in the manifest; None after the last."""
        keys = [station.key for station in self.stations]
        index = keys.index(key)
        return self.stations[index + 1] if index + 1 < len(self.stations) else None


def is_root_card(item: Item, key: str) -> bool:
    """Whether the card is the root card of an item of this workflow: its idempotency key is `<key>:<local id>`."""
    idempotency = item.idempotency_key or ""
    return idempotency.startswith(f"{key}:") and len(idempotency) > len(key) + 1 and "/" not in idempotency


def lanes_in_use(reader: BoardReader, key: str, board: str) -> set[str]:
    """The lanes the workflow's unfinished root cards are assigned to. A board whose base does not exist yet holds
    nothing: the workflow is new. Only reads."""
    try:
        items = reader.read_items(board)
    except BoardUnavailableError:
        return set()
    return {
        item.assignee for item in items if item.assignee and item.status not in FINISHED and is_root_card(item, key)
    }


def _why(error: Exception) -> str:
    if isinstance(error, ValidationError) and error.errors():
        first = error.errors()[0]
        return f"{'.'.join(str(part) for part in first['loc']) or 'racine'} : {first['msg']}"
    return str(error) or type(error).__name__


def plug(workflow: Workflow, *, reader: BoardReader | None = None) -> PluggedWorkflow:
    """The workflow plugged by the manifest of its repository, once judged; `ManifestRefusedError` with the reason
    otherwise. Without a reader, no lane is in use."""
    key = workflow.key
    repo = workflow.repo_path
    if repo is None:
        raise ManifestRefusedError(key, "aucun dépôt déclaré")
    path = repo / MANIFEST_FILE
    if not path.is_file():
        raise ManifestRefusedError(key, f"manifeste absent : {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ManifestRefusedError(key, f"manifeste illisible : {path} ({_why(error)})") from error
    lanes: set[str] = set()
    if reader is not None:
        try:
            lanes = lanes_in_use(reader, key, workflow.board)
        except Exception as error:
            raise ManifestRefusedError(key, f"voies en usage illisibles : {_why(error)}") from error

    def read_sheet(relative: str) -> object:
        return json.loads((repo / relative).read_text(encoding="utf-8"))

    verdict = judge_manifest(data, key=key, read_sheet=read_sheet, lanes_in_use=lanes)
    if not verdict.ok:
        raise ManifestRefusedError(key, f"manifeste de {key} refusé : {verdict.rule} : {verdict.detail}")
    manifest = verdict.manifest
    return PluggedWorkflow(
        key=key,
        board=workflow.board,
        repo=repo,
        stations=tuple(manifest.stations),
        issues_repo=manifest.issues_repo,
        intake=manifest.intake,
        issue_label=workflow.issue_label,
    )
