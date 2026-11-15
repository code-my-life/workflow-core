"""The runtime's version, read again while the core runs: a version other than the one the contract was checked
against puts the core in observer mode until the service restarts (ADR 0019).

At the start, the version read by the contract check is not taken for granted: no write passes until a first read
confirms it (`write_refusal`), and the engine's first pass makes that read before anything else (`ensure_confirmed`).
Then the version is read again every `every` seconds. A changed or unreadable version is added to the contract's
problems, which `/api/machine` serves, and written to the journal; the service then opens the broken contract's issue
(or comments the open one, `workflow_core.notify.contract_issue`); no read follows, and only a restart, whose
contract check judges the new version, brings the normal mode back.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from collections.abc import Callable

from workflow_core.clock import utc_now
from workflow_core.model.contract import ContractVerdict
from workflow_core.model.state import CoreState
from workflow_core.notify.notifier import journal_line

OBSERVER = "mode observateur"
NOT_CONFIRMED = "version du runtime pas encore relue depuis le démarrage"


class VersionWatch:
    """Reads the runtime's version again, through `recheck`, which returns the problem of a changed version or
    None."""

    def __init__(
        self,
        state: CoreState,
        recheck: Callable[[], str | None],
        *,
        every: float,
        clock: Callable[[], float] = time.monotonic,
        log: Callable[[str], None] = journal_line,
        on_change: Callable[[ContractVerdict], None] | None = None,
    ) -> None:
        self.state = state
        # Called once with the broken verdict when the version changes: the service opens the contract's issue.
        self.on_change = on_change
        self.recheck = recheck
        self.every = every
        self.clock = clock
        self.log = log
        self.confirmed = False
        self._last: float | None = None
        self._lock = threading.Lock()

    def check(self) -> None:
        """Read the version again; a change puts the core in observer mode for good. In observer mode, nothing is
        read: the core writes nothing anyway."""
        with self._lock:
            if self.state.mode != "normal":
                return
            self._last = self.clock()
            try:
                problem = self.recheck()
            except Exception as error:  # a read that cannot run is a broken version check, never a crash
                problem = f"relecture de version impossible : {type(error).__name__}: {error}"
            if problem is None:
                self.confirmed = True
                return
            previous = self.state.contract
            verdict = ContractVerdict(
                ok=False, checked_at=utc_now(), problems=[*(previous.problems if previous else []), problem]
            )
            self.state.contract = verdict
        self.log(f"<3>workflow-core: {problem} ; socle en observateur jusqu'au redémarrage")
        # Out of the lock: the report reaches GitHub, and the mode is observer already, so no other read follows.
        if self.on_change is not None:
            self.on_change(verdict)

    def tick(self) -> None:
        """Read the version again when no read was made yet, or when the last one is `every` seconds old."""
        with self._lock:
            due = self._last is None or self.clock() - self._last >= self.every
        if due:
            self.check()

    def ensure_confirmed(self) -> bool:
        """Whether the core may write: the version is confirmed, read now when it was not yet, and the mode is
        normal."""
        if not self.confirmed:
            self.check()
        return self.confirmed and self.state.mode == "normal"

    def write_refusal(self) -> str | None:
        """The reason to refuse a write now, or None."""
        if self.state.mode != "normal":
            return OBSERVER
        if not self.confirmed:
            return NOT_CONFIRMED
        return None


class VersionWatchRunner:
    """Reads the version again in the service: at the start, then every `every` seconds, off the event loop."""

    def __init__(self, watch: VersionWatch, every: float) -> None:
        self.watch = watch
        self.every = every
        self._task: asyncio.Task | None = None

    async def _loop(self) -> None:
        while True:
            await asyncio.to_thread(self.watch.tick)
            await asyncio.sleep(self.every)

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
