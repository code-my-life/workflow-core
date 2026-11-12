"""The assembly of a diagnosis from the findings of the rules (ADR 0012): deterministic, whatever the order of facts.

Ported from factory-live's `diagnose` at commit d47f235 (packages/domain/src/diagnosis/diagnose.ts): every rule,
plus the agent's question and `unexplained` when nothing applies; the primary cause by rank; the other findings
(four at most), without the ones a kept cause explains; the verdict of each failed run; the unblocking advice; the
state of the work; the sources read. The contract's form is `diagnosisView` (apps/server/src/application).
"""

from __future__ import annotations

import re

from workflow_core.clock import iso_ms
from workflow_core.config import Workflow
from workflow_core.diagnosis.episode import Episode, Observation
from workflow_core.diagnosis.findings import NONE, UNKNOWN, RawFinding, evidence, finding_view, step, subject, unique
from workflow_core.diagnosis.rules import findings_of
from workflow_core.diagnosis.rules_runs import ended_on_refusals
from workflow_core.diagnosis.support import GIT_COMMIT, is_refusal, locale_key
from workflow_core.model.diagnosis import (
    Diagnosis,
    EvidenceFile,
    Message,
    RunVerdict,
    Sources,
    UnblockAdvice,
    WorkState,
    message,
)
from workflow_core.model.diagnosis import Episode as EpisodeView
from workflow_core.model.facts import DiagnosisInput, RuntimeTexts
from workflow_core.text import js_regex, js_trim, quote

# What a cause explains: its consequences are not causes on their own.
EXPLAINS: dict[str, tuple[str, ...]] = {
    "guard_hook_timeout": ("stopped_after_refusals", "protocol_breaker", "question", "guard_refusal"),
    "guard_hook_hung": ("stopped_after_refusals", "protocol_breaker", "question"),
    "guard_hook_raised": ("stopped_after_refusals", "protocol_breaker", "question"),
    "plugin_load_failed": ("question",),
    "triage_loop": ("question",),
    "refusal_loop": ("stopped_after_refusals", "protocol_breaker", "iteration_budget", "question"),
    "guard_refusal": ("stopped_after_refusals", "protocol_breaker"),
    "stopped_after_refusals": ("protocol_breaker",),
    "stopped_without_kanban_call": ("protocol_breaker",),
    "pr_acceptance_infra": ("stopped_after_refusals", "protocol_breaker", "question"),
    "pr_acceptance_missing": ("stopped_after_refusals", "protocol_breaker", "question"),
    "provider_unavailable": ("protocol_breaker",),
    "spawn_failed": ("protocol_breaker",),
    "tool_deadline": ("command_timeout",),
}
# The lower the tier, the earlier the cause: infrastructure, transient, agent, question.
TIER: dict[str, int] = {
    "guard_hook_timeout": 1,
    "guard_hook_hung": 1,
    "guard_hook_raised": 1,
    "plugin_load_failed": 1,
    "spawn_failed": 1,
    "provider_terminal": 1,
    "pr_acceptance_infra": 1,
    "tool_deadline": 1,
    "iteration_budget": 1,
    "runtime_limit": 1,
    "held_active_pr": 1,
    "triage_loop": 1,
    "provider_unavailable": 2,
    "provider_quota": 2,
    "refusal_loop": 3,
    "pr_acceptance_missing": 3,
    "stopped_without_kanban_call": 3,
    "stopped_after_refusals": 3,
    "protocol_breaker": 3,
    "guard_refusal": 3,
    "command_timeout": 3,
    "question": 4,
    "unexplained": 5,
}
ORDER = list(TIER)
# What the card lives now (held, in triage) comes before what happened in its runs.
CURRENT_STATE = frozenset({"held_active_pr", "triage_loop"})
# The causes that make a run fail by refusing its tools: the run suffers them.
SUFFERED = frozenset(
    {"guard_hook_timeout", "guard_hook_hung", "guard_hook_raised", "stopped_after_refusals", "guard_refusal"}
)
# A commit made: git writes « [<branch> <short sha>] <subject> ». A branch name holds neither `[` nor `]` nor a
# space: excluding them keeps the search linear, even on a tool output made of brackets.
COMMIT_MADE = js_regex(r"\[[^[\]\s]{1,250} [0-9a-f]{7,40}\]")
GIT_PUSH = js_regex(r"\bgit\s+push\b")
FAILED_EXIT = js_regex(r'"exit_code":\s*[1-9]')


