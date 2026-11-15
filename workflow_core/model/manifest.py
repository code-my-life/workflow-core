"""The manifest of a workflow and its judge (ADR 0005, ADR 0006).

A workflow plugs into the core by `workflow-manifest.json` at the root of its repository: its key, its sheet, the
repositories of its issues (each one read by this workflow alone), its intake command, its stations and the gestures
it recognises. The form is a Pydantic model, published as a JSON Schema in `schema/`; the cross rules are checked
after it, in a fixed order, and the first broken one is named. The judge never raises: every input gets a verdict.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, field_validator, model_validator

from workflow_core.model.sheet import WorkflowSheet, _whole_number, judge_sheet

# The manifest versions the core accepts, first and last: a manifest outside them is refused, never guessed.
MANIFEST_VERSIONS = (1, 1)

REPO_PATTERN = r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$"

Text = Annotated[str, Field(min_length=1)]
Command = Annotated[list[Text], Field(min_length=1)]
Timeout = Annotated[int, Field(ge=1, le=3600), BeforeValidator(_whole_number)]
Version = Annotated[int, BeforeValidator(_whole_number)]
Repo = Annotated[str, Field(pattern=REPO_PATTERN)]


class _Form(BaseModel):
    # Strict: a number is not a text, a boolean is not a delay. Unknown fields are ignored (ADR 0018).
    model_config = ConfigDict(strict=True, frozen=True)


class ManifestIntake(_Form):
    """The command that says whether an issue becomes an item, run in the workflow's repository, and its delay."""

    command: Command
    timeout_seconds: Timeout


class ManifestStation(_Form):
    """A station of the manifest. A `code` or `owner` station names its command, a list of arguments run in the
    workflow's repository, and its delay in seconds; an `agent` station names its profile."""

    key: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9-]*$")
    nature: Literal["code", "owner", "agent"]
    command: Command | None = None
    timeout_seconds: Timeout | None = None
    profile: Text | None = None


class ManifestGesture(_Form):
    """A gesture the workflow recognises: `comment` on the issue of an item, or `issue` opened in `repo`."""

    # The validator below, said in the published schema too: a repo for an issue gesture, none for a comment.
    model_config = ConfigDict(
        json_schema_extra={
            "if": {"properties": {"kind": {"const": "issue"}}},
            "then": {"required": ["repo"], "properties": {"repo": {"type": "string"}}},
            "else": {"properties": {"repo": {"type": "null"}}},
        }
    )

    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9-]*$")
    kind: Literal["comment", "issue"]
    repo: Repo | None = None

    @model_validator(mode="after")
    def _repo_goes_with_issue(self) -> ManifestGesture:
        if self.kind == "issue" and self.repo is None:
            raise ValueError(f'gesture "{self.name}": an issue gesture needs its repo')
        if self.kind == "comment" and self.repo is not None:
            raise ValueError(f'gesture "{self.name}": a comment gesture has no repo')
        return self


