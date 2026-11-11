"""The judge of a workflow sheet: the form, then the cross rules, the first broken one named (ADR 0006)."""

import copy
import json
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest
from workflow_core.model import judge_sheet

SHEETS = Path(__file__).resolve().parent / "fixtures" / "sheets"


def load(name: str) -> dict:
    return json.loads((SHEETS / f"{name}.json").read_text(encoding="utf-8"))


def station(key: str, col: int, row: int, kind: str = "script", **extra) -> dict:
    return {"key": key, "label": key, "icon": "dot", "kind": kind, "col": col, "row": row, **extra}


def test_the_current_factory_and_studio_sheets_pass():
    for name in ("factory", "studio"):
        data = load(name)
        verdict = judge_sheet(data)
        assert verdict.ok, f"{name}: {verdict}"
        # The accepted sheet is the input itself: nothing added, nothing lost.
        assert verdict.sheet.model_dump(by_alias=True, exclude_none=True) == data


def _models_group(sheet: dict) -> None:
    sheet["groups"].append({"key": "models", "label": "Modèles", "icon": "cpu"})


def _duplicate_group(sheet: dict) -> None:
    sheet["groups"].append({"key": "gate", "label": "Gate bis", "icon": "shield"})


def _duplicate_station(sheet: dict) -> None:
    sheet["stations"].append(station("merge", 9, 9, kind="github"))


def _station_is_group(sheet: dict) -> None:
    sheet["stations"].append(station("gate", 9, 9))


def _shared_cell(sheet: dict) -> None:
    sheet["stations"].append(station("release", 3, 2))


def _unknown_group(sheet: dict) -> None:
    sheet["stations"].append(station("extra", 9, 9, group="proofs"))


def _empty_group(sheet: dict) -> None:
    sheet["groups"].append({"key": "spare", "label": "Spare", "icon": "dot"})


def _mixed_group(sheet: dict) -> None:
    sheet["stations"].append(station("gate:extra", 9, 9, kind="ai", group="gate"))


def _duplicate_edge_id(sheet: dict) -> None:
    sheet["edges"].append({"from": "brief", "to": "implementation", "kind": "return", "id": "loop:gate"})


def _unknown_edge_end(sheet: dict) -> None:
    sheet["edges"].append({"from": "ci", "to": "release", "kind": "line", "id": "line:ci2"})


def _unknown_model_station(sheet: dict) -> None:
    sheet["models"].append("triage")


# Each case breaks one cross rule of a valid sheet, and only that one; the detail names the culprit.
BROKEN: dict[str, tuple[Callable[[dict], None], str]] = {
    "reserved-models-key": (_models_group, '"models"'),
    "duplicate-group": (_duplicate_group, '"gate"'),
    "duplicate-station": (_duplicate_station, '"merge"'),
    "station-is-group": (_station_is_group, '"gate"'),
    "shared-cell": (_shared_cell, "col 3, row 2"),
    "unknown-group": (_unknown_group, '"proofs"'),
    "empty-group": (_empty_group, '"spare"'),
    "mixed-group": (_mixed_group, '"gate"'),
    "duplicate-edge-id": (_duplicate_edge_id, '"loop:gate"'),
    "unknown-edge-end": (_unknown_edge_end, '"release"'),
    "unknown-model-station": (_unknown_model_station, '"triage"'),
}


@pytest.mark.parametrize("rule", list(BROKEN), ids=list(BROKEN))
def test_each_broken_rule_is_refused_with_its_name(rule):
    sheet = copy.deepcopy(load("factory"))
    breaks, culprit = BROKEN[rule]
    breaks(sheet)
    verdict = judge_sheet(sheet)
    assert not verdict.ok
    assert verdict.rule == rule
    assert culprit in verdict.detail


@pytest.mark.parametrize(
    ("station_key", "edge_id"),
    [("models", None), (None, "models:brief")],
    ids=["station-key", "edge-id"],
)
def test_the_models_key_is_reserved_for_stations_and_edge_ids(station_key, edge_id):
    sheet = copy.deepcopy(load("factory"))
    if station_key:
        sheet["stations"].append(station(station_key, 9, 9))
    if edge_id:
        sheet["edges"].append({"from": "brief", "to": "implementation", "kind": "line", "id": edge_id})
    verdict = judge_sheet(sheet)
    assert (verdict.ok, verdict.rule) == (False, "reserved-models-key")


@pytest.mark.parametrize(
    ("change", "path"),
    [
        (lambda s: s["stations"][3].update(kind="robot"), "stations.3.kind"),
        (lambda s: s["stations"][3].update(col=-1), "stations.3.col"),
        (lambda s: s["edges"][2].pop("from"), "edges.2.from"),
        (lambda s: s["item"].pop("type"), "item.type"),
    ],
    ids=["unknown-kind", "negative-col", "missing-edge-from", "missing-item-type"],
)
def test_an_invalid_form_names_the_field_path(change, path):
    sheet = copy.deepcopy(load("factory"))
    change(sheet)
    verdict = judge_sheet(sheet)
    assert (verdict.ok, verdict.rule) == (False, "form")
    assert verdict.detail.startswith(f"{path}: ")


class Hostile(Mapping):
    """A mapping whose every read throws, like the getter of factory-live's own hostile case."""

    def __getitem__(self, key):
        raise RuntimeError("no item here")

    def __iter__(self):
        raise RuntimeError("no item here")

    def __len__(self):
        return 1


@pytest.mark.parametrize(
    "data",
    [
        None,
        "{",
        42,
        [],
        {"item": None, "stations": [], "edges": [], "models": []},
        {"item": {"type": "issue", "label": "Issue"}, "stations": "all", "edges": [], "models": []},
        {"item": {"type": ["issue"], "label": "Issue"}, "stations": [], "edges": [], "models": []},
        {"item": {"type": "issue", "label": "Issue"}, "stations": [None], "edges": [{}], "models": [None]},
        {"item": {"type": "issue", "label": "Issue"}, "stations": [{"col": True}], "edges": [], "models": []},
        Hostile(),
    ],
    ids=[
        "null",
        "text",
        "number",
        "list",
        "null-item",
        "text-stations",
        "list-type",
        "null-entries",
        "bool-col",
        "throwing",
    ],
)
def test_a_hostile_input_gets_a_verdict_not_an_exception(data):
    verdict = judge_sheet(data)
    assert not verdict.ok
    assert verdict.rule == "form"
    assert verdict.detail
