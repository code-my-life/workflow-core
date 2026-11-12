"""The rules of the guards and the hooks, ported from factory-live (ADR 0012).

The expected findings are the reference outputs of tests/fixtures/diagnosis/ (factory-live's TypeScript diagnosis on
13 real incidents) and the expected values of factory-live's `rules.test.ts` at commit d47f235, copied out by hand.
The assembly of the diagnosis (primary cause, verdicts of the runs, unblocking advice) is ported later: these tests
look at the findings of the rules.
"""

from pathlib import Path

from workflow_core.adapters.hermes.texts import TEXTS
from workflow_core.config import Workflow, load_workflows
from workflow_core.diagnosis.episode import Episode
from workflow_core.diagnosis.findings import finding_view
from workflow_core.diagnosis.rules import findings_of
from workflow_core.model.diagnosis import diagnosis_json
from workflow_core.model.facts import (
    CardDetail,
    DiagnosisInput,
    EventFact,
    LogFact,
    PluginHook,
    ProfileFacts,
    RunFact,
    SessionFacts,
    ToolCallFact,
    ToolResultFact,
)

from conftest import diagnosis_input_of, reference_cases

ROOT = Path(__file__).resolve().parent.parent
FACTORY = load_workflows(ROOT / "config" / "workflows.toml")["factory"]
CODES = {
    "guard_hook_timeout",
    "guard_hook_hung",
    "guard_hook_raised",
    "plugin_load_failed",
    "refusal_loop",
    "guard_refusal",
}
T0 = 1_800_000_000

# The hooks of the factory's guards, as factory-live's `FACTORY_HOOKS` (incidents.fixture-builder.test-helpers.ts).
FACTORY_HOOKS = [
    PluginHook(
        plugin="coder-guard",
        hook="pre_tool_call",
        callback="pre_tool",
        source='ctx.register_hook("pre_tool_call", pre_tool)',
    ),
    PluginHook(
        plugin="coder-guard",
        hook="post_tool_call",
        callback="post_tool",
        source='ctx.register_hook("post_tool_call", post_tool)',
    ),
    PluginHook(
        plugin="coder-guard", hook="pre_llm_call", callback="inject", source='ctx.register_hook("pre_llm_call", inject)'
    ),
    PluginHook(
        plugin="comment-guard",
        hook="pre_tool_call",
        callback="guard",
        source='ctx.register_hook("pre_tool_call", guard)',
    ),
    PluginHook(
        plugin="gate-readonly",
        hook="pre_tool_call",
        callback="guard",
        source='ctx.register_hook("pre_tool_call", guard)',
    ),
    PluginHook(
        plugin="merge-guard", hook="pre_tool_call", callback="guard", source='ctx.register_hook("pre_tool_call", guard)'
    ),
]


def coder_profile(**fields) -> ProfileFacts:
    """coder's configuration before the fix of 2026-10-29: no hook delay, 30 s by default (`coderProfile`)."""
    values = {
        "profile": "coder",
        "hook_timeout": None,
        "tool_deadline": None,
        "terminal_timeout": 900,
        "max_turns": 400,
        "changed_at": 1_793_236_882,  # 2026-10-29T01:21:22Z
        "repo_read": True,
        "repo_hook_timeout": None,
        "plugins": ["coder-guard", "comment-guard", "factory-notify"],
        "hooks": FACTORY_HOOKS,
        "longest_commands": {},
    }
    return ProfileFacts(**{**values, **fields})


def card(**fields) -> CardDetail:
    values = {
        "id": "t_1",
        "title": "Carte",
        "body": "",
        "status": "blocked",
        "assignee": "coder",
        "created_at": T0 - 10,
        "started_at": None,
        "completed_at": None,
        "block_kind": "capability",
        "current_run_id": None,
        "workspace_path": None,
        "skills": [],
        "last_heartbeat_at": None,
        "branch_name": None,
        "consecutive_failures": 0,
        "block_recurrences": 0,
        "last_failure_error": None,
        "max_runtime_seconds": None,
        "idempotency_key": None,
        "model_override": None,
        "provider_override": None,
    }
    return CardDetail(**{**values, **fields})


