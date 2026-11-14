"""The JSON Schema of the manifest's form, published in schema/ for the workflows' own checks (ADR 0006)."""

import json
import subprocess
import sys
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "schema" / "workflow-manifest.schema.json"
MANIFEST = ROOT / "tests" / "fixtures" / "manifests" / "essai" / "workflow-manifest.json"


def test_the_published_schema_is_what_the_script_writes(tmp_path):
    written = tmp_path / "workflow-manifest.schema.json"

    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "write_manifest_schema.py"), "--out", str(written)],
        check=True,
        cwd=ROOT,
        capture_output=True,
    )

    published = SCHEMA.read_bytes()
    assert published == written.read_bytes()
    assert published.endswith(b"}\n")
    validator = jsonschema.Draft202012Validator(json.loads(published))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    validator.validate(manifest)
    # The published schema carries the delay's bounds: a workflow's own check refuses a delay out of range.
    for timeout in (0, 3601):
        manifest["stations"][0]["timeout_seconds"] = timeout
        assert not validator.is_valid(manifest), timeout
