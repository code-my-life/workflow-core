"""The interventions of a board: its triggers, with the title, question, severity, notification and actions that
factory-live's `interventionsOf` gave them at commit d47f235 (packages/domain/src/interventions.ts).

The expectations are copied by hand from its `interventions.test.ts`, without the links to an issue or a pull request,
which factory-live keeps (it knows to which issue a card belongs).
"""

import json
from pathlib import Path

import jsonschema
from fastapi.testclient import TestClient
from workflow_core.adapters import runtime_adapter
from workflow_core.adapters.hermes.board import TEXTS
from workflow_core.config import Gesture, Workflow, load_workflows
from workflow_core.interventions import interventions_of
from workflow_core.service import build_service

from conftest import RUNTIME_HOME_VAR, make_runtime_board
from test_triggers import blocked, card, event, facts

ROOT = Path(__file__).resolve().parent.parent
SECRET = "test-secret-not-a-real-one"
GOOD = {"Authorization": f"Bearer {SECRET}"}
NOW = 100_000
UNBLOCK = {"kind": "gesture", "gesture": "unblock"}
SHOW = {
    "kind": "copy",
    "label": {"code": "intervention.action.showCard", "params": {}},
    "command": "hermes kanban --board factory show t_b",
}
FACTORY = Workflow(
    key="factory",
    board="factory",
    sheet=Path("factory.json"),
    approval_card_title=r"^release\s",
    main_red_reason=r"main rouge|workflows? de main|watch-main",
    default_agent="coder",
    relay_agent="scribe",
    gestures=[Gesture(name="unblock", kind="comment")],
)


def code(name: str, **params) -> dict:
    return {"code": name, "params": params}


def quote(text: str) -> dict:
    return {"quote": text}


def blocked_event(reason: str, kind: str | None, event_id: int = 10):
    return event(event_id, "blocked", 90_000, {"reason": reason, "kind": kind})


def of(detail, events=(), workflow: Workflow = FACTORY) -> list[dict]:
    found = interventions_of([facts(detail, list(events))], workflow, NOW, TEXTS)
    return [intervention.model_dump(mode="json") for intervention in found]


