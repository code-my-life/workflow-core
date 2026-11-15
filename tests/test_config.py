"""The core's configuration: a workflow plugged by its manifest declares its key and its repository; the file comes
from WORKFLOW_CORE_CONFIG; a manifest that is absent or of another key leaves its workflow unplugged (ADR 0005,
amended)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from workflow_core.adapters import runtime_adapter
from workflow_core.config import DEFAULT_CONFIG, Workflow, config_path, load_checks, load_workflows
from workflow_core.service import build_service

from conftest import ESSAI_REPO

ROOT = Path(__file__).resolve().parent.parent
SECRET = "test-secret-not-a-real-one"


def test_a_manifest_entry_has_its_key_and_its_repository(tmp_path):
    config = tmp_path / "workflows.toml"
    config.write_text(
        '[[workflow]]\nkey = "essai"\nrepo = "~/perso/workflow-essai"\n\n'
        '[[workflow]]\nkey = "autre"\nrepo = "/srv/autre"\nboard = "autre-test"\n',
        encoding="utf-8",
    )

    workflows = load_workflows(config)

    essai = workflows["essai"]
    assert (essai.key, essai.board, essai.sheet) == ("essai", "essai", None)
    assert essai.repo_path == Path("~/perso/workflow-essai").expanduser()
    assert essai.plugged_by_manifest is True
    assert workflows["autre"].board == "autre-test"
    with pytest.raises(ValidationError):
        Workflow(key="seul")

    # The factory and the studio keep their form: a sheet, a board, served by the API.
    declared = load_workflows(DEFAULT_CONFIG)
    assert sorted(declared) == ["factory", "studio"]
    for key, workflow in declared.items():
        assert (workflow.board, workflow.plugged_by_manifest) == (key, False)
        assert workflow.sheet is not None
    assert declared["factory"].sheet == Path("~/perso/hermes-software-factory/config/workflow.json")
    assert declared["factory"].repo == Path("~/perso/hermes-software-factory")
    assert declared["factory"].manifest == "factory.yaml"
    test_machine = load_workflows(ROOT / "config" / "essai-test.toml")
    assert [(w.key, w.board, w.repo, w.sheet, w.issue_label) for w in test_machine.values()] == [
        ("essai", "essai-test", Path("~/perso/workflow-essai"), None, "test")
    ]


def test_the_configuration_path_comes_from_its_variable(tmp_path, monkeypatch):
    config = tmp_path / "other.toml"
    config.write_text(
        '[[workflow]]\nkey = "essai"\nrepo = "/srv/essai"\n\n'
        '[[check]]\nid = "web"\nlabel = "Web"\ngroup = "tools"\nkind = "tcp"\ntarget = "8080"\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("WORKFLOW_CORE_CONFIG", raising=False)
    assert config_path() == DEFAULT_CONFIG
    assert sorted(load_workflows()) == ["factory", "studio"]

    monkeypatch.setenv("WORKFLOW_CORE_CONFIG", str(config))

    assert config_path() == config
    assert list(load_workflows()) == ["essai"]
    assert [check.id for check in load_checks()] == ["web"]
    assert config_path({"WORKFLOW_CORE_CONFIG": "~/essai.toml"}) == Path("~/essai.toml").expanduser()


def test_a_missing_manifest_leaves_the_workflow_unplugged(runtime_home, tmp_path, capsys):
    other = tmp_path / "other-key"
    shutil.copytree(ESSAI_REPO, other)
    manifest = json.loads((other / "workflow-manifest.json").read_text(encoding="utf-8"))
    (other / "workflow-manifest.json").write_text(json.dumps({**manifest, "key": "autre"}), encoding="utf-8")
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "workflow-manifest.json").write_text("{pas du json", encoding="utf-8")
    workflows = {
        **load_workflows(DEFAULT_CONFIG),
        "absent": Workflow(key="absent", repo=tmp_path / "nowhere"),
        "essai": Workflow(key="essai", repo=other),
        "casse": Workflow(key="casse", repo=broken),
        "bon": Workflow(key="bon", repo=_repo_of_key(tmp_path, "bon")),
    }

    app = build_service(secret=SECRET, workflows=workflows, adapter=runtime_adapter())

    err = capsys.readouterr().err.splitlines()
    unplugged = [line for line in err if "non branché" in line]
    assert len(unplugged) == 3
    assert any("absent non branché : manifeste absent" in line for line in unplugged)
    assert any("essai non branché : manifeste de essai refusé : key-mismatch : " in line for line in unplugged)
    assert any("casse non branché : manifeste illisible" in line for line in unplugged)
    assert any("workflow bon branché par son manifeste" in line for line in err)
    assert [workflow.key for workflow in app.state.engine.workflows] == ["bon"]
    # The other workflows run: the factory is still served, and the contract holds; the plugged one is served too
    # (#60), the unplugged ones are not listed.
    assert app.state.core.mode == "normal"
    response = TestClient(app).get("/api/workflows/factory/items", headers={"Authorization": f"Bearer {SECRET}"})
    assert response.status_code == 200
    listed = TestClient(app).get("/api/workflows", headers={"Authorization": f"Bearer {SECRET}"}).json()
    assert sorted(workflow["key"] for workflow in listed["workflows"]) == ["bon", "factory", "studio"]


def _repo_of_key(tmp_path: Path, key: str) -> Path:
    repo = tmp_path / key
    shutil.copytree(ESSAI_REPO, repo)
    manifest = json.loads((repo / "workflow-manifest.json").read_text(encoding="utf-8"))
    (repo / "workflow-manifest.json").write_text(json.dumps({**manifest, "key": key}), encoding="utf-8")
    return repo
