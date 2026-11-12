"""The verdicts of the failed runs of a card, finished or not (ADR 0011, ADR 0012).

Ported from factory-live's `verdictsOf` at commit d47f235 (apps/server/src/application/diagnosis.ts). The expected
verdicts of the reference cases are the ones factory-live computes on the same facts, its sources answering with the
case's input, copied out by hand.
"""

from workflow_core.adapters.hermes.texts import TEXTS
from workflow_core.diagnosis.service import run_verdicts
from workflow_core.model.facts import CardFacts, EventFact, RunFact

from conftest import diagnosis_input_of, reference_cases
from test_rules_guards import FACTORY, T0, card

# The verdicts factory-live's `verdictsOf` computes on the facts of each reference case (run, finding, role,
# session), copied out by hand, newest run first.
EXPECTED = {
    "breaker": [
        (185, "stopped_without_kanban_call", "cause", "20261029_021800_e38c22"),
        (184, "stopped_without_kanban_call", "cause", "20261029_021700_c25e60"),
        (183, "stopped_without_kanban_call", "cause", "20261029_021501_69cb42"),
        (181, "refusal_loop", "cause", "20261029_005253_8854d2"),
        (180, "stopped_after_refusals", "consequence", "20261029_004450_6eed34"),
    ],
    "budget": [
        (77, "iteration_budget", "cause", "20261017_233125_066ce0"),
        (75, "tool_deadline", "cause", "20261017_224400_8b99c0"),
    ],
    "failing": [
        (20, "provider_unavailable", "cause", "20261016_043743_d90490"),
        (15, "provider_unavailable", "cause", "20261016_041227_f66774"),
    ],
    "held": [],
    "hookTimeout": [
        (191, "guard_hook_timeout", "consequence", "20261029_093652_fc383d"),
        (190, "guard_hook_timeout", "consequence", "20261029_091353_0a2b3f"),
        (189, "stopped_without_kanban_call", "cause", "20261029_085654_8bd48a"),
    ],
    "prChecks": [
        (160, "pr_acceptance_missing", "cause", "20261020_025734_e83290"),
    ],
    "prInfra": [
        (28, "pr_acceptance_infra", "cause", "20261016_113649_e971a9"),
        (27, "pr_acceptance_infra", "cause", "20261016_111920_683e85"),
    ],
    "question": [
        (144, "question", "cause", "20261018_215657_3d013b"),
    ],
    "quota": [
        (70, "provider_quota", "cause", "20261017_213211_2af8b8"),
        (69, "provider_quota", "cause", "20261017_210753_392ff8"),
    ],
    "refusalLoop": [
        (150, "refusal_loop", "cause", "20261019_020811_6e32d0"),
        (147, "refusal_loop", "cause", "20261019_004628_6ff846"),
    ],
    "spawn": [
        (12, "spawn_failed", "cause", None),
        (10, "spawn_failed", "cause", None),
    ],
    "stalled": [
        (15, "provider_unavailable", "cause", "20261016_041227_f66774"),
    ],
    "triage": [
        (165, "triage_loop", "cause", "20261020_060316_537087"),
    ],
}


class CaseSources:
    """The sources of the diagnosis answering with a case's input, as the stubs given to factory-live's service: the
    sessions and the configuration as they are, the journal cut to the asked window."""

    def __init__(self, input) -> None:
        self.texts = TEXTS
        self.input = input
        self.sessions = self
        self.logs = self
        self.profiles = _Profiles(input)
        self.evidence = _Evidence(input)

    def run_facts(self, agent, card, runs):
        return self.input.sessions

    def facts(self, agent, since, until):
        logs = self.input.logs
        return None if logs is None else [fact for fact in logs if since <= fact.at <= until]


class _Evidence:
    def __init__(self, input) -> None:
        self.input = input

    def evidence(self, board, card):
        return self.input.evidence


class _Profiles:
    def __init__(self, input) -> None:
        self.input = input

    def facts(self, agent, workflow):
        return self.input.profile


def verdicts_of(case: dict) -> list[tuple]:
    input = diagnosis_input_of(case["input"])
    facts = CardFacts(card=input.card, runs=input.runs, events=input.events)
    found = run_verdicts(facts, FACTORY, input.now, CaseSources(input))
    return [(verdict.run_id, verdict.finding, verdict.role, verdict.session_id) for verdict in found]


def test_a_finished_card_gets_the_verdicts_factory_live_computes():
    assert {case["case"]: verdicts_of(case) for case in reference_cases()} == EXPECTED


def failed(run_id: int, at: int, outcome: str = "crashed") -> RunFact:
    return RunFact(
        id=run_id,
        profile="coder",
        status=outcome,
        outcome=outcome,
        started_at=at,
        ended_at=at + 50,
        summary=None,
        metadata={},
        error=None,
    )


def test_only_failed_runs_get_a_verdict_newest_first_four_episodes_at_most():
    # Six episodes, each opened by an unblocking and closed by its run's block; run 7 completes the card.
    runs, events = [], [EventFact(id=1, run_id=None, kind="created", payload={}, created_at=T0)]
    for index in range(6):
        at = T0 + 1_000 * (index + 1)
        runs.append(failed(index + 1, at))
        events.append(EventFact(id=10 * (index + 1), run_id=None, kind="unblocked", payload={}, created_at=at - 10))
        events.append(
            EventFact(id=10 * (index + 1) + 1, run_id=index + 1, kind="blocked", payload={}, created_at=at + 50)
        )
    runs.append(failed(7, T0 + 7_000, outcome="completed"))
    facts = CardFacts(card=card(status="done", block_kind=None), runs=runs, events=events)
    input = diagnosis_input_of(next(iter(reference_cases()))["input"]).model_copy(
        update={"sessions": [], "logs": [], "profile": None, "evidence": None}
    )

    found = run_verdicts(facts, FACTORY, T0 + 8_000, CaseSources(input))

    # The completed run has no verdict; the four most recent episodes are read, newest first.
    assert [verdict.run_id for verdict in found] == [6, 5, 4, 3]
    assert all(verdict.text.code.startswith("diagnosis.run.") for verdict in found)
