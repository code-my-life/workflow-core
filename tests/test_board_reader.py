"""The adapter reads a board's base read-only, and only the board it is named (ADR 0001, ADR 0003)."""

import sqlite3

import pytest
from workflow_core.adapters import runtime_adapter
from workflow_core.model.board import BoardUnavailableError

from conftest import FACTORY_CARDS, STUDIO_CARDS


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
