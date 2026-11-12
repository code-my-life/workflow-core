"""The journal of each Hermès agent (`profiles/<agent>/logs/agent.log`, after `agent.log.1`), read-only (ADR 0012).

Ported from factory-live at commit d47f235: the reader (apps/server/src/adapters/hermes/agentLog.ts), the safe paths
(apps/server/src/adapters/hermes/paths.ts) and the line format with its nine catalogued kinds
(packages/domain/src/diagnosis/hermesText.ts, « Journal d'Hermès »).

The journal is read on demand and in chunks: only the lines the diagnosis knows become facts, dated in the machine's
time zone (`HERMES_TZ`, `Europe/Paris` by default). A reread resumes where the previous one stopped; a replaced
(rotated) or shortened file is read again from its start. A line without session takes the session of the tool call
that follows it closely, else the only open session of the agent at that time.
"""

from __future__ import annotations

import calendar
import math
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from workflow_core.model.facts import LogFact, LogFactKind
from workflow_core.text import js_regex, quote

TIME_ZONE_VARIABLE = "HERMES_TZ"
DEFAULT_TIME_ZONE = "Europe/Paris"

# At the first read, only the end of a big journal counts: the recent blocks.
FIRST_READ_BYTES = 16 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
# The facts kept per file, at most: the most recent.
FACTS_KEPT = 20_000
# A line without session takes the one of the `agent.tool_executor` line that follows it closely.
ATTACH_SECONDS = 2
TOOL_EXECUTOR = "agent.tool_executor"
JOURNAL_FILES = ("agent.log.1", "agent.log")

# The open sessions of an agent at an instant (Unix seconds), from Hermès' sessions; `None` when unknown.
OpenSessions = Callable[[str, float], list[str] | None]

# The header of a line: local time without zone, milliseconds, level, optional session, logger, message.
LOG_LINE = js_regex(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),(\d{3}) (DEBUG|INFO|WARNING|ERROR|CRITICAL)(?: \[([^\]]+)\])? (\S+): (.*)$"
)
_WALL_CLOCK = js_regex(r"^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})$")
_NAME = js_regex(r"^[\w-]+$")


def _js_round(text: str) -> int | None:
    """JavaScript's `Math.round(Number(text))`: halves go up; what is not a number (`1.2.3`) gives `None`, as
    JavaScript's `NaN` is written `null` in JSON."""
    try:
        value = float(text)
    except ValueError:
        return None
    return math.floor(value + 0.5) if math.isfinite(value) else None


@dataclass(frozen=True)
class LogLine:
    wall_clock: str
    millis: int
    level: str
    session_id: str | None
    logger: str
    message: str


def parse_log_line(line: str) -> LogLine | None:
    match = LOG_LINE.fullmatch(line)
    if match is None:
        return None
    return LogLine(match[1], int(match[2]), match[3], match[4], match[5], match[6])


# The nine kinds of lines the diagnosis reads, in factory-live's order (the first that matches wins), each with the
# fields it gives.
_Fields = dict[str, str | int | None]
LOG_PATTERNS: tuple[tuple[LogFactKind, re.Pattern[str], Callable[[re.Match[str]], _Fields]], ...] = (
    (
        "hook_timeout",
        js_regex(r"^Hook '(\w+)' callback (\S+) timed out after ([\d.]+)s"),
        lambda m: {"hook": m[1], "callback": m[2], "seconds": _js_round(m[3])},
    ),
    (
        "hook_abandoned",
        js_regex(r"^Hook '(\w+)' callback (\S+) \(([^)]+)\) skipped: (\d+) abandoned worker\(s\) still running"),
        lambda m: {"hook": m[1], "callback": m[2], "module": m[3], "count": int(m[4])},
    ),
    (
        "hook_skipped",
        js_regex(r"^Hook '(\w+)' callback (\S+) skipped after previous timeout or while still running"),
        lambda m: {"hook": m[1], "callback": m[2]},
    ),
    (
        "hook_raised",
        js_regex(r"^(?:Hook|Middleware) '(\w+)' callback (\S+) raised: ([\s\S]*?)(?: \(\w+ provides:[\s\S]*)?$"),
        lambda m: {"hook": m[1], "callback": m[2], "detail": m[3]},
    ),
    (
        "plugin_load_failed",
        js_regex(r"^Failed to load plugin '([^']+)': ([\s\S]*)$"),
        lambda m: {"plugin": m[1], "detail": m[2]},
    ),
    (
        "stop_nudge",
        js_regex(r"^kanban stop-loop nudge issued \(attempt (\d+)\) task=(\S*)$"),
        lambda m: {"count": int(m[1]), "detail": m[2]},
    ),
    ("turn_ended", js_regex(r"^Turn ended: reason=(\S+)"), lambda m: {"detail": m[1]}),
    (
        "tool_deadline",
        js_regex(r"^sequential tool (\S+) timed out after ([\d.]+)s$"),
        lambda m: {"detail": m[1], "seconds": _js_round(m[2])},
    ),
    (
        "api_failed",
        js_regex(r"^API call failed \(attempt \d+\/\d+(?:, not retryable)?\) error_type=(\w+) .*?provider=(\S+)"),
        lambda m: {"detail": m[1], "plugin": m[2]},
    ),
)
_FIELDS = ("hook", "callback", "module", "seconds", "plugin", "detail", "count")


