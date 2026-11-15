"""The decisions of docs/decisions/: the seven amended by the architecture of 2026-11-12 say so (ADR 0014)."""

from __future__ import annotations

from pathlib import Path

DECISIONS = Path(__file__).resolve().parent.parent / "docs" / "decisions"
AMENDED = ("0001", "0004", "0005", "0007", "0013", "0016", "0019")


def test_the_seven_amended_decisions_say_so_in_their_status():
    for path in sorted(DECISIONS.glob("0*.md")):
        text = path.read_text(encoding="utf-8")
        status = next(line for line in text.splitlines() if "Statut :" in line)
        if path.name[:4] in AMENDED:
            assert "Statut : accepté, amendé le 2026-11-12." in status, path.name
            assert text.count("\n## Amendement du 2026-11-12\n") == 1, path.name
            # The amendment comes last; the original text stays before it.
            assert text.index("## Conséquences") < text.index("## Amendement du 2026-11-12"), path.name
        else:
            assert "amendé" not in status, path.name
    # The engine never takes a card any more.
    engine = next(DECISIONS.glob("0004-*.md")).read_text(encoding="utf-8")
    assert "ne prend jamais une carte (`claim`)" in engine.split("## Amendement du 2026-11-12")[1]
