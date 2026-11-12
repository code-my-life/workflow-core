"""The assembly of the diagnosis: primary cause, other findings, run verdicts, unblocking advice, state of the work
(ADR 0012).

The cases are the variants of factory-live's `diagnose.test.ts` and the cases « l'état du travail » and « rien de
reconnu » of `rules.test.ts` at commit d47f235, with their expected values copied out by hand. The variants of the
fix need the whole incident factory-live#2, run 192 included: tests/fixtures/diagnosis-variants/ holds it, exported
from factory-live (`incidentInput('hookTimeout')`), anonymous.
"""

import json
import time
from pathlib import Path

from workflow_core.adapters.hermes.texts import TEXTS
from workflow_core.diagnosis.diagnose import diagnose
from workflow_core.model.diagnosis import diagnosis_json

from conftest import diagnosis_input_of, reference_cases
from test_diagnosis_fixtures import personal_data
from test_rules_guards import FACTORY, FACTORY_HOOKS, an_input, card, coder_profile, log, session

VARIANTS = Path(__file__).resolve().parent / "fixtures" / "diagnosis-variants"
# 2026-10-29T09:13:08Z: the fix of coder's configuration.
CONFIG_FIXED_AT = 1_793_265_188


def full(input) -> dict:
    return diagnosis_json(diagnose(input, TEXTS, FACTORY))


def at_block(**fields):
    """factory-live#2 at 09:47, when it blocked (`atBlock`): the reference output's input."""
    data = next(case for case in reference_cases() if case["case"] == "hookTimeout")["input"]
    return diagnosis_input_of(data).model_copy(update=fields)


def incident(**fields):
    """The whole incident factory-live#2, run 192 included (`incidentInput('hookTimeout')`)."""
    text = (VARIANTS / "hookTimeout-incident.json").read_text(encoding="utf-8")
    assert personal_data(text) == []
    return diagnosis_input_of(json.loads(text)).model_copy(update=fields)


def test_the_cause_becomes_fixed_then_verified_when_the_config_changes():
    diagnosis = full(incident(profile=coder_profile(hook_timeout=600, changed_at=CONFIG_FIXED_AT)))

    primary = diagnosis["primary"]
    assert primary["cause"] == {"state": "fixed", "at": "2026-10-29T09:13:08.000Z", "verified": True, "durable": False}
    assert (primary["setting"]["current"], primary["setting"]["effective"]) == (600, 420)
    assert diagnosis["unblock"]["when"] == "now"
    assert [step["done"] for step in primary["steps"]] == [True, False, False]
    facts = [item["fact"] for item in primary["evidence"]]
    assert {
        "code": "diagnosis.evidence.settingChanged",
        "params": {"key": "plugins.hook_callback_timeout", "value": 600},
    } in facts
    # The first commit checked after the fix: refused by coder-guard (format), but in 100 s.
    assert {"code": "diagnosis.evidence.commitPassed", "params": {"seconds": 100}} in facts


def test_the_fix_is_durable_when_the_workflow_repo_holds_the_same_value():
    profile = coder_profile(hook_timeout=600, changed_at=CONFIG_FIXED_AT, repo_hook_timeout=600)

    primary = full(incident(profile=profile))["primary"]

    assert (primary["cause"]["state"], primary["cause"]["durable"]) == ("fixed", True)
    assert [step["done"] for step in primary["steps"]] == [True, True, False]


def test_without_journal_the_guard_is_named_only_if_one_plugin_hooks_the_tool_call():
    without_log = full(at_block(logs=None))
    # coder-guard and comment-guard both hook pre_tool_call: the cause stays likely.
    assert without_log["primary"]["confidence"] == "likely"
    assert without_log["primary"]["subject"]["plugin"] is None
    assert without_log["primary"]["summary"]["params"] == {"plugin": None, "seconds": 30}
    assert without_log["sources"] == {"transcript": True, "agentLog": False, "config": True, "workspace": True}

    alone = full(at_block(logs=None, profile=coder_profile(plugins=["coder-guard", "factory-notify"])))
    assert (alone["primary"]["subject"]["plugin"], alone["primary"]["confidence"]) == ("coder-guard", "certain")


def test_without_agent_config_the_cause_is_unknown():
    diagnosis = full(at_block(profile=None))

    assert diagnosis["primary"]["cause"] == {"state": "unknown"}
    assert diagnosis["primary"]["setting"]["effective"] is None
    assert diagnosis["unblock"]["when"] == "now"
    assert diagnosis["sources"]["config"] is False


