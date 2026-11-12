"""The issue of a card, found as the factory finds it (`issue_of`): the title's issue first, else the first address
quoted, else the parent cards'."""

from __future__ import annotations

from workflow_core.adapters import runtime_adapter

from conftest import make_board

WANTED = "https://github.com/code-my-life/workflow-core/issues/99"
OTHER = "https://github.com/code-my-life/factory-live/issues/7"


def card(id: str, title: str, body: str | None) -> dict:
    return {
        "id": id,
        "title": title,
        "body": body,
        "status": "blocked",
        "assignee": "coder",
        "created_at": 1762800000,
        "started_at": None,
        "completed_at": None,
        "block_kind": "needs_input",
    }


def test_the_issue_named_by_the_title_wins(runtime_home, tmp_path, monkeypatch):
    home = tmp_path / "issues"
    make_board(
        home,
        "factory",
        [
            card("t_00000001", "impl workflow-core#99 : le notificateur", f"Voir {OTHER}, puis {WANTED}"),
            card("t_00000002", "Une carte sans numéro", f"Voir {OTHER}, puis {WANTED}"),
        ],
    )
    monkeypatch.setenv("HER" + "MES_HOME", str(home))

    urls = runtime_adapter().make_board_reader().read_issue_urls("factory")

    assert urls == {"t_00000001": WANTED, "t_00000002": OTHER}


def test_a_card_without_an_issue_takes_its_parent_s(runtime_home, tmp_path, monkeypatch):
    home = tmp_path / "issues"
    make_board(
        home,
        "factory",
        [
            card("t_00000001", "Coordination", None),
            card("t_00000002", "Enfant", "Rien ici"),
            card("t_00000003", "Petit-enfant", None),
            card("t_00000004", "Sans parent ni adresse", None),
        ],
        links=[("t_00000001", "t_00000002"), ("t_00000002", "t_00000003")],
        comments=[{"id": 1, "task_id": "t_00000001", "author": "coordinator", "body": WANTED, "created_at": 1}],
    )
    monkeypatch.setenv("HER" + "MES_HOME", str(home))

    urls = runtime_adapter().make_board_reader().read_issue_urls("factory")

    assert urls == {"t_00000001": WANTED, "t_00000002": WANTED, "t_00000003": WANTED, "t_00000004": None}
