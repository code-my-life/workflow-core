"""The data a workflow declares besides its cards, served as generic values (ADR 0011, issue #36).

The factory's entry of config/workflows.toml declares them (twins, gate proofs, the sessions of a run, the card's log,
the drift of an agent); the runtime's adapter reads them at the declared paths. The expected entries of the transcripts
and of the drift are copied from factory-live's tests at commit d47f235 (sessions.test.ts, files.test.ts,
drift.test.ts).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from workflow_core.adapters import runtime_adapter
from workflow_core.api.app import create_app
from workflow_core.config import load_workflows

from conftest import RUNTIME_HOME_VAR, make_runtime_board, make_session_base

ROOT = Path(__file__).resolve().parent.parent
SECRET = "test-secret-not-a-real-one"
GOOD = {"Authorization": f"Bearer {SECRET}"}
CARD = "t_abc12345"
RUN = 7
RUN_START = 1794470000  # 2026-11-12 07:53:20 UTC
RUN_END = RUN_START + 600

TWINS = {
    "state": "ready",
    "divergence": None,
    "mirror": 18_100,
    "surfaces": [
        {
            "id": "app",
            "label": "App",
            "kind": "web",
            "changed": True,
            "differences": 2,
            # A twin's stream carries Neko's password: it is never read.
            "base": {"sha": "aaaaaaa", "stream": "http://127.0.0.1:18101/?usr=recette&pwd=neko-secret", "poster": None},
            "head": {"sha": "bbbbbbb", "stream": "http://127.0.0.1:18102/?usr=recette&pwd=neko-secret", "poster": None},
        },
        {
            "id": "site",
            "label": "Site",
            "kind": "site",
            "changed": False,
            "differences": None,
            "base": {"sha": "aaaaaaa", "stream": None, "poster": None},
            "head": {"sha": "bbbbbbb", "stream": None, "poster": None},
        },
    ],
}


@pytest.fixture
def factory(tmp_path, monkeypatch) -> dict:
    """A runtime home with the factory's board (one card, one run of `coder`), the factory's repository and Claude
    Code's projects, all in the test's folder; the factory's declared data as config/workflows.toml declares them."""
    home = tmp_path / "runtime"
    workspace = "/work/factory-live-24"
    make_runtime_board(
        home,
        "factory",
        tasks=[
            {
                "id": CARD,
                "title": "impl factory-live#24",
                "status": "review",
                "assignee": "coder",
                "created_at": RUN_START - 60,
                "workspace_path": workspace,
            }
        ],
        runs=[
            {
                "id": RUN,
                "task_id": CARD,
                "profile": "coder",
                "status": "done",
                "started_at": RUN_START,
                "ended_at": RUN_END,
                "metadata": json.dumps({"worker_session_id": "s1"}),
            }
        ],
    )
    repo = tmp_path / "factory-repo"
    claude = tmp_path / "claude-projects"
    claude.mkdir()
    monkeypatch.setenv(RUNTIME_HOME_VAR, str(home))
    monkeypatch.setenv("CLAUDE_PROJECTS", str(claude))
    workflows = load_workflows(ROOT / "config" / "workflows.toml")
    workflows["factory"] = workflows["factory"].model_copy(update={"repo": repo})
    client = TestClient(create_app(secret=SECRET, workflows=workflows, boards=runtime_adapter().make_board_reader()))
    return {
        "home": home,
        "board": home / "kanban" / "boards" / "factory",
        "repo": repo,
        "claude": claude,
        "workspace": workspace,
        "client": client,
    }


def data(factory: dict, subject: str, item: str) -> dict:
    response = factory["client"].get(
        "/api/workflows/factory/data", params={"subject": subject, "id": item}, headers=GOOD
    )
    assert response.status_code == 200, response.text
    return response.json()


def named(answer: dict, name: str) -> dict:
    return next(entry for entry in answer["data"] if entry["name"] == name)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_the_twins_of_a_pr_are_served_as_generic_values(factory):
    twins = write(factory["board"] / "twins" / "factory-live-24.json", json.dumps(TWINS))
    before = twins.read_bytes()

    answer = data(factory, "pr", "factory-live#24")

    assert (answer["schema_version"], answer["workflow"], answer["subject"], answer["id"]) == (
        1,
        "factory",
        "pr",
        "factory-live#24",
    )
    values = named(answer, "twins")["values"]
    assert [value["type"] for value in values] == ["status", "table", "text"]
    assert values[0] == {"type": "status", "label": "État", "status": "ok", "text": "ready"}
    # Since #120, each surface also carries its id and the commits and images of its base and head.
    assert values[1]["columns"][1:5] == ["Surface", "Sorte", "Touchée", "Différences"]
    assert [row[1:5] for row in values[1]["rows"]] == [["App", "web", True, 2], ["Site", "site", False, None]]
    assert values[2] == {"type": "text", "label": "Miroir", "text": "18100"}
    # No divergence: no text; and nothing of the streams, which carry a password.
    assert "neko-secret" not in json.dumps(answer)
    assert twins.read_bytes() == before