class WorkflowManifest(_Form):
    # Not bounded here: a version outside MANIFEST_VERSIONS is the rule `version-out-of-range`.
    schema_version: Version
    key: str = Field(min_length=1, pattern=r"^[a-z0-9-]+$")
    # The path of the workflow's sheet, relative to its repository; the schema says what `_sheet_is_relative` checks.
    sheet: Annotated[
        str,
        Field(
            min_length=1,
            json_schema_extra={"not": {"anyOf": [{"pattern": "^[/~]"}, {"pattern": "(^|/)\\.\\.(/|$)"}]}},
        ),
    ]
    # The repositories where the workflow's issues are born, `<organisation>/<repository>`: no other workflow reads
    # them.
    issues_repos: list[Repo] = Field(min_length=1)
    intake: ManifestIntake
    stations: list[ManifestStation] = Field(min_length=1)
    gestures: list[ManifestGesture] = Field(default_factory=list)

    @field_validator("sheet")
    @classmethod
    def _sheet_is_relative(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or value.startswith("~") or ".." in path.parts:
            raise ValueError("the sheet's path must be relative to the repository, without '..'")
        return value


class ManifestAccepted(BaseModel):
    ok: Literal[True] = True
    manifest: WorkflowManifest
    sheet: WorkflowSheet


class ManifestRefused(BaseModel):
    ok: Literal[False] = False
    rule: str
    detail: str


ManifestVerdict = ManifestAccepted | ManifestRefused


def _form_error(error: ValidationError) -> str:
    first = error.errors(include_url=False)[0]
    path = ".".join(str(part) for part in first["loc"]) or "manifest"
    return f"{path}: {first['msg']}"


def _manifest_rule(
    manifest: WorkflowManifest, key: str, issues_repos_taken: Mapping[str, str]
) -> tuple[str, str] | None:
    """The first rule a well-formed manifest breaks on its own, before its sheet is read, or None."""
    low, high = MANIFEST_VERSIONS
    if not low <= manifest.schema_version <= high:
        return (
            "version-out-of-range",
            f"schema_version {manifest.schema_version} is outside the accepted range {low} to {high}",
        )
    if manifest.key != key:
        return "key-mismatch", f'key "{manifest.key}" is not the configured key "{key}"'
    seen: set[str] = set()
    for station in manifest.stations:
        if station.key in seen:
            return "duplicate-station", f'duplicate station "{station.key}"'
        seen.add(station.key)
    seen = set()
    for gesture in manifest.gestures:
        if gesture.name in seen:
            return "duplicate-gesture", f'duplicate gesture "{gesture.name}"'
        seen.add(gesture.name)
    for station in manifest.stations:
        if station.nature in ("code", "owner"):
            for field in ("command", "timeout_seconds"):
                if getattr(station, field) is None:
                    return "missing-command", f'{station.nature} station "{station.key}" has no {field}'
    # An owner station waits for a gesture given by a comment: without one, nothing can ever answer it.
    if not any(gesture.kind == "comment" for gesture in manifest.gestures):
        for station in manifest.stations:
            if station.nature == "owner":
                return (
                    "owner-without-gesture",
                    f'owner station "{station.key}" waits for the owner, but the manifest declares no comment gesture',
                )
    for station in manifest.stations:
        if station.nature == "agent" and station.profile is None:
            return "missing-profile", f'agent station "{station.key}" has no profile'
    # The published sheet names each agent station's profile: two stations of one profile would break its rule
    # `shared-agent`.
    profiles: dict[str, str] = {}
    for station in manifest.stations:
        if station.nature != "agent" or station.profile is None:
            continue
        taken = profiles.get(station.profile)
        if taken is not None:
            return (
                "shared-agent-profile",
                f'agent stations "{taken}" and "{station.key}" both declare the profile "{station.profile}"',
            )
        profiles[station.profile] = station.key
    for repo in manifest.issues_repos:
        owner = issues_repos_taken.get(repo)
        if owner is not None and owner != key:
            return (
                "shared-issues-repo",
                f'issues repository "{repo}" is already read by the plugged workflow "{owner}"',
            )
    # The core reads only the issues repositories of the manifest: an issue opened elsewhere by a gesture would never
    # be read.
    for gesture in manifest.gestures:
        if gesture.kind == "issue" and gesture.repo not in manifest.issues_repos:
            return (
                "gesture-repo-not-read",
                f'issue gesture "{gesture.name}" opens its issue in "{gesture.repo}", which is not one of the '
                "manifest's issues_repos",
            )
    return None


def _sheet_rule(manifest: WorkflowManifest, sheet: WorkflowSheet) -> tuple[str, str] | None:
    """The first rule between the manifest and its valid sheet that is broken, or None."""
    for station in sheet.stations:
        if station.agents is not None:
            return (
                "agents-on-sheet",
                f'sheet station "{station.key}" still names its agents: the manifest declares the stations',
            )
    on_sheet = {station.key for station in sheet.stations}
    for station in manifest.stations:
        if station.key not in on_sheet:
            return "station-not-on-sheet", f'station "{station.key}" has no station of the same key on the sheet'
    return None


def _removed_station(manifest: WorkflowManifest, lanes_in_use: Iterable[str]) -> tuple[str, str] | None:
    """A lane `<key>-<station>` still holding items whose station is no longer in the manifest, or None."""
    stations = {station.key for station in manifest.stations}
    prefix = f"{manifest.key}-"
    for lane in sorted(set(lanes_in_use)):
        if not lane.startswith(prefix) or len(lane) == len(prefix):
            continue
        station = lane[len(prefix) :]
        if station not in stations:
            return (
                "station-removed-with-items",
                f'station "{station}" is not in the manifest, but the lane "{lane}" still holds items',
            )
    return None


def judge_manifest(
    data: Any,
    *,
    key: str,
    read_sheet: Callable[[str], Any],
    lanes_in_use: Iterable[str],
    issues_repos_taken: Mapping[str, str] | None = None,
) -> ManifestVerdict:
    """Judge a manifest already read from JSON, for the configured `key`: the valid manifest and its sheet, or the
    first broken rule, named. `read_sheet` reads the sheet at a path relative to the repository; `lanes_in_use` are
    the lanes of the board's unfinished root cards; `issues_repos_taken` maps each issues repository of the workflows
    already plugged to its workflow's key. Never raises."""
    try:
        try:
            manifest = WorkflowManifest.model_validate(data)
        except ValidationError as error:
            return ManifestRefused(rule="form", detail=_form_error(error))
        broken = _manifest_rule(manifest, key, issues_repos_taken or {})
        if broken is None:
            try:
                sheet_data = read_sheet(manifest.sheet)
            except Exception as error:
                return ManifestRefused(
                    rule="sheet-unreadable", detail=f'sheet "{manifest.sheet}" cannot be read: {error}'
                )
            verdict = judge_sheet(sheet_data)
            if not verdict.ok:
                return ManifestRefused(rule=f"sheet:{verdict.rule}", detail=verdict.detail)
            broken = _sheet_rule(manifest, verdict.sheet) or _removed_station(manifest, lanes_in_use)
            if broken is None:
                return ManifestAccepted(manifest=manifest, sheet=verdict.sheet)
        rule, detail = broken
        return ManifestRefused(rule=rule, detail=detail)
    except Exception as error:  # a hostile input whose reads throw: still a verdict
        return ManifestRefused(rule="form", detail=f"manifest: {error}" if str(error) else "manifest: unreadable input")


def published_sheet(manifest: WorkflowManifest, sheet: WorkflowSheet) -> WorkflowSheet:
    """The sheet as the core serves it: each station's agents come from the manifest's station of the same key, the
    profile of an `agent` station, none for a `code` or `owner` station or a station the manifest does not declare."""
    stations = {station.key: station for station in manifest.stations}

    def agents_of(key: str) -> list[str] | None:
        station = stations.get(key)
        return [station.profile] if station is not None and station.nature == "agent" else None

    return sheet.model_copy(
        update={
            "stations": [station.model_copy(update={"agents": agents_of(station.key)}) for station in sheet.stations]
        }
    )


def manifest_schema_text() -> str:
    """The JSON Schema of the manifest's form alone, as published in schema/workflow-manifest.schema.json."""
    schema = WorkflowManifest.model_json_schema()
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"
