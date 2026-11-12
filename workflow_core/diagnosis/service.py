"""The diagnosis of the triggers of a board: the input of each card, built as factory-live's diagnosis service
builds it, then diagnosed (ADR 0012).

Ported from factory-live at commit d47f235 (apps/server/src/application/diagnosis.ts, `compute`; `branchOf`,
packages/domain/src/interventions.ts). A source that cannot be read gives `None` in the input, and the diagnosis says
it in its `sources`: it never makes the answer fail.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable

from workflow_core.config import Workflow
from workflow_core.diagnosis.diagnose import diagnose
from workflow_core.diagnosis.triggers import trigger_of, triggers_of
from workflow_core.model.diagnosis import Diagnosis, RunVerdict
from workflow_core.model.facts import CardFacts, DiagnosisInput, DiagnosisSources, EventFact, RunFact, SessionFacts
from workflow_core.model.triggers import Trigger
from workflow_core.text import js_regex, js_trim

# The runs read for a diagnosis: the episode, and what follows it, hold in the last ones.
RUNS_READ = 8
# A git branch name: no space, no `[`, no control character (and no `..`).
BRANCH = js_regex(r"^[\w./-]{1,250}$")


def _safe[T](read: Callable[[], T]) -> T | None:
    """A source read: one that fails is unreadable, never a failed answer."""
    try:
        return read()
    except Exception:
        return None


def branch_of(facts: CardFacts, workflow: Workflow) -> str | None:
    """A card's branch: declared on the card, else noted by its last run, else the one its body names (the
    workflow's `body_branch`)."""
    noted = [
        js_trim(run.metadata["branch"])
        for run in sorted(facts.runs, key=lambda run: run.id)
        if isinstance(run.metadata.get("branch"), str) and js_trim(run.metadata["branch"])
    ]
    briefed = None
    if workflow.body_branch and facts.card.body:
        found = re.search(workflow.body_branch, facts.card.body, re.MULTILINE)
        briefed = found[1] if found else None
    branch = facts.card.branch_name or (noted[-1] if noted else None) or briefed
    return branch if branch and BRANCH.fullmatch(branch) and ".." not in branch else None


def _sessions(sources: DiagnosisSources, card: str, runs: list[RunFact]) -> list[SessionFacts] | None:
    """The sessions of the runs read, agent by agent; unreadable only when no agent's base could be read."""
    by_agent: dict[str, list[RunFact]] = {}
    for run in runs:
        by_agent.setdefault((run.profile or "").strip() or "?", []).append(run)
    readable = False
    found: list[SessionFacts] = []
    for agent, agent_runs in by_agent.items():
        facts = _safe(lambda agent=agent, agent_runs=agent_runs: sources.sessions.run_facts(agent, card, agent_runs))
        if facts is None:
            continue
        readable = True
        found += facts
    return found if readable or not runs else None


def examined_agent(facts: CardFacts, anchor: EventFact | None) -> str | None:
    """The agent whose configuration and journal count: the told event's run's, else the last run's, else the
    card's assignee (factory-live's `compute`)."""
    runs = sorted(facts.runs, key=lambda run: run.id)
    if anchor is not None and anchor.run_id:
        told = next((run for run in runs if run.id == anchor.run_id), None)
        if told is not None:
            return (told.profile or "").strip() or "?"
    return ((runs[-1].profile or "").strip() or "?") if runs else facts.card.assignee


