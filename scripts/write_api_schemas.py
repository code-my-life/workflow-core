#!/usr/bin/env python3
"""Write the JSON Schema of each response of the API in schema/ from its model (ADR 0011). Run it after a change of
a response's form."""

from __future__ import annotations

from pathlib import Path

from workflow_core.model.api import API_SCHEMAS

FOLDER = Path(__file__).resolve().parent.parent / "schema"


def main() -> int:
    FOLDER.mkdir(exist_ok=True)
    for name, text in API_SCHEMAS.items():
        path = FOLDER / name
        path.write_text(text(), encoding="utf-8")
        print(f"Wrote {path.relative_to(Path.cwd()) if path.is_relative_to(Path.cwd()) else path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
