"""The sheet of a workflow and its judge (ADR 0006).

A sheet says what flows through a workflow (`item`), its stations laid on a grid, their groups, the links between
them and the stations the model providers feed. The form is a Pydantic model, published as a JSON Schema in
`schema/`; the cross rules are checked after it, in the order of factory-live's rules at commit d47f235, and the
first broken one is named. The judge never raises: every input gets a verdict.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

# The reserved key of the model providers' box.
MODELS_KEY = "models"

StationKind = Literal["ai", "script", "github", "owner", "end"]
EdgeKind = Literal["line", "return", "branch", "fan"]


def _whole_number(value: Any) -> Any:
    # JSON has one number type: 2.0 is a whole number, as the original rules accept it. A boolean is not.
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


Key = Annotated[str, Field(min_length=1)]
Cell = Annotated[int, BeforeValidator(_whole_number), Field(ge=0)]


class _Form(BaseModel):
    # Strict: a number is not a text, a boolean is not a cell. Unknown fields are ignored, as by the original rules.
    model_config = ConfigDict(strict=True, frozen=True)


class SheetItem(_Form):
    type: Key
    label: str


class SheetGroup(_Form):
    key: Key
    label: str
    icon: str


class SheetStation(_Form):
    key: Key
    label: str
    # The name of an Octicon (`issue-opened`, `code`...).
    icon: str
    kind: StationKind
    col: Cell
    row: Cell
    group: Key | None = None
    # The item types the station handles, written under its name.
    types: list[str] | None = None


class SheetEdge(_Form):
    model_config = ConfigDict(populate_by_name=True)

    from_: Key = Field(alias="from")
    to: Key
    kind: EdgeKind
    # The stable id of the link; without it, it follows from `from`, `to` and `kind`.
    id: Key | None = None
    # The fallback label of the link's legend entry.
    label: str | None = None


class WorkflowSheet(_Form):
    item: SheetItem
    groups: list[SheetGroup] | None = None
    stations: list[SheetStation]
    edges: list[SheetEdge]
    models: list[Key]


class SheetAccepted(BaseModel):
    ok: Literal[True] = True
    sheet: WorkflowSheet


class SheetRefused(BaseModel):
    ok: Literal[False] = False
    rule: str
    detail: str


SheetVerdict = SheetAccepted | SheetRefused


def _broken_rule(sheet: WorkflowSheet) -> tuple[str, str] | None:
    """The first cross rule a well-formed sheet breaks, with its detail, or None."""
    groups: dict[str, SheetGroup] = {}
    for group in sheet.groups or []:
        if group.key == MODELS_KEY:
            return "reserved-models-key", f'group key "{MODELS_KEY}" is reserved for the model providers'
        if group.key in groups:
            return "duplicate-group", f'duplicate group "{group.key}"'
        groups[group.key] = group

    stations: dict[str, SheetStation] = {}
    cells: dict[tuple[int, int], str] = {}
    for station in sheet.stations:
        if station.key == MODELS_KEY:
            return "reserved-models-key", f'station key "{MODELS_KEY}" is reserved for the model providers'
        if station.key in stations:
            return "duplicate-station", f'duplicate station "{station.key}"'
        if station.key in groups:
            return "station-is-group", f'station "{station.key}" has the key of a group'
        stations[station.key] = station
        taken = cells.get((station.col, station.row))
        if taken is not None:
            return (
                "shared-cell",
                f'stations "{taken}" and "{station.key}" share the cell col {station.col}, row {station.row}',
            )
        cells[(station.col, station.row)] = station.key
        if station.group is not None and station.group not in groups:
            return "unknown-group", f'station "{station.key}" names an unknown group "{station.group}"'

    for key in groups:
        kinds = {station.kind for station in sheet.stations if station.group == key}
        if not kinds:
            return "empty-group", f'group "{key}" has no station'
        if len(kinds) > 1:
            return "mixed-group", f'the stations of group "{key}" are not of one kind'

    ids: set[str] = set()
    for edge in sheet.edges:
        if edge.id is not None:
            if edge.id in ids:
                return "duplicate-edge-id", f'duplicate edge id "{edge.id}"'
            if edge.id.startswith(f"{MODELS_KEY}:"):
                return "reserved-models-key", f'edge id "{edge.id}" is reserved for the model providers\' feeds'
            ids.add(edge.id)
        name = edge.id or f"{edge.kind} {edge.from_} -> {edge.to}"
        for end, verb in ((edge.from_, "leaves"), (edge.to, "reaches")):
            if end not in stations and end not in groups:
                return "unknown-edge-end", f'edge "{name}" {verb} an unknown station "{end}"'

    for key in sheet.models:
        if key not in stations:
            return "unknown-model-station", f'models names an unknown station "{key}"'
    return None


def _form_error(error: ValidationError) -> str:
    first = error.errors(include_url=False)[0]
    path = ".".join(str(part) for part in first["loc"]) or "sheet"
    return f"{path}: {first['msg']}"


def judge_sheet(data: Any) -> SheetVerdict:
    """Judge a sheet already read from JSON: the valid sheet, or the first broken rule, named. Never raises."""
    try:
        sheet = WorkflowSheet.model_validate(data)
    except ValidationError as error:
        return SheetRefused(rule="form", detail=_form_error(error))
    except Exception as error:  # a hostile input whose reads throw: still a verdict
        return SheetRefused(rule="form", detail=f"sheet: {error}" if str(error) else "sheet: unreadable input")
    broken = _broken_rule(sheet)
    if broken is not None:
        rule, detail = broken
        return SheetRefused(rule=rule, detail=detail)
    return SheetAccepted(sheet=sheet)


def sheet_schema_text() -> str:
    """The JSON Schema of the sheet's form alone, as published in schema/workflow-sheet.schema.json."""
    schema = WorkflowSheet.model_json_schema(by_alias=True)
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"
