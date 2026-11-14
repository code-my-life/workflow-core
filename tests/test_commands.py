"""The launcher of a station's command: past its delay or with an unreadable output, the command is a `fail`, and the
engine blocks the card with the station and the reason (ADR 0005)."""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
from pathlib import Path

from workflow_core.adapters import runtime_adapter
from workflow_core.config import Workflow
from workflow_core.engine import Engine
from workflow_core.model.contract import ContractVerdict
from workflow_core.model.state import CoreState
from workflow_core.plugin.commands import STDERR_LIMIT, command_input, run_command
from workflow_core.plugin.manifest import plug

from conftest import ESSAI_REPO, make_runtime_board

CARD = "t_0a0b0c0d"


def payload() -> dict:
    return command_input(item="essai:1", issue=None, title="Premier essai", station="preparer", attempt=1)


def repo_with_preparer(tmp_path: Path, command: str, timeout: float) -> Path:
    """A copy of the trial repository whose `preparer` station runs this sample command with this delay."""
    repo = tmp_path / "workflow-essai"
    shutil.copytree(ESSAI_REPO, repo)
    manifest = json.loads((repo / "workflow-manifest.json").read_text(encoding="utf-8"))
    manifest["stations"][0].update(command=["python", f"commands/{command}"], timeout_seconds=timeout)
    (repo / "workflow-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return repo


def pass_blocks_the_card(fake_runtime, repo: Path, reason: str) -> list[str]:
    """Run one engine pass on a ready root card at `preparer`; the card must be blocked at its lane with the station
    and the reason, and nothing else written. Returns the journal."""
    make_runtime_board(
        fake_runtime.home,
        "essai",
        tasks=[
            {
                "id": CARD,
                "title": "Premier essai",
                "assignee": "essai-preparer",
                "status": "ready",
                "created_at": 1762900000,
                "idempotency_key": "essai:1",
            }
        ],
    )
    adapter = runtime_adapter()
    lines: list[str] = []
    verdict = ContractVerdict(ok=True, checked_at="2026-11-13T00:00:00+00:00", problems=[])
    Engine(
        workflows=[plug(Workflow(key="essai", repo=repo))],
        reader=adapter.make_board_reader(),
        writer=adapter.make_board_writer(),
        state=CoreState(contract=verdict),
        log=lines.append,
    ).run_pass()
    motive = f"Échec de la station preparer : {reason}"
    assert fake_runtime.writes() == [["kanban", "--board", "essai", "block", CARD, motive, "--kind", "needs_input"]]
    with sqlite3.connect(fake_runtime.home / "kanban" / "boards" / "essai" / "kanban.db") as db:
        row = db.execute("SELECT assignee, status, block_kind FROM tasks WHERE id = ?", (CARD,)).fetchone()
        assert row == ("essai-preparer", "blocked", "needs_input")
    db.close()
    return lines


def test_a_command_past_its_timeout_is_a_fail(fake_runtime, tmp_path):
    lines: list[str] = []
    began = time.monotonic()

    decision = run_command(ESSAI_REPO, ["python", "commands/sleeps.py"], payload(), 2, lines.append)

    assert time.monotonic() - began < 15
    assert (decision.decision, decision.reason, decision.to) == ("fail", "délai de 2 s dépassé", None)
    journal = pass_blocks_the_card(fake_runtime, repo_with_preparer(tmp_path, "sleeps.py", 2), "délai de 2 s dépassé")
    assert any(
        f"carte {CARD} bloquée : Échec de la station preparer : délai de 2 s dépassé" in line for line in journal
    )


def test_an_unreadable_output_is_a_fail(fake_runtime, tmp_path):
    lines: list[str] = []

    decision = run_command(ESSAI_REPO, ["python", "commands/chatty.py"], payload(), 30, lines.append)

    assert (decision.decision, decision.reason) == ("fail", "sortie illisible")
    assert any("sortie illisible" in line for line in lines)
    # A JSON output of the wrong shape is unreadable too.
    wrong = run_command(ESSAI_REPO, ["python", "-c", 'print(\'{"decision": "jump"}\')'], payload(), 30, lines.append)
    assert (wrong.decision, wrong.reason) == ("fail", "sortie illisible")
    journal = pass_blocks_the_card(fake_runtime, repo_with_preparer(tmp_path, "chatty.py", 30), "sortie illisible")
    assert any(f"carte {CARD} bloquée : Échec de la station preparer : sortie illisible" in line for line in journal)


def test_the_error_output_goes_to_the_journal_within_its_limit(fake_runtime):
    lines: list[str] = []
    noisy = 'import sys; sys.stderr.write(\'x\' * 5000); print(\'{"decision": "wait", "reason": "plus tard"}\')'

    decision = run_command(ESSAI_REPO, ["python", "-c", noisy], payload(), 30, lines.append, label="essai")

    assert (decision.decision, decision.reason) == ("wait", "plus tard")
    [line] = [line for line in lines if "sortie d'erreur" in line]
    assert line.endswith("x" * STDERR_LIMIT)
    assert "x" * (STDERR_LIMIT + 1) not in line


def test_a_command_does_not_inherit_the_core_secrets(fake_runtime, monkeypatch):
    monkeypatch.setenv("WORKFLOW_CORE_SECRET", "not-a-real-secret")
    monkeypatch.setenv("WORKFLOW_CORE_OWNER_LOGIN", "proprietaire")
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv("ESSAI_VISIBLE", "oui")
    lines: list[str] = []
    seen = (
        "import json, os; names = sorted(n for n in os.environ if n.startswith(('WORKFLOW_CORE_', 'GITHUB_APP_', "
        "'ESSAI_'))); print(json.dumps({'decision': 'wait', 'reason': ','.join(names)}))"
    )

    decision = run_command(ESSAI_REPO, ["python", "-c", seen], payload(), 30, lines.append)

    assert (decision.decision, decision.reason) == ("wait", "ESSAI_VISIBLE")
