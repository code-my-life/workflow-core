#!/usr/bin/env python3
"""Write schema/diagnosis.schema.json and schema/run-verdicts.schema.json from the diagnosis' model (ADR 0012).
Run it after a change of the form."""

from __future__ import annotations

from pathlib import Path

from workflow_core.model.diagnosis import diagnosis_schema_text, run_verdicts_schema_text

SCHEMA = Path(__file__).resolve().parent.parent / "schema" / "diagnosis.schema.json"
VERDICTS = SCHEMA.parent / "run-verdicts.schema.json"


def main() -> int:
    SCHEMA.parent.mkdir(exist_ok=True)
    for path, text in ((SCHEMA, diagnosis_schema_text()), (VERDICTS, run_verdicts_schema_text())):
        path.write_text(text, encoding="utf-8")
        print(f"Wrote {path.relative_to(Path.cwd()) if path.is_relative_to(Path.cwd()) else path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
