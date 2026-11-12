"""The proofs of a card: the files an agent leaves in `evidence/` of its work folder, read-only (ADR 0012).

Ported from factory-live at commit d47f235: `CardFiles.evidence` and `evidenceFiles`
(apps/server/src/adapters/hermes/files.ts), and the safe paths `evidenceDir` and `evidenceEntry`
(apps/server/src/adapters/hermes/paths.ts).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from workflow_core.adapters.hermes.agent_log import safe_file
from workflow_core.model.facts import EvidenceFile
from workflow_core.text import js_regex

EVIDENCE_MAX_FILES = 50
EVIDENCE_MAX_DEPTH = 3
_NAME = js_regex(r"^[\w-]+$")
_CARD = js_regex(r"^t_[0-9a-f]{8}$")


def safe_entry(root: Path, parts: list[str], kind: Literal["file", "dir"]) -> Path | None:
    """A file or folder under its root, without a symbolic link on the way."""
    if kind == "file":
        return safe_file(root, *parts)
    path = root.joinpath(*parts)
    try:
        if not path.is_relative_to(root) or path == root or path.is_symlink() or not path.is_dir():
            return None
        return path if path.resolve() == root.resolve().joinpath(*parts) else None
    except OSError:
        return None


def evidence_dir(home: Path, board: str, card: str) -> Path | None:
    """`evidence/` of the card's work folder, else the copy the board keeps of it, which outlives the work folder."""
    if not _NAME.fullmatch(board) or not _CARD.fullmatch(card):
        return None
    root = home / "kanban" / "boards" / board
    return safe_entry(root, ["workspaces", card, "evidence"], "dir") or safe_entry(root, ["evidence", card], "dir")


def evidence_entry(folder: Path, parts: list[str], kind: Literal["file", "dir"]) -> Path | None:
    """A file under an evidence folder, without a link and without leaving the folder."""
    return safe_entry(folder, parts, kind) if all(part not in ("", ".", "..") for part in parts) else None


def _js_sorted(names: list[str]) -> list[str]:
    # JavaScript's default sort: by UTF-16 code units.
    return sorted(names, key=lambda name: name.encode("utf-16-be", "surrogatepass"))


def evidence_files(folder: Path) -> list[EvidenceFile]:
    """The files of an evidence folder, subfolders included, sorted by name: no hidden file (the factory's markers),
    no symbolic link, no more than 50 files."""
    files: list[EvidenceFile] = []

    def visit(parts: list[str]) -> None:
        try:
            names = _js_sorted([entry.name for entry in folder.joinpath(*parts).iterdir()])
        except OSError:
            return
        for name in names:
            if len(files) >= EVIDENCE_MAX_FILES:
                return
            if name.startswith("."):
                continue
            entry = [*parts, name]
            file = evidence_entry(folder, entry, "file")
            if file is not None:
                try:
                    stat = file.stat()
                except OSError:
                    continue  # deleted between the listing and the reading: no longer a proof
                at = (stat.st_mtime_ns // 1_000_000_000 * 1000 + (stat.st_mtime_ns % 1_000_000_000) / 1e6) / 1000
                files.append(EvidenceFile(name="/".join(entry), bytes=stat.st_size, at=at))
            elif len(entry) < EVIDENCE_MAX_DEPTH and evidence_entry(folder, entry, "dir") is not None:
                visit(entry)

    visit([])
    return files


class EvidenceReader:
    """Lists the proofs of a card of a named board, under Hermès' home."""

    def __init__(self, home: Path) -> None:
        self.home = home

    def evidence(self, board: str, card: str) -> list[EvidenceFile] | None:
        """The proofs; `None` when the card has no evidence folder."""
        folder = evidence_dir(self.home, board, card)
        return evidence_files(folder) if folder is not None else None


def make_evidence_reader(environ: dict[str, str] | None = None) -> EvidenceReader:
    """The proofs reader of the Hermès home named by `HERMES_HOME`."""
    from workflow_core.adapters.hermes.board import hermes_home

    return EvidenceReader(hermes_home(environ))