def an_input(**fields) -> DiagnosisInput:
    """A card blocked after a run of coder, from T0 to T0 + 600; each test gives its facts (`input` of
    rules.test.ts)."""
    blocked = EventFact(id=10, run_id=1, kind="blocked", payload={"kind": "capability"}, created_at=T0 + 600)
    run = RunFact(
        id=1,
        profile="coder",
        status="running",
        outcome="blocked",
        started_at=T0,
        ended_at=T0 + 600,
        summary=None,
        metadata={},
        error=None,
    )
    values = {
        "now": T0 + 610,
        "board": "factory",
        "card": card(),
        "runs": [run],
        "events": [EventFact(id=1, run_id=None, kind="created", payload={}, created_at=T0 - 10), blocked],
        "anchor": blocked,
        "sessions": [],
        "logs": [],
        "profile": coder_profile(),
    }
    return DiagnosisInput(**{**values, **fields})


def session(run_id: int, steps: list[tuple]) -> SessionFacts:
    """A session: each step is a call and its result, at the given seconds after T0."""
    return SessionFacts(
        id=f"s_{run_id}",
        run_id=run_id,
        calls=[
            ToolCallFact(at=T0 + at, tool=tool, input=command, call_id=f"c{run_id}_{index}")
            for index, (tool, command, _, at, *_) in enumerate(steps)
        ],
        results=[
            ToolResultFact(
                at=T0 + at + (rest[0] if rest else 1), tool=tool, call_id=f"c{run_id}_{index}", content=content
            )
            for index, (tool, _, content, at, *rest) in enumerate(steps)
        ],
    )


def log(kind: str, at: int, **fields) -> LogFact:
    values = {
        "at": T0 + at,
        "session_id": None,
        "kind": kind,
        "hook": None,
        "callback": None,
        "module": None,
        "seconds": None,
        "plugin": None,
        "detail": None,
        "count": None,
        "line": kind,
    }
    return LogFact(**{**values, **fields})


def found(input: DiagnosisInput, workflow: Workflow = FACTORY) -> list[dict]:
    """The findings of the ported rules, in the contract's form (camelCase keys)."""
    return [diagnosis_json(finding_view(finding)) for finding in findings_of(Episode(input, TEXTS, workflow))]


def of_code(findings: list[dict], code: str) -> dict:
    [finding] = [item for item in findings if item["code"] == code]
    return finding


def test_guard_and_hook_findings_match_the_reference_outputs():
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
        "hookTimeout:guard_hook_timeout",
        "prChecks:refusal_loop",
        "prInfra:refusal_loop",
        "refusalLoop:refusal_loop",
    ]


def test_a_guard_whose_abandoned_calls_still_run_is_named_by_its_module():
    hung = log(
        "hook_abandoned", 300, hook="pre_tool_call", callback="guard", module="hermes_plugins.gate_readonly", count=3
    )

    finding = of_code(found(an_input(logs=[hung])), "guard_hook_hung")

    assert (finding["actor"], finding["runIds"]) == ("you_factory", [1])
    assert finding["why"]["params"] == {"plugin": "gate-readonly", "workers": 3}
    assert [item["text"]["code"] for item in finding["steps"]] == [
        "diagnosis.step.fixPlugin",
        "diagnosis.step.reinstall",
    ]


def test_a_crashing_guard_is_found_by_its_callback():
    raised = '{"error": "pre_tool_call plugin callback guard raised KeyError: \'args\'"}'
    input = an_input(
        profile=coder_profile(plugins=["comment-guard"]),
        sessions=[session(1, [("terminal", "gh pr view 3", raised, 100)])],
    )

    finding = of_code(found(input), "guard_hook_raised")

    assert finding["subject"]["plugin"] == "comment-guard"
    assert finding["confidence"] == "certain"


def test_a_plugin_that_does_not_load_at_agent_start():
    failed = log("plugin_load_failed", 2, plugin="factory-notify", detail="No module named 'factory_issue'")

    finding = of_code(found(an_input(logs=[failed])), "plugin_load_failed")

    assert finding["cause"] == {"state": "present"}
    assert finding["summary"]["params"] == {"plugin": "factory-notify"}


def test_a_refusal_ending_the_run_without_loop_is_for_the_agent():
    steps = [
        ("terminal", "npm test", '{"output": "ok", "exit_code": 0, "error": null}', 50),
        (
            "terminal",
            "git push origin main",
            '{"error": "coder-guard : push direct sur main interdit : branche + PR."}',
            100,
        ),
    ]
    input = an_input(card=card(block_kind=None), sessions=[session(1, steps)])

    finding = of_code(found(input), "guard_refusal")

    assert (finding["actor"], finding["subject"]["plugin"]) == ("agent", "coder-guard")
    assert finding["runIds"] == [1]