def test_the_same_diagnosis_whatever_the_order_of_the_facts():
    base = at_block()
    shuffled = base.model_copy(
        update={
            "runs": list(reversed(base.runs)),
            "sessions": [
                session.model_copy(update={"calls": session.calls[::-1], "results": session.results[::-1]})
                for session in reversed(base.sessions or [])
            ],
            "logs": list(reversed(base.logs or [])),
        }
    )

    assert full(shuffled) == full(base)


def test_the_agent_text_appears_only_in_quotes():
    def without_quotes(value):
        if isinstance(value, dict):
            return {key: without_quotes(item) for key, item in value.items() if key != "quote"}
        if isinstance(value, list):
            return [without_quotes(item) for item in value]
        return value

    text = json.dumps(without_quotes(full(at_block())), ensure_ascii=False)

    assert "Terminal et tous les outils bloqués" not in text


def test_the_work_state_is_read_in_the_terminal_outputs():
    steps = [
        ("write_file", "apps/web/a.ts", '{"bytes_written": 10}', 10),
        ("patch", "apps/web/a.ts", '{"success": true}', 20),
        (
            "terminal",
            'git commit -m "fix: x"',
            '{"output": "[fix/1-x b055de5] fix: x\\n 1 file changed", "exit_code": 0, "error": null}',
            30,
        ),
        (
            "terminal",
            "git push -u origin fix/1-x",
            '{"output": "To github.com:code-my-life/factory-live.git", "exit_code": 0, "error": null}',
            40,
        ),
    ]
    pull = "https://github.com/code-my-life/factory-live/pull/3"

    work = full(an_input(branch="fix/1-x", pr_url=pull, sessions=[session(1, steps)]))["work"]

    assert work == {
        "branch": "fix/1-x",
        "commits": "some",
        "commitsRefused": 0,
        "pushed": "yes",
        "prUrl": None,
        "editedFiles": 1,
        "evidence": [],
    }
    # The core never serves a pull request link; a known one still proves the push, even without a readable session.
    assert full(an_input(sessions=None, branch="fix/1-x", pr_url=pull))["work"] | {"evidence": []} == {
        "branch": "fix/1-x",
        "commits": "unknown",
        "commitsRefused": 0,
        "pushed": "yes",
        "prUrl": None,
        "editedFiles": 0,
        "evidence": [],
    }


def test_a_bracket_trap_is_read_in_linear_time():
    trap = '{"output": "' + "[" * 200_000 + '", "exit_code": 0, "error": null}'
    started = time.monotonic()

    work = full(an_input(sessions=[session(1, [("terminal", 'git commit -m "x"', trap, 30)])]))["work"]

    assert time.monotonic() - started < 0.5
    assert work["commits"] == "none"


def test_without_readable_sessions_the_work_is_unknown():
    work = full(an_input(sessions=None, branch="fix/1-x"))["work"]
    assert (work["commits"], work["pushed"]) == ("unknown", "unknown")
    assert full(an_input(sessions=None))["work"] is None


def test_nothing_recognised_gives_the_last_signals_as_is():
    diagnosis = full(
        an_input(
            card=card(block_kind=None), sessions=[session(1, [("patch", "a.ts", '{"error": "path required"}', 100)])]
        )
    )

    primary = diagnosis["primary"]
    assert (primary["code"], primary["confidence"], primary["actor"]) == ("unexplained", "likely", "you_answer")
    assert primary["evidence"][0]["quote"] == '{"error": "path required"}'
    assert diagnosis["unblock"] == {"when": "answer", "template": None, "nextBlock": "blocked"}


def test_a_journal_line_attached_by_time_to_the_only_session_names_the_guard():
    timeout = '{"error": "pre_tool_call plugin callback timed out or is still running"}'
    line = "Hook 'pre_tool_call' callback pre_tool timed out after 30s"
    input = an_input(
        profile=coder_profile(hooks=FACTORY_HOOKS),
        sessions=[session(1, [("terminal", "git commit -m x", timeout, 100, 30)])],
        logs=[log("hook_timeout", 130, hook="pre_tool_call", callback="pre_tool", seconds=30, line=line)],
    )

    primary = full(input)["primary"]

    assert (primary["code"], primary["confidence"], primary["subject"]["plugin"]) == (
        "guard_hook_timeout",
        "certain",
        "coder-guard",
    )
