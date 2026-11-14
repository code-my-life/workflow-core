"""Plugging a workflow at the start: a refused manifest leaves its workflow unplugged, with the broken rule in the
journal, and the other workflows run (ADR 0005, amended; ADR 0006)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from workflow_core.adapters import runtime_adapter
from workflow_core.config import DEFAULT_CONFIG, Workflow, load_workflows
from workflow_core.service import build_service

from conftest import ESSAI_REPO

SECRET = "test-secret-not-a-real-one"


def repo_of(tmp_path: Path, key: str, change=None) -> Path:
    """A copy of the trial repository whose manifest has this key, changed by `change`."""
    repo = tmp_path / key
    shutil.copytree(ESSAI_REPO, repo)
    manifest = json.loads((repo / "workflow-manifest.json").read_text(encoding="utf-8"))
    manifest["key"] = key
    if change is not None:
        change(manifest)
    (repo / "workflow-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return repo


def test_a_refused_manifest_leaves_its_workflow_unplugged(runtime_home, tmp_path, capsys):
    refused = repo_of(tmp_path, "double", lambda manifest: manifest["stations"].append(manifest["stations"][0]))
    workflows = {
        **load_workflows(DEFAULT_CONFIG),
        "double": Workflow(key="double", repo=refused),
        "bon": Workflow(key="bon", repo=repo_of(tmp_path, "bon")),
    }

    app = build_service(secret=SECRET, workflows=workflows, adapter=runtime_adapter())

    err = capsys.readouterr().err.splitlines()
    lines = [line for line in err if "manifeste de double refusé : duplicate-station : " in line]
    assert len(lines) == 1
    assert "workflow double non branché : " in lines[0]
    assert '"preparer"' in lines[0]
    assert any("workflow bon branché par son manifeste" in line for line in err)
    assert [workflow.key for workflow in app.state.engine.workflows] == ["bon"]
    assert app.state.core.mode == "normal"