def _last_evidence_at(finding: RawFinding) -> float:
    return max(
        [0.0]
        + [item.until if item.until is not None else item.at if item.at is not None else 0 for item in finding.evidence]
    )


def explained_by(finding: RawFinding, every: list[RawFinding]) -> bool:
    return any(
        other is not finding
        and finding.code in EXPLAINS.get(other.code, ())
        and (not finding.run_ids or any(run_id in other.run_ids for run_id in finding.run_ids))
        for other in every
    )


def _rank(episode: Episode, every: list[RawFinding]):
    def key(finding: RawFinding) -> tuple:
        return (
            0 if finding.code in CURRENT_STATE else 1,
            0 if episode.blocking_run_id is not None and episode.blocking_run_id in finding.run_ids else 1,
            1 if explained_by(finding, every) else 0,
            TIER[finding.code],
            0 if finding.confidence == "certain" else 1,
            -_last_evidence_at(finding),
            ORDER.index(finding.code),
        )

    return key


def question(episode: Episode) -> RawFinding | None:
    """A real question of the agent: blocked for an answer, without any technical cause found."""
    if episode.block_kind != "needs_input":
        return None
    blocking = episode.blocking_run_id
    return RawFinding(
        code="question",
        confidence="certain",
        actor="you_answer",
        cause=NONE,
        summary=message("diagnosis.summary.question"),
        why=message("diagnosis.why.question"),
        subject=subject(episode.card.assignee),
        setting=None,
        evidence=[],
        steps=[step(message("diagnosis.step.answer"))],
        run_ids=[blocking] if blocking is not None else [],
    )


def unexplained(episode: Episode) -> RawFinding:
    """Nothing recognised: the episode's last refusals and errors, as is, to read oneself."""
    signals = [item for item in episode.observations if is_refusal(item.outcome) or item.outcome.kind == "error"][-3:]
    last = signals[-1] if signals else None
    blocking = episode.blocking_run_id
    return RawFinding(
        code="unexplained",
        confidence="likely",
        actor="you_answer",
        cause=UNKNOWN,
        summary=message("diagnosis.summary.unexplained"),
        why=message("diagnosis.why.unexplained"),
        subject=subject(episode.card.assignee),
        setting=None,
        evidence=[
            evidence(
                "transcript",
                signals[0].result.at,
                message("diagnosis.evidence.lastSignals", count=len(signals)),
                until=last.result.at,
                run_id=last.run_id,
                quote=quote("\n".join(item.result.content for item in signals)),
                count=len(signals),
            )
        ]
        if last is not None
        else [],
        steps=[],
        run_ids=[blocking] if blocking is not None else [],
    )


def _refused_by(episode: Episode, code: str, run_id: int) -> int:
    """The refusals of a run the cause explains: the runtime's for a hook, the guards' otherwise."""

    def counted(item: Observation) -> bool:
        if code in ("guard_hook_timeout", "guard_hook_hung"):
            return item.outcome.kind == "hook_timeout"
        if code == "guard_hook_raised":
            return item.outcome.kind == "hook_raised"
        if code == "guard_refusal":
            return item.outcome.kind == "guard"
        return is_refusal(item.outcome)

    return sum(1 for item in episode.observations_of_run(run_id) if counted(item))


