#!/usr/bin/env python3
"""Refuse any mention of the agent runtime in the package outside its adapter (ADR 0001).

The runtime's name may appear only under workflow_core/adapters/hermes/. Docs, ADR, tests and profiles may name it.
Exit code 1 lists every offending line; 0 means the package is clean.
"""

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

PACKAGE = "workflow_core"
ALLOWED = Path(PACKAGE) / "adapters" / "hermes"
# Accents are stripped before matching, so both spellings of the name are caught, in any case.
NAME = re.compile("her" + "mes", re.IGNORECASE)


def plain(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def offenses(root: Path) -> list[str]:
    """Every « path:line: text » of the package that names the runtime outside its adapter."""
    found: list[str] = []
    for path in sorted((root / PACKAGE).rglob("*")):
        rel = path.relative_to(root)
        if not path.is_file() or rel.is_relative_to(ALLOWED) or "__pycache__" in rel.parts:
            continue
        if NAME.search(plain(str(rel))):
            found.append(f"{rel}: the file name names the runtime")
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        found += [f"{rel}:{n}: {line.strip()}" for n, line in enumerate(lines, 1) if NAME.search(plain(line))]
    return found


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path.cwd()
    found = offenses(root)
    if found:
        print(f"The agent runtime is named outside {ALLOWED}/ (ADR 0001):")
        print("\n".join(f"- {line}" for line in found))
        return 1
    print(f"Runtime confined to {ALLOWED}/.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
