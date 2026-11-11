"""The contract between the core and the installed Hermès: everything the adapter reads, declared in one place.

Hermès guarantees nothing about its bases: an update may rename a column or add a status. The check compares this
declaration with the base of each declared board, opened read-only, and names every gap (ADR 0001, ADR 0019).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from workflow_core.adapters.hermes.board import (
    CARD_COLUMNS,
    CARD_FACT_COLUMNS,
    COMMENT_COLUMNS,
    EVENT_COLUMNS,
    LINK_COLUMNS,
    RUN_COLUMNS,
    RUN_FACT_COLUMNS,
    HermesBoardReader,
    make_board_reader,
)
from workflow_core.adapters.hermes.version import read_runtime_version
from workflow_core.clock import utc_now
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
        columns=CARD_COLUMNS + CARD_FACT_COLUMNS,
        values={
            "status": frozenset(
                {"triage", "todo", "scheduled", "ready", "running", "blocked", "review", "done", "archived"}
            ),
            "block_kind": frozenset({"dependency", "needs_input", "capability", "transient"}),
        },
        nullable=frozenset({"block_kind"}),
    ),
    "task_runs": Table(columns=RUN_COLUMNS + RUN_FACT_COLUMNS),
    "task_links": Table(columns=LINK_COLUMNS),
    "task_comments": Table(columns=COMMENT_COLUMNS),
    "task_events": Table(columns=EVENT_COLUMNS),
}
# The pragmas the adapter reads, on a read-only connection: data_version must be an integer (HermesBoardWatcher).
PRAGMAS = ("data_version",)


def pragma_problems(db) -> list[str]:
    problems = []
    for pragma in PRAGMAS:
        try:
            row = db.execute(f"PRAGMA {pragma}").fetchone()
        except sqlite3.Error as error:
            problems.append(f"PRAGMA {pragma} illisible : {error}")
            continue
        value = row[0] if row else None
        if not isinstance(value, int) or isinstance(value, bool):
            problems.append(f"PRAGMA {pragma} illisible : {value!r}")
    return problems


# The version output is part of the contract too: its first line must keep the form VERSION_LINE reads.


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
            problems += pragma_problems(db)
    except BoardUnavailableError as error:
        return [f"{board}: base absente ou illisible ({error})"]
    except sqlite3.Error as error:
        return [f"{board}: base illisible ({error})"]
    return [f"{board}: {problem}" for problem in problems]


def check_contract(boards: list[str], environ: dict[str, str] | None = None) -> ContractVerdict:
    """Check the contract against the base of each named board, read-only, and against the version output."""
    reader = make_board_reader(environ)
    problems = [problem for board in boards for problem in board_problems(reader, board)]
    _, version_problem = read_runtime_version()
    if version_problem is not None:
        problems.append(version_problem)
    return ContractVerdict(
        ok=not problems,
        checked_at=utc_now(),
        problems=problems,
    )
