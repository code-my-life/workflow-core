"""The notifier on the workflows plugged by their manifest: each wait of an `owner` station and each failure of an
item alerted once in its issue, under the factory's switch, switch-on time, list of repositories and register
(story 12 of the engine's epic; ADR 0009, ADR 0010, ADR 0019)."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from workflow_core.adapters import runtime_adapter
from workflow_core.config import Workflow
from workflow_core.model.contract import ContractVerdict
from workflow_core.model.state import CoreState
from workflow_core.notify import Notifier, NotifySettings
from workflow_core.plugin.manifest import plug

from conftest import ESSAI_REPO, RUNTIME_HOME_VAR, make_runtime_board
from test_notify import FakeGitHub

NOON = datetime(2026, 11, 12, 12, 0, tzinfo=UTC).timestamp()
SINCE = "2026-11-12T13:00:00+01:00"  # noon UTC
LIVE = "https://live.example.ts.net:8446"
ISSUE = "https://github.com/code-my-life/workflow-essai/issues/7"
REPO = "code-my-life/workflow-essai"
ROOT = "t_0a0b0c07"
AGENT_REPO = Path(__file__).resolve().parent / "fixtures" / "workflows" / "essai-agent"


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "runtime"
    monkeypatch.setenv(RUNTIME_HOME_VAR, str(path))
    return path


def environ(**more: str) -> dict[str, str]:
    return {
        "WORKFLOW_CORE_NOTIFY": "on",
        "WORKFLOW_CORE_NOTIFY_SINCE": SINCE,
        "WORKFLOW_CORE_LIVE_URL": LIVE,
        **more,
    }


def essai(repo: Path = ESSAI_REPO):
    return plug(Workflow(key="essai", board="essai", repo=repo))


def notifier(tmp_path, github, env=None, *, mode_ok=True, log=None, plugged=None, records=None) -> Notifier:
    verdict = ContractVerdict(ok=mode_ok, checked_at="2026-11-12T11:00:00+00:00", problems=[] if mode_ok else ["x"])
    return Notifier(
        workflows={},
        plugged=[essai()] if plugged is None else plugged,
        records=records,
        reader=runtime_adapter().make_board_reader(),
        state=CoreState(contract=verdict),
        settings=NotifySettings.from_environ(environ() if env is None else env),
        post_comment=github.post_comment,
        read_comments=github.list_comments,
        register_path=tmp_path / "state" / "alerts.db",
        clock=lambda: NOON + 600,
        log=(log.append if log is not None else lambda line: None),
    )


def root_card(status: str = "scheduled", assignee: str = "essai-revue", **fields) -> dict:
    return {
        "id": ROOT,
        "title": "essai:7 · Septième essai",
        "body": ISSUE,
        "assignee": assignee,
        "status": status,
        "created_at": int(NOON) - 3600,
        "idempotency_key": "essai:7",
        **fields,
    }


def event(id: int, kind: str, at: float, task: str = ROOT, payload: dict | None = None) -> dict:
    return {"id": id, "task_id": task, "kind": kind, "payload": json.dumps(payload or {}), "created_at": int(at)}


def waiting_root(home: Path, at: float = NOON + 60) -> None:
    make_runtime_board(
        home,
        "essai",
        tasks=[root_card()],
        events=[event(41, "scheduled", at, payload={"reason": "En attente du propriétaire à la station revue"})],
    )


def test_an_owner_wait_of_a_plugged_workflow_gets_one_alert_with_its_wait_id(home, tmp_path):
    waiting_root(home)
    github = FakeGitHub()

    notifier(tmp_path, github).run_pass()

    assert len(github.posted) == 1
    repo, number, body = github.posted[0]
    assert (repo, number) == (REPO, 7)
    wait = f"essai:owner:{ROOT}:41"
    assert body.startswith("### ⏸ Ta réponse est attendue : Revue\n")
    assert f"Workflow `essai` · élément `essai:7` · attente `{wait}`" in body
    assert "/essai valider" in body
    assert "/essai refaire" in body
    # The issue gesture of the manifest is not given by a comment.
    assert "/essai ouvrir" not in body
    assert f"{LIVE}/workflows/essai" in body
    assert "Alerte du socle workflow-core" in body
    assert "@" not in body
    assert body.endswith(f"<!-- workflow-core:attente {wait} -->")


def test_a_wait_is_not_alerted_again_after_a_rebuild(fake_runtime, tmp_path):
    waiting_root(fake_runtime.home)
    github = FakeGitHub()
    state = tmp_path / "state"

    def service():
        from workflow_core.service import build_service

        return build_service(
            secret="s",
            workflows={"essai": Workflow(key="essai", board="essai", repo=ESSAI_REPO)},
            adapter=runtime_adapter(),
            environ={**environ(), "WORKFLOW_CORE_STATE_DIR": str(state)},
            post_comment=github.post_comment,
            read_comments=github.list_comments,
            clock=lambda: NOON + 600,
        )

    alerts = service().state.notifier
    alerts.run_pass()
    alerts.run_pass()
    assert len(github.posted) == 1

    # The register and the event journal deleted, the service rebuilt: the marker is found in the issue.
    (state / "alerts.db").unlink()
    (state / "events.db").unlink(missing_ok=True)
    service().state.notifier.run_pass()
    assert len(github.posted) == 1


def blocked(id: int, at: float, reason: str, task: str = ROOT, kind: str | None = "needs_input") -> dict:
    payload = {"reason": reason, **({"kind": kind} if kind else {})}
    return event(id, "blocked", at, task, payload)


def test_a_failed_station_gets_a_failure_alert(home, tmp_path):
    motive = "Échec de la station preparer : délai de 30 s dépassé"
    make_runtime_board(
        home,
        "essai",
        tasks=[
            root_card(status="blocked", assignee="essai-preparer", block_kind="needs_input"),
            # A block without a kind counts as `needs_input`; a dependency's is never alerted.
            root_card(id="t_00000002", idempotency_key="essai:8", status="blocked", assignee="essai-clore"),
            root_card(id="t_00000003", idempotency_key="essai:9", status="blocked", block_kind="dependency"),
        ],
        events=[
            blocked(51, NOON + 60, motive),
            blocked(52, NOON + 60, "Échec de la station clore : sortie illisible", "t_00000002", None),
            blocked(53, NOON + 60, "attend une autre carte", "t_00000003", "dependency"),
        ],
    )
    github = FakeGitHub()
    alerts = notifier(tmp_path, github)

    alerts.run_pass()
    alerts.run_pass()

    assert [(repo, number) for repo, number, _ in github.posted] == [(REPO, 7), (REPO, 7)]
    root_body = next(body for _, _, body in github.posted if "Préparer" in body)
    assert root_body == (
        "### ⛔ Échec à la station Préparer\n"
        f"Workflow `essai` · élément `essai:7` · carte `{ROOT}` : essai:7 · Septième essai\n"
        "\n"
        "**Motif**\n"
        "\n"
        f"> {motive}\n"
        "\n"
        "**Pour reprendre** (seul le propriétaire est écouté) : commente sur cette issue\n"
        "```\n"
        "/essai unblock [note]\n"
        "```\n"
        "\n"
        f"[Voir l'élément dans factory-live]({LIVE}/workflows/essai)\n"
        "\n"
        "<sub>Alerte du socle workflow-core</sub>\n"
        "\n"
        f"<!-- workflow-core:attente essai:needs_input:{ROOT}:51 -->"
    )
    other = next(body for _, _, body in github.posted if body != root_body)
    assert other.startswith("### ⛔ Échec à la station Clore\n")
    assert "élément `essai:8`" in other
    assert other.endswith("<!-- workflow-core:attente essai:needs_input:t_00000002:52 -->")


def test_a_blocked_child_card_alerts_its_element_s_issue(home, tmp_path):
    child = "t_c41d0001"
    make_runtime_board(
        home,
        "essai",
        tasks=[
            # The root card has no issue of its own here: the alert goes to the one the child's body cites.
            root_card(status="ready", assignee="essai-brouillon", body="essai:7"),
            {
                "id": child,
                "title": "essai:7 · brouillon (passage 1)",
                "body": f"essai:7\n{ISSUE}",
                "assignee": "essai-agent",
                "status": "blocked",
                "block_kind": "needs_input",
                "created_at": int(NOON),
                "idempotency_key": "essai:7/brouillon/1",
            },
        ],
        events=[blocked(61, NOON + 60, "il manque la source", child)],
    )
    github = FakeGitHub()

    notifier(tmp_path, github, plugged=[essai(AGENT_REPO)]).run_pass()

    assert len(github.posted) == 1
    repo, number, body = github.posted[0]
    assert (repo, number) == (REPO, 7)
    assert body.startswith("### ⛔ Échec à la station Brouillon\n")
    assert f"Workflow `essai` · élément `essai:7` · carte `{child}` : essai:7 · brouillon (passage 1)" in body
    assert "**Question de l'agent**\n\n> il manque la source" in body
    # `unblock` is for the root card only: the child is unblocked on the board.
    assert "unblock" not in body
    assert f"débloquer la carte fille `{child}` du board `essai` (opérateur)" in body
    assert body.endswith(f"<!-- workflow-core:attente essai:needs_input:{child}:61 -->")


def test_waits_before_the_switch_on_time_or_while_off_are_never_alerted(home, tmp_path):
    waiting_root(home, at=NOON - 60)
    github = FakeGitHub()
    alerts = notifier(tmp_path, github)
    alerts.run_pass()
    alerts.run_pass()
    assert github.posted == []

    # A wait opened after the switch-on time: nothing while switched off, in observer mode, or out of the listed
    # repositories.
    shutil.rmtree(home / "kanban" / "boards" / "essai")
    waiting_root(home)
    for env, mode_ok in (
        ({**environ(), "WORKFLOW_CORE_NOTIFY": "off"}, True),
        ({}, True),
        (environ(), False),
        (environ(WORKFLOW_CORE_NOTIFY_REPOS="code-my-life/workflow-core"), True),
    ):
        log: list[str] = []
        notifier(tmp_path, github, env, mode_ok=mode_ok, log=log).run_pass()
        assert github.posted == []
    # Then switched on, it is.
    notifier(tmp_path, github).run_pass()
    assert len(github.posted) == 1


def engine_and_notifier(fake_runtime, tmp_path, tasks: list[dict], runs: list[dict] = ()):
    """The engine's pass on a board, then the notifier's, with the engine's notes; the notifier's posts."""
    from workflow_core.signals import ENGINE_DB, Signals

    from test_engine import Launches, engine

    make_runtime_board(fake_runtime.home, "essai", tasks=tasks, runs=runs)
    records = Signals(tmp_path / "state" / ENGINE_DB)
    core, _ = engine(launches=Launches(), workflow=essai(AGENT_REPO))
    core.run_pass()
    github = FakeGitHub()
    alerts = notifier(tmp_path, github, plugged=[essai(AGENT_REPO)], records=records)
    alerts.run_pass()
    alerts.run_pass()
    core.run_pass()
    notifier(tmp_path, github, plugged=[essai(AGENT_REPO)], records=records).run_pass()
    return github.posted


def test_a_root_card_finished_by_another_is_alerted_once_in_its_issue(fake_runtime, tmp_path):
    posted = engine_and_notifier(
        fake_runtime, tmp_path, [root_card(status="done", assignee="essai-clore", completed_at=int(NOON))]
    )

    assert len(posted) == 1
    repo, number, body = posted[0]
    assert (repo, number) == (REPO, 7)
    assert body.startswith("### ⚠️ Carte racine terminée par un autre que le moteur\n")
    assert f"Workflow `essai` · élément `essai:7` · carte `{ROOT}` · station Clore" in body
    assert f"La carte racine {ROOT} a été terminée à la station clore par un autre que le moteur." in body
    assert "Le moteur ne touche plus à cet élément." in body
    assert body.endswith(f"<!-- workflow-core:attente essai:finished_elsewhere:{ROOT} -->")


def test_a_root_card_taken_by_an_agent_is_alerted_once_per_run_in_its_issue(fake_runtime, tmp_path):
    run = {"task_id": ROOT, "profile": "essai-agent", "status": "running", "started_at": int(NOON)}
    posted = engine_and_notifier(
        fake_runtime, tmp_path, [root_card(status="ready", assignee="essai-brouillon")], runs=[run]
    )

    assert len(posted) == 1
    repo, number, body = posted[0]
    assert (repo, number) == (REPO, 7)
    assert body.startswith("### ⚠️ Carte racine prise par un agent\n")
    assert f"La carte racine {ROOT} a été prise par essai-agent (run 1, en cours)." in body
    assert body.endswith(f"<!-- workflow-core:attente essai:taken:{ROOT}:1 -->")


def test_the_notifier_never_creates_the_engine_s_base(home, tmp_path):
    from workflow_core.signals import ENGINE_DB, Signals

    waiting_root(home)
    path = tmp_path / "state" / ENGINE_DB
    notifier(tmp_path, FakeGitHub(), records=Signals(path)).run_pass()

    assert not path.exists()


def test_a_root_card_finished_by_another_before_the_switch_on_time_is_never_alerted(fake_runtime, tmp_path):
    from workflow_core.signals import ENGINE_DB, Signals

    from test_engine import Launches, engine

    make_runtime_board(
        fake_runtime.home,
        "essai",
        tasks=[root_card(status="done", assignee="essai-clore", completed_at=int(NOON) - 60)],
    )
    records = Signals(tmp_path / "state" / ENGINE_DB)
    core, lines = engine(launches=Launches(), workflow=essai(AGENT_REPO))
    core.run_pass()
    github = FakeGitHub()
    notifier(tmp_path, github, plugged=[essai(AGENT_REPO)], records=records).run_pass()

    # Told in the journal and abandoned by the engine, with the card's own end as its time: no alert.
    assert [line for line in lines if "anomalie" in line] == [
        f"<4>workflow-core: moteur, essai:7 : anomalie : carte racine {ROOT} terminée à la station clore "
        "par un autre que le moteur, élément abandonné par le moteur"
    ]
    assert records.abandoned("essai", "essai:7")
    assert [signal.opened_at for signal in records.signals_of("essai")] == [int(NOON) - 60]
    assert github.posted == []