def test_the_gate_proofs_of_a_card_are_texts_in_rule_order(factory):
    gate = factory["board"] / "evidence" / CARD / "gate" / "abc1234"
    write(gate / "03-tests.txt", "3 passed")
    write(gate / "01-format.txt", "formatted")
    write(gate / "02-lint.txt", "GITHUB_TOKEN=ghp_" + "a" * 36)

    values = named(data(factory, "card", CARD), "gate-proofs")["values"]

    assert [(value["type"], value["label"]) for value in values] == [
        ("text", "format"),
        ("text", "lint"),
        ("text", "tests"),
    ]
    assert values[0]["text"] == "formatted"
    assert "ghp_" not in values[1]["text"]


def test_the_transcripts_of_a_run_are_tables_with_factory_live_entries(factory):
    # The agent's session, the case of factory-live's sessions.test.ts (« transcrit messages, raisonnement... »).
    db = make_session_base(factory["home"], "coder")
    db.execute("INSERT INTO sessions (id, source, model, started_at) VALUES ('s1', 'kanban', 'auto', ?)", (RUN_START,))

    def message(role: str, at: float, **fields) -> None:
        columns = ["session_id", "role", "timestamp", *fields]
        db.execute(
            f"INSERT INTO messages ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
            ("s1", role, at, *fields.values()),
        )

    message("user", RUN_START + 1, content="Implémente la carte")
    message(
        "assistant",
        RUN_START + 2,
        content="Je lance les tests",
        reasoning="Il faut vérifier",
        tool_calls=json.dumps(
            [{"id": "c1", "type": "function", "function": {"name": "terminal", "arguments": '{"command":"npm test"}'}}]
        ),
    )
    message("tool", RUN_START + 3, tool_name="terminal", content='{"output": "GITHUB_TOKEN=ghp_' + "a" * 36 + '"}')
    message("tool", RUN_START + 4, tool_name="read_file", content='{"error": "File not found"}')
    db.close()
    # A Claude Code lane launched from the card's workspace during the run (files.test.ts).
    folder = factory["claude"] / factory["workspace"].replace("/", "-")
    lane = write(
        folder / "lane.jsonl",
        "\n".join(
            json.dumps(record)
            for record in [
                {"type": "custom-title", "customTitle": "factory-live#24"},
                {
                    "type": "assistant",
                    "uuid": "u1",
                    "timestamp": "2026-11-12T08:15:00.000Z",
                    "message": {
                        "model": "claude-opus",
                        "content": [
                            {"type": "text", "text": "Je lis le code"},
                            {"type": "tool_use", "name": "Read", "input": {"file_path": "a.ts"}},
                        ],
                    },
                },
                {
                    "type": "user",
                    "uuid": "u2",
                    "timestamp": "2026-11-12T08:15:01.000Z",
                    "message": {"content": [{"type": "tool_result", "content": "ok", "is_error": True}]},
                },
            ]
        ),
    )
    os.utime(lane, (RUN_START + 300, RUN_START + 300))
    write(factory["board"] / "logs" / f"{CARD}.log", "\x1b[32mstart\x1b[0m\nend\n")

    answer = data(factory, "run", str(RUN))

    [session] = named(answer, "agent-session")["values"]
    assert session["type"] == "table"
    assert session["columns"] == ["Heure", "Rôle", "Texte", "Outil", "Modèle", "Statut"]
    assert [(row[1], row[3], row[5]) for row in session["rows"]] == [
        ("user", None, None),
        ("reasoning", None, None),
        ("assistant", None, None),
        ("tool_call", "terminal", None),
        ("tool_result", "terminal", "ok"),
        ("tool_result", "read_file", "error"),
    ]
    assert session["rows"][3][2] == '{"command":"npm test"}'
    assert "ghp_" not in session["rows"][4][2]
    assert session["rows"][0][0] == "2026-11-12T07:53:21.000Z"

    [lane_table] = named(answer, "claude-sessions")["values"]
    assert lane_table["label"] == "factory-live#24"
    assert [(row[1], row[2], row[3], row[4], row[5]) for row in lane_table["rows"]] == [
        ("assistant", "Je lis le code", None, "claude-opus", None),
        ("tool_call", '{"file_path":"a.ts"}', "Read", "claude-opus", None),
        ("tool_result", "ok", None, None, "error"),
    ]

    [log] = named(answer, "card-log")["values"]
    assert log == {"type": "text", "label": "Journal de la carte", "text": "\x1b[32mstart\x1b[0m\nend\n"}


