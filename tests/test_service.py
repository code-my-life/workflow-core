"""The service: its start without a secret, its systemd unit and its install (ADR 0019)."""

import configparser
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from conftest import FACTORY_CARDS, make_board

ROOT = Path(__file__).resolve().parent.parent
UNIT = ROOT / "systemd" / "workflow-core.service"
INSTALL = ROOT / "scripts" / "install.sh"
RUNTIME_HOME_VAR = "HER" + "MES_HOME"


def test_the_service_refuses_to_start_without_a_secret(tmp_path):
    env = {key: value for key, value in os.environ.items() if key != "WORKFLOW_CORE_SECRET"}
    for empty in ({}, {"WORKFLOW_CORE_SECRET": ""}):
        result = subprocess.run(
            [sys.executable, "-m", "workflow_core"],
            cwd=ROOT,
            env={**env, **empty},
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode != 0
        assert "WORKFLOW_CORE_SECRET" in result.stderr
        assert result.stdout == ""


def read_unit() -> configparser.ConfigParser:
    unit = configparser.ConfigParser(strict=False, interpolation=None)
    unit.optionxform = str
    unit.read(UNIT, encoding="utf-8")
    return unit


def test_the_unit_serves_on_127_0_0_1_8447_with_the_machine_env():
    service = read_unit()["Service"]

    command = service["ExecStart"].split()
    assert command[1:3] == ["-m", "workflow_core"]
    assert command[command.index("--host") + 1] == "127.0.0.1"
    assert command[command.index("--port") + 1] == "8447"
    assert service["EnvironmentFile"] == "%h/.config/workflow-core/.env"
    assert service["Restart"] == "on-failure"
    assert service["WorkingDirectory"] == "%h/perso/workflow-core"
    # Its logs go to its own output, which journald collects.
    assert service.get("StandardOutput", "journal") == "journal"


def run_install(home: Path, calls: Path, runtime_home: Path | None = None) -> subprocess.CompletedProcess:
    # A fake systemctl on the PATH records its calls: the test never reaches the machine's systemd. The contract tests
    # the install runs read the given runtime home, by default an absent one (they are then skipped).
    bin_dir = home / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "systemctl"
    fake.write_text(f'#!/bin/sh\necho "$@" >> {calls}\n', encoding="utf-8")
    fake.chmod(0o755)
    env = {**os.environ, "HOME": str(home), "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    env.pop("XDG_CONFIG_HOME", None)
    env[RUNTIME_HOME_VAR] = str(runtime_home or home / "no-runtime")
    return subprocess.run(["bash", str(INSTALL)], env=env, capture_output=True, text=True, timeout=30)


def snapshot(folder: Path) -> dict[str, tuple[bytes, int]]:
    return {
        str(path.relative_to(folder)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


def test_a_second_install_changes_nothing(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    calls = tmp_path / "systemctl.calls"

    first = run_install(home, calls)
    assert first.returncode == 0, first.stderr
    installed = home / ".config" / "systemd" / "user" / "workflow-core.service"
    assert installed.read_bytes() == UNIT.read_bytes()
    after_first = snapshot(home / ".config")

    second = run_install(home, calls)
    assert second.returncode == 0, second.stderr
    assert snapshot(home / ".config") == after_first

    # systemd is reloaded, never asked to enable or start the service: that is the owner's task.
    lines = calls.read_text(encoding="utf-8").splitlines()
    assert lines and all(line == "--user daemon-reload" for line in lines)


def test_the_install_runs_the_contract_tests_and_reports_a_failure(tmp_path):
    runtime = tmp_path / "runtime"
    base = make_board(runtime, "factory", FACTORY_CARDS)
    home = tmp_path / "home"
    home.mkdir()
    calls = tmp_path / "systemctl.calls"

    kept = run_install(home, calls, runtime)
    assert kept.returncode == 0, kept.stdout + kept.stderr
    assert "tests/contract" in kept.stdout
    assert "1 passed" in kept.stdout

    with sqlite3.connect(base) as db:
        db.execute("ALTER TABLE tasks DROP COLUMN assignee")
    db.close()
    broken = run_install(home, calls, runtime)

    assert broken.returncode == 1
    assert "factory: tasks.assignee absente" in broken.stdout
    assert broken.stdout.strip().splitlines()[-1].startswith("Contrat cassé")
    # The unit stays installed: the service will start in observer mode.
    assert (home / ".config" / "systemd" / "user" / "workflow-core.service").read_bytes() == UNIT.read_bytes()
