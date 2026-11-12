"""The full diagnosis of the 13 real incidents, field by field against factory-live's reference outputs (ADR 0012).

The expected values are the `output` of tests/fixtures/diagnosis/ (factory-live's TypeScript diagnosis at commit
f2ed60d, `diagnosisView(diagnose(input))`), never computed by the core's code.
"""

import json
from pathlib import Path

import jsonschema
from workflow_core.adapters.hermes.texts import TEXTS
from workflow_core.config import load_workflows
from workflow_core.diagnosis.diagnose import diagnose
from workflow_core.model.diagnosis import diagnosis_json

from conftest import diagnosis_input_of, reference_cases

ROOT = Path(__file__).resolve().parent.parent
FACTORY = load_workflows(ROOT / "config" / "workflows.toml")["factory"]
SCHEMA = json.loads((ROOT / "schema" / "diagnosis.schema.json").read_text(encoding="utf-8"))


def first_difference(ours, theirs, path: str = "") -> str | None:
    """The path of the first field where two JSON values differ, `None` when they are equal."""
    if isinstance(ours, dict) and isinstance(theirs, dict):
        for key in sorted(set(ours) | set(theirs)):
            if key not in ours or key not in theirs:
                return f"{path}.{key}"
            found = first_difference(ours[key], theirs[key], f"{path}.{key}")
            if found:
                return found
        return None
    if isinstance(ours, list) and isinstance(theirs, list):
        if len(ours) != len(theirs):
            return f"{path} (longueur {len(ours)} au lieu de {len(theirs)})"
        for index, (left, right) in enumerate(zip(ours, theirs, strict=True)):
            found = first_difference(left, right, f"{path}[{index}]")
            if found:
                return found
        return None
    return None if ours == theirs else f"{path}: {ours!r} au lieu de {theirs!r}"


def computed(case: dict) -> dict:
    return diagnosis_json(diagnose(diagnosis_input_of(case["input"]), TEXTS, FACTORY))


def test_the_13_diagnoses_match_the_reference_outputs_field_by_field():
    cases = reference_cases()
    assert len(cases) == 13
    differences = {case["case"]: first_difference(computed(case), case["output"]) for case in cases}

    assert {name: path for name, path in differences.items() if path} == {}


def test_the_published_schema_validates_the_13_outputs():
    validator = jsonschema.Draft202012Validator(SCHEMA)
    for case in reference_cases():
        validator.validate(case["output"])
        validator.validate(computed(case))