def test_the_drift_of_an_agent_is_a_table_of_differences(factory):
    repo_config = (
        "model:\n  default: agnes-2.5-flash\n  provider: freellmapi\nagent:\n  reasoning_effort: low\n"
        "plugins:\n  enabled: [coder-guard, factory-notify]\n"
    )
    write(factory["repo"] / "profiles" / "coder" / "config.yaml", repo_config)
    write(
        factory["home"] / "profiles" / "coder" / "config.yaml",
        repo_config.replace("agnes-2.5-flash", "Qwen3.5-9B").replace("freellmapi", "llamacpp"),
    )

    [drift] = named(data(factory, "agent", "coder"), "drift")["values"]

    assert drift == {
        "type": "table",
        "label": "Dérive de la configuration",
        "columns": ["Champ", "Service", "Dépôt"],
        "rows": [["model", "Qwen3.5-9B (llamacpp)", "agnes-2.5-flash (freellmapi)"]],
    }


def test_a_missing_or_unreadable_data_never_fails_the_response(factory):
    assert named(data(factory, "pr", "factory-live#24"), "twins")["values"] == []
    assert named(data(factory, "card", CARD), "gate-proofs")["values"] == []
    assert named(data(factory, "agent", "coder"), "drift")["values"] == []
    unknown_run = data(factory, "run", "999")
    assert all(entry["values"] == [] for entry in unknown_run["data"])

    write(factory["board"] / "twins" / "factory-live-24.json", "{ not json")
    [value] = named(data(factory, "pr", "factory-live#24"), "twins")["values"]
    assert value["type"] == "status"
    assert value["status"] == "error"
    assert value["text"]

    # The route's own errors: an unknown subject, an id out of its subject's form, an unknown workflow, no secret.
    client = factory["client"]
    for params, code in (
        ({"subject": "issue", "id": "1"}, "unknown_subject"),
        ({"subject": "pr", "id": "factory-live"}, "invalid_id"),
        ({"subject": "run", "id": "seven"}, "invalid_id"),
        ({"subject": "agent", "id": "../coder"}, "invalid_id"),
    ):
        response = client.get("/api/workflows/factory/data", params=params, headers=GOOD)
        assert (response.status_code, response.json()["error"]) == (422, code)
    missing = client.get("/api/workflows/nope/data", params={"subject": "pr", "id": "a#1"}, headers=GOOD)
    assert (missing.status_code, missing.json()["error"]) == (404, "unknown_workflow")
    refused = client.get("/api/workflows/factory/data", params={"subject": "pr", "id": "a#1"})
    assert (refused.status_code, refused.json()["error"]) == (401, "unauthorized")
    # The studio declares no data.
    studio = client.get("/api/workflows/studio/data", params={"subject": "pr", "id": "a#1"}, headers=GOOD)
    assert studio.json()["data"] == []


def test_a_declared_path_outside_its_root_is_not_read(factory, tmp_path):
    from workflow_core.config import DataDeclaration

    secret = write(tmp_path / "outside.json", json.dumps({"state": "ready"}))
    # A symbolic link laid where the twins are read: not followed.
    link = factory["board"] / "twins" / "factory-live-24.json"
    link.parent.mkdir(parents=True)
    link.symlink_to(secret)
    assert named(data(factory, "pr", "factory-live#24"), "twins")["values"] == []

    # A declared path that climbs out of its root is never read.
    reader = runtime_adapter().make_board_reader().data_reader()
    escaping = DataDeclaration(
        name="escape",
        subject="pr",
        label="x",
        path="{board}/../../../outside.json",
        reader="json",
        fields=[{"key": "state", "label": "État", "kind": "text"}],
    )
    workflow = load_workflows(ROOT / "config" / "workflows.toml")["factory"].model_copy(update={"data": [escaping]})
    [entry] = reader.read(workflow, "pr", "factory-live#24")
    assert entry.values == []
    assert secret.read_text(encoding="utf-8") == json.dumps({"state": "ready"})


