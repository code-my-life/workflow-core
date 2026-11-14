"""The judge of a workflow manifest: the form, then the cross rules, the first broken one named (ADR 0005, ADR 0006)."""

import copy
import json
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest
from workflow_core.adapters import runtime_adapter
from workflow_core.config import Workflow
from workflow_core.model.manifest import judge_manifest
from workflow_core.model.sheet import judge_sheet
from workflow_core.plugin.manifest import ManifestRefusedError, plug

from conftest import ESSAI_REPO, make_runtime_board

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REFERENCE = FIXTURES / "manifests" / "essai"


def load(name: str) -> dict:
    return json.loads((REFERENCE / name).read_text(encoding="utf-8"))


def read_reference(path: str) -> dict:
    return json.loads((REFERENCE / path).read_text(encoding="utf-8"))


def judge(manifest: dict, sheet: dict | None = None, lanes=(), key: str = "essai"):
    """Judge a manifest whose sheet, at the path it names, is `sheet` (the reference sheet by default)."""
    sheet = load("workflow-sheet.json") if sheet is None else sheet

    def read_sheet(path: str) -> dict:
        if path != "workflow-sheet.json":
            raise FileNotFoundError(f"no file {path}")
        return copy.deepcopy(sheet)

    return judge_manifest(manifest, key=key, read_sheet=read_sheet, lanes_in_use=lanes)


def sheet_station(key: str, col: int, kind: str = "script", **extra) -> dict:
    return {"key": key, "label": key, "icon": "dot", "kind": kind, "col": col, "row": 0, **extra}


def test_the_essai_manifest_is_accepted():
    data = load("workflow-manifest.json")

    verdict = judge_manifest(data, key="essai", read_sheet=read_reference, lanes_in_use=[])

    assert verdict.ok, verdict
    assert [station.key for station in verdict.manifest.stations] == ["preparer", "revue", "clore"]
    assert [station.key for station in verdict.sheet.stations] == ["preparer", "revue", "clore"]
    assert verdict.manifest.issues_repos == ["code-my-life/workflow-essai"]
    # The trial repository the engine's tests use carries the same two files, byte for byte.
    for name in ("workflow-manifest.json", "workflow-sheet.json"):
        assert (ESSAI_REPO / name).read_bytes() == (REFERENCE / name).read_bytes()


# Each case breaks one rule of the valid reference, and only that one: it changes the manifest, the sheet, or the
# lanes in use. The detail names the culprit: the station, the gesture or the field.
def _version_out_of_range(manifest: dict, sheet: dict) -> list[str]:
    manifest["schema_version"] = 2
    return []


def _key_mismatch(manifest: dict, sheet: dict) -> list[str]:
    manifest["key"] = "autre"
    return []


def _duplicate_station(manifest: dict, sheet: dict) -> list[str]:
    manifest["stations"].append(copy.deepcopy(manifest["stations"][0]))
    return []


def _duplicate_gesture(manifest: dict, sheet: dict) -> list[str]:
    manifest["gestures"].append({"name": "valider", "kind": "comment"})
    return []


def _missing_command(manifest: dict, sheet: dict) -> list[str]:
    del manifest["stations"][1]["command"]
    return []


def _missing_profile(manifest: dict, sheet: dict) -> list[str]:
    manifest["stations"].append({"key": "relire", "nature": "agent"})
    sheet["stations"].append(sheet_station("relire", 3, kind="ai"))
    return []


def _shared_agent_profile(manifest: dict, sheet: dict) -> list[str]:
    for col, key in ((3, "relire"), (4, "corriger")):
        manifest["stations"].append({"key": key, "nature": "agent", "profile": "relecteur"})
        sheet["stations"].append(sheet_station(key, col, kind="ai"))
    return []


def _sheet_unreadable(manifest: dict, sheet: dict) -> list[str]:
    manifest["sheet"] = "absente.json"
    return []


def _sheet_rule(manifest: dict, sheet: dict) -> list[str]:
    sheet["stations"].append(sheet_station("preparer", 3))
    return []


def _agents_on_sheet(manifest: dict, sheet: dict) -> list[str]:
    sheet["stations"][1]["agents"] = ["relecteur"]
    return []


def _station_not_on_sheet(manifest: dict, sheet: dict) -> list[str]:
    sheet["stations"].pop()
    sheet["edges"].pop()
    return []


def _station_removed_with_items(manifest: dict, sheet: dict) -> list[str]:
    return ["essai-preparer", "essai-ancienne", "autre-ancienne"]


