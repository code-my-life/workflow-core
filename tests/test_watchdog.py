"""The watchdog: an outage of /healthz of 5 minutes or more is written to journald once (ADR 0019)."""

import ast
import io
import json
import re
import sys
import unicodedata
from datetime import UTC, datetime, timedelta
from pathlib import Path

from healthz_watchdog import HEALTHZ_URL, run_once

WATCHDOG = Path(__file__).resolve().parent.parent / "scripts" / "healthz_watchdog.py"
START = datetime(2026, 11, 11, 20, 0, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> None:
        self.now += timedelta(**delta)


def passage(state: Path, clock: Clock, up: bool) -> list[str]:
    """One run of the watchdog with a simulated probe; the lines it writes."""
    out = io.StringIO()
    run_once(probe=lambda: up, clock=clock, state_path=state, out=out)
    return out.getvalue().splitlines()


def read_state(state: Path) -> dict:
    return json.loads(state.read_text(encoding="utf-8"))


def test_an_outage_of_five_minutes_is_written_once(tmp_path):
    state, clock = tmp_path / "state" / "watchdog.json", Clock()

    assert passage(state, clock, up=False) == []  # the first failure seen
    assert read_state(state) == {"first_failure_at": "2026-11-11T20:00:00+00:00", "reported": False}
    clock.advance(minutes=5)
    lines = passage(state, clock, up=False)

    assert lines == ["<3>workflow-core /healthz en échec depuis 2026-11-11T20:00:00+00:00 (5 min)"]
    assert read_state(state) == {"first_failure_at": "2026-11-11T20:00:00+00:00", "reported": True}
    for _ in range(3):
        clock.advance(minutes=1)
        assert passage(state, clock, up=False) == []
    assert read_state(state)["reported"] is True


def test_an_outage_shorter_than_five_minutes_is_not_written(tmp_path):
    state, clock = tmp_path / "watchdog.json", Clock()

    assert passage(state, clock, up=False) == []
    clock.advance(minutes=4, seconds=59)
    assert passage(state, clock, up=False) == []
    clock.advance(seconds=30)
    assert passage(state, clock, up=True) == []

    assert read_state(state) == {"first_failure_at": None, "reported": False}


def test_a_recovery_after_a_written_outage_is_written_and_resets(tmp_path):
    state, clock = tmp_path / "watchdog.json", Clock()
    passage(state, clock, up=False)
    clock.advance(minutes=7)
    assert passage(state, clock, up=False)[0].startswith("<3>")

    clock.advance(minutes=1)
    lines = passage(state, clock, up=True)

    assert lines == [
        "<6>workflow-core /healthz répond de nouveau, après une panne depuis 2026-11-11T20:00:00+00:00 (8 min)"
    ]
    assert read_state(state) == {"first_failure_at": None, "reported": False}
    clock.advance(minutes=1)
    assert passage(state, clock, up=True) == []


def test_the_watchdog_uses_only_the_standard_library():
    source = WATCHDOG.read_text(encoding="utf-8")
    tree = ast.parse(source)
    modules = {
        alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    }
    modules |= {
        node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.level == 0
    }

    assert modules
    assert modules <= set(sys.stdlib_module_names) | {"__future__"}, modules - set(sys.stdlib_module_names)
    assert "workflow_core" not in source
    plain = "".join(c for c in unicodedata.normalize("NFKD", source) if not unicodedata.combining(c))
    assert not re.search("her" + "mes", plain, re.IGNORECASE)
    # Its only connection is the core's /healthz, on the loopback.
    assert re.findall(r"https?://[^\s\"']+", source) == [HEALTHZ_URL]
    assert HEALTHZ_URL == "http://127.0.0.1:8447/healthz"
