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


DAY = 86_400
# Three days: 2026-11-10, 2026-11-11 and 2026-11-12 (the day of STARTED), UTC.
DAYS = ("2026-11-10", "2026-11-11", "2026-11-12")


def usage_row(base: sqlite3.Connection, model: str, provider: str, at: int, *values) -> None:
    """One line of the runtime's usage table: calls, input and output tokens, estimated and actual cost."""
    calls, tokens_in, tokens_out, estimated, actual = values
    base.execute(
        "INSERT INTO session_model_usage (session_id, model, billing_provider, api_call_count, input_tokens,"
        " output_tokens, estimated_cost_usd, actual_cost_usd, first_seen, last_seen)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (f"s-{model}-{at}", model, provider, calls, tokens_in, tokens_out, estimated, actual, at, at),
    )


@pytest.fixture
def two_agents(runtime_home, working) -> dict[str, sqlite3.Connection]:
    """`coder` (the working agent) and `gate`, which holds a card of the board too, each with its usage over three
    days: two models, an actual cost and estimated ones."""
    write(
        board_base(runtime_home, "factory"),
        "INSERT INTO tasks (id, title, status, assignee, created_at)"
        " VALUES ('t_gate0001', 'La gate', 'todo', 'gate', ?)",
        STARTED,
    )
    gate = make_session_base(runtime_home, "gate")
    usage_row(working, "glm-5.3", "freellmapi", STARTED + 3600, 4, 1000, 200, 0.5, 0.7)
    usage_row(working, "glm-5.3", "freellmapi", STARTED - 2 * DAY, 1, 100, 10, 0.1, 0.0)
    usage_row(working, "claude-opus", "anthropic", STARTED - DAY, 2, 300, 100, 0.2, 0.0)
    usage_row(gate, "glm-5.3", "freellmapi", STARTED - 2 * DAY + 60, 3, 500, 50, 0.3, 0.4)
    yield {"coder": working, "gate": gate}
    gate.close()


def failed_call(at: str, error: str, model: str, summary: str) -> str:
    return (
        f"{at},000 WARNING [{SESSION}] agent.conversation_loop: API call failed (attempt 1/3) error_type={error}"
        f" thread=Thread-4 provider=freellmapi base_url=http://127.0.0.1:3001/v1/ model={model} summary={summary}"
    )


def rows_of(usage) -> list[dict]:
    return [row.model_dump() for row in usage.rows]


def row(day, agent, model, provider, calls, tokens_in, tokens_out, cost, rate_limited=0, errors=0) -> dict:
    return {
        "day": day,
        "agent": agent,
        "model": model,
        "provider": provider,
        "calls": calls,
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "cost_usd": cost,
        "rate_limited": rate_limited,
        "errors": errors,
    }


def test_usage_rows_per_day_model_and_agent(runtime_home, two_agents):
    usage = runtime_adapter().make_live_reader().usage(FACTORY, days=7, now=STARTED + 3600 + 60)

    assert rows_of(usage) == [
        row(DAYS[0], "coder", "glm-5.3", "freellmapi", 1, 100, 10, 0.1),
        row(DAYS[0], "gate", "glm-5.3", "freellmapi", 3, 500, 50, 0.4),
        row(DAYS[1], "coder", "claude-opus", "anthropic", 2, 300, 100, 0.2),
        row(DAYS[2], "coder", "glm-5.3", "freellmapi", 4, 1000, 200, 0.7),
    ]


def test_usage_rows_count_refused_and_failed_calls(runtime_home, two_agents):
    journal(
        runtime_home,
        "coder",
        [
            # A 429 and a failed call on a day and a model the agent has usage for.
            failed_call("2026-11-12 10:30:00", "RateLimitError", "glm-5.3", "HTTP 429: All models rate-limited"),
            failed_call("2026-11-12 10:31:00", "APIConnectionError", "glm-5.3", "Connection error"),
            # A 429 on a model without any usage line: it has its own line, without calls or cost.
            failed_call("2026-11-11 09:00:00", "RateLimitError", "qwen-x", "HTTP 429: rate-limited"),
        ],
    )

    usage = runtime_adapter().make_live_reader().usage(FACTORY, days=7, now=STARTED + 3600 + 60)

    rows = rows_of(usage)
    assert row(DAYS[2], "coder", "glm-5.3", "freellmapi", 4, 1000, 200, 0.7, rate_limited=1, errors=1) in rows
    assert row(DAYS[1], "coder", "qwen-x", "freellmapi", 0, 0, 0, None, rate_limited=1) in rows
    assert len(rows) == 5


def test_usage_rows_sum_to_the_aggregates(runtime_home, two_agents):
    journal(
        runtime_home,
        "coder",
        [
            failed_call("2026-11-12 10:30:00", "RateLimitError", "glm-5.3", "HTTP 429: All models rate-limited"),
            failed_call("2026-11-11 09:00:00", "RateLimitError", "qwen-x", "HTTP 429: rate-limited"),
            failed_call("2026-11-10 09:00:00", "APIConnectionError", "claude-opus", "Connection error"),
        ],
    )
    journal(
        runtime_home,
        "gate",
        [failed_call("2026-11-10 12:00:00", "RateLimitError", "glm-5.3", "HTTP 429: rate-limited")],
    )

    for days in (1, 2, 7):
        usage = runtime_adapter().make_live_reader().usage(FACTORY, days=days, now=STARTED + 3600 + 60)
        assert usage.rows, days

        models: dict[tuple[str, str], dict] = {}
        agents: dict[str, int] = {}
        daily: dict[str, list[int]] = {}
        for line in usage.rows:
            total = models.setdefault(
                (line.model, line.provider),
                {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost": None, "rate_limited": 0, "errors": 0},
            )
            for field in ("calls", "input_tokens", "output_tokens", "rate_limited", "errors"):
                total[field] += getattr(line, field)
            if line.cost_usd is not None:
                total["cost"] = (total["cost"] or 0.0) + line.cost_usd
            tokens = line.input_tokens + line.output_tokens
            if line.calls or tokens:
                agents[line.agent] = agents.get(line.agent, 0) + tokens
            if tokens or line.rate_limited:
                day = daily.setdefault(line.day, [0, 0])
                day[0] += tokens
                day[1] += line.rate_limited

        assert {
            (usage_.model, usage_.provider): {
                "calls": usage_.calls,
                "input_tokens": usage_.input_tokens,
                "output_tokens": usage_.output_tokens,
                "cost": None if usage_.cost_usd is None else round(usage_.cost_usd, 6),
                "rate_limited": usage_.rate_limited,
                "errors": usage_.errors,
            }
            for usage_ in usage.models
        } == {
            key: {**total, "cost": None if total["cost"] is None else round(total["cost"], 6)}
            for key, total in models.items()
        }, days
        assert {agent.agent: agent.tokens for agent in usage.agents} == agents, days
        assert {day.day: [day.tokens, day.rate_limited] for day in usage.daily} == daily, days
