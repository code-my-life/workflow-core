"""Hermès' own checks of the machine (ADR 0008): its pause, its gateway, its scheduled jobs, its local model router and
the disk of its home, read-only.

Ported from the factory's probe (hermes-software-factory, plugins/factory-live-probe, `default_checks`): the same
statuses and the same details in the same cases. The gateway, which the probe said alive while it sent, is read from
its heartbeats in the root `state.db`.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from workflow_core.adapters.hermes.board import hermes_home
from workflow_core.model.checks import MachineCheck

GROUP = "hermes"
# A gateway whose last heartbeat is older than this is down.
GATEWAY_ALIVE_SECONDS = 90
LLAMA_TIMEOUT_SECONDS = 1.5
# The files the checks read under Hermès' home, and the table of the gateway's heartbeats in its root base.
ESTOP = "ESTOP"
CRON_JOBS = "cron/jobs.json"
LLAMA_SERVER = "runtimes/llamacpp/server.json"
HEARTBEAT_COLUMNS = ("pid", "last_heartbeat")


def _iso(at: float) -> str:
    return datetime.fromtimestamp(at, UTC).isoformat().replace("+00:00", "Z")


def cron_jobs(data: object) -> list[dict] | None:
    """The jobs of `cron/jobs.json`: a list or a mapping under `jobs`; None when the file has another form."""
    jobs = data.get("jobs") if isinstance(data, dict) else data
    if isinstance(jobs, dict):
        jobs = list(jobs.values())
    return [job for job in jobs if isinstance(job, dict)] if isinstance(jobs, list) else None


class HermesMachineChecks:
    """The runtime's checks, under one Hermès home."""

    def __init__(self, home: Path) -> None:
        self.home = home

    def _check(self, check_id: str, label: str, status: str, detail: str, now: float, group: str = GROUP, **metrics):
        return MachineCheck(
            id=check_id,
            label=label,
            group=group,
            status=status,
            detail=detail,
            metrics={key: str(value) for key, value in metrics.items()},
            checked_at=_iso(now),
        )

    def pause(self, now: float) -> MachineCheck:
        """`hermes pause` sets `ESTOP`; empty or corrupt, it still means a pause (agent/estop.py)."""
        sentinel = self.home / ESTOP
        if not sentinel.exists():
            return self._check("pause", "Pause (ESTOP)", "ok", "Factory active, aucune pause posée", now)
        try:
            data = json.loads(sentinel.read_text(encoding="utf-8") or "{}")
            reason = (data.get("reason") if isinstance(data, dict) else "") or ""
        except (OSError, ValueError):
            reason = ""
        detail = f"Factory en pause{f' : {reason}' if reason else ''}"
        return self._check("pause", "Pause (ESTOP)", "degraded", detail, now)

    def gateway(self, now: float) -> MachineCheck:
        """Alive while its last heartbeat is fresh; its process id comes with it."""
        base = self.home / "state.db"
        if not base.is_file():
            return self._check("gateway", "Gateway", "down", "Aucun battement du gateway", now)
        try:
            db = sqlite3.connect(f"file:{quote(str(base))}?mode=ro", uri=True)
            try:
                columns = ", ".join(HEARTBEAT_COLUMNS)
                row = db.execute(
                    f"SELECT {columns} FROM gateway_heartbeats ORDER BY last_heartbeat DESC LIMIT 1"
                ).fetchone()
            finally:
                db.close()
        except sqlite3.Error as error:
            return self._check("gateway", "Gateway", "unknown", f"Battements illisibles ({error})", now)
        if row is None:
            return self._check("gateway", "Gateway", "down", "Aucun battement du gateway", now)
        pid, last = row
        age = max(0, round(now - float(last)))
        if age < GATEWAY_ALIVE_SECONDS:
            return self._check("gateway", "Gateway", "ok", f"Vivant, dernier battement il y a {age} s", now, pid=pid)
        return self._check("gateway", "Gateway", "down", f"Dernier battement il y a {age} s", now, pid=pid)

    def cron(self, now: float) -> MachineCheck:
        try:
            jobs = cron_jobs(json.loads((self.home / CRON_JOBS).read_text(encoding="utf-8")))
        except (OSError, ValueError) as error:
            detail = f"jobs.json illisible ({error.__class__.__name__})"
            return self._check("cron", "Tâches cron", "unknown", detail, now, group="intake")
        failing = [
            job.get("name") or job.get("id") or "?" for job in jobs or [] if (job.get("failure_streak") or 0) > 0
        ]
        if failing:
            return self._check("cron", "Tâches cron", "degraded", f"En échec : {', '.join(failing)}", now, "intake")
        return self._check("cron", "Tâches cron", "ok", "Toutes les tâches sont vertes", now, group="intake")

    def llamacpp(self, now: float) -> MachineCheck:
        """The models loaded by the llama.cpp router. Its key is used for this local request only, never kept."""
        try:
            server = json.loads((self.home / LLAMA_SERVER).read_text(encoding="utf-8"))
            base, key = server["base_url"].removesuffix("/v1"), server.get("api_key", "")
        except (OSError, KeyError, ValueError, TypeError, AttributeError):
            return self._check("llamacpp", "llama.cpp local", "unknown", "Routeur non configuré", now, group="models")
        request = urllib.request.Request(base + "/models", headers={"Authorization": f"Bearer {key}"})
        try:
            with urllib.request.urlopen(request, timeout=LLAMA_TIMEOUT_SECONDS) as response:
                models = json.loads(response.read() or b"{}").get("data", [])
        except (OSError, ValueError):
            detail = "Routeur arrêté (démarré à la demande)"
            return self._check("llamacpp", "llama.cpp local", "unknown", detail, now, group="models")
        loaded = [
            model.get("id", "?")
            for model in models
            if isinstance(model, dict) and (model.get("status") or {}).get("value", "loaded") == "loaded"
        ]
        return self._check(
            "llamacpp",
            "llama.cpp local",
            "ok",
            f"{len(loaded)} modèle(s) chargé(s)",
            now,
            group="models",
            modeles=", ".join(loaded) or "aucun",
        )

    def disk(self, now: float) -> MachineCheck:
        try:
            usage = shutil.disk_usage(self.home)
        except OSError as error:
            return self._check("disk", "Disque de ~/.hermes", "unknown", f"Illisible ({error.__class__.__name__})", now)
        free = usage.free / usage.total
        status = "ok" if free >= 0.1 else "degraded"
        return self._check(
            "disk", "Disque de ~/.hermes", status, f"{usage.free / 1e9:.0f} Go libres", now, libre=f"{free:.0%}"
        )

    def checks(self, now: float) -> list[MachineCheck]:
        return [self.gateway(now), self.pause(now), self.llamacpp(now), self.cron(now), self.disk(now)]


def make_machine_checks(environ: dict[str, str] | None = None) -> HermesMachineChecks:
    """The runtime's checks of the Hermès home named by `HERMES_HOME`, `~/.hermes` by default."""
    return HermesMachineChecks(hermes_home(environ))
