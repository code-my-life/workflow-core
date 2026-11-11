#!/usr/bin/env python3
"""Write schema/workflow-sheet.schema.json from the sheet's model (ADR 0006). Run it after a change of the form."""

from __future__ import annotations

from pathlib import Path

from workflow_core.model import sheet_schema_text

SCHEMA = Path(__file__).resolve().parent.parent / "schema" / "workflow-sheet.schema.json"


def main() -> int:
    SCHEMA.parent.mkdir(exist_ok=True)
    SCHEMA.write_text(sheet_schema_text(), encoding="utf-8")
    print(f"Wrote {SCHEMA.relative_to(Path.cwd()) if SCHEMA.is_relative_to(Path.cwd()) else SCHEMA}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
