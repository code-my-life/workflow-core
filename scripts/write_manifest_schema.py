#!/usr/bin/env python3
"""Write schema/workflow-manifest.schema.json from the manifest's model (ADR 0005, ADR 0006). Run it after a change of
the form; `--out <path>` writes it elsewhere."""

from __future__ import annotations

import argparse
from pathlib import Path

from workflow_core.model.manifest import manifest_schema_text

SCHEMA = Path(__file__).resolve().parent.parent / "schema" / "workflow-manifest.schema.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=SCHEMA, help="where to write the schema")
    target = parser.parse_args(argv).out.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(manifest_schema_text(), encoding="utf-8")
    print(f"Wrote {target.relative_to(Path.cwd()) if target.is_relative_to(Path.cwd()) else target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
