#!/usr/bin/env python3
"""Write schema/diagnosis.schema.json from the diagnosis' model (ADR 0012). Run it after a change of the form."""

from __future__ import annotations

from pathlib import Path

from workflow_core.model.diagnosis import diagnosis_schema_text

SCHEMA = Path(__file__).resolve().parent.parent / "schema" / "diagnosis.schema.json"


def main() -> int:
    SCHEMA.parent.mkdir(exist_ok=True)
    SCHEMA.write_text(diagnosis_schema_text(), encoding="utf-8")
    print(f"Wrote {SCHEMA.relative_to(Path.cwd()) if SCHEMA.is_relative_to(Path.cwd()) else SCHEMA}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
