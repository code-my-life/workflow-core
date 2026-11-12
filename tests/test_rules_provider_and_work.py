"""The rules of the provider and of the work, ported from factory-live (ADR 0012): the provider's quota, a provider
rejecting the key or the model, a silent provider or a failing fallback chain, the pull request acceptance check, a
ready card held by an active pull request, a triage loop.

The expected findings are the reference outputs of tests/fixtures/diagnosis/ and the expected values of
factory-live's `rules.test.ts` at commit d47f235, copied out by hand. The workflow's agents and acceptance text come
from its entry in config/workflows.toml.
"""

from workflow_core.model.facts import EventFact

from conftest import diagnosis_input_of, reference_cases
from test_rules_guards import FACTORY, T0, an_input, found, of_code

CODES = {
    "provider_quota",
    "provider_terminal",
    "provider_unavailable",
    "pr_acceptance_infra",
    "pr_acceptance_missing",
    "held_active_pr",
    "triage_loop",
}


def reference(name: str) -> dict:
    return next(case for case in reference_cases() if case["case"] == name)


def test_provider_and_work_findings_match_the_reference_outputs():
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
        "failing:provider_unavailable",
        "held:held_active_pr",
        "held:pr_acceptance_missing",
        "prChecks:pr_acceptance_missing",
        "prInfra:pr_acceptance_infra",
        "prInfra:pr_acceptance_missing",
        "quota:provider_quota",
        "stalled:provider_unavailable",
        "triage:triage_loop",
    ]


def test_a_provider_rejecting_the_key_or_the_model():
    crashed = EventFact(
        id=10,
        run_id=1,
        kind="crashed",
        payload={
            "exit_kind": "terminal_provider",
            "exit_code": 78,
            "terminal_provider": True,
            "worker_output": "auth failed",
        },
        created_at=T0 + 600,
    )
    input = an_input(
        events=[EventFact(id=1, run_id=None, kind="created", payload={}, created_at=T0 - 10), crashed], anchor=None
    )

    finding = of_code(found(input), "provider_terminal")

    assert (finding["actor"], finding["runIds"]) == ("you_factory", [1])
    assert finding["evidence"][0]["quote"] == "auth failed"


def held_input(**fields):
    data = reference("held")["input"]
    return diagnosis_input_of(data).model_copy(update=fields)


def test_the_relay_command_names_the_declared_relay_and_owner_agents():
    held = held_input()
    factory = of_code(found(held), "held_active_pr")["steps"][0]
    assert factory["text"] == {"code": "diagnosis.step.relay", "params": {"owner": "coder"}}
    assert " reassign t_04382be9 scribe " in factory["command"]

    relay = FACTORY.model_copy(update={"relay_agent": "relais-test"})
    command = of_code(found(held, relay), "held_active_pr")["steps"][0]["command"]
    assert " reassign t_04382be9 relais-test " in command
    assert command.endswith(' reassign t_04382be9 coder --reason "reprise sur la PR"')
    # The owner: the input's, else the card's assignee, else the workflow's default agent.
    other = held.model_copy(update={"owner": None, "card": held.card.model_copy(update={"assignee": "autre-agent"})})
    assert " reassign t_04382be9 autre-agent " in of_code(found(other), "held_active_pr")["steps"][0]["command"]
    nobody = held.model_copy(update={"owner": None, "card": held.card.model_copy(update={"assignee": None})})
    default = FACTORY.model_copy(update={"default_agent": "agent-par-defaut"})
    assert of_code(found(nobody, default), "held_active_pr")["steps"][0]["text"]["params"] == {
        "owner": "agent-par-defaut"
    }


def test_without_a_relay_agent_the_held_card_has_no_command():
    without = FACTORY.model_copy(update={"relay_agent": None})

    step = of_code(found(held_input(), without), "held_active_pr")["steps"][0]

    assert step["command"] is None
    assert step["text"]["code"] == "diagnosis.step.relay"


def test_without_a_declared_prefix_no_pr_acceptance_finding():
    input = diagnosis_input_of(reference("prInfra")["input"])
    assert {"pr_acceptance_infra", "pr_acceptance_missing"} <= {finding["code"] for finding in found(input)}

    without = FACTORY.model_copy(update={"pr_acceptance": None})
    codes = {finding["code"] for finding in found(input, without)}

    assert not codes & {"pr_acceptance_infra", "pr_acceptance_missing"}
