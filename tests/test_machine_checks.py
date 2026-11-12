"""The state of the machine: the tiles of the former probe, published by the core (ADR 0008, ADR 0011).

The expected statuses and details are copied from the cases of the factory's probe tests
(hermes-software-factory, tests/test_factory_live_probe.py); a declared http check that answers 401 is ok here, as the
issue asks (freellmapi answers 401 without a key).
"""

from __future__ import annotations

import http.server
import json
import socket
import sqlite3
import threading
from pathlib import Path

import jsonschema
import pytest
from fastapi.testclient import TestClient
from workflow_core.adapters import runtime_adapter
from workflow_core.api.app import create_app
from workflow_core.config import load_checks, load_workflows
from workflow_core.model.checks import DeclaredCheck

from conftest import runtime_schema

ROOT = Path(__file__).resolve().parent.parent
SECRET = "test-secret-not-a-real-one"
GOOD = {"Authorization": f"Bearer {SECRET}"}
NOW = 1794477600.0  # 2026-11-12 10:00:00 UTC


def gateway_base(home: Path, last_heartbeat: float, pid: int = 4242) -> None:
    """The runtime's root state.db with one gateway heartbeat, as the gateway writes it."""
    home.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(home / "state.db") as db:
        db.executescript(runtime_schema("state", ["gateway_heartbeats"]))
        db.execute(
            "INSERT INTO gateway_heartbeats (backend_id, pid, started_at, last_heartbeat) VALUES (?, ?, ?, ?)",
            (f"default@test:{pid}:abc", pid, last_heartbeat - 3600, last_heartbeat),
        )
    db.close()


def machine(home: Path, checks: list[DeclaredCheck] = (), clock=lambda: NOW) -> list[dict]:
    """GET /api/machine's checks, once the core ran its first round of checks."""
    app = create_app(
        secret=SECRET,
        workflows=load_workflows(ROOT / "config" / "workflows.toml"),
        boards=runtime_adapter().make_board_reader(),
        runtime_checks=runtime_adapter().make_machine_checks(),
        checks=list(checks),
        clock=clock,
    )
    with TestClient(app) as client:
        response = client.get("/api/machine", headers=GOOD)
    assert response.status_code == 200
    # Every answer is valid against the published schema of the machine's state (C7).
    jsonschema.Draft202012Validator(json.loads((ROOT / "schema" / "machine.schema.json").read_text())).validate(
        response.json()
    )
    return response.json()["checks"]


def by_id(checks: list[dict]) -> dict[str, dict]:
    return {check["id"]: check for check in checks}


@pytest.fixture
def no_tools(tmp_path, monkeypatch) -> Path:
    """A PATH holding only a test folder: the machine's nvidia-smi and tailscale are never run."""
    folder = tmp_path / "bin"
    folder.mkdir()
    monkeypatch.setenv("PATH", str(folder))
    return folder


def test_the_gateway_is_alive_while_its_heartbeat_is_fresh(runtime_home, no_tools):
    gateway_base(runtime_home, NOW - 10)
    fresh = by_id(machine(runtime_home))["gateway"]
    assert fresh["status"] == "ok"
    assert fresh["metrics"]["pid"] == "4242"
    assert fresh["group"] == "her" + "mes"
    assert fresh["checked_at"].endswith("Z") or "+00:00" in fresh["checked_at"]

    (runtime_home / "state.db").unlink()
    gateway_base(runtime_home, NOW - 120)
    stale = by_id(machine(runtime_home))["gateway"]
    assert stale["status"] == "down"
    assert "120" in stale["detail"]


def test_a_pause_and_a_failing_cron_job_are_degraded(runtime_home, no_tools):
    gateway_base(runtime_home, NOW - 10)
    (runtime_home / "cron").mkdir()
    (runtime_home / "cron" / "jobs.json").write_text(
        json.dumps(
            {
                "jobs": [
                    {"name": "intake-github-poll", "last_status": "completed", "failure_streak": 0},
                    {"name": "factory-watch", "last_status": "failed", "failure_streak": 3},
                ]
            }
        ),
        encoding="utf-8",
    )
    calm = by_id(machine(runtime_home))
    assert (calm["pause"]["status"], calm["pause"]["detail"]) == ("ok", "Factory active, aucune pause posée")

    (runtime_home / "ESTOP").write_text(json.dumps({"reason": "maintenance"}), encoding="utf-8")
    checks = by_id(machine(runtime_home))
    assert checks["pause"]["status"] == "degraded"
    assert "maintenance" in checks["pause"]["detail"]
    assert checks["cron"]["status"] == "degraded"
    assert "factory-watch" in checks["cron"]["detail"]
    assert "intake-github-poll" not in checks["cron"]["detail"]
    assert checks["cron"]["group"] == "intake"
    # A corrupt ESTOP still counts as a pause, as the runtime reads it.
    (runtime_home / "ESTOP").write_text("{", encoding="utf-8")
    assert by_id(machine(runtime_home))["pause"]["status"] == "degraded"


