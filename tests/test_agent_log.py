"""The agents' journals, read by the runtime's adapter as factory-live reads them (ADR 0012).

The expected facts come from factory-live at commit d47f235: `agentLog.test.ts` copied out by hand, and the
`journal.facts` of the 13 reference outputs (tests/fixtures/diagnosis/), never computed by the core's code.
"""

import json
from datetime import datetime
from pathlib import Path

import pytest
from workflow_core.adapters.hermes.agent_log import AgentLogReader, zoned_epoch

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "diagnosis"
PARIS = "Europe/Paris"
SESSION = "20261029_093652_fc383d"
DASH = "—"

# The lines of factory-live#2, as the runtime wrote them (Paris time, no time zone).
TIMEOUT = (
    f"2026-10-29 09:46:10,160 WARNING [{SESSION}] hermes_cli.plugins: Hook 'pre_tool_call' callback pre_tool "
    f"timed out after 30s {DASH} skipping"
)
EXECUTOR = f"2026-10-29 09:46:10,161 INFO [{SESSION}] agent.tool_executor: tool terminal completed (30.01s, 72 chars)"
HTTP = "2026-10-29 09:46:11,000 INFO httpx2: HTTP Request: POST http://127.0.0.1:8081/mcp"
ALL = (0, 4_000_000_000)


def utc(text: str) -> float:
    """JavaScript's `Date.parse(text) / 1000` for an ISO time in UTC: whole milliseconds, divided once."""
    moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return round(moment.timestamp() * 1000) / 1000


def bare(time: str) -> str:
    return (
        f"2026-10-29 {time},000 WARNING hermes_cli.plugins: Hook 'pre_tool_call' callback guard timed out after 30s "
        f"{DASH} skipping"
    )


@pytest.fixture
def home(tmp_path) -> Path:
    (tmp_path / "profiles" / "coder" / "logs").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def logs(home) -> Path:
    return home / "profiles" / "coder" / "logs"


@pytest.fixture
def reader(home) -> AgentLogReader:
    return AgentLogReader(home, PARIS)


def fact_json(fact) -> dict:
    """A fact with factory-live's JSON keys."""
    data = fact.model_dump()
    data["sessionId"] = data.pop("session_id")
    return data


def test_the_13_incident_journals_give_the_facts_of_factory_live(tmp_path):
    cases = sorted(path for path in FIXTURES.glob("*.json") if path.name != "source.json")
    assert len(cases) == 13
    for case in cases:
        journal = json.loads(case.read_text(encoding="utf-8"))["journal"]
        home = tmp_path / case.stem
        folder = home / "profiles" / "agent" / "logs"
        folder.mkdir(parents=True)
        (folder / "agent.log").write_text("".join(f"{line}\n" for line in journal["lines"]), encoding="utf-8")

        facts = AgentLogReader(home, journal["timeZone"]).facts("agent", float("-inf"), float("inf"))

        assert [fact_json(fact) for fact in facts or []] == journal["facts"], case.stem


def test_only_catalogued_lines_are_kept_dated_in_the_runtime_time_zone(reader, logs):
    (logs / "agent.log").write_text(f"{HTTP}\n{TIMEOUT}\n{EXECUTOR}\nTraceback (most recent call last):\n")

    facts = reader.facts("coder", *ALL)

    assert [(f.kind, f.callback, f.seconds, f.session_id, f.at) for f in facts] == [
        ("hook_timeout", "pre_tool", 30, SESSION, utc("2026-10-29T08:46:10.160Z"))
    ]
    assert facts[0].hook == "pre_tool_call"
    assert facts[0].line == f"Hook 'pre_tool_call' callback pre_tool timed out after 30s {DASH} skipping"
    assert reader.facts("coder", 0, utc("2026-10-29T08:00:00Z")) == []


def test_a_reread_resumes_where_it_stopped_with_an_unfinished_line(reader, logs):
    path = logs / "agent.log"
    path.write_text(f"{TIMEOUT}\n{TIMEOUT[:40]}")
    assert len(reader.facts("coder", *ALL)) == 1
    with path.open("a") as file:
        file.write(f"{TIMEOUT[40:]}\n")
    assert len(reader.facts("coder", *ALL)) == 2
    with path.open("a") as file:
        file.write(TIMEOUT.replace("09:46:10", "09:47:00") + "\n")

    assert [fact.at for fact in reader.facts("coder", *ALL)] == [
        utc("2026-10-29T08:46:10.160Z"),
        utc("2026-10-29T08:46:10.160Z"),
        utc("2026-10-29T08:47:00.160Z"),
    ]


