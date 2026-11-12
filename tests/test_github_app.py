"""The App's tokens, minted by the core from its own `.env` and kept in its own cache, against a local fake GitHub."""

from __future__ import annotations

import json
import stat
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest
from workflow_core.adapters.github import AppTokens
from workflow_core.adapters.github.__main__ import main

EXPIRES = "2030-01-01T00:00:00Z"


class FakeGitHub(BaseHTTPRequestHandler):
    minted = 0
    calls: ClassVar[list[tuple[str, str, str]]] = []

    def _answer(self, status: int, body: object) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        FakeGitHub.calls.append(("GET", self.path, self.headers.get("Authorization", "")))
        if self.path == "/orgs/code-my-life/installation":
            self._answer(200, {"id": 42})
        elif self.path == "/repos/code-my-life/workflow-core":
            self._answer(200, {"full_name": "code-my-life/workflow-core"})
        else:
            self._answer(404, {"message": "Not Found"})

    def do_POST(self) -> None:
        FakeGitHub.calls.append(("POST", self.path, self.headers.get("Authorization", "")))
        if self.path == "/app/installations/42/access_tokens":
            FakeGitHub.minted += 1
            self._answer(
                201,
                {
                    "token": f"ghs_test_{FakeGitHub.minted}",
                    "expires_at": EXPIRES,
                    "permissions": {"issues": "write", "metadata": "read"},
                },
            )
        else:
            self._answer(404, {"message": "Not Found"})

    def log_message(self, *args) -> None:
        pass


@pytest.fixture
def github():
    FakeGitHub.minted = 0
    FakeGitHub.calls = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture
def core(tmp_path, monkeypatch) -> dict:
    """A test RSA key, the core's `.env` naming it, and the core's state folder, all in the test's folder."""
    key = tmp_path / "app-key.pem"
    subprocess.run(["openssl", "genrsa", "-out", str(key), "2048"], check=True, capture_output=True)
    env_file = tmp_path / "core.env"
    env_file.write_text(f"GITHUB_APP_ID=123456\nGITHUB_APP_PRIVATE_KEY_PATH={key}\n", encoding="utf-8")
    state = tmp_path / "state"
    monkeypatch.setenv("WORKFLOW_CORE_STATE_DIR", str(state))
    for name in ("GITHUB_APP_ID", "GITHUB_APP_PRIVATE_KEY_PATH"):
        monkeypatch.delenv(name, raising=False)
    return {"env_file": env_file, "state": state, "key": key}


def run(command: str, core: dict, api: str, env_file: Path | None = None) -> tuple[int, list[str], list[str]]:
    out: list[str] = []
    err: list[str] = []
    code = main(
        [command],
        environ={"WORKFLOW_CORE_STATE_DIR": str(core["state"])},
        env_file=env_file or core["env_file"],
        api_url=api,
        out=out.append,
        err=err.append,
    )
    return code, out, err


def test_a_token_is_minted_from_the_core_env_and_cached(core, github):
    code, out, _ = run("token", core, github)

    assert code == 0
    assert out == ["ghs_test_1"]
    cache = core["state"] / "app-token.json"
    assert stat.S_IMODE(cache.stat().st_mode) == 0o600
    assert json.loads(cache.read_text())["token"] == "ghs_test_1"
    # The JWT is signed with the test key and names the App's id.
    jwt = next(auth for method, path, auth in FakeGitHub.calls if path.endswith("/installation"))
    payload = jwt.removeprefix("Bearer ").split(".")[1]
    assert json.loads(__import__("base64").urlsafe_b64decode(payload + "==").decode())["iss"] == "123456"

    # Reused while more than 10 minutes are left.
    assert run("token", core, github)[1] == ["ghs_test_1"]
    assert FakeGitHub.minted == 1

    # Less than 10 minutes left: a new one.
    soon = AppTokens(env_file=core["env_file"], cache_dir=core["state"], api_url=github, clock=lambda: 1893455400.0)
    assert soon.token() == "ghs_test_2"

    code, out, _ = run("check", core, github)
    assert code == 0
    assert out[0].startswith("jeton valable jusqu'à 20")
    assert "ghs_test" not in "\n".join(out)
    assert out[1] == "permissions de l'installation : issues: write, metadata: read"
    assert out[2] == "code-my-life/workflow-core : accessible"


def test_the_factory_cache_is_never_read(core, github, tmp_path, monkeypatch):
    runtime = tmp_path / "runtime-home"
    (runtime / "cache").mkdir(parents=True)
    factory_cache = runtime / "cache" / "app-token.json"
    factory_cache.write_text(json.dumps({"org": "code-my-life", "token": "factory", "expires_at": 4e9}))
    factory_cache.chmod(0o000)
    (runtime / ".env").write_text("GITHUB_APP_ID=999\n", encoding="utf-8")
    monkeypatch.setenv("HER" + "MES_HOME", str(runtime))
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".hermes").symlink_to(runtime)

    code, out, _ = run("token", core, github)

    assert (code, out) == (0, ["ghs_test_1"])
    assert stat.S_IMODE(factory_cache.stat().st_mode) == 0
    factory_cache.chmod(0o600)


def test_a_missing_app_id_is_named(core, github, tmp_path):
    empty = tmp_path / "empty.env"
    empty.write_text("", encoding="utf-8")
    for command in ("token", "check"):
        code, out, err = run(command, core, github, env_file=empty)
        assert code == 1
        assert out == []
        assert "GITHUB_APP_ID" in err[0]
    assert FakeGitHub.calls == []

    missing_key = tmp_path / "missing-key.env"
    missing_key.write_text(f"GITHUB_APP_ID=1\nGITHUB_APP_PRIVATE_KEY_PATH={tmp_path / 'nowhere.pem'}\n")
    code, _, err = run("token", core, github, env_file=missing_key)
    assert code == 1
    assert "GITHUB_APP_PRIVATE_KEY_PATH" in err[0]
