"""The tool results as the runtime writes them, recognised by its adapter (ADR 0012).

The cases are those of factory-live's `hermesText.test.ts` at commit d47f235 on the tool results the rules of the
guards and the hooks read, with their exact texts, copied out by hand.
"""

from pathlib import Path

from workflow_core.adapters.hermes.texts import HOOK_TIMEOUT_MESSAGE, TEXTS
from workflow_core.config import load_workflows
from workflow_core.diagnosis.support import is_refusal, normalized_refusal
from workflow_core.model.facts import ToolOutcome
from workflow_core.text import quote

FACTORY = load_workflows(Path(__file__).resolve().parent.parent / "config" / "workflows.toml")["factory"]


def classify(content: str) -> dict:
    outcome = TEXTS.classify_tool_result(content, FACTORY.guard_suffixes, FACTORY.pr_acceptance)
    return outcome.model_dump(exclude_none=True)


def test_a_hook_timeout_refusal_is_recognised_by_its_exact_text_only():
    assert classify(f'{{"error": "{HOOK_TIMEOUT_MESSAGE}"}}') == {"kind": "hook_timeout"}
    # kanban_show of a blocked card quotes this text: it is not a refusal.
    quoting = '{"task": {"reason": "bloqué par \'' + HOOK_TIMEOUT_MESSAGE + "'\"}}"
    assert classify(quoting) == {"kind": "ok"}


def test_a_crashing_guard_and_a_refusing_guard_are_recognised():
    assert classify('{"error": "pre_tool_call plugin callback guard raised KeyError: \'command\'"}') == {
        "kind": "hook_raised",
        "callback": "guard",
        "error": "KeyError: 'command'",
    }
    assert classify('{"error": "coder-guard : git add . / -A interdit : ajoute les fichiers un par un."}') == {
        "kind": "guard",
        "plugin": "coder-guard",
        "message": "git add . / -A interdit : ajoute les fichiers un par un.",
    }
    assert classify('{"error": "gate-readonly : redirection vers /tmp/x refusée, écris sous evidence/."}')[
        "plugin"
    ] == ("gate-readonly")
    assert classify(
        '{"error": "PR acceptance missing: No repository-required checks are configured; '
        'explicitly use a local-only contract."}'
    ) == {
        "kind": "pr_acceptance",
        "classification": "missing",
        "detail": "No repository-required checks are configured; explicitly use a local-only contract.",
    }
    assert classify('{"error": "path required"}') == {"kind": "error", "message": "path required"}
    # Cut contents: classified by their start (a refusal) or their end (an exit code).
    assert classify('{"error": "coder-guard : commit refusé, corrige puis recom')["kind"] == "guard"
    assert classify('{"output": "npm ERR …", "exit_code": 124, "error": null')["kind"] == "command_timeout"
    for nothing in ('{"output": "coupé sans fin', '{"error": "\\', "[1, 2]", "texte libre"):
        assert classify(nothing) == {"kind": "ok"}, nothing
    # The tool's ceiling and a command stopped by its delay.
    assert classify("Error executing tool 'terminal': timed out after 420.0s") == {
        "kind": "tool_deadline",
        "tool": "terminal",
        "seconds": 420,
    }
    assert classify('{"output": "[Command timed out after 900s]", "exit_code": 124, "error": null}') == {
        "kind": "command_timeout",
        "seconds": 900,
    }
    # Without the workflow's guard suffixes, a guard's refusal is an ordinary error.
    plain = TEXTS.classify_tool_result('{"error": "coder-guard : non."}', [], None)
    assert plain.kind == "error"


def test_only_what_prevents_acting_counts_as_a_refusal():
    assert is_refusal(ToolOutcome(kind="hook_timeout"))
    assert is_refusal(ToolOutcome(kind="guard", plugin="coder-guard", message="x"))
    assert not is_refusal(ToolOutcome(kind="error", message="x"))


def test_two_refusals_differing_by_a_path_or_a_number_are_grouped():
    assert normalized_refusal("redirection vers /tmp/a.txt refusée, 3 fois\nsuite") == normalized_refusal(
        "redirection vers /tmp/b.txt refusée, 4 fois"
    )


def test_a_quote_is_kept_as_is_with_secrets_masked_and_cut_at_300():
    assert quote(None) is None
    assert quote("") is None
    assert "ghp_ABCDEF" not in quote("jeton ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
    assert len(quote("x" * 400)) == 300


def test_an_exit_without_kanban_call_and_the_runtime_retry_are_recognised():
    assert TEXTS.is_protocol_violation(
        "worker exited cleanly (rc=0) without kanban_complete, kanban_block or kanban_request_review "
        "\u2014 protocol violation. If…"
    )
    assert not TEXTS.is_protocol_violation(None)
    assert TEXTS.was_nudged("⚠️ Kanban worker tried to exit without a terminal board call (…) nudging to finish")
    assert not TEXTS.was_nudged(None)


def test_the_turn_budget_the_runtime_limit_and_the_run_session_are_read():
    assert TEXTS.budget_of(
        "Iteration budget exhausted (150/150) \u2014 task could not complete within the allowed iterations"
    ) == (150, 150)
    assert TEXTS.budget_of(None) is None
    assert TEXTS.runtime_of("elapsed 7300s > limit 7200s") == (7300, 7200)
    assert TEXTS.runtime_of("autre chose") is None
    assert TEXTS.resume_session_of("Resume this session with: hermes --resume 20261029_091353_0a2b3f -p coder") == (
        "20261029_091353_0a2b3f"
    )
    assert TEXTS.resume_session_of(None) is None


def test_a_tool_deadline_and_a_command_timeout_are_recognised():
    assert classify("Error executing tool 'terminal': timed out after 420.0s") == {
        "kind": "tool_deadline",
        "tool": "terminal",
        "seconds": 420,
    }
    assert classify('{"output": "[Command timed out after 900s]", "exit_code": 124, "error": null}') == {
        "kind": "command_timeout",
        "seconds": 900,
    }
    assert classify('{"output": "", "exit_code": 124, "error": null}') == {"kind": "command_timeout"}


def test_a_silent_provider_or_a_failing_fallback_chain_is_recognised():
    assert TEXTS.provider_failure_of("Sending after interrupt: 'Turn made no progress for 669s; aborting") == (
        "stalled",
        669,
    )
    assert TEXTS.provider_failure_of("every provider in the fallback chain kept failing over, so the turn stopped") == (
        "failing",
        None,
    )
    assert TEXTS.provider_failure_of("Duration: 35m 37s") is None
    assert TEXTS.provider_failure_of(None) is None


def test_the_pr_acceptance_lines_are_recognised_by_the_declared_prefix():
    missing = (
        '{"error": "PR acceptance missing: No repository-required checks are configured; '
        'explicitly use a local-only contract."}'
    )
    assert classify(missing) == {
        "kind": "pr_acceptance",
        "classification": "missing",
        "detail": "No repository-required checks are configured; explicitly use a local-only contract.",
    }
    assert classify('{"error": "PR acceptance infra: gh: Could not resolve host"}') == {
        "kind": "pr_acceptance",
        "classification": "infra",
        "detail": "gh: Could not resolve host",
    }
    # Another prefix, or none declared: an ordinary error.
    assert TEXTS.classify_tool_result(missing, FACTORY.guard_suffixes, "Contrôle de PR").kind == "error"
    assert TEXTS.classify_tool_result(missing, FACTORY.guard_suffixes, None).kind == "error"