def test_guards_are_recognised_only_by_the_declared_suffixes():
    refusal = '{"error": "coder-guard : commit refusé : les tests échouent."}'
    looping = an_input(sessions=[session(1, [("terminal", "git commit", refusal, at) for at in (100, 200, 300)])])
    assert of_code(found(looping), "refusal_loop")["evidence"][0]["count"] == 3

    without = FACTORY.model_copy(update={"guard_suffixes": []})
    codes = [finding["code"] for finding in found(looping, without)]
    assert "refusal_loop" not in codes
    assert "guard_refusal" not in codes


def test_the_repo_step_follows_the_declared_profiles_folder():
    timeout = '{"error": "pre_tool_call plugin callback timed out or is still running"}'
    input = an_input(sessions=[session(1, [("terminal", "git commit -m x", timeout, 100, 30)])])

    for folder, path in (
        (FACTORY, "profiles/coder/config.yaml"),
        (FACTORY.model_copy(update={"profiles": "hermes/profiles"}), "hermes/profiles/coder/config.yaml"),
    ):
        finding = of_code(found(input, folder), "guard_hook_timeout")
        report = finding["steps"][1]
        assert report["text"] == {"code": "diagnosis.step.reportInRepo", "params": {"path": path}}
    # Without a declared folder, the runtime's default.
    studio = Workflow(key="studio", board="studio", sheet=Path("s.json"))
    assert of_code(found(input, studio), "guard_hook_timeout")["steps"][1]["text"]["params"]["path"] == (
        "hermes/profiles/coder/config.yaml"
    )


def _guard_facts(profile: str, **patch) -> ProfileFacts:
    """The facts of factory-live's `coderProfile` (packages/domain/src/diagnosis/, file
    incidents.fixture-builder.test-helpers.ts at release/0.12.0), with the longest commands of `guards.test.ts`."""
    hooks = [
        PluginHook(plugin=plugin, hook=hook, callback=callback, source=f'ctx.register_hook("{hook}", {callback})')
        for plugin, hook, callback in (
            ("coder-guard", "pre_tool_call", "pre_tool"),
            ("coder-guard", "post_tool_call", "post_tool"),
            ("coder-guard", "pre_llm_call", "inject"),
            ("comment-guard", "pre_tool_call", "guard"),
            ("gate-readonly", "pre_tool_call", "guard"),
            ("merge-guard", "pre_tool_call", "guard"),
        )
    ]
    fields = {
        "profile": profile,
        "hook_timeout": None,
        "tool_deadline": None,
        "terminal_timeout": 900,
        "max_turns": 400,
        "changed_at": None,
        "repo_read": False,
        "repo_hook_timeout": None,
        "plugins": ["coder-guard", "comment-guard", "factory-notify"],
        "hooks": hooks,
        "longest_commands": {"coder-guard": 900, "merge-guard": 30},
    }
    return ProfileFacts(**{**fields, **patch})


def test_guard_risks_match_the_cases_of_factory_live():
    from workflow_core.adapters.hermes.texts import TERMS
    from workflow_core.diagnosis.guards import guard_risks

    def risks(profiles):
        return [risk.model_dump() for risk in guard_risks(profiles, TERMS)]

    # « signale, avant tout incident, coder-guard à 900 s sous un hook de 30 s »
    assert risks([_guard_facts("coder")]) == [
        {"plugin": "coder-guard", "agents": ["coder"], "longest_seconds": 900.0, "limit_seconds": 30.0}
    ]

    # « regroupe les agents d'une même garde aux mêmes délais, une ligne par garde et par délai »
    release = _guard_facts("release", plugins=["comment-guard", "merge-guard"])
    fixed = [_guard_facts(name, hook_timeout=600) for name in ("coder", "coder-claude", "scribe")]
    assert risks([*fixed, release, _guard_facts("gate")]) == [
        {"plugin": "coder-guard", "agents": ["gate"], "longest_seconds": 900.0, "limit_seconds": 30.0},
        {
            "plugin": "coder-guard",
            "agents": ["coder", "coder-claude", "scribe"],
            "longest_seconds": 900.0,
            "limit_seconds": 420.0,
        },
        {"plugin": "merge-guard", "agents": ["release"], "longest_seconds": 30.0, "limit_seconds": 30.0},
    ]

    # « ignore une garde sans hook, sans délai connu, ou qui tient dans le délai »
    assert risks([_guard_facts("coder", hooks=[])]) == []
    assert risks([_guard_facts("coder", longest_commands={})]) == []
    assert risks([_guard_facts("coder", hook_timeout=0, tool_deadline=0)]) == []
    assert risks([_guard_facts("release", hook_timeout=60, plugins=["merge-guard"])]) == []
