"""The rules of the runs, ported from factory-live (ADR 0012): an exit without a kanban call, after refusals or not,
the breaker, the turn budget, the run's maximum duration, the tool ceiling, a command stopped by its delay, a worker
that does not start.

The expected findings are the reference outputs of tests/fixtures/diagnosis/ and the expected values of
factory-live's `rules.test.ts` at commit d47f235, copied out by hand. The assembly (primary cause, run verdicts,
unblocking advice) is ported later: these tests look at the findings of the rules.
"""

from workflow_core.model.facts import EventFact, RunFact

from conftest import diagnosis_input_of, reference_cases
from test_rules_guards import T0, an_input, card, coder_profile, found, of_code, session

CODES = {
    "stopped_after_refusals",
    "stopped_without_kanban_call",
    "protocol_breaker",
    "iteration_budget",
    "runtime_limit",
    "tool_deadline",
    "command_timeout",
    "spawn_failed",
}


def run(**fields) -> RunFact:
    values = {
        "id": 1,
        "profile": "coder",
        "status": "running",
        "outcome": "blocked",
        "started_at": T0,
        "ended_at": T0 + 600,
        "summary": None,
        "metadata": {},
        "error": None,
    }
    return RunFact(**{**values, **fields})


def event(event_id: int, kind: str, created_at: int, run_id: int | None = None, **payload) -> EventFact:
    return EventFact(id=event_id, run_id=run_id, kind=kind, payload=payload, created_at=created_at)


def test_run_findings_match_the_reference_outputs():
    compared = []
    for case in reference_cases():
        output = case["output"]
        expected = [finding for finding in [output["primary"], *output["others"]] if finding["code"] in CODES]
        if not expected:
            continue
        ours = found(diagnosis_input_of(case["input"]))
        for finding in expected:
            assert of_code(ours, finding["code"]) == finding, (case["case"], finding["code"])
            compared.append(f"{case['case']}:{finding['code']}")

    # The incidents where factory-live raises one of these rules, listed: the comparison is not empty.
    assert compared == [
        "breaker:stopped_without_kanban_call",
        "budget:iteration_budget",
        "hookTimeout:stopped_without_kanban_call",
        "spawn:spawn_failed",
    ]


def test_the_runtime_limit_is_read_in_the_run_error_or_in_its_timeout_event():
    from_error = an_input(
        card=card(block_kind=None, max_runtime_seconds=7200),
        runs=[run(outcome="timed_out", error="elapsed 7300s > limit 7200s")],
    )
    finding = of_code(found(from_error), "runtime_limit")
    assert finding["cause"] == {"state": "present"}
    assert finding["why"]["params"] == {"seconds": 7300, "limit": 7200}

    raised = an_input(
        card=card(block_kind=None, max_runtime_seconds=10_000),
        events=[
            event(1, "created", T0 - 10),
            event(5, "timed_out", T0 + 500, run_id=1, elapsed_seconds=7300, limit_seconds=7200),
            event(10, "blocked", T0 + 600, run_id=1),
        ],
    )
    assert of_code(found(raised), "runtime_limit")["cause"] == {"state": "unknown"}


DEADLINE = [
    session(1, [("terminal", "npm run e2e", "Error executing tool 'terminal': timed out after 420.0s", 100, 420)])
]


def test_a_tool_deadline_is_present_fixed_afterwards_or_unknown_without_config():
    present = of_code(found(an_input(sessions=DEADLINE)), "tool_deadline")
    assert present["cause"] == {"state": "present"}
    assert (present["setting"]["key"], present["setting"]["observed"], present["setting"]["effective"]) == (
        "timeouts.tools.sequential_call",
        420,
        420,
    )
    assert present["steps"][0]["command"] == "hermes -p coder config set timeouts.tools.sequential_call 900"

    fixed = of_code(
        found(an_input(sessions=DEADLINE, profile=coder_profile(tool_deadline=900, changed_at=T0 + 700))),
        "tool_deadline",
    )
    assert fixed["cause"]["state"] == "fixed"
    assert fixed["cause"]["at"] == "2027-01-15T08:11:40.000Z"
    contradiction = found(an_input(sessions=DEADLINE, profile=coder_profile(tool_deadline=900, changed_at=T0)))
    assert of_code(contradiction, "tool_deadline")["cause"] == {"state": "unknown"}
    assert of_code(found(an_input(sessions=DEADLINE, profile=None)), "tool_deadline")["cause"] == {"state": "unknown"}


def test_a_command_stopped_by_the_terminal_timeout_is_for_the_agent():
    stopped = '{"output": "[Command timed out after 900s]", "exit_code": 124, "error": null}'

    finding = of_code(
        found(an_input(sessions=[session(1, [("terminal", "npm run e2e", stopped, 100)])])), "command_timeout"
    )

    assert finding["actor"] == "agent"
    assert finding["summary"]["params"] == {"seconds": 900}


def test_without_duration_the_agent_terminal_timeout_then_the_runtime_default():
    silent = [session(1, [("terminal", "npm run e2e", '{"output": "", "exit_code": 124}', 100)])]

    def seconds(profile):
        return of_code(found(an_input(sessions=silent, profile=profile)), "command_timeout")["summary"]["params"]

    assert seconds(coder_profile(terminal_timeout=600)) == {"seconds": 600}
    assert seconds(coder_profile(terminal_timeout=None)) == {"seconds": 180}


def test_the_breaker_alone_three_exits_without_kanban_call_without_their_runs():
    gave_up = event(10, "gave_up", T0 + 600, protocol_violations=3, error="protocol violation")
    input = an_input(runs=[], events=[event(1, "created", T0 - 10), gave_up], anchor=gave_up)

    finding = of_code(found(input), "protocol_breaker")

    assert finding["summary"]["params"] == {"count": 3}
    assert finding["runIds"] == []


def test_an_exit_after_kanban_calls_in_error_counts_those_errors():
    error = '{"error": "kanban_complete could not preserve the declared artifacts"}'
    input = an_input(
        runs=[run(outcome="crashed", metadata={"protocol_violation": True})],
        sessions=[session(1, [("kanban_complete", None, error, 100), ("kanban_complete", None, error, 200)])],
    )

    codes = [finding["code"] for finding in found(input)]
    finding = of_code(found(input), "stopped_after_refusals")

    assert "stopped_without_kanban_call" not in codes
    assert (finding["evidence"][0]["count"], finding["runIds"]) == (2, [1])
