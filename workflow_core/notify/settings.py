"""The notifier's settings, read from the core's environment (its `.env`, loaded by the service unit).

- `WORKFLOW_CORE_NOTIFY`: `on` switches the notifier on; absent or any other value, it is off and reads nothing.
- `WORKFLOW_CORE_NOTIFY_SINCE`: the switch-on time, ISO 8601 with its offset, required with `on`. A wait opened before
  it is never alerted, so switching on does not post again what the factory already announced.
- `WORKFLOW_CORE_NOTIFY_REPOS`: optional, the repositories (`owner/name`, comma separated) whose issues may get an
  alert; empty, all of them.
- `WORKFLOW_CORE_LIVE_URL`: factory-live's address, for the link to the item; without it, the link is left out.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime

SWITCH = "WORKFLOW_CORE_NOTIFY"
SINCE = "WORKFLOW_CORE_NOTIFY_SINCE"
REPOS = "WORKFLOW_CORE_NOTIFY_REPOS"
LIVE_URL = "WORKFLOW_CORE_LIVE_URL"


@dataclass(frozen=True)
class NotifySettings:
    on: bool
    # Unix seconds; `None` when off.
    since: float | None
    # `None`: every repository.
    repos: frozenset[str] | None
    live_url: str | None
    # Why the notifier is off although switched on, if it is.
    problem: str | None = None

    @classmethod
    def from_environ(cls, environ: dict[str, str] | None = None) -> NotifySettings:
        env = os.environ if environ is None else environ
        listed = frozenset(repo.strip() for repo in env.get(REPOS, "").split(",") if repo.strip())
        repos = listed or None
        live = env.get(LIVE_URL, "").strip().rstrip("/") or None
        if env.get(SWITCH, "").strip() != "on":
            return cls(on=False, since=None, repos=repos, live_url=live)
        raw = env.get(SINCE, "").strip()
        problem = None
        since = None
        if not raw:
            problem = f"{SWITCH}=on mais {SINCE} absent"
        else:
            try:
                moment = datetime.fromisoformat(raw)
            except ValueError:
                moment = None
            if moment is None or moment.utcoffset() is None:
                problem = f"{SWITCH}=on mais {SINCE} illisible (« {raw} », ISO 8601 avec décalage attendu)"
            else:
                since = moment.timestamp()
        if problem is not None:
            return cls(on=False, since=None, repos=repos, live_url=live, problem=problem)
        return cls(on=True, since=since, repos=repos, live_url=live)

    def describe(self) -> list[str]:
        """The startup lines of the journal: the switch, its repositories, its time, and the link."""
        if self.problem is not None:
            return [f"notificateur éteint : {self.problem}"]
        if not self.on:
            return ["notificateur éteint"]
        repos = ", ".join(sorted(self.repos)) if self.repos else "tous les dépôts"
        since = datetime.fromtimestamp(self.since).astimezone().isoformat(timespec="seconds")
        lines = [f"notificateur allumé pour {repos}, attentes ouvertes depuis {since}"]
        if self.live_url is None:
            lines.append(f"{LIVE_URL} absent : les alertes n'auront pas de lien vers factory-live")
        return lines
