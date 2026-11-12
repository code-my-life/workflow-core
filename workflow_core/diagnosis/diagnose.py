"""The assembly of a diagnosis from the findings of the rules (ADR 0012).

At this stage, the primary cause is the first finding found. The ranking of the findings, the verdict of each run,
the state of the work and the unblocking advice come with the full assembly; until then they hold a valid value of
the contract, without rule. Ported from factory-live's `diagnose` at commit d47f235
(packages/domain/src/diagnosis/diagnose.ts).
"""

from __future__ import annotations

from workflow_core.clock import iso_ms
from workflow_core.config import Workflow
from workflow_core.diagnosis.episode import Episode
from workflow_core.diagnosis.findings import finding_view
from workflow_core.diagnosis.rules import findings_of
from workflow_core.model.diagnosis import Diagnosis, Sources, UnblockAdvice
from workflow_core.model.diagnosis import Episode as EpisodeView
from workflow_core.model.facts import CardFacts, DiagnosisInput, RuntimeTexts


def input_of(facts: CardFacts, board: str, now: float) -> DiagnosisInput:
    """The diagnosis input of a card read from its board alone: no session, journal, configuration nor proof yet."""
    return DiagnosisInput(now=now, board=board, card=facts.card, runs=facts.runs, events=facts.events)


def diagnose(input: DiagnosisInput, texts: RuntimeTexts, workflow: Workflow) -> Diagnosis | None:
    """The diagnosis of the card's current episode; `None` without finding."""
    episode = Episode(input, texts, workflow)
    found = findings_of(episode)
    if not found:
        return None
    return Diagnosis(
        computed_at=iso_ms(input.now),
        episode=EpisodeView(from_=iso_ms(episode.start), run_ids=[run.id for run in episode.runs]),
        primary=finding_view(found[0]),
        others=[],
        unblock=UnblockAdvice(when="after_fix", template=None, next_block=None),
        runs=[],
        work=None,
        sources=Sources(
            transcript=input.sessions is not None,
            agent_log=input.logs is not None,
            config=input.profile is not None,
            workspace=input.evidence is not None,
        ),
    )