def diagnosis_input(
    facts: CardFacts,
    anchor_id: int | None,
    board: str,
    now: float,
    sources: DiagnosisSources,
    workflow: Workflow,
) -> DiagnosisInput:
    """The input of a card: the card, all its runs and events, the event told, the sessions of the last eight runs,
    the examined agent's journal from the first run read minus 60 s, its configuration, the card's proofs, its
    branch and its agent."""
    runs = sorted(facts.runs, key=lambda run: run.id)
    events = sorted(facts.events, key=lambda event: event.id)
    anchor = next((event for event in events if event.id == anchor_id), None) if anchor_id is not None else None
    read = runs[-RUNS_READ:]
    agent = examined_agent(facts, anchor)
    start = read[0].started_at if read else facts.card.created_at
    return DiagnosisInput(
        now=now,
        board=board,
        card=facts.card,
        runs=runs,
        events=events,
        anchor=anchor,
        sessions=_sessions(sources, facts.card.id, read),
        logs=_safe(lambda: sources.logs.facts(agent, start - 60, now)) if agent else None,
        profile=_safe(lambda: sources.profiles.facts(agent, workflow)) if agent else None,
        evidence=_safe(lambda: sources.evidence.evidence(board, facts.card.id)),
        branch=branch_of(facts, workflow),
        # The core never knows the pull request (the engine's epic); factory-live fills it in.
        pr_url=None,
        owner=facts.card.assignee,
    )


def diagnose_card(
    facts: CardFacts, workflow: Workflow, now: float, sources: DiagnosisSources
) -> tuple[Trigger, Diagnosis] | None:
    """The trigger of a card and its diagnosis; `None` when the card is not a trigger."""
    trigger = trigger_of(facts, workflow, now)
    if trigger is None:
        return None
    input = diagnosis_input(facts, trigger.event, workflow.board, now, sources, workflow)
    return trigger, diagnose(input, sources.texts, workflow)


def diagnose_board(
    cards: list[CardFacts], workflow: Workflow, now: float, sources: DiagnosisSources
) -> list[tuple[Trigger, Diagnosis]]:
    """Every trigger of a board with its diagnosis, by opening time."""
    by_id = {facts.card.id: facts for facts in cards}
    found = []
    for trigger in triggers_of(cards, workflow, now):
        input = diagnosis_input(by_id[trigger.card], trigger.event, workflow.board, now, sources, workflow)
        found.append((trigger, diagnose(input, sources.texts, workflow)))
    return found


# A run's outcomes that are failures, and the events that tell the failure of a run.
FAILED = frozenset({"crashed", "blocked", "gave_up", "timed_out", "rate_limited", "spawn_failed"})
FAILURE_EVENTS = frozenset(
    {"blocked", "gave_up", "block_loop_detected", "crashed", "timed_out", "protocol_violation", "spawn_failed"}
)
# The episodes read again for the verdicts of a card's runs.
EPISODES_READ = 4


def anchor_of(events: list[EventFact], runs: list[RunFact], run: RunFact) -> int | None:
    """The event that tells a run's failure: the last of its own, or the one the runtime notes without a run just
    after it (the breaker's give-up), before the next run starts."""
    own = [event for event in events if event.run_id == run.id]
    last = own[-1].id if own else None
    following = next((item for item in runs if item.id > run.id), None)
    next_start = math.inf
    if following is not None:
        first = next((event for event in events if event.run_id == following.id), None)
        next_start = first.id if first is not None else math.inf
    told = [
        event
        for event in events
        if event.kind in FAILURE_EVENTS
        and (event.run_id == run.id or (event.run_id is None and last is not None and last < event.id < next_start))
    ]
    return told[-1].id if told else None


def run_verdicts(facts: CardFacts, workflow: Workflow, now: float, sources: DiagnosisSources) -> list[RunVerdict]:
    """The verdict of each failed run of a card, finished or not, newest run first: each failed run without a
    verdict yet has its episode diagnosed, four episodes at most (factory-live's `verdictsOf`)."""
    runs = sorted(facts.runs, key=lambda run: run.id)
    failed = [run for run in runs if run.outcome is not None and run.outcome in FAILED]
    events = sorted(facts.events, key=lambda event: event.id)
    found: dict[int, RunVerdict] = {}
    read = 0
    for run in reversed(failed):
        if run.id in found:
            continue
        if read >= EPISODES_READ:
            break
        read += 1
        input = diagnosis_input(facts, anchor_of(events, runs, run), workflow.board, now, sources, workflow)
        for verdict in diagnose(input, sources.texts, workflow).runs:
            found.setdefault(verdict.run_id, verdict)
    return sorted(found.values(), key=lambda verdict: -verdict.run_id)