def _search(pattern: re.Pattern[str], text: str) -> re.Match[str] | None:
    # JavaScript's `$` without the `m` flag is the very end of the text, never before a final newline.
    match = pattern.search(text)
    if match is not None and pattern.pattern.endswith("$") and match.end() != len(text):
        return None
    return match


def log_fact_of(line: LogLine, at: float) -> LogFact | None:
    """The fact of a line the diagnosis knows, dated at `at`; `None` for any other line."""
    for kind, pattern, read in LOG_PATTERNS:
        match = _search(pattern, line.message)
        if match is None:
            continue
        fields = read(match)
        return LogFact(
            at=at,
            session_id=line.session_id,
            kind=kind,
            **{name: fields.get(name) for name in _FIELDS},
            line=quote(line.message) or "",
        )
    return None


def _wall_at(epoch_ms: int, zone: ZoneInfo) -> int:
    """The zone's wall clock at this instant, as if it were UTC (milliseconds, whole seconds)."""
    moment = datetime.fromtimestamp(epoch_ms // 1000, UTC).astimezone(zone)
    return calendar.timegm(moment.timetuple()) * 1000


def zoned_epoch(wall_clock: str, millis: int, time_zone: str, not_before: float | None = None) -> float | None:
    """The instant of a wall-clock time of Hermès (`2026-10-29 09:46:10`, without zone) in the machine's zone.

    In autumn, the same wall-clock time exists twice: the first one not before the previous line (`not_before`) is
    taken, the journal being written in order. A time that does not exist (the spring jump) keeps the old offset,
    as a clock would.
    """
    match = _WALL_CLOCK.fullmatch(wall_clock)
    if match is None:
        return None
    year, month, day, hour, minute, second = (int(part) for part in match.groups())
    try:
        as_utc = calendar.timegm((year, month, day, hour, minute, second, 0, 0, 0)) * 1000
    except ValueError:
        return None
    zone = ZoneInfo(time_zone)
    offsets = list(
        dict.fromkeys(_wall_at(as_utc + hours * 3_600_000, zone) - (as_utc + hours * 3_600_000) for hours in (-3, 3))
    )
    candidates = sorted(as_utc - offset for offset in offsets if _wall_at(as_utc - offset, zone) == as_utc)
    chosen = next(
        (epoch for epoch in candidates if not_before is None or epoch / 1000 >= not_before - 1),
        candidates[-1] if candidates else as_utc - (offsets[0] if offsets else 0),
    )
    return (chosen + millis) / 1000


def safe_file(root: Path, *parts: str) -> Path | None:
    """A file under its root, without a symbolic link on the way: its real path must be the path built. A link laid
    in a profile thus makes nothing else readable."""
    path = root.joinpath(*parts)
    try:
        if not path.is_relative_to(root) or path == root:
            return None
        if path.is_symlink() or not path.is_file():
            return None
        return path if path.resolve() == root.resolve().joinpath(*parts) else None
    except OSError:
        return None


def journal_paths(home: Path, agent: str) -> list[Path]:
    """The agent's journal: the previous file (rotation) first, then the current one; nothing for an unsafe name."""
    if not _NAME.fullmatch(agent):
        return []
    found = (safe_file(home / "profiles", agent, "logs", name) for name in JOURNAL_FILES)
    return [path for path in found if path is not None]


@dataclass
class _Entry:
    fact: LogFact
    # The session attached afterwards to a line that named none: unset, or one (or `None`: none, or several).
    attached: str | None = None
    asked: bool = False


@dataclass
class _FileCache:
    inode: int
    # Bytes already read; an unfinished line waits for what follows in `pending`.
    offset: int
    pending: str = ""
    facts: list[_Entry] = field(default_factory=list)
    # The last time read, to settle the ambiguity of the autumn hour.
    last_at: float | None = None
    # Lines without session, waiting for an `agent.tool_executor` line within 2 s.
    orphans: list[_Entry] = field(default_factory=list)


class AgentLogReader:
    """Reads the agents' journals under Hermès' home, read-only. It keeps, per file, where it stopped."""

    def __init__(self, home: Path, time_zone: str, open_sessions: OpenSessions | None = None) -> None:
        self.home = home
        self.time_zone = time_zone
        self.open_sessions = open_sessions
        self._files: dict[Path, _FileCache] = {}

    def facts(self, agent: str, since: float, until: float) -> list[LogFact] | None:
        files = journal_paths(self.home, agent)
        if not files:
            return None
        found: list[LogFact] = []
        for path in files:
            cache = self._read(path)
            if cache is None:
                continue
            for entry in [*cache.facts, *cache.orphans]:
                if since <= entry.fact.at <= until:
                    found.append(self._attach(agent, entry))
        return sorted(found, key=lambda fact: fact.at)

    def _attach(self, agent: str, entry: _Entry) -> LogFact:
        """The only session of the agent open at the line's time; several or none: no guess."""
        if entry.fact.session_id or self.open_sessions is None:
            return entry.fact
        if not entry.asked:
            try:
                open_now = self.open_sessions(agent, entry.fact.at)
            except Exception:  # a source of sessions that fails is no session, never a crash
                open_now = None
            entry.attached = open_now[0] if open_now is not None and len(open_now) == 1 else None
            entry.asked = True
        return entry.fact.model_copy(update={"session_id": entry.attached}) if entry.attached else entry.fact

    def _read(self, path: Path) -> _FileCache | None:
        try:
            handle = path.open("rb")
        except OSError:
            self._files.pop(path, None)
            return None
        with handle:
            stat = os.fstat(handle.fileno())
            cache = self._files.get(path)
            if cache is None or cache.inode != stat.st_ino or stat.st_size < cache.offset:
                start = max(0, stat.st_size - FIRST_READ_BYTES)
                cache = _FileCache(inode=stat.st_ino, offset=start)
                # Started in the middle of a line: it is dropped.
                if start > 0:
                    cache.pending = "\0"
                self._files[path] = cache
            while cache.offset < stat.st_size:
                handle.seek(cache.offset)
                chunk = handle.read(min(CHUNK_BYTES, stat.st_size - cache.offset))
                if not chunk:
                    break
                cache.offset += len(chunk)
                self._parse(cache, chunk.decode("utf-8", errors="replace"))
            if len(cache.facts) > FACTS_KEPT:
                cache.facts = cache.facts[-FACTS_KEPT:]
            return cache

    def _parse(self, cache: _FileCache, chunk: str) -> None:
        lines = (cache.pending + chunk).split("\n")
        cache.pending = lines.pop()
        if lines and lines[0].startswith("\0"):
            lines.pop(0)
        for raw in lines:
            parsed = parse_log_line(raw.removesuffix("\r"))
            if parsed is None:
                continue
            at = zoned_epoch(parsed.wall_clock, parsed.millis, self.time_zone, cache.last_at)
            if at is None:
                continue
            cache.last_at = at
            if parsed.logger == TOOL_EXECUTOR and parsed.session_id:
                for orphan in cache.orphans:
                    if at - orphan.fact.at <= ATTACH_SECONDS:
                        cache.facts.append(_Entry(orphan.fact.model_copy(update={"session_id": parsed.session_id})))
                cache.orphans = []
            # A line without session stays alone past 2 s: kept as is, without a guessed session.
            kept = []
            for orphan in cache.orphans:
                if at - orphan.fact.at <= ATTACH_SECONDS:
                    kept.append(orphan)
                else:
                    cache.facts.append(orphan)
            cache.orphans = kept
            fact = log_fact_of(parsed, at)
            if fact is None:
                continue
            if fact.session_id:
                cache.facts.append(_Entry(fact))
            else:
                cache.orphans.append(_Entry(fact))


def make_agent_log_reader(
    environ: dict[str, str] | None = None, open_sessions: OpenSessions | None = None
) -> AgentLogReader:
    """The journal reader of the Hermès home named by `HERMES_HOME`, in the zone named by `HERMES_TZ`."""
    from workflow_core.adapters.hermes.board import hermes_home

    env = os.environ if environ is None else environ
    return AgentLogReader(hermes_home(env), env.get(TIME_ZONE_VARIABLE) or DEFAULT_TIME_ZONE, open_sessions)