# The state file the twin bench writes for a ready pair (scripts/factory-twins.py of the factory, `write_state`): each
# twin's Neko session carries the viewers' password, and its HTTP port on this machine.
BENCH_STATE = {
    "state": "ready",
    "divergence": None,
    "mirror": 52004,
    "surfaces": [
        {
            "id": "app",
            "label": "App",
            "kind": "web",
            "changed": True,
            "differences": 3,
            "base": {
                "sha": "1111111aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "stream": "http://127.0.0.1:52000/?usr=proprietaire&pwd=s3cr3t-viewers",
                "poster": "",
            },
            "head": {
                "sha": "2222222bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "stream": "http://127.0.0.1:52001/?usr=proprietaire&pwd=s3cr3t-viewers",
                "poster": "",
            },
        }
    ],
}


def test_the_twins_publish_ids_commits_posters_and_the_mirror(factory):
    write(factory["board"] / "twins" / "factory-live-24.json", json.dumps(BENCH_STATE))

    values = named(data(factory, "pr", "factory-live#24"), "twins")["values"]

    table = next(value for value in values if value["type"] == "table")
    assert table["label"] == "Surfaces"
    assert table["columns"] == [
        "Identifiant",
        "Surface",
        "Sorte",
        "Touchée",
        "Différences",
        "Commit de base",
        "Image de base",
        "Commit de tête",
        "Image de tête",
    ]
    assert table["rows"] == [
        [
            "app",
            "App",
            "web",
            True,
            3,
            "1111111aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "",
            "2222222bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "",
        ]
    ]
    assert {"type": "text", "label": "Miroir", "text": "52004"} in values


def test_no_neko_session_nor_password_leaves_the_core(factory):
    write(factory["board"] / "twins" / "factory-live-24.json", json.dumps(BENCH_STATE))

    text = json.dumps(data(factory, "pr", "factory-live#24"))

    assert "s3cr3t-viewers" not in text
    assert "pwd=" not in text
    assert "proprietaire" not in text
    # Nor the HTTP port of a twin on this machine.
    assert "52000" not in text
    assert "52001" not in text


def test_the_gate_proofs_of_a_given_commit(factory):
    gate = factory["board"] / "evidence" / CARD / "gate"
    write(gate / "aaaaaaa" / "01-tests.txt", "tests of aaaaaaa")
    older = gate / "aaaaaaa"
    write(gate / "bbbbbbb" / "01-tests.txt", "tests of bbbbbbb")
    # bbbbbbb is the newest pass.
    os.utime(older, (1_700_000_000, 1_700_000_000))

    def proofs(item: str) -> list[str]:
        return [value["text"] for value in named(data(factory, "card", item), "gate-proofs")["values"]]

    assert proofs(f"{CARD}@aaaaaaa") == ["tests of aaaaaaa"]
    assert proofs(f"{CARD}@aaaaaaa0123456789") == ["tests of aaaaaaa"]
    assert proofs(CARD) == ["tests of bbbbbbb"]
    assert proofs(f"{CARD}@ccccccc") == []


def test_a_card_id_with_a_malformed_commit_is_invalid(factory):
    for item in (f"{CARD}@zz", f"{CARD}@", f"{CARD}@abc"):
        response = factory["client"].get(
            "/api/workflows/factory/data", params={"subject": "card", "id": item}, headers=GOOD
        )
        assert (response.status_code, response.json()["error"]) == (422, "invalid_id"), item
    # The other subjects keep their ids.
    for subject, item in (("pr", "factory-live#24"), ("run", str(RUN)), ("agent", "coder"), ("card", CARD)):
        response = factory["client"].get(
            "/api/workflows/factory/data", params={"subject": subject, "id": item}, headers=GOOD
        )
        assert response.status_code == 200, (subject, item)


def test_a_dotted_column_reads_a_nested_key(factory):
    state = json.loads(json.dumps(BENCH_STATE))
    del state["surfaces"][0]["head"]
    state["surfaces"][0]["base"].pop("sha")
    write(factory["board"] / "twins" / "factory-live-24.json", json.dumps(state))

    values = named(data(factory, "pr", "factory-live#24"), "twins")["values"]

    [row] = next(value for value in values if value["type"] == "table")["rows"]
    # base.sha absent, base.poster present, head absent: empty cells.
    assert row[5:] == [None, "", None, None]
