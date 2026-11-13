"""The runtime adapter's writer: every write goes through the runtime's command line, `kanban --board <slug>`, with a
list of arguments, and a failing command is a named error (ADR 0001, amended; ADR 0003)."""

from __future__ import annotations

import sqlite3

import pytest
from workflow_core.adapters import runtime_adapter
from workflow_core.config import Workflow
from workflow_core.engine import Engine
from workflow_core.model.contract import ContractVerdict
from workflow_core.model.state import CoreState
from workflow_core.model.writer import BoardWriteError
from workflow_core.plugin.manifest import plug

from conftest import ESSAI_REPO, make_runtime_board

ISSUE = "https://github.com/code-my-life/workflow-essai/issues/1"
# A title a shell would split and expand: it reaches the runtime as one argument.
TITLE = 'essai:1 · Premier essai; echo "$HOME" $(id) > /tmp/x'


def commands():
    return runtime_adapter().contract.COMMANDS


def declared(call: list[str]) -> bool:
    """Whether a write uses only a command and options the contract declares."""
    rest = call[3:]
    for command, options in commands().items():
        words = command.split()
        if rest[: len(words)] == words:
            used = [argument for argument in rest[len(words) :] if argument.startswith("--")]
            return all(option in options for option in used)
    return False


def test_every_write_names_its_board_and_goes_through_the_command_line(fake_runtime):
    writer = runtime_adapter().make_board_writer()

    assert writer.has_board("essai") is False
    writer.create_board("essai")
    assert writer.has_board("essai") is True
    card = writer.create_root_card("essai", title=TITLE, lane="essai-preparer", item="essai:1", issue=ISSUE)
    writer.assign("essai", card, "essai-revue")

    assert fake_runtime.writes() == [
        ["kanban", "--board", "essai", "boards", "create", "essai"],
        [
            "kanban",
            "--board",
            "essai",
            "create",
            TITLE,
            "--assignee",
            "essai-preparer",
            "--idempotency-key",
            "essai:1",
            "--body",
            ISSUE,
            "--json",
        ],
        ["kanban", "--board", "essai", "assign", card, "essai-revue"],
    ]
    assert all(declared(call) for call in fake_runtime.writes())
    with sqlite3.connect(fake_runtime.home / "kanban" / "boards" / "essai" / "kanban.db") as db:
        row = db.execute("SELECT title, body, assignee, status, idempotency_key FROM tasks").fetchone()
    db.close()
    assert row == (TITLE, ISSUE, "essai-revue", "ready", "essai:1")


def test_a_failing_command_is_a_named_error(fake_runtime, capsys):
    make_runtime_board(
        fake_runtime.home,
        "essai",
        tasks=[
            {
                "id": "t_0a0b0c0d",
                "title": "Premier essai",
                "assignee": "essai-preparer",
                "status": "ready",
                "created_at": 1762900000,
                "idempotency_key": "essai:1",
            }
        ],
    )
    adapter = runtime_adapter()
    fake_runtime.fail("assign")

    with pytest.raises(BoardWriteError) as raised:
        adapter.make_board_writer().assign("essai", "t_0a0b0c0d", "essai-revue")

    message = str(raised.value)
    assert "kanban --board <board> assign <card> <lane>" in message
    assert "code 1" in message
    assert "assign refusé par le faux runtime" in message
    # The command is named without its values.
    assert "t_0a0b0c0d" not in message and "essai-revue" not in message
    assert (raised.value.code, raised.value.command) == (1, "hermes kanban --board <board> assign <card> <lane>")

    # In the engine's pass, the error goes to the journal and the service goes on.
    verdict = ContractVerdict(ok=True, checked_at="2026-11-13T00:00:00+00:00", problems=[])
    core = Engine(
        workflows=[plug(Workflow(key="essai", repo=ESSAI_REPO))],
        reader=adapter.make_board_reader(),
        writer=adapter.make_board_writer(),
        state=CoreState(contract=verdict),
    )
    core.run_pass()
    errors = [line for line in capsys.readouterr().err.splitlines() if line.startswith("<3>")]
    assert len(errors) == 1
    assert "essai:1" in errors[0] and "assign <card> <lane>" in errors[0] and "code 1" in errors[0]
