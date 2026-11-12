"""The text of an alert, in French, as the architecture's conventions want a message to the owner: what waits, which
workflow and item, how to answer, the link to factory-live, and the hidden marker that names the wait."""

from __future__ import annotations

import re

ISSUE = re.compile(r"^https://github\.com/([\w.-]+)/([\w.-]+)/issues/(\d+)$")
# The only account whose answers the workflows read on GitHub (the factory's decider).
DECIDER = "cddev"
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
        f"**Pour répondre** (seul `{DECIDER}` est écouté) : commente sur cette issue",
        "```",
        f"/{workflow} unblock ta décision ou ta réponse",
        "```",
    ]
    if live_url:
        lines += ["", f"[Voir l'élément dans factory-live]({live_url}/issues/{repo}/{number})"]
    lines += ["", FOOTER, "", marker(key)]
    return "\n".join(lines)
