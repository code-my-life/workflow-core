"""The only files of Hermès and of a workflow's repository the diagnosis reads: paths built from checked names,
never received from a client, without a symbolic link on the way (ADR 0012).

Ported from factory-live at commit d47f235 (apps/server/src/adapters/hermes/paths.ts, `real`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from workflow_core.text import js_regex

# A safe name of an agent, a plugin or a board: letters, digits, `_` and `-` (ASCII).
NAME = js_regex(r"^[\w-]+$")
# A card's id.
CARD = js_regex(r"^t_[0-9a-f]{8}$")


def safe_entry(root: Path, parts: list[str], kind: Literal["file", "dir"]) -> Path | None:
    """A file or folder under its root, without a symbolic link on the way: its real path must be the path built. A
    link laid in a profile thus makes nothing else readable."""
    path = root.joinpath(*parts)
    try:
        if not path.is_relative_to(root) or path == root or path.is_symlink():
            return None
        if not (path.is_file() if kind == "file" else path.is_dir()):
            return None
        return path if path.resolve() == root.resolve().joinpath(*parts) else None
    except OSError:
        return None


def safe_file(root: Path, *parts: str) -> Path | None:
    """A file under its root, without a symbolic link on the way."""
    return safe_entry(root, list(parts), "file")
