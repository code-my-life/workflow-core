"""The contract between the core and the installed Hermès: everything the adapter reads, declared in one place.

Hermès guarantees nothing about its bases: an update may rename a column or add a status. The check compares this
declaration with the base of each declared board, opened read-only, and names every gap (ADR 0001, ADR 0019).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime

from workflow_core.adapters.hermes.board import HermesBoardReader, make_board_reader
from workflow_core.model.board import BoardUnavailableError
from workflow_core.model.contract import ContractVerdict


@dataclass(frozen=True)
class Table:
    columns: tuple[str, ...]
    # Closed-valued columns: their known values. NULL and "" are allowed only where listed in `nullable`.
    values: dict[str, frozenset[str]] = field(default_factory=dict)
    nullable: frozenset[str] = frozenset()


# What the adapter reads of Hermès 0.22.2. Every story that reads something new adds it here.
CONTRACT: dict[str, Table] = {
    "tasks": Table(
        columns=("id", "title", "body", "status", "assignee", "created_at", "started_at", "completed_at", "block_kind"),
        values={
            "status": frozenset(
                {"triage", "todo", "scheduled", "ready", "running", "blocked", "review", "done", "archived"}
            ),
            "block_kind": frozenset({"dependency", "needs_input", "capability", "transient"}),
        },
        nullable=frozenset({"block_kind"}),
    ),
}


def _table_problems(db: sqlite3.Connection, name: str, table: Table) -> list[str]:
    present = {row[1] for row in db.execute(f'PRAGMA table_info("{name}")')}
    if not present:
        return [f"table {name} absente"]
    problems = [f"{name}.{column} absente" for column in table.columns if column not in present]
    for column, known in table.values.items():
        if column not in present:
            continue
        for (value,) in db.execute(f'SELECT DISTINCT "{column}" FROM "{name}" ORDER BY 1'):
            if (value is None or value == "") and column in table.nullable:
                continue
            if value not in known:
                problems.append(f'valeur de {column} inconnue : "{value}"')
    return problems


def board_problems(reader: HermesBoardReader, board: str) -> list[str]:
    """Every gap between the contract and the base of this board, each prefixed by the board."""
    try:
        with reader.open_board(board) as db:
            problems = [problem for name, table in CONTRACT.items() for problem in _table_problems(db, name, table)]
    except BoardUnavailableError as error:
        return [f"{board}: base absente ou illisible ({error})"]
    except sqlite3.Error as error:
        return [f"{board}: base illisible ({error})"]
    return [f"{board}: {problem}" for problem in problems]


def check_contract(boards: list[str], environ: dict[str, str] | None = None) -> ContractVerdict:
    """Check the contract against the base of each named board, read-only."""
    reader = make_board_reader(environ)
    problems = [problem for board in boards for problem in board_problems(reader, board)]
    return ContractVerdict(
        ok=not problems,
        checked_at=datetime.now(UTC).isoformat(timespec="seconds"),
        problems=problems,
    )
