"""The text of an alert, in French, as the architecture's conventions want a message to the owner: what waits, which
workflow and item, how to answer, the link to factory-live, and the hidden marker that names the wait."""

from __future__ import annotations

import re

ISSUE = re.compile(r"^https://github\.com/([\w.-]+)/([\w.-]+)/issues/(\d+)$")
QUESTION_MAX = 3000
FOOTER = "<sub>Alerte du socle workflow-core</sub>"
KIND_FR = {
    "needs_input": "Décision attendue",
    "capability": "Outil ou accès manquant",
    "transient": "Échec passager",
}


def marker(key: str) -> str:
    """The hidden marker of a wait, exactly: one space on each side of the key."""
    return f"<!-- workflow-core:attente {key} -->"


def item_link(live_url: str | None, key: str) -> str | None:
    """The link of a workflow's items in factory-live, `<live_url>/workflows/<key>`; None without factory-live's
    address."""
    return f"{live_url}/workflows/{key}" if live_url else None


def parse_issue(url: str) -> tuple[str, str, int] | None:
    """`(owner, repository, number)` of an issue address."""
    found = ISSUE.match(url)
    return (found[1], found[2], int(found[3])) if found else None


def blocked_alert(
    *,
    key: str,
    workflow: str,
    kind: str,
    issue: str,
    card: str,
    title: str,
    question: str,
    live_url: str | None,
) -> str:
    _, repo, number = parse_issue(issue)
    quoted = (question.strip() or "(pas de raison fournie)")[:QUESTION_MAX]
    lines = [
        f"### ⏸ {KIND_FR.get(kind, 'Bloquée')}",
        f"Workflow `{workflow}` · élément `{workflow}:{repo}#{number}` · carte `{card}` : {title}",
        "",
        "**Question de l'agent**",
        "",
        "> " + quoted.replace("\n", "\n> "),
        "",
        "**Pour répondre** (seul le propriétaire est écouté) : commente sur cette issue",
        "```",
        f"/{workflow} unblock ta décision ou ta réponse",
        "```",
    ]
    if live_url:
        lines += ["", f"[Voir l'élément dans factory-live]({live_url}/issues/{repo}/{number})"]
    lines += ["", FOOTER, "", marker(key)]
    return "\n".join(lines)


def held_alert(
    *,
    key: str,
    workflow: str,
    issue: str,
    card: str,
    title: str,
    minutes: int,
    relaunch: str | None,
    live_url: str | None,
) -> str:
    _, repo, number = parse_issue(issue)
    lines = [
        "### ⚠️ Carte prête mais retenue",
        f"Workflow `{workflow}` · élément `{workflow}:{repo}#{number}` · carte `{card}` : {title}",
        "",
        f"Prête depuis {minutes} min, aucun agent ne la prend : la garde `active_pr` la retient (un commentaire de la "
        "carte cite déjà sa pull request, et aucun relais n'a suivi). Ne pas la bloquer : elle passerait en triage.",
        "",
    ]
    if relaunch:
        lines += ["**Relance** (opérateur) :", "```", relaunch, "```"]
    else:
        lines += ["Relance : voir le RUNBOOK de la factory, « Incidents connus »."]
    if live_url:
        lines += ["", f"[Voir l'élément dans factory-live]({live_url}/issues/{repo}/{number})"]
    lines += ["", FOOTER, "", marker(key)]
    return "\n".join(lines)


def _element_link(live_url: str | None, workflow: str) -> list[str]:
    link = item_link(live_url, workflow)
    return ["", f"[Voir l'élément dans factory-live]({link})"] if link else []


def _quote(text: str, empty: str) -> str:
    quoted = (text.strip() or empty)[:QUESTION_MAX]
    return "> " + quoted.replace("\n", "\n> ")


def owner_wait_alert(
    *, key: str, workflow: str, element: str, station: str, gestures: list[str], live_url: str | None
) -> str:
    """The alert of a wait of an `owner` station of a workflow plugged by its manifest: its key is the wait's id."""
    lines = [
        f"### ⏸ Ta réponse est attendue : {station}",
        f"Workflow `{workflow}` · élément `{element}` · attente `{key}`",
        "",
        "**Pour répondre** (seul le propriétaire est écouté) : commente sur cette issue l'un de ces gestes, suivi "
        "d'une note si tu veux",
        "```",
        *[f"/{workflow} {gesture} [note]" for gesture in gestures],
        "```",
    ]
    lines += _element_link(live_url, workflow)
    lines += ["", FOOTER, "", marker(key)]
    return "\n".join(lines)


def failure_alert(
    *,
    key: str,
    workflow: str,
    element: str,
    station: str,
    card: str,
    title: str,
    reason: str,
    board: str,
    child: bool,
    live_url: str | None,
) -> str:
    """The alert of a failure at a station of a workflow plugged by its manifest: its root card blocked (with its
    motive, unblocked by the owner's `unblock`), or a child card of an `agent` station blocked (with the agent's
    question, unblocked by the operator on the board)."""
    lines = [
        f"### ⛔ Échec à la station {station}",
        f"Workflow `{workflow}` · élément `{element}` · carte `{card}` : {title}",
        "",
        "**Question de l'agent**" if child else "**Motif**",
        "",
        _quote(reason, "(pas de raison fournie)"),
        "",
    ]
    if child:
        lines += [f"**Pour reprendre** : débloquer la carte fille `{card}` du board `{board}` (opérateur)."]
    else:
        lines += [
            "**Pour reprendre** (seul le propriétaire est écouté) : commente sur cette issue",
            "```",
            f"/{workflow} unblock [note]",
            "```",
        ]
    lines += _element_link(live_url, workflow)
    lines += ["", FOOTER, "", marker(key)]
    return "\n".join(lines)


SIGNAL_TITLE = {
    "finished_elsewhere": "Carte racine terminée par un autre que le moteur",
    "taken": "Carte racine prise par un agent",
}
SIGNAL_EFFECT = {
    "finished_elsewhere": "Le moteur ne touche plus à cet élément.",
    "taken": "Le moteur laisse l'élément tant que ce run dure, puis le reprend.",
}


def signal_alert(
    *, key: str, workflow: str, element: str, kind: str, station: str, card: str, detail: str, live_url: str | None
) -> str:
    """The alert of an anomaly the engine noted on an item's root card: finished by another, or taken by an agent."""
    lines = [
        f"### ⚠️ {SIGNAL_TITLE.get(kind, 'Anomalie du moteur')}",
        f"Workflow `{workflow}` · élément `{element}` · carte `{card}` · station {station}",
        "",
        detail,
        "",
        SIGNAL_EFFECT.get(kind, ""),
        "",
        "Le moteur seul fait avancer un élément : voir le RUNBOOK du socle, « Le notificateur ».",
    ]
    lines += _element_link(live_url, workflow)
    lines += ["", FOOTER, "", marker(key)]
    return "\n".join(lines)