BROKEN: dict[str, tuple[Callable[[dict, dict], list[str]], tuple[str, ...]]] = {
    "version-out-of-range": (_version_out_of_range, ("schema_version", "2")),
    "key-mismatch": (_key_mismatch, ('"autre"', '"essai"')),
    "duplicate-station": (_duplicate_station, ('"preparer"',)),
    "duplicate-gesture": (_duplicate_gesture, ('"valider"',)),
    "missing-command": (_missing_command, ('"revue"', "command")),
    "missing-profile": (_missing_profile, ('"relire"', "profile")),
    "shared-agent-profile": (_shared_agent_profile, ('"relire"', '"corriger"', '"relecteur"')),
    "sheet-unreadable": (_sheet_unreadable, ('"absente.json"',)),
    "sheet:duplicate-station": (_sheet_rule, ('"preparer"',)),
    "agents-on-sheet": (_agents_on_sheet, ('"revue"',)),
    "station-not-on-sheet": (_station_not_on_sheet, ('"clore"',)),
    "station-removed-with-items": (_station_removed_with_items, ('"ancienne"', '"essai-ancienne"')),
}


@pytest.mark.parametrize("rule", list(BROKEN), ids=list(BROKEN))
def test_each_broken_rule_is_named(rule):
    manifest, sheet = load("workflow-manifest.json"), load("workflow-sheet.json")
    breaks, culprits = BROKEN[rule]
    lanes = breaks(manifest, sheet)

    verdict = judge(manifest, sheet, lanes)

    assert not verdict.ok
    assert verdict.rule == rule
    assert [culprit for culprit in culprits if culprit not in verdict.detail] == []


def test_an_issues_repo_of_another_plugged_workflow_is_refused():
    manifest = load("workflow-manifest.json")
    manifest["issues_repos"] = ["code-my-life/essai-a", "code-my-life/workflow-essai"]
    taken = {"code-my-life/workflow-essai": "studio", "code-my-life/autre": "factory"}

    verdict = judge_manifest(
        manifest, key="essai", read_sheet=read_reference, lanes_in_use=[], issues_repos_taken=taken
    )

    assert (verdict.ok, verdict.rule) == (False, "shared-issues-repo")
    assert '"code-my-life/workflow-essai"' in verdict.detail and '"studio"' in verdict.detail
    # A repository no other workflow reads is its own.
    del taken["code-my-life/workflow-essai"]
    accepted = judge_manifest(
        manifest, key="essai", read_sheet=read_reference, lanes_in_use=[], issues_repos_taken=taken
    )
    assert accepted.ok, accepted


def test_a_refused_sheet_names_the_sheet_rule():
    sheet = load("workflow-sheet.json")
    sheet["stations"][1]["col"] = 0

    verdict = judge(load("workflow-manifest.json"), sheet)

    assert (verdict.ok, verdict.rule) == (False, "sheet:shared-cell")
    assert verdict.detail == judge_sheet(sheet).detail
    assert '"preparer"' in verdict.detail and '"revue"' in verdict.detail


def test_unknown_fields_are_ignored():
    plain = judge(load("workflow-manifest.json"))
    manifest = load("workflow-manifest.json")
    manifest["owner"] = "quelqu'un"
    manifest["stations"][0]["colour"] = "red"
    manifest["intake"]["retries"] = 3
    manifest["gestures"][0]["emoji"] = "+1"

    verdict = judge(manifest)

    assert verdict.ok and plain.ok
    assert verdict.manifest == plain.manifest
    assert verdict.manifest.model_dump() == plain.manifest.model_dump()

    # A refused manifest is refused the same way with an unknown field.
    del manifest["stations"][1]["command"]
    refused = judge(manifest)
    assert (refused.ok, refused.rule) == (False, "missing-command")


class Hostile(Mapping):
    """A mapping whose every read throws."""

    def __getitem__(self, key):
        raise RuntimeError("no item here")

    def __iter__(self):
        raise RuntimeError("no item here")

    def __len__(self):
        return 1


class HostileLanes:
    """Lanes whose every read throws."""

    def __iter__(self):
        raise RuntimeError("no lane here")


