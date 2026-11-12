"""The agents' sessions, read by the runtime's adapter as factory-live reads them (ADR 0012).

The cases are those of factory-live's `sessions.test.ts` at commit d47f235 about `runFacts` and the open sessions,
with their expected values copied out by hand, on the runtime's real schema (tests/fixtures/hermes-schema/state.sql).
"""

import json
from pathlib import Path

import pytest
from workflow_core.adapters.hermes.sessions import SessionReader
from workflow_core.model.facts import RunFact

from conftest import make_session_base

CARD = "t_665ba7d7"
SESSION = "20261029_093652_fc383d"
HOOK_TIMEOUT_MESSAGE = "pre_tool_call plugin callback timed out or is still running"


def run(**fields) -> RunFact:
    values = {
        "id": 191,
        "profile": "coder",
        "status": "blocked",
        "outcome": "blocked",
        "started_at": 1_793_263_010,
        "ended_at": 1_793_263_637,
        "summary": None,
        "metadata": {},
        "error": None,
    }
    return RunFact(**{**values, **fields})


def call(call_id: str, name: str, args: dict) -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class Base:
    """A session base of the agent `coder`, filled the way the factory-live tests fill theirs."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self.db = make_session_base(home, "coder")

    def session(self, session_id: str, started_at: float, **fields) -> None:
        row = {
            "id": session_id,
            "source": "kanban",
            "profile_name": "coder",
            "model": "auto",
            "started_at": started_at,
            "ended_at": started_at + 600,
            "input_tokens": 1200,
            "output_tokens": 80,
            "estimated_cost_usd": 0.01,
            "title": "impl cv-site#49",
            **fields,
        }
        self.insert("sessions", row)

    def message(self, session_id: str, role: str, **fields) -> None:
        self.insert("messages", {"session_id": session_id, "role": role, "timestamp": 1_000, **fields})

    def insert(self, table: str, row: dict) -> None:
        self.db.execute(
            f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values())
        )


@pytest.fixture
def base(tmp_path) -> Base:
    base = Base(tmp_path)
    base.session(SESSION, 1_793_263_013.77, title="impl factory-live#2 #3")
    base.message(SESSION, "user", content=f"work kanban task {CARD}", timestamp=1_793_263_013.5)
    return base


@pytest.fixture
def reader(base) -> SessionReader:
    reader = SessionReader(base.home)
    yield reader
    reader.close()


def resolved(reader: SessionReader, runs: list[RunFact]) -> list[tuple[str, int | None]] | None:
    found = reader.run_facts("coder", CARD, runs)
    return None if found is None else [(facts.id, facts.run_id) for facts in found]


def test_a_run_is_attached_to_its_session_by_window_confirmed_by_its_first_message(base, reader):
    # Another card launched at the same time is not taken.
    base.session("20261029_093700_aaaaaa", 1_793_263_020)
    base.message("20261029_093700_aaaaaa", "user", content="work kanban task t_00000000", timestamp=1_793_263_020)

    assert resolved(reader, [run()]) == [(SESSION, 191)]
    # Outside the run's window: no session, never a neighbouring one.
    assert resolved(reader, [run(started_at=1_793_270_000)]) == []


def test_the_noted_session_comes_first_then_the_resume_session(base, reader):
    base.session("20261029_101049_0612ab", 1_793_265_050)
    runs = [
        run(id=192, metadata={"worker_session_id": "20261029_101049_0612ab"}),
        run(
            id=193,
            metadata={"worker_session_id": "inconnue"},
            error="Resume with: hermes --resume 20261029_101049_0612ab -p coder",
        ),
    ]

    assert resolved(reader, runs) == [("20261029_101049_0612ab", 192)]
    # Without a noted session, the one `hermes --resume` names in the run's error, then in its output.
    assert resolved(reader, [runs[1]]) == [("20261029_101049_0612ab", 193)]
    output = run(id=194, metadata={"worker_output": "hermes --resume 20261029_101049_0612ab -p coder"})
    assert resolved(reader, [output]) == [("20261029_101049_0612ab", 194)]


def test_two_matching_sessions_give_no_session(base, reader):
    base.session("20261029_093653_bbbbbb", 1_793_263_014)
    base.message("20261029_093653_bbbbbb", "user", content=f"work kanban task {CARD}", timestamp=1_793_263_014)

    assert resolved(reader, [run()]) == []


def test_calls_and_results_keep_the_telling_argument_and_both_ends(base, reader):
    base.message(
        SESSION,
        "assistant",
        timestamp=1_793_263_100,
        tool_calls=json.dumps(
            [
                call("call_1", "terminal", {"command": 'git commit -m "feat: version"', "timeout": 600}),
                call("call_2", "write_file", {"path": "apps/web/src/a.ts", "content": "x" * 5_000}),
            ]
        ),
    )
    refusal = json.dumps({"error": HOOK_TIMEOUT_MESSAGE})
    base.message(SESSION, "tool", timestamp=1_793_263_131, tool_call_id="call_1", tool_name="terminal", content=refusal)
    long_output = (
        '{"output": "' + "ligne\\n" * 2_000 + '[Command timed out after 900s]", "exit_code": 124, "error": null}'
    )
    base.message(
        SESSION, "tool", timestamp=1_793_263_132, tool_call_id="call_2", tool_name="write_file", content=long_output
    )
    # Unreadable calls do not break the reading.
    base.message(SESSION, "assistant", timestamp=1_793_263_140, tool_calls='[{"id": "call_3"')
    base.message(SESSION, "assistant", timestamp=1_793_263_141, tool_calls='{"id": "call_4"}')

    [facts] = reader.run_facts("coder", CARD, [run()])

    assert [call.model_dump() for call in facts.calls] == [
        {"at": 1_793_263_100, "tool": "terminal", "input": 'git commit -m "feat: version"', "call_id": "call_1"},
        {"at": 1_793_263_100, "tool": "write_file", "input": "apps/web/src/a.ts", "call_id": "call_2"},
    ]
    # The exact refusal stays whole; a long result keeps its first 1 500 and last 500 characters.
    assert facts.results[0].content == refusal
    assert (facts.results[0].tool, facts.results[0].call_id) == ("terminal", "call_1")
    long = facts.results[1].content
    assert long == f"{long_output[:1_500]}\n…\n{long_output[-500:]}"
    assert len(long) < 2_100
    assert long.endswith('[Command timed out after 900s]", "exit_code": 124, "error": null}')
    assert facts.results[1].call_id == "call_2"


def test_secrets_of_commands_and_results_are_masked(base, reader):
    token = "ghp_" + "a" * 36
    base.message(
        SESSION,
        "assistant",
        timestamp=1_793_263_100,
        tool_calls=json.dumps([call("c1", "terminal", {"command": f"GH_TOKEN={token} gh pr view"})]),
    )
    base.message(
        SESSION,
        "tool",
        timestamp=1_793_263_101,
        tool_call_id="c1",
        tool_name="terminal",
        content=f'{{"output": "{token}", "exit_code": 0}}',
    )

    [facts] = reader.run_facts("coder", CARD, [run()])

    assert token not in facts.model_dump_json()
    # factory-live's own output: the token is masked, then the `VARIABLE=` rule masks the mask's first word again.
    assert facts.calls[0].input == "GH_TOKEN=[secret masqué] masqué] gh pr view"


def test_open_sessions_at_an_instant_include_a_never_closed_one(base, reader):
    base.session("20261029_101049_0612ab", 1_793_265_050, ended_at=None)

    assert reader.open_sessions("coder", 1_793_263_100) == [SESSION]
    assert reader.open_sessions("coder", 1_793_265_100) == ["20261029_101049_0612ab"]
    assert reader.open_sessions("coder", 1_793_200_000) == []


def test_a_missing_base_an_unknown_or_unsafe_agent_gives_none(base, reader):
    for agent in ("unknown", "../coder", "coder/..", ".."):
        assert reader.run_facts(agent, CARD, [run()]) is None, agent
        assert reader.open_sessions(agent, 1_793_263_100) is None, agent
    # A base that is a link reads nothing either.
    (base.home / "profiles" / "linked").mkdir()
    (base.home / "profiles" / "linked" / "state.db").symlink_to(base.home / "profiles" / "coder" / "state.db")
    assert reader.open_sessions("linked", 1_793_263_100) is None


def test_reading_leaves_the_session_base_unchanged(base):
    base.db.close()
    path = base.home / "profiles" / "coder" / "state.db"
    before = path.read_bytes()
    reader = SessionReader(base.home)

    reader.run_facts("coder", CARD, [run()])
    reader.open_sessions("coder", 1_793_263_100)
    reader.close()

    assert path.read_bytes() == before


def test_the_session_base_is_read_from_any_thread(base, reader):
    import threading

    first = (reader.open_sessions("coder", 1_793_263_100), resolved(reader, [run()]))
    other: list = []
    thread = threading.Thread(
        target=lambda: other.append((reader.open_sessions("coder", 1_793_263_100), resolved(reader, [run()])))
    )
    thread.start()
    thread.join()

    assert first == ([SESSION], [(SESSION, 191)])
    assert other == [first]


def test_no_connection_is_kept_after_a_reading(base):
    base.db.close()
    path = base.home / "profiles" / "coder" / "state.db"
    before = path.read_bytes()
    reader = SessionReader(base.home)

    assert reader.run_facts("coder", CARD, [run()]) is not None
    assert reader.open_sessions("coder", 1_793_263_100) == [SESSION]

    assert not vars(reader).get("_open")
    assert path.read_bytes() == before
