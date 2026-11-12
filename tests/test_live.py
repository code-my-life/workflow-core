"""The live state of the agents, their activity and their usage, read from the runtime's bases (ADR 0011)."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest
from workflow_core.adapters import runtime_adapter
from workflow_core.config import Workflow

from conftest import make_session_base
from test_events import Stream, board_base, running_service, write

CARD = "t_0a0b0c0d"
SESSION = "20261112_100000_abc123"
OTHER_SESSION = "20261112_110000_def456"
STARTED = 1794477600  # 2026-11-12 10:00:00 UTC
FACTORY = Workflow(key="factory", board="factory", sheet=Path("factory.json"), guard_suffixes=["-guard", "-readonly"])


def terminal_call(call_id: str, command: str) -> str:
    return json.dumps(
        [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": "terminal", "arguments": json.dumps({"command": command})},
            }
        ]
    )


@pytest.fixture
def working(runtime_home, monkeypatch) -> sqlite3.Connection:
    """The agent `coder` holds the running card t_0a0b0c0d, whose current run 3 noted its session; the session base
    is returned open for writing, as the runtime writes it."""
    monkeypatch.setenv("HER" + "MES_TZ", "UTC")
    write(
        board_base(runtime_home, "factory"),
        "INSERT INTO tasks (id, title, status, assignee, created_at, started_at, current_run_id, last_heartbeat_at)"
        " VALUES (?, 'Le direct', 'running', 'coder', ?, ?, 3, ?)",
        CARD,
        STARTED,
        STARTED,
        STARTED + 30,
    )
    write(
        board_base(runtime_home, "factory"),
        "INSERT INTO task_runs (id, task_id, profile, status, started_at, metadata)"
        " VALUES (3, ?, 'coder', 'running', ?, ?)",
        CARD,
        STARTED,
        json.dumps({"worker_session_id": SESSION}),
    )
    base = make_session_base(runtime_home, "coder")
    base.execute(
        "INSERT INTO sessions (id, source, model, started_at) VALUES (?, 'kanban', 'claude-opus', ?)",
        (SESSION, STARTED),
    )
    base.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, 'user', 'work kanban task ' || ?, ?)",
        (SESSION, CARD, STARTED + 1),
    )
    yield base
    base.close()


def journal(home: Path, agent: str, lines: list[str]) -> None:
    logs = home / "profiles" / agent / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    with (logs / "agent.log").open("a", encoding="utf-8") as file:
        file.write("".join(f"{line}\n" for line in lines))


def next_live(stream: Stream, timeout: float = 2) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        _, kind, event = stream.next(timeout=max(0.01, deadline - time.monotonic()))
        if kind == "live":
            return event


def test_a_tool_call_without_result_is_the_current_action_on_the_stream(runtime_home, working):
    with running_service() as base:
        stream = Stream(base)
        time.sleep(0.5)  # the first probe has read the boards and the sessions

        at = STARTED + 40
        working.execute(
            "INSERT INTO messages (session_id, role, tool_calls, timestamp) VALUES (?, 'assistant', ?, ?)",
            (SESSION, terminal_call("c1", "npm test"), at),
        )
        written = time.monotonic()
        event = next_live(stream)
        elapsed = time.monotonic() - written
        stream.close()

    assert (event["workflow"], event["item"]) == ("factory", CARD)
    state = event["state"]
    assert (state["agent"], state["item"], state["run"], state["state"]) == ("coder", CARD, 3, "tool")
    assert state["action"] == {
        "kind": "tool",
        "label": "terminal",
        "detail": "npm test",
        "started_at": "2026-11-12T10:00:40.000Z",
    }
    assert elapsed < 1.0, f"the action took {elapsed:.3f} s to reach the stream"


def test_a_tool_result_then_a_block_change_the_agent_state(runtime_home, working):
    working.execute(
        "INSERT INTO messages (session_id, role, tool_calls, timestamp) VALUES (?, 'assistant', ?, ?)",
        (SESSION, terminal_call("c1", "npm test"), STARTED + 40),
    )
    with running_service() as base:
        stream = Stream(base)
        time.sleep(0.5)

        working.execute(
            "INSERT INTO messages (session_id, role, tool_call_id, tool_name, content, timestamp)"
            " VALUES (?, 'tool', 'c1', 'terminal', '{\"exit_code\": 0, \"output\": \"ok\"}', ?)",
            (SESSION, STARTED + 50),
        )
        thinking = next_live(stream)["state"]
        write(board_base(runtime_home, "factory"), "UPDATE tasks SET status = 'blocked' WHERE id = ?", CARD)
        blocked = next_live(stream)["state"]
        stream.close()

    assert thinking["state"] == "thinking"
    assert thinking["action"]["kind"] == "llm"
    assert thinking["action"]["started_at"] == "2026-11-12T10:00:50.000Z"
    assert blocked["state"] == "blocked"
    assert blocked["action"] is None


def test_the_served_model_comes_from_the_agent_log_else_the_session(runtime_home, working):
    working.execute("UPDATE sessions SET model = 'auto' WHERE id = ?", (SESSION,))
    reader = runtime_adapter().make_live_reader()

    (agent,) = reader.live(FACTORY)
    assert agent.model.model_dump() == {"name": "auto", "provider": None, "source": "session"}

    journal(
        runtime_home,
        "coder",
        [
            f"2026-11-12 10:00:05,000 INFO [{SESSION}] agent.conversation_loop: API call #1: model=auto"
            " provider=freellmapi in=10 out=2 total=12 latency=1.0s id=x1",
            f"2026-11-12 10:00:09,000 INFO [{SESSION}] agent.conversation_loop: API call #2: model=glm-5.3"
            " provider=freellmapi in=10 out=2 total=12 latency=1.0s id=x2",
            f"2026-11-12 10:00:12,000 INFO [{OTHER_SESSION}] agent.conversation_loop: API call #1: model=devstral-2"
            " provider=custom in=10 out=2 total=12 latency=1.0s id=x3",
        ],
    )
    (agent,) = reader.live(FACTORY)
    assert agent.model.model_dump() == {"name": "glm-5.3", "provider": "freellmapi", "source": "live"}

    # A session that names its model is the model served.
    working.execute("UPDATE sessions SET model = 'claude-opus' WHERE id = ?", (SESSION,))
    (agent,) = reader.live(FACTORY)
    assert agent.model.model_dump() == {"name": "claude-opus", "provider": None, "source": "session"}


def test_usage_and_costs_per_model_agent_and_day(runtime_home, working):
    day = STARTED + 3600
    for model, provider, calls, tokens_in, tokens_out, estimated, actual in (
        ("glm-5.3", "freellmapi", 4, 1000, 200, 0.5, 0.7),
        ("claude-opus", "anthropic", 2, 300, 100, 0.2, 0.0),
    ):
        working.execute(
            "INSERT INTO session_model_usage (session_id, model, billing_provider, api_call_count, input_tokens,"
            " output_tokens, estimated_cost_usd, actual_cost_usd, first_seen, last_seen)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (SESSION, model, provider, calls, tokens_in, tokens_out, estimated, actual, STARTED, day),
        )
    journal(
        runtime_home,
        "coder",
        [
            f"2026-11-12 10:30:00,000 WARNING [{SESSION}] agent.conversation_loop: API call failed (attempt 1/3)"
            " error_type=RateLimitError thread=Thread-4 provider=freellmapi base_url=http://127.0.0.1:3001/v1/"
            " model=glm-5.3 summary=HTTP 429: All models rate-limited",
            f"2026-11-12 10:31:00,000 WARNING [{SESSION}] agent.conversation_loop: API call failed (attempt 1/3)"
            " error_type=APIConnectionError thread=Thread-4 provider=freellmapi base_url=http://127.0.0.1:3001/v1/"
            " model=glm-5.3 summary=Connection error",
        ],
    )

    usage = runtime_adapter().make_live_reader().usage(FACTORY, days=7, now=day + 60)

    by_model = {row.model: row for row in usage.models}
    assert by_model["glm-5.3"].model_dump() == {
        "model": "glm-5.3",
        "provider": "freellmapi",
        "calls": 4,
        "input_tokens": 1000,
        "output_tokens": 200,
        "cost_usd": 0.7,
        "rate_limited": 1,
        "errors": 1,
    }
    assert by_model["claude-opus"].cost_usd == 0.2
    assert [row.model_dump() for row in usage.agents] == [{"agent": "coder", "tokens": 1600}]
    assert [row.model_dump() for row in usage.daily] == [{"day": "2026-11-12", "tokens": 1600, "rate_limited": 1}]


def test_a_guard_refusal_is_a_blocked_activity_fact(runtime_home, working):
    refusal = json.dumps({"error": "coder-guard : écriture refusée hors du dossier de la carte"})
    working.execute(
        "INSERT INTO messages (session_id, role, tool_calls, timestamp) VALUES (?, 'assistant', ?, ?)",
        (SESSION, terminal_call("c1", "rm -rf /tmp/x"), STARTED + 40),
    )
    working.execute(
        "INSERT INTO messages (session_id, role, tool_call_id, tool_name, content, timestamp)"
        " VALUES (?, 'tool', 'c1', 'terminal', ?, ?)",
        (SESSION, refusal, STARTED + 41),
    )

    # The bases, their write-ahead logs and the journal; SQLite's shared-memory index (-shm) is not the base.
    coder = runtime_home / "profiles" / "coder"
    files = [
        coder / "state.db",
        coder / "state.db-wal",
        coder / "logs" / "agent.log",
        board_base(runtime_home, "factory"),
    ]
    before = {path: path.read_bytes() for path in files if path.is_file()}
    reader = runtime_adapter().make_live_reader()

    facts = reader.activity(FACTORY, limit=50, now=STARTED + 60)
    reader.live(FACTORY)
    reader.usage(FACTORY, days=7, now=STARTED + 60)

    # The bases read stay the same, byte for byte.
    assert {path: path.read_bytes() for path in before} == before

    (refused,) = [fact for fact in facts if fact.kind in ("tool", "guard")]
    assert (refused.kind, refused.status, refused.agent, refused.item) == ("guard", "blocked", "coder", CARD)
    assert "rm -rf /tmp/x" in refused.text
    assert refused.at == "2026-11-12T10:00:40.000Z"
