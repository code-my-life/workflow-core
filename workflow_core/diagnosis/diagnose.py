"""The assembly of a diagnosis from the findings of the rules (ADR 0012).

At this stage, the primary cause is the first finding found. The ranking of the findings, the verdict of each run,
the state of the work and the unblocking advice come with the full assembly; until then they hold a valid value of
the contract, without rule. Ported from factory-live's `diagnose` at commit d47f235
(packages/domain/src/diagnosis/diagnose.ts).
"""

from __future__ import annotations

from workflow_core.clock import iso_ms
from workflow_core.diagnosis.episode import Episode
from workflow_core.diagnosis.rules import RULES
from workflow_core.model.diagnosis import Diagnosis, Sources, UnblockAdvice
from workflow_core.model.diagnosis import Episode as EpisodeView
from workflow_core.model.facts import CardFacts, RuntimeTerms


def diagnose(facts: CardFacts, terms: RuntimeTerms, now: float) -> Diagnosis | None:
    """The diagnosis of the card's current episode, computed at `now` (Unix seconds); `None` without finding."""
    episode = Episode(facts, now)
    found = [finding for rule in RULES for finding in rule(episode, terms)]
    if not found:
        return None
    return Diagnosis(
        computed_at=iso_ms(now),
        episode=EpisodeView(from_=iso_ms(episode.start), run_ids=[run.id for run in episode.runs]),
        primary=found[0],
        others=[],
        unblock=UnblockAdvice(when="after_fix", template=None, next_block=None),
        runs=[],
        work=None,
        # No session, agent log, configuration or work folder is read yet.
        sources=Sources(transcript=False, agent_log=False, config=False, workspace=False),
    )