def test_rotation_reads_the_old_file_then_the_new_one(reader, logs):
    path = logs / "agent.log"
    path.write_text(f"{TIMEOUT}\n")
    assert len(reader.facts("coder", *ALL)) == 1
    path.rename(logs / "agent.log.1")
    path.write_text(TIMEOUT.replace("09:46:10", "10:00:00") + "\n")

    assert [fact.at for fact in reader.facts("coder", *ALL)] == [
        utc("2026-10-29T08:46:10.160Z"),
        utc("2026-10-29T09:00:00.160Z"),
    ]


def test_a_line_without_session_takes_the_tool_call_within_two_seconds_never_beyond(reader, logs):
    lines = [
        bare("09:00:00"),
        f"2026-10-29 09:00:01,500 INFO [{SESSION}] agent.tool_executor: tool terminal completed (30.0s, 1 chars)",
        bare("09:10:00"),
        HTTP.replace("09:46:11", "09:10:05"),
        bare("09:20:00"),
    ]
    (logs / "agent.log").write_text("\n".join(lines) + "\n")

    assert [fact.session_id for fact in reader.facts("coder", *ALL)] == [SESSION, None, None]


def test_otherwise_a_line_takes_the_only_open_session_of_the_agent(home, logs):
    (logs / "agent.log").write_text(f"{bare('09:00:00')}\n{bare('10:00:00')}\n{bare('11:00:00')}\n")
    nine = utc("2026-10-29T08:00:00Z")
    ten = utc("2026-10-29T09:00:00Z")
    asked: list[float] = []

    def open_sessions(agent: str, at: float):
        asked.append(at)
        # One session at 9, two at 10, none at 11: never a guess.
        if agent != "coder":
            return None
        return [SESSION] if at == nine else ["a", "b"] if at == ten else []

    reader = AgentLogReader(home, PARIS, open_sessions)

    assert [fact.session_id for fact in reader.facts("coder", *ALL)] == [SESSION, None, None]
    # The answer is kept: reading the journal again does not ask the sessions again.
    reader.facts("coder", *ALL)
    assert len(asked) == 3


def test_an_agent_without_journal_gives_none(reader):
    assert reader.facts("unknown", *ALL) is None


def test_an_unsafe_agent_name_or_a_symlink_reads_nothing(home, logs, tmp_path):
    (logs / "agent.log").write_text(f"{TIMEOUT}\n")
    reader = AgentLogReader(home, PARIS)
    for name in ("../coder", "coder/..", "..", "co der", "coder/logs"):
        assert reader.facts(name, *ALL) is None, name

    outside = tmp_path / "elsewhere.log"
    outside.write_text(f"{TIMEOUT}\n")
    linked = home / "profiles" / "linked" / "logs"
    linked.mkdir(parents=True)
    (linked / "agent.log").symlink_to(outside)
    assert reader.facts("linked", *ALL) is None
    # A link on the way (the agent's folder itself) reads nothing either.
    (home / "profiles" / "alias").symlink_to(home / "profiles" / "coder")
    assert reader.facts("alias", *ALL) is None


def test_the_ambiguous_autumn_hour_follows_the_journal_order():
    # 2026-10-25: 03:00 CEST turns back to 02:00 CET; 02:30 exists twice.
    first = zoned_epoch("2026-10-25 02:30:00", 0, PARIS, None)
    assert first == utc("2026-10-25T00:30:00Z")
    second = zoned_epoch("2026-10-25 02:30:00", 0, PARIS, first + 1_800)
    assert second == utc("2026-10-25T01:30:00Z")
    assert zoned_epoch("2026-10-25 03:30:00", 0, PARIS, second) == utc("2026-10-25T02:30:00Z")


def test_a_skipped_spring_hour_keeps_the_old_offset_and_an_unknown_format_is_refused():
    assert zoned_epoch("2026-03-29 02:30:00", 0, PARIS) == utc("2026-03-29T01:30:00Z")
    assert zoned_epoch("2026-07-14 12:00:00", 250, PARIS) == utc("2026-07-14T10:00:00.250Z")
    assert zoned_epoch("29/10/2026 09:46:10", 0, PARIS) is None


def test_a_line_without_session_takes_the_only_session_open_in_the_session_base(home, logs):
    from workflow_core.adapters.hermes.agent_log import make_agent_log_reader

    from conftest import make_session_base

    base = make_session_base(home, "coder")
    # Open from 08:59 Paris time (07:59 UTC) to 09:30, and nothing at 10:00.
    for row in ((SESSION, utc("2026-10-29T07:59:00Z"), utc("2026-10-29T08:30:00Z")),):
        base.execute("INSERT INTO sessions (id, source, started_at, ended_at) VALUES (?, 'kanban', ?, ?)", row)
    base.close()
    (logs / "agent.log").write_text(f"{bare('09:00:00')}\n{bare('10:00:00')}\n")

    reader = make_agent_log_reader({"HERMES_HOME": str(home), "HERMES_TZ": PARIS})

    assert [fact.session_id for fact in reader.facts("coder", *ALL)] == [SESSION, None]