@pytest.mark.parametrize(
    "data",
    [
        None,
        "{",
        42,
        [],
        {},
        Hostile(),
        {**load("workflow-manifest.json"), "stations": "all"},
        {**load("workflow-manifest.json"), "stations": [None]},
        {**load("workflow-manifest.json"), "schema_version": True},
        {**load("workflow-manifest.json"), "sheet": "/etc/workflow-sheet.json"},
        {**load("workflow-manifest.json"), "sheet": "../workflow-sheet.json"},
        {**load("workflow-manifest.json"), "intake": {"command": [], "timeout_seconds": 60}},
        {**load("workflow-manifest.json"), "issues_repos": []},
        {**load("workflow-manifest.json"), "issues_repos": "code-my-life/workflow-essai"},
        {**load("workflow-manifest.json"), "issues_repos": ["workflow-essai"]},
        {
            **{name: value for name, value in load("workflow-manifest.json").items() if name != "issues_repos"},
            "issues_repo": "code-my-life/workflow-essai",
        },
        {**load("workflow-manifest.json"), "gestures": [{"name": "valider", "kind": "comment", "repo": "a/b"}]},
        {**load("workflow-manifest.json"), "gestures": [{"name": "ouvrir", "kind": "issue"}]},
        *(
            {
                **load("workflow-manifest.json"),
                "stations": [{**load("workflow-manifest.json")["stations"][0], "timeout_seconds": timeout}],
            }
            for timeout in (0, 3601, 1.5)
        ),
    ],
    ids=[
        "null",
        "text",
        "number",
        "list",
        "empty",
        "throwing",
        "text-stations",
        "null-station",
        "bool-version",
        "absolute-sheet",
        "parent-sheet",
        "empty-intake-command",
        "no-issues-repo",
        "issues-repos-not-a-list",
        "issues-repo-without-owner",
        "single-issues-repo",
        "comment-with-repo",
        "issue-without-repo",
        "zero-timeout",
        "timeout-over-an-hour",
        "fractional-timeout",
    ],
)
def test_a_hostile_input_still_gets_a_verdict(data):
    verdict = judge(data)
    assert not verdict.ok
    assert verdict.rule == "form"
    assert verdict.detail


def test_a_throwing_sheet_reader_or_lanes_still_get_a_verdict():
    manifest = load("workflow-manifest.json")

    def read_sheet(path: str):
        raise RuntimeError("disk gone")

    unreadable = judge_manifest(manifest, key="essai", read_sheet=read_sheet, lanes_in_use=[])
    assert (unreadable.ok, unreadable.rule) == (False, "sheet-unreadable")
    assert "disk gone" in unreadable.detail

    hostile = judge_manifest(manifest, key="essai", read_sheet=read_reference, lanes_in_use=HostileLanes())
    assert (hostile.ok, hostile.rule) == (False, "form")
    assert hostile.detail


def root_card(status: str) -> dict:
    return {
        "id": "t_09090909",
        "title": "essai:9 · Ancien essai",
        "assignee": "essai-ancienne",
        "status": status,
        "created_at": 1762900000,
        "idempotency_key": "essai:9",
    }


def test_removing_a_station_that_holds_items_is_refused(fake_runtime):
    reader = runtime_adapter().make_board_reader()
    make_runtime_board(fake_runtime.home, "essai-ouvert", tasks=[root_card("ready")])
    make_runtime_board(fake_runtime.home, "essai-fini", tasks=[root_card("done")])

    with pytest.raises(ManifestRefusedError) as refused:
        plug(Workflow(key="essai", board="essai-ouvert", repo=ESSAI_REPO), reader=reader)
    assert "manifeste de essai refusé : station-removed-with-items : " in refused.value.reason
    assert '"ancienne"' in refused.value.reason

    # The same card, finished, holds nothing: the workflow plugs.
    plugged = plug(Workflow(key="essai", board="essai-fini", repo=ESSAI_REPO), reader=reader)
    assert [station.key for station in plugged.stations] == ["preparer", "revue", "clore"]
    # A board not created yet holds nothing either.
    assert plug(Workflow(key="essai", board="essai-neuf", repo=ESSAI_REPO), reader=reader).key == "essai"
    assert fake_runtime.writes() == []


def test_a_sheet_that_still_names_agents_is_refused():
    sheet = load("workflow-sheet.json")
    sheet["stations"][2]["agents"] = ["cloreur"]
    # The sheet alone is valid: its own judge accepts it.
    assert judge_sheet(sheet).ok

    verdict = judge(load("workflow-manifest.json"), sheet)

    assert (verdict.ok, verdict.rule) == (False, "agents-on-sheet")
    assert '"clore"' in verdict.detail

    del sheet["stations"][2]["agents"]
    assert judge(load("workflow-manifest.json"), sheet).ok
