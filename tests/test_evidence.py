"""The proofs an agent leaves in `evidence/` of a card's work folder, listed by the runtime's adapter (ADR 0012).

The cases are those of factory-live's `files.test.ts` (`CardFiles.evidence`) at commit d47f235.
"""

from pathlib import Path

import pytest
from workflow_core.adapters.hermes.evidence import EvidenceReader


@pytest.fixture
def board(tmp_path) -> Path:
    board = tmp_path / "runtime" / "kanban" / "boards" / "factory"
    board.mkdir(parents=True)
    return board


@pytest.fixture
def reader(tmp_path) -> EvidenceReader:
    return EvidenceReader(tmp_path / "runtime")


def test_the_worktree_proofs_are_listed_with_subfolders_without_hidden_files_or_links(board, reader, tmp_path):
    evidence = board / "workspaces" / "t_665ba7d7" / "evidence"
    (evidence / "gate" / "1").mkdir(parents=True)
    (evidence / "02-test-passes-on-tip.txt").write_text("ok")
    (evidence / "01-test-fails-on-base.txt").write_text("FAIL")
    (evidence / ".reviewer").write_text("claude")
    (evidence / "gate" / "1" / "01-fail-first-on-base.txt").write_text("x" * 42)
    (tmp_path / "secret.env").write_text("TOKEN=x")
    (evidence / "03-lien.txt").symlink_to(tmp_path / "secret.env")

    listed = reader.evidence("factory", "t_665ba7d7")

    assert [(file.name, file.bytes) for file in listed] == [
        ("01-test-fails-on-base.txt", 4),
        ("02-test-passes-on-tip.txt", 2),
        ("gate/1/01-fail-first-on-base.txt", 42),
    ]
    assert listed[0].at > 0


def test_the_board_copy_then_nothing_is_the_fallback(board, reader):
    (board / "evidence" / "t_03d9d40d").mkdir(parents=True)
    (board / "evidence" / "t_03d9d40d" / "01.txt").write_text("")

    assert [file.name for file in reader.evidence("factory", "t_03d9d40d")] == ["01.txt"]
    assert reader.evidence("factory", "t_00000000") is None
    assert reader.evidence("factory", "../t_03d9d40d") is None
    assert reader.evidence("../factory", "t_03d9d40d") is None


def test_the_listing_stops_at_fifty_files(board, reader):
    evidence = board / "evidence" / "t_03d9d40d"
    evidence.mkdir(parents=True)
    for index in range(60):
        (evidence / f"{index:02d}.txt").write_text("")

    assert len(reader.evidence("factory", "t_03d9d40d")) == 50
