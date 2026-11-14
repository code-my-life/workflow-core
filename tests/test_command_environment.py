"""The environment of a workflow's command: the core's own virtual environment never reaches it, so `uv run
--directory` has no foreign `VIRTUAL_ENV` to warn about; its input is unchanged."""

from __future__ import annotations

import json

from workflow_core.plugin.commands import command_input, run_command

from conftest import ESSAI_REPO

# Prints the virtual environment the command sees and the input it read, as the reason of a `wait`.
SEEN = (
    "import json, os, sys; read = json.load(sys.stdin); "
    "print(json.dumps({'decision': 'wait', 'reason': json.dumps([os.environ.get('VIRTUAL_ENV'), read])}))"
)


def test_a_command_does_not_inherit_the_core_virtual_environment(fake_runtime, monkeypatch, tmp_path):
    core_venv = str(tmp_path / "core-venv")
    monkeypatch.setenv("VIRTUAL_ENV", core_venv)
    payload = command_input(item="essai:1", issue=None, title="Premier essai", station="preparer", attempt=1)
    lines: list[str] = []

    decision = run_command(ESSAI_REPO, ["python", "-c", SEEN], payload, 30, lines.append)

    assert decision.decision == "wait"
    seen_venv, seen_input = json.loads(decision.reason)
    assert seen_venv != core_venv
    assert seen_input == payload
