"""The effective delay of a guard's hook, and the hook timeouts per plugin (ADR 0012).

The cases are those of factory-live's `guards.test.ts` at commit d47f235 on `effectiveHookLimit` and
`hookTimeoutsByPlugin`, with their expected values copied out by hand.
"""

from workflow_core.adapters.hermes.texts import TERMS
from workflow_core.diagnosis.guards import effective_hook_limit, hook_timeouts_by_plugin
from workflow_core.model.facts import LogFact

from test_rules_guards import FACTORY_HOOKS, coder_profile


def limit(hook_timeout, tool_deadline):
    return effective_hook_limit(coder_profile(hook_timeout=hook_timeout, tool_deadline=tool_deadline), TERMS)


def timeout(callback: str, hook: str = "pre_tool_call") -> LogFact:
    return LogFact(
        at=1_793_263_570,
        session_id=None,
        kind="hook_timeout",
        hook=hook,
        callback=callback,
        module=None,
        seconds=30,
        plugin=None,
        detail=None,
        count=None,
        line=f"Hook '{hook}' callback {callback} timed out after 30s",
    )


def test_the_hook_limit_follows_the_default_the_maximum_and_the_tool_ceiling():
    assert limit(None, None) == 30
    assert limit(-5, None) == 30
    assert limit(600, None) == 420
    assert limit(900, 1_200) == 600
    assert limit(120, 900) == 120
    # Without a hook delay nor a tools' ceiling, nothing cuts it any more.
    assert limit(0, None) == 420
    assert limit(600, 0) == 600
    assert limit(0, 0) is None


def test_a_hook_timeout_goes_to_the_only_active_plugin_with_that_callback():
    counts = hook_timeouts_by_plugin(
        [timeout("pre_tool"), timeout("pre_tool"), timeout("post_tool", "post_tool_call")], coder_profile()
    )
    assert list(counts.items()) == [("coder-guard", 3)]

    gate = coder_profile(plugins=["comment-guard", "gate-readonly"], hooks=FACTORY_HOOKS)
    assert hook_timeouts_by_plugin([timeout("guard")], gate) == {}
    # Only one of the two active: the timeout is its own.
    alone = coder_profile(plugins=["gate-readonly"], hooks=FACTORY_HOOKS)
    assert hook_timeouts_by_plugin([timeout("guard")], alone) == {"gate-readonly": 1}
    # The other lines of the journal are not counted.
    skipped = timeout("pre_tool").model_copy(update={"kind": "hook_skipped"})
    assert hook_timeouts_by_plugin([skipped], coder_profile()) == {}