def _verdict_text(episode: Episode, finding: RawFinding | None, run_id: int, outcome: str) -> Message:
    code = finding.code if finding is not None else None
    if code in SUFFERED:
        count = (
            len(ended_on_refusals(episode, run_id))
            if code == "stopped_after_refusals"
            else _refused_by(episode, code, run_id)
        )
        return message(
            "diagnosis.run.blockedAfterRefusals" if outcome == "blocked" else "diagnosis.run.refusedThenStopped",
            count=count,
        )
    texts = {
        "stopped_without_kanban_call": "diagnosis.run.modelStopped",
        "protocol_breaker": "diagnosis.run.breaker",
        "iteration_budget": "diagnosis.run.timedOut",
        "runtime_limit": "diagnosis.run.timedOut",
        "tool_deadline": "diagnosis.run.timedOut",
        "provider_quota": "diagnosis.run.quota",
        "provider_unavailable": "diagnosis.run.providerUnavailable",
        "provider_terminal": "diagnosis.run.providerUnavailable",
        "question": "diagnosis.run.question",
        "triage_loop": "diagnosis.run.question",
        "spawn_failed": "diagnosis.run.spawnFailed",
        "pr_acceptance_infra": "diagnosis.run.prAcceptance",
        "pr_acceptance_missing": "diagnosis.run.prAcceptance",
    }
    if code in texts:
        return message(texts[code])
    if code == "refusal_loop":
        count = sum(1 for item in episode.observations_of_run(run_id) if item.outcome.kind == "guard")
        return message("diagnosis.run.refusalLoop", count=count)
    return message("diagnosis.run.unexplained", outcome=outcome)


def _role(finding: RawFinding, every: list[RawFinding]) -> str:
    """Is the run the cause of its own failure, or did it suffer the consequence of a cause?"""
    return "consequence" if finding.code in SUFFERED or explained_by(finding, every) else "cause"


def work_state_of(input: DiagnosisInput, texts: RuntimeTexts, workflow: Workflow) -> WorkState | None:
    """Where the card's work stands, from the agent's calls (commit, push, edited files) and its work folder. The
    core never knows the pull request: a known one still proves the push."""
    sessions = input.sessions
    proofs = input.evidence or []
    if sessions is None and not proofs and not input.branch and not input.pr_url:
        return None
    items: list[Observation] = []
    for session in sessions or []:
        calls = {call.call_id: call for call in session.calls if call.call_id}
        for result in session.results:
            call = calls.get(result.call_id) if result.call_id else None
            if call is not None:
                outcome = texts.classify_tool_result(result.content, workflow.guard_suffixes, workflow.pr_acceptance)
                items.append(Observation(session.run_id, session.id, result, call, outcome))
    commits = [item for item in items if GIT_COMMIT.search(item.call.input or "")]
    made = [item for item in commits if item.outcome.kind == "ok" and COMMIT_MADE.search(item.result.content)]
    refused = [item for item in commits if is_refusal(item.outcome)]
    pushes = [
        item
        for item in items
        if GIT_PUSH.search(item.call.input or "")
        and item.outcome.kind == "ok"
        and not FAILED_EXIT.search(item.result.content)
    ]
    edited = unique(
        call.input
        for session in sessions or []
        for call in session.calls
        if call.tool in texts.terms.edit_tools and call.input
    )
    return WorkState(
        branch=input.branch,
        commits=("some" if made else "none") if sessions is not None else "unknown",
        commits_refused=len(refused),
        pushed="yes" if pushes or input.pr_url else "no" if sessions is not None else "unknown",
        pr_url=None,
        edited_files=len(edited),
        evidence=[
            EvidenceFile(name=file.name, bytes=file.bytes, at=iso_ms(file.at))
            for file in sorted(proofs, key=lambda file: locale_key(file.name))[:50]
        ],
    )