class _Answer(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(401)
        self.end_headers()

    def log_message(self, *args):
        pass


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_declared_tcp_and_http_checks(runtime_home, no_tools):
    gateway_base(runtime_home, NOW - 10)
    listening = socket.socket()
    listening.bind(("127.0.0.1", 0))
    listening.listen()
    server = http.server.HTTPServer(("127.0.0.1", 0), _Answer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    closed = free_port()
    declared = [
        DeclaredCheck(
            id="graphify", label="MCP graphify", group="tools", kind="tcp", target=str(listening.getsockname()[1])
        ),
        DeclaredCheck(id="webhook", label="Écouteur webhook", group="intake", kind="tcp", target=f"127.0.0.1:{closed}"),
        DeclaredCheck(
            id="freellmapi",
            label="freellmapi",
            group="models",
            kind="http",
            target=f"http://127.0.0.1:{server.server_address[1]}/v1/models",
        ),
        DeclaredCheck(
            id="dashboard", label="Dashboard", group="hermes", kind="http", target=f"http://127.0.0.1:{closed}/"
        ),
    ]
    port = listening.getsockname()[1]
    try:
        checks = by_id(machine(runtime_home, declared))
    finally:
        server.shutdown()
        listening.close()

    assert (checks["graphify"]["status"], checks["graphify"]["group"]) == ("ok", "tools")
    assert checks["graphify"]["detail"] == f"Écoute sur {port}"
    assert (checks["webhook"]["status"], checks["webhook"]["detail"]) == ("down", f"Rien n'écoute sur {closed}")
    assert (checks["freellmapi"]["status"], checks["freellmapi"]["detail"]) == ("ok", "Répond (401)")
    assert checks["dashboard"]["status"] == "down"
    assert checks["dashboard"]["detail"].startswith("Ne répond pas")
    # The declared checks of the machine live in the configuration, never in the code (C7).
    configured = {check.id: check for check in load_checks(ROOT / "config" / "workflows.toml")}
    assert {"dashboard", "graphify", "freellmapi", "webhook", "vram", "funnel"} <= set(configured)
    assert configured["vram"].every_seconds == 60
    assert configured["funnel"].every_seconds == 300


def fake_command(folder: Path, name: str, output: str) -> None:
    command = folder / name
    command.write_text(f"#!/bin/sh\nprintf '%s\\n' '{output}'\n", encoding="utf-8")
    command.chmod(0o755)


def test_a_missing_tool_is_unknown_not_an_error(runtime_home, no_tools):
    gateway_base(runtime_home, NOW - 10)
    declared = [
        DeclaredCheck(id="vram", label="VRAM", group="models", kind="vram", every_seconds=60),
        DeclaredCheck(id="funnel", label="Tailscale Funnel", group="intake", kind="funnel", every_seconds=300),
    ]
    missing = by_id(machine(runtime_home, declared))
    assert (missing["vram"]["status"], missing["vram"]["detail"]) == ("unknown", "nvidia-smi indisponible")
    assert (missing["funnel"]["status"], missing["funnel"]["detail"]) == ("unknown", "tailscale indisponible")

    # The same checks with test commands: the probe's readings.
    fake_command(no_tools, "nvidia-smi", "2662, 16376, 7")
    fake_command(no_tools, "tailscale", json.dumps({"AllowFunnel": {"machine:443": True}}))
    found = by_id(machine(runtime_home, declared))
    assert (found["vram"]["status"], found["vram"]["detail"]) == ("ok", "2.6 sur 16 Go")
    assert found["vram"]["metrics"] == {"utilisation": "7 %"}
    assert (found["funnel"]["status"], found["funnel"]["detail"]) == ("ok", "Funnel actif vers le webhook")
