"""The write cycle of the contract test, on the fake runtime: each step read back, the throwaway board always
removed (ADR 0019)."""

from __future__ import annotations

import pytest
from workflow_core.adapters import runtime_adapter

from conftest import make_runtime_board

NOW = 1762900000


def boards(home) -> list[str]:
    folder = home / "kanban" / "boards"
    return sorted(path.name for path in folder.iterdir()) if folder.is_dir() else []


def cycle():
    adapter = runtime_adapter()
    return adapter.run_write_cycle(adapter.make_board_writer(), adapter.make_board_reader(), now=lambda: NOW)


def test_the_full_write_cycle_passes_and_removes_its_board(fake_runtime):
    make_runtime_board(fake_runtime.home, "essai", tasks=[])

    assert cycle() == f"workflow-core-contrat-{NOW}"

    assert boards(fake_runtime.home) == ["essai"]
    steps = [call[3:5] if call[1] == "--board" else call[1:3] for call in fake_runtime.writes()]
    assert [step[0] for step in steps] == [
        "boards",
        "boards",
        "create",
        "create",
        # The same key again, through the root card's creation: the existing card comes back, whole.
        "create",
        "create",
        "assign",
        "create",
        "schedule",
        "unblock",
        "block",
        "unblock",
        # The engine's blocks alternate their kinds: the card stays blocked, never in triage.
        "block",
        "unblock",
        "comment",
        "complete",
        "boards",
        "boards",
    ]
    # `schedule` and `block` are called as the engine calls them: with a reason, the kinds in turn.
    board = f"workflow-core-contrat-{NOW}"
    lifecycle = [call[3:] for call in fake_runtime.writes() if call[3:4] in (["schedule"], ["block"])]
    card = lifecycle[0][1]
    assert lifecycle == [
        ["schedule", card, "--", "contrat : en attente"],
        ["block", card, "--kind", "needs_input", "--", "contrat : premier blocage"],
        ["block", card, "--kind", "capability", "--", "contrat : second blocage"],
    ]
    assert board in {call[2] for call in fake_runtime.writes() if call[1] == "--board"}
    # Only the throwaway board is written to.
    assert {call[2] for call in fake_runtime.writes() if call[1] == "--board"} == {f"workflow-core-contrat-{NOW}"}


def test_the_throwaway_board_is_removed_even_after_a_failed_step(fake_runtime):
    adapter = runtime_adapter()
    fake_runtime.fail("schedule")

    with pytest.raises(adapter.WriteCycleError) as raised:
        cycle()

    assert raised.value.step == "schedule"
    assert str(raised.value).startswith("cycle d'écriture, pas « schedule » : ")
    assert "schedule refusé par le faux runtime" in str(raised.value)
    assert boards(fake_runtime.home) == []
    assert fake_runtime.writes()[-1] == [
        "kanban",
        "--board",
        f"workflow-core-contrat-{NOW}",
        "boards",
        "rm",
        "--delete",
        f"workflow-core-contrat-{NOW}",
    ]


def test_a_throwaway_board_left_by_an_interrupted_cycle_is_removed_first(fake_runtime):
    make_runtime_board(fake_runtime.home, "workflow-core-contrat-123", tasks=[])

    cycle()

    assert boards(fake_runtime.home) == []
    assert fake_runtime.writes()[:2] == [
        ["kanban", "boards", "list", "--json"],
        ["kanban", "--board", "workflow-core-contrat-123", "boards", "rm", "--delete", "workflow-core-contrat-123"],
    ]


def test_a_wrong_state_read_back_fails_the_cycle_at_its_step(fake_runtime, monkeypatch):
    adapter = runtime_adapter()
    writer = adapter.make_board_writer()
    # A runtime whose `unblock` does nothing: the card read back is still `scheduled`.
    monkeypatch.setattr(writer, "unblock", lambda board, card: None)

    with pytest.raises(adapter.WriteCycleError) as raised:
        adapter.run_write_cycle(writer, adapter.make_board_reader(), now=lambda: NOW)

    assert raised.value.step == "unblock"
    assert "statut relu « scheduled », attendu « ready »" in str(raised.value)
    assert boards(fake_runtime.home) == []


def test_only_a_board_of_the_reserved_prefix_is_removed_first(fake_runtime):
    # A board whose name merely starts with `contrat-` is not the cycle's: it is never removed.
    make_runtime_board(fake_runtime.home, "contrat-client", tasks=[])
    make_runtime_board(fake_runtime.home, "workflow-core-contrat-123", tasks=[])

    cycle()

    assert boards(fake_runtime.home) == ["contrat-client"]


def test_a_block_sent_to_triage_fails_the_cycle(fake_runtime, monkeypatch):
    adapter = runtime_adapter()
    writer = adapter.make_board_writer()
    block = writer.block
    # A runtime that kept the first kind: the second block of the same kind sends the card to triage.
    monkeypatch.setattr(
        writer, "block", lambda board, card, reason=None, *, kind=None: block(board, card, reason, kind="needs_input")
    )

    with pytest.raises(adapter.WriteCycleError) as raised:
        adapter.run_write_cycle(writer, adapter.make_board_reader(), now=lambda: NOW)

    assert raised.value.step == "block --kind capability"
    assert "statut relu « triage », attendu « blocked »" in str(raised.value)
    assert boards(fake_runtime.home) == []