def _template(episode: Episode, primary: RawFinding, work: WorkState | None) -> Message | None:
    terms = episode.terms
    branch = work.branch if work is not None else None
    code = primary.code
    if code == "guard_hook_timeout":
        setting = primary.setting
        observed = (
            setting.observed if setting is not None and setting.observed is not None else terms.hook_timeout_default
        )
        current = setting.current if setting is not None else None
        value = current if current is not None and current > observed else terms.hook_timeout_max
        return message(
            "diagnosis.unblock.hookTimeoutFixed",
            plugin=primary.subject.plugin,
            seconds=observed,
            value=value,
            branch=branch,
            committed=work is not None and work.commits == "some",
            evidence=len(work.evidence) if work is not None and work.evidence else None,
        )
    if code == "refusal_loop":
        quoted = (primary.evidence[0].quote if primary.evidence else None) or ""
        suffixes = "|".join(re.escape(suffix) for suffix in episode.workflow.guard_suffixes)
        found = (
            js_regex(rf'"error":\s*"(?:[a-z][a-z0-9-]*(?:{suffixes})) : ([^"\\]*)').search(quoted) if suffixes else None
        )
        refusal = js_trim(found[1] if found else quoted)
        return message("diagnosis.unblock.refusalLoop", plugin=primary.subject.plugin or "?", refusal=refusal)
    if code in ("stopped_without_kanban_call", "stopped_after_refusals", "protocol_breaker"):
        return message("diagnosis.unblock.finishWithKanbanCall")
    if code in ("question", "unexplained", "provider_quota"):
        return None
    return message("diagnosis.unblock.causeFixed", branch=branch)


def _advice(episode: Episode, primary: RawFinding, kept: list[RawFinding], work: WorkState | None) -> UnblockAdvice:
    card = episode.card
    if card.status == "blocked":
        next_block = "triage" if card.block_recurrences + 1 >= episode.terms.block_recurrence_limit else "blocked"
    else:
        next_block = None
    if primary.code in ("held_active_pr", "triage_loop") or card.status == "triage":
        return UnblockAdvice(when="not_unblockable", template=None, next_block=None)
    factory_present = any(finding.actor == "you_factory" and finding.cause.state == "present" for finding in kept)
    if factory_present:
        when = "after_fix"
    elif primary.actor == "wait":
        when = "wait"
    elif primary.code in ("question", "unexplained"):
        when = "answer"
    else:
        when = "now"
    return UnblockAdvice(when=when, template=_template(episode, primary, work), next_block=next_block)


def diagnose(input: DiagnosisInput, texts: RuntimeTexts, workflow: Workflow) -> Diagnosis:
    """Why the card stops, from its facts: the same input gives the same diagnosis."""
    episode = Episode(input, texts, workflow)
    found = findings_of(episode)
    asked = question(episode)
    candidates = [*found, asked] if asked is not None else found
    every = candidates or [unexplained(episode)]
    ranked = sorted(every, key=_rank(episode, every))
    primary = ranked[0]
    roots = [finding for finding in ranked if finding is not primary and not explained_by(finding, every)]
    work = work_state_of(input, texts, workflow)
    verdicts = []
    for run in episode.failed_runs():
        # The run's own cause: the breaker and the consequences only come for lack of anything else.
        touching = sorted(
            (finding for finding in ranked if run.id in finding.run_ids),
            key=lambda finding: (finding.code == "protocol_breaker", explained_by(finding, every)),
        )
        finding = touching[0] if touching else None
        session = next((item.id for item in input.sessions or [] if item.run_id == run.id), None)
        verdicts.append(
            RunVerdict(
                run_id=run.id,
                finding=finding.code if finding is not None else None,
                role=_role(finding, every) if finding is not None else None,
                text=_verdict_text(episode, finding, run.id, run.outcome or "?"),
                session_id=session,
            )
        )
    return Diagnosis(
        computed_at=iso_ms(input.now),
        episode=EpisodeView(from_=iso_ms(episode.start), run_ids=[run.id for run in episode.runs]),
        primary=finding_view(primary),
        others=[finding_view(finding) for finding in roots[:4]],
        unblock=_advice(episode, primary, [primary, *roots], work),
        runs=verdicts,
        work=work,
        sources=Sources(
            transcript=input.sessions is not None,
            agent_log=input.logs is not None,
            config=input.profile is not None,
            workspace=input.evidence is not None,
        ),
    )
