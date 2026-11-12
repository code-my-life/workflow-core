"""The values proper to a workflow live in its entry of config/workflows.toml, never in the core's code (R5)."""

import re
from pathlib import Path

from workflow_core.config import load_workflows

ROOT = Path(__file__).resolve().parent.parent
FACTORY_VALUES = re.compile(r"\bcoder\b|\bscribe\b|-guard|-readonly|PR acceptance")


def test_the_factory_entry_carries_the_factory_values():
    workflows = load_workflows(ROOT / "config" / "workflows.toml")
    factory = workflows["factory"]

    assert factory.repo == Path("~/perso/hermes-software-factory")
    assert (factory.profiles, factory.plugins) == ("profiles", "plugins")
    assert factory.guard_suffixes == ["-guard", "-readonly"]
    assert (factory.default_agent, factory.relay_agent, factory.pr_acceptance) == ("coder", "scribe", "PR acceptance")
    studio = workflows["studio"]
    assert studio.repo == Path("~/perso/video-factory")
    assert (studio.profiles, studio.plugins, studio.guard_suffixes) == (None, None, [])
    assert (studio.default_agent, studio.relay_agent, studio.pr_acceptance) == (None, None, None)


def test_no_workflow_value_is_written_in_the_package():
    found = [
        f"{path.relative_to(ROOT)}:{number}: {line.strip()}"
        for path in sorted((ROOT / "workflow_core").rglob("*.py"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if FACTORY_VALUES.search(line)
    ]

    assert found == []
