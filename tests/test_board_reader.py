"""The adapter reads a board's base read-only, and only the board it is named (ADR 0001, ADR 0003)."""

import sqlite3

import pytest
from workflow_core.adapters import runtime_adapter
from workflow_core.adapters.hermes.board import HermesBoardReader
from workflow_core.model.board import BoardUnavailableError

from conftest import FACTORY_CARDS, STUDIO_CARDS, make_runtime_board


def test_a_write_on_the_opened_board_is_refused(runtime_home):
    base = runtime_home / "kanban" / "boards" / "factory" / "kanban.db"
    before = base.read_bytes()
    reader = runtime_adapter().make_board_reader()

    with reader.open_board("factory") as db:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            db.execute("UPDATE tasks SET status = 'done'")
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            db.execute("CREATE TABLE intruder (x)")
    assert [card.id for card in reader.read_cards("factory")] == ["t_0001", "t_0002"]

    assert base.read_bytes() == before


def test_the_reader_reads_only_the_named_board(runtime_home):
    reader = runtime_adapter().make_board_reader()

    factory = reader.read_cards("factory")
    studio = reader.read_cards("studio")

    assert [card.model_dump() for card in factory] == FACTORY_CARDS
    assert [card.model_dump() for card in studio] == STUDIO_CARDS
    # No notion of a current board: the board is a parameter of every read, never a state of the reader.
    assert not [name for name in vars(reader) if "board" in name]


def test_a_missing_base_is_a_named_error_not_an_empty_list(runtime_home):
    reader = runtime_adapter().make_board_reader()
    with pytest.raises(BoardUnavailableError, match="nowhere"):
        reader.read_cards("nowhere")


def test_reading_the_facts_of_a_card_leaves_the_base_unchanged(tmp_path):
    home = tmp_path / "runtime"
    base = make_runtime_board(
        home,
        "factory",
        tasks=[
            {
                "id": "t_0042",
                "title": "Une carte",
                "status": "ready",
                "created_at": 1793000000,
                "skills": '["github"]',
                "max_runtime_seconds": 7200,
            }
        ],
        runs=[
            {
                "id": 1,
                "task_id": "t_0042",
                "profile": "coder",
                "status": "timed_out",
                "started_at": 1793000060,
                "metadata": '{"worker_session_id": "s1"}',
                "error": "timed out",
            }
        ],
        events=[
            {"task_id": "t_0042", "run_id": 1, "kind": "timed_out", "payload": "not json", "created_at": 1793007320}
        ],
    )
    before = base.read_bytes()
    reader = HermesBoardReader(home)

    facts = reader.read_card_facts("factory", "t_0042")

    assert facts.card.skills == ["github"]
    assert facts.card.max_runtime_seconds == 7200
    assert facts.runs[0].metadata == {"worker_session_id": "s1"}
    assert facts.runs[0].error == "timed out"
    # A payload that is not a JSON object is read as an empty one, as factory-live reads it.
    assert facts.events[0].payload == {}
    assert reader.read_card_facts("factory", "t_9999") is None
    assert base.read_bytes() == before