def test_a_blocked_card_is_an_intervention_with_its_title_and_actions(tmp_path, monkeypatch):
    home = tmp_path / "runtime"
    make_runtime_board(
        home,
        "factory",
        tasks=[
            {
                "id": "t_b",
                "title": "cv-site#49 : Page Projets",
                "status": "blocked",
                "assignee": "coordinator",
                "created_at": 1_000,
                "block_kind": "needs_input",
            }
        ],
        runs=[{"id": 4, "task_id": "t_b", "profile": "coordinator", "status": "done", "started_at": 1_100}],
        events=[
            {
                "id": 10,
                "task_id": "t_b",
                "run_id": 4,
                "kind": "blocked",
                "payload": json.dumps(
                    {"reason": "Carte UI sans maquette jointe.\nDeux options…", "kind": "needs_input"}
                ),
                "created_at": 90_000,
            }
        ],
    )
    make_runtime_board(home, "studio", tasks=[])
    monkeypatch.setenv(RUNTIME_HOME_VAR, str(home))
    client = TestClient(
        build_service(
            secret=SECRET, workflows=load_workflows(ROOT / "config" / "workflows.toml"), adapter=runtime_adapter()
        )
    )

    response = client.get("/api/workflows/factory/interventions", headers=GOOD)

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "schema_version": 1,
        "workflow": "factory",
        "interventions": [
            {
                "id": "needs_input:t_b:10",
                "kind": "needs_input",
                "item": "t_b",
                "event": 10,
                "opened_at": "1970-01-02T01:00:00.000Z",
                "agent": "coordinator",
                "severity": "critical",
                "push": True,
                "title": quote("Carte UI sans maquette jointe."),
                "question": quote("Carte UI sans maquette jointe.\nDeux options…"),
                "actions": [UNBLOCK, SHOW],
            }
        ],
    }
    schema = json.loads((ROOT / "schema" / "interventions.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(body)
    assert client.get("/api/workflows/factory/interventions").status_code == 401
    assert client.get("/api/workflows/nope/interventions", headers=GOOD).json()["error"] == "unknown_workflow"


def test_the_six_kinds_get_factory_live_severity_push_title_question_and_actions():
    # A decision awaited: notified, unblocked from the app, the whole question.
    (found,) = of(
        blocked("needs_input"), [blocked_event("Carte UI sans maquette jointe.\nDeux options…", "needs_input")]
    )
    assert (found["kind"], found["severity"], found["push"]) == ("needs_input", "critical", True)
    assert found["title"] == quote("Carte UI sans maquette jointe.")
    assert "Deux options" in found["question"]["quote"]
    assert UNBLOCK in found["actions"]

    # A missing tool and a passing failure are notified too; the passing failure has a command to copy.
    (capability,) = of(blocked("capability"), [blocked_event("gh absent", "capability")])
    assert (capability["kind"], capability["severity"], capability["push"]) == ("capability", "critical", True)
    assert capability["actions"] == [UNBLOCK, SHOW]
    (transient,) = of(blocked("transient"), [blocked_event("lane CLI en échec deux fois", "transient")])
    assert (transient["kind"], transient["severity"], transient["push"]) == ("transient", "warning", True)
    assert any(action["kind"] == "copy" for action in transient["actions"])

    # A give-up of the breaker prevails over the block it causes, with the number of failures.
    gave_up = [
        blocked_event("gave_up", None, 10),
        event(11, "gave_up", 90_100, {"failures": 2, "error": "Iteration budget exhausted (150/150)"}),
    ]
    (found,) = of(blocked(None), gave_up)
    assert (found["kind"], found["severity"], found["push"]) == ("gave_up", "critical", True)
    assert found["title"] == code("intervention.title.gaveUp", failures=2)
    assert found["question"] == quote("Iteration budget exhausted (150/150)")
    assert found["actions"] == [UNBLOCK, SHOW]

    # A block loop: the card is in triage, a procedure to copy, no unblocking.
    loop = card(title="impl cv-site#49 : filtres", status="triage")
    (found,) = of(loop, [event(1, "block_loop_detected", 1_000, {"recurrences": 2, "limit": 2})])
    assert (found["kind"], found["severity"], found["push"]) == ("triage_loop", "critical", True)
    assert (found["title"], found["question"]) == (
        code("intervention.title.triageLoop"),
        code("intervention.question.triageLoop"),
    )
    assert found["actions"] == [SHOW]

    # A card held by the active pull request beyond 30 minutes, since the last claim.
    held = card("t_h", title="impl cv-site#44 : liens", status="ready", assignee="coder")
    guarded = [
        event(1, "respawn_guarded", 97_000, {"reason": "active_pr"}),
        event(2, "respawn_guarded", 99_900, {"reason": "active_pr"}),
    ]
    assert of(held, [event(1, "respawn_guarded", 99_000, {"reason": "active_pr"})]) == []
    (found,) = of(held, guarded)
    assert (found["kind"], found["severity"], found["push"], found["event"]) == (
        "held_active_pr",
        "warning",
        True,
        None,
    )
    assert found["title"] == code("intervention.title.held", minutes=50)
    assert found["question"] == code("intervention.question.held")
    (relay,) = found["actions"]
    assert relay["kind"] == "copy"
    assert "reassign t_h scribe" in relay["command"]
    assert relay["label"] == code("intervention.action.relay", owner="coder")

    # A block without a type stays visible, not notified: it is no question of the agent.
    (found,) = of(blocked(None), [blocked_event("Arrêt opérateur", None)])
    assert (found["kind"], found["severity"], found["push"]) == ("needs_input", "warning", False)
    assert found["title"] == quote("Arrêt opérateur")
    assert [action["kind"] for action in found["actions"]] == ["gesture", "copy"]

    # Without a reason given by the agent, title and question are codes the client writes.
    def fallback(kind):
        return of(blocked(kind), [blocked_event("", kind)])[0]

    for kind, name in (("needs_input", "needsInput"), ("capability", "capability"), ("transient", "transient")):
        assert (fallback(kind)["title"], fallback(kind)["question"]) == (
            code(f"intervention.title.{name}"),
            code(f"intervention.question.{name}"),
        )
    assert fallback("transient")["actions"][1]["label"] == code("intervention.action.reassignCoder")
    assert (fallback(None)["title"], fallback(None)["question"]) == (
        code("intervention.title.blocked"),
        code("intervention.question.blocked"),
    )
    assert fallback(None)["actions"][1:] == [SHOW]
    (found,) = of(blocked(None), [event(12, "gave_up", 90_000)])
    assert (found["title"], found["question"]) == (
        code("intervention.title.gaveUp", failures=None),
        code("intervention.question.gaveUp"),
    )

    # A dependency block is no intervention; the id is stable, to notify once per block.
    assert of(card("t_d", status="todo", block_kind="dependency"), [event(1, "dependency_wait")]) == []
    first = of(blocked("needs_input"), [blocked_event("Question", "needs_input", 42)])
    assert (
        first[0]["id"]
        == "needs_input:t_b:42"
        == of(blocked("needs_input"), [blocked_event("Question", "needs_input", 42)])[0]["id"]
    )


def test_the_transient_command_uses_the_declared_default_agent():
    passing = [blocked_event("lane CLI en échec deux fois", "transient")]

    (found,) = of(blocked("transient"), passing)
    assert found["actions"] == [
        UNBLOCK,
        {
            "kind": "copy",
            "label": code("intervention.action.reassignCoder"),
            "command": 'hermes kanban --board factory reassign t_b coder --reason "échec passager de la lane" && '
            "hermes kanban --board factory unblock t_b",
        },
    ]
    (other,) = of(blocked("transient"), passing, FACTORY.model_copy(update={"default_agent": "builder"}))
    assert "reassign t_b builder " in other["actions"][1]["command"]
    (none,) = of(blocked("transient"), passing, FACTORY.model_copy(update={"default_agent": None}))
    assert none["actions"] == [UNBLOCK]


def test_a_workflow_without_gestures_has_no_gesture_action():
    bare = FACTORY.model_copy(update={"gestures": []})
    cases = [
        (blocked("needs_input"), [blocked_event("Question", "needs_input")]),
        (blocked("capability"), [blocked_event("gh absent", "capability")]),
        (blocked("transient"), [blocked_event("lane", "transient")]),
        (blocked(None), [event(11, "gave_up", 90_100, {"failures": 2})]),
        (blocked(None), [blocked_event("Arrêt opérateur", None)]),
    ]
    for detail, events in cases:
        (found,) = of(detail, events, bare)
        assert all(action["kind"] != "gesture" for action in found["actions"]), found
    # A gesture declared under another name is not the unblocking.
    other = FACTORY.model_copy(update={"gestures": [Gesture(name="retry", kind="comment")]})
    (found,) = of(blocked("needs_input"), [blocked_event("Question", "needs_input")], other)
    assert all(action["kind"] != "gesture" for action in found["actions"])
