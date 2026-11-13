"""The checks of the machine (ADR 0008): the runtime's own, given by its adapter, and the declared ones, run here.

Checks run apart from the API: a route serves the last results, never waits for a check. Each check runs every 30
seconds (or its declared cadence), bounded to 5 seconds. A status that changes is what the event stream announces.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

from workflow_core.model.checks import (
    CHECK_EVERY_SECONDS,
    CHECK_TIMEOUT_SECONDS,
    DeclaredCheck,
    MachineCheck,
    RuntimeChecks,
    machine_check,
)

# The command each kind of tool check runs, found on the PATH, else at the declared target.
TOOLS = {"vram": "nvidia-smi", "funnel": "tailscale"}


def _result(check: DeclaredCheck, status: str, detail: str, now: float, **metrics: object) -> MachineCheck:
    return machine_check(check.id, check.label, check.group, status, detail, now, **metrics)


def _http(check: DeclaredCheck, now: float) -> MachineCheck:
    """The address answers with a status below 500: it runs, even when it asks for a key (401)."""
    try:
        with urllib.request.urlopen(check.target or "", timeout=CHECK_TIMEOUT_SECONDS) as response:
            status = response.status
    except urllib.error.HTTPError as error:
        status = error.code
    except (OSError, ValueError) as error:
        return _result(check, "down", f"Ne répond pas ({error.__class__.__name__})", now)
    return _result(check, "ok" if status < 500 else "down", f"Répond ({status})", now)


def _tcp(check: DeclaredCheck, now: float) -> MachineCheck:
    host, _, port = (check.target or "").rpartition(":")
    try:
        with socket.create_connection((host or "127.0.0.1", int(port)), timeout=CHECK_TIMEOUT_SECONDS):
            return _result(check, "ok", f"Écoute sur {port}", now)
    except (OSError, ValueError):
        return _result(check, "down", f"Rien n'écoute sur {port}", now)


def _command(check: DeclaredCheck) -> str | None:
    found = shutil.which(TOOLS[check.kind])
    if found:
        return found
    if check.target and Path(check.target).is_file():
        return check.target
    return None


def _run(command: str, *args: str) -> str:
    return subprocess.run(
        [command, *args], capture_output=True, text=True, timeout=CHECK_TIMEOUT_SECONDS, check=True
    ).stdout


def _vram(check: DeclaredCheck, now: float) -> MachineCheck:
    command = _command(check)
    try:
        if command is None:
            raise FileNotFoundError(TOOLS["vram"])
        output = _run(command, "--query-gpu=memory.used,memory.total,utilization.gpu", "--format=csv,noheader,nounits")
        used, total, use = (float(part) for part in output.strip().splitlines()[0].split(","))
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return _result(check, "unknown", "nvidia-smi indisponible", now)
    return _result(check, "ok", f"{used / 1024:.1f} sur {total / 1024:.0f} Go", now, utilisation=f"{use:.0f} %")


def _funnel(check: DeclaredCheck, now: float) -> MachineCheck:
    """The public funnel only carries GitHub's webhook."""
    command = _command(check)
    try:
        if command is None:
            raise FileNotFoundError(TOOLS["funnel"])
        status = json.loads(_run(command, "funnel", "status", "--json") or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return _result(check, "unknown", "tailscale indisponible", now)
    allowed = status.get("AllowFunnel") if isinstance(status, dict) else None
    if isinstance(allowed, dict) and any(allowed.values()):
        return _result(check, "ok", "Funnel actif vers le webhook", now)
    return _result(check, "down", "Funnel coupé : les webhooks GitHub ne passent plus", now)


KINDS: dict[str, Callable[[DeclaredCheck, float], MachineCheck]] = {
    "http": _http,
    "tcp": _tcp,
    "vram": _vram,
    "funnel": _funnel,
}
RUNTIME = "runtime"


class MachineChecks:
    """Runs the checks when they are due and keeps their last results, in a stable order."""

    def __init__(
        self,
        *,
        runtime: RuntimeChecks | None,
        declared: list[DeclaredCheck],
        clock: Callable[[], float],
        every_seconds: float = CHECK_EVERY_SECONDS,
    ) -> None:
        self.runtime = runtime
        self.declared = declared
        self.clock = clock
        # The base cadence; a declared check keeps its own when it is slower (the tests shorten both).
        self.every_seconds = every_seconds
        self._lock = threading.Lock()
        self._results: dict[str, list[MachineCheck]] = {}
        self._ran_at: dict[str, float] = {}
        self._pool = ThreadPoolExecutor(max_workers=max(2, len(declared) + 1), thread_name_prefix="machine-check")

    def _cadence(self, check: DeclaredCheck) -> float:
        if check.every_seconds <= CHECK_EVERY_SECONDS:
            return self.every_seconds
        return check.every_seconds * self.every_seconds / CHECK_EVERY_SECONDS

    def _due(self, key: str, cadence: float, now: float) -> bool:
        return key not in self._ran_at or now - self._ran_at[key] >= cadence

    def _run_runtime(self, now: float) -> list[MachineCheck]:
        try:
            return self.runtime.checks(now)
        except Exception as error:  # a broken check never stops the others
            detail = f"Vérification en erreur ({error.__class__.__name__})"
            return [machine_check("checks-error", "Contrôles", "tools", "unknown", detail, now)]

    def run_due(self) -> bool:
        """Run every check that is due, in parallel, each bounded; True when a status changed."""
        now = self.clock()
        jobs: dict[str, Callable[[], list[MachineCheck]]] = {}
        if self.runtime is not None and self._due(RUNTIME, self.every_seconds, now):
            jobs[RUNTIME] = lambda: self._run_runtime(now)
        for check in self.declared:
            if self._due(check.id, self._cadence(check), now):
                jobs[check.id] = lambda check=check: [KINDS[check.kind](check, now)]
        if not jobs:
            return False
        futures = {key: self._pool.submit(job) for key, job in jobs.items()}
        found: dict[str, list[MachineCheck]] = {}
        for key, future in futures.items():
            try:
                found[key] = future.result(timeout=CHECK_TIMEOUT_SECONDS + 1)
            except (FutureTimeout, Exception):
                check = next((item for item in self.declared if item.id == key), None)
                if check is not None:
                    found[key] = [_result(check, "unknown", "Vérification trop longue", now)]
        with self._lock:
            before = self._statuses()
            self._results.update(found)
            for key in found:
                self._ran_at[key] = now
            return self._statuses() != before

    def _statuses(self) -> list[tuple[str, str]]:
        return [(check.id, check.status) for check in self._ordered()]

    def _ordered(self) -> list[MachineCheck]:
        order = [RUNTIME, *(check.id for check in self.declared)]
        return [check for key in order for check in self._results.get(key, [])]

    def current(self) -> list[MachineCheck]:
        with self._lock:
            return self._ordered()

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
