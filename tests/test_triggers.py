"""The triggers of the diagnosis: the cards waiting for the owner, of the six kinds factory-live diagnoses (ADR 0012).

The expected kinds, events and ids are those of factory-live's `interventions.test.ts` at commit d47f235, copied out
by hand, without the titles, questions and actions that only serve factory-live's display.
"""

import json
from pathlib import Path

from workflow_core.adapters.hermes.board import HermesBoardReader
from workflow_core.config import Workflow
from workflow_core.diagnosis.triggers import triggers_of
from workflow_core.model.facts import CardDetail, CardFacts, EventFact, RunFact

from conftest import make_runtime_board

NOW = 100_000
# The factory's entry: its conventions for the two kinds of interventions that are not diagnosed.
FACTORY = Workflow(
    key="factory",
    board="factory",
    sheet=Path("factory.json"),
    approval_card_title=r"^release\s",
    main_red_reason=r"main rouge|workflows? de main|watch-main",
)
STUDIO = Workflow(key="studio", board="studio", sheet=Path("studio.json"))


def card(card_id: str = "t_b", **fields) -> CardDetail:
    values = {
        "id": card_id,
        "title": "cv-site#49 : Page Projets",
        "body": "",
        "status": "blocked",
        "assignee": "coordinator",
        "created_at": 1_000,
        "started_at": None,
        "completed_at": None,
        "block_kind": None,
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


def event(event_id: int = 1, kind: str = "created", created_at: int = 1_000, payload: dict | None = None) -> EventFact:
    return EventFact(id=event_id, run_id=None, kind=kind, payload=payload or {}, created_at=created_at)


def run(run_id: int, profile: str, started_at: int = 1_000) -> RunFact:
    return RunFact(
        id=run_id,
        profile=profile,
        status="done",
        outcome="blocked",
        started_at=started_at,
        ended_at=None,
        summary=None,
        metadata={},
        error=None,
    )


def facts(detail: CardDetail, events: list[EventFact] = (), runs: list[RunFact] = ()) -> CardFacts:
    return CardFacts(card=detail, runs=list(runs), events=list(events))


def blocked(kind: str | None, **fields) -> CardDetail:
    return card(status="blocked", block_kind=kind, **fields)


def blocked_event(reason: str, kind: str | None, event_id: int = 10) -> EventFact:
    return event(event_id, "blocked", 90_000, {"reason": reason, "kind": kind})


def found(cards: list[CardFacts], workflow: Workflow = FACTORY, now: int = NOW) -> list[tuple]:
    return [(t.kind, t.card, t.event, t.opened_at, t.id) for t in triggers_of(cards, workflow, now)]


def test_a_blocked_card_of_the_studio_board_is_a_trigger(tmp_path):
    home = tmp_path / "runtime"
    make_runtime_board(
        home,
        "studio",
        tasks=[
            {
                "id": "t_0100",
                "title": "Vidéo de lancement",
                "assignee": "scriptwriter",
                "status": "blocked",
                "block_kind": "needs_input",
                "created_at": 1793000000,
            },
            {"id": "t_0101", "title": "Montage", "status": "running", "created_at": 1793000000},
        ],
        runs=[{"id": 1, "task_id": "t_0100", "profile": "scriptwriter", "status": "blocked", "started_at": 1793000100}],
        events=[
            {"id": 1, "task_id": "t_0100", "kind": "created", "payload": "{}", "created_at": 1793000000},
            {
                "id": 2,
                "task_id": "t_0100",
                "run_id": 1,
                "kind": "blocked",
                "payload": json.dumps({"reason": "Quelle durée ?", "kind": "needs_input"}),
                "created_at": 1793000500,
            },
        ],
    )
    cards = HermesBoardReader(home).read_board_facts("studio")

    triggers = triggers_of(cards, STUDIO, 1793009000)

    assert [t.model_dump() for t in triggers] == [
        {
            "id": "needs_input:t_0100:2",
            "kind": "needs_input",
            "card": "t_0100",
            "event": 2,
            "opened_at": 1793000500,
            "agent": "scriptwriter",
        }
    ]


def test_an_awaited_decision_a_missing_tool_and_a_transient_failure_are_triggers():
    decision = facts(
        blocked("needs_input"), [blocked_event("Carte UI sans maquette jointe.\nDeux options", "needs_input")]
    )
    assert found([decision]) == [("needs_input", "t_b", 10, 90_000, "needs_input:t_b:10")]

    tool = facts(blocked("capability"), [blocked_event("gh absent", "capability")])
    assert found([tool]) == [("capability", "t_b", 10, 90_000, "capability:t_b:10")]

    transient = facts(blocked("transient"), [blocked_event("lane CLI en échec deux fois", "transient")])
    assert found([transient]) == [("transient", "t_b", 10, 90_000, "transient:t_b:10")]


def test_a_give_up_wins_over_the_block_it_causes():
    events = [
        blocked_event("gave_up", None, 10),
        event(11, "gave_up", 90_100, {"failures": 2, "error": "Iteration budget exhausted (150/150)"}),
    ]
    assert found([facts(blocked(None), events)]) == [("gave_up", "t_b", 11, 90_100, "gave_up:t_b:11")]


def test_a_block_loop_in_triage_is_a_trigger():
    loop = facts(
        card(title="impl cv-site#49 : filtres", status="triage"),
        [event(1, "block_loop_detected", 1_000, {"recurrences": 2, "limit": 2})],
    )
    assert found([loop]) == [("triage_loop", "t_b", 1, 1_000, "triage_loop:t_b:1")]
    # In triage without a loop: nothing to diagnose.
    assert found([facts(card(status="triage"))]) == []


def guarded(event_id: int, created_at: int) -> EventFact:
    return event(event_id, "respawn_guarded", created_at, {"reason": "active_pr"})


def test_a_card_held_by_active_pr_past_the_delay_is_a_trigger():
    held = card("t_h", title="impl cv-site#44 : liens", status="ready", assignee="coder")

    assert found([facts(held, [guarded(1, 99_000)])]) == []
    # Since the first guard after the last claim: 97 000, 50 minutes before NOW. No event is told.
    assert found([facts(held, [guarded(1, 97_000), guarded(2, 99_900)])]) == [
        ("held_active_pr", "t_h", None, 97_000, "held_active_pr:t_h:97000")
    ]
    # Another reason of the guard does not hold the card.
    assert found([facts(held, [event(1, "respawn_guarded", 90_000, {"reason": "other"})])]) == []


def test_a_claim_resets_the_held_delay():
    held = card("t_h", title="impl cv-site#44 : liens", status="ready")
    events = [guarded(1, 90_000), event(2, "claimed", 95_000), guarded(3, 99_000)]

    assert found([facts(held, events)]) == []


def test_an_untyped_block_is_a_needs_input_trigger():
    assert found([facts(blocked(None), [blocked_event("Arrêt opérateur", None)])]) == [
        ("needs_input", "t_b", 10, 90_000, "needs_input:t_b:10")
    ]
    # Without any block event, the card's creation opens it.
    assert found([facts(blocked(None))]) == [("needs_input", "t_b", None, 1_000, "needs_input:t_b:1000")]


def test_a_dependency_block_is_not_a_trigger():
    waiting = card("t_d", title="release cv-site#47", status="todo", block_kind="dependency")
    assert found([facts(waiting, [event(1, "dependency_wait")])]) == []
    # Blocked for a dependency: it resumes alone.
    assert found([facts(blocked("dependency"), [blocked_event("parent", "dependency")])]) == []


def test_the_trigger_id_is_stable_between_computations():
    cards = [facts(blocked("needs_input"), [blocked_event("Question", "needs_input", 42)])]

    assert found(cards)[0][4] == "needs_input:t_b:42"
    assert found(cards) == found(cards)


def test_the_examined_agent_is_the_event_run_then_the_last_run_then_the_assignee():
    runs = [run(5, "gate", 80_000), run(7, "coder", 85_000)]
    told = facts(blocked("needs_input", assignee="coordinator"), [blocked_event("?", "needs_input")], runs)
    told.events[0] = told.events[0].model_copy(update={"run_id": 5})
    assert triggers_of([told], FACTORY, NOW)[0].agent == "gate"

    last_run = facts(blocked("needs_input", assignee="coordinator"), [blocked_event("?", "needs_input")], runs)
    assert triggers_of([last_run], FACTORY, NOW)[0].agent == "coder"

    assignee = facts(blocked("needs_input", assignee="coordinator"), [blocked_event("?", "needs_input")])
    assert triggers_of([assignee], FACTORY, NOW)[0].agent == "coordinator"


def test_a_pr_to_approve_or_a_red_main_is_not_a_trigger():
    # A card in review, even with a green of the gate: a pull request to approve.
    review = facts(card("t_r", title="impl claude-cockpit#68 : tag", status="review"))
    # A release card ready to merge: a pull request to approve, even after a give-up.
    ready = facts(
        card("t_rel", title="release claude-cockpit#68", status="blocked", block_kind="needs_input"),
        [event(3, "blocked", 95_000, {"reason": "Prêt à merger claude-cockpit#74", "kind": "needs_input"})],
    )
    # A release card blocked by a red main.
    red = facts(
        card("t_red", title="release claude-cockpit#68", status="blocked", block_kind=None),
        [event(4, "blocked", 95_000, {"reason": "Main rouge après le merge : workflow Tests en échec", "kind": None})],
    )

    assert found([review, ready, red]) == []
    # The same cards on a workflow without these conventions are blocked cards like any other.
    assert [kind for kind, *_ in found([review, ready, red], STUDIO)] == ["needs_input", "needs_input"]


def test_the_held_delay_comes_from_the_workflow_entry():
    held = facts(card("t_h", status="ready"), [guarded(1, NOW - 11 * 60)])

    assert found([held]) == []
    quick = FACTORY.model_copy(update={"held_after_minutes": 10})
    assert found([held], quick) == [("held_active_pr", "t_h", None, NOW - 660, f"held_active_pr:t_h:{NOW - 660}")]
