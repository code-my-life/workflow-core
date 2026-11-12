"""The App's token without the service: `python -m workflow_core.adapters.github token|check`.

`token` prints an installation token (the cached one while it is still good). `check` prints, without the token, its
expiry, the installation's permissions and whether `code-my-life/workflow-core` is reachable. Both exit with 1 and a
message naming the cause when the App is not configured or GitHub refuses.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from workflow_core.adapters.github import AppConfigError, AppTokens, GitHubClient, GitHubError

CHECKED_REPO = "code-my-life/workflow-core"


def main(
    argv: list[str] | None = None,
    *,
    environ: dict[str, str] | None = None,
    env_file: Path | None = None,
    api_url: str | None = None,
    out: Callable[[str], None] = print,
    err: Callable[[str], None] = lambda line: print(line, file=sys.stderr),
) -> int:
    parser = argparse.ArgumentParser(prog="python -m workflow_core.adapters.github")
    parser.add_argument("command", choices=["token", "check"])
    args = parser.parse_args(argv)
    tokens = AppTokens(environ=environ, env_file=env_file, api_url=api_url)
    try:
        current = tokens.current()
        if args.command == "token":
            out(current["token"])
            return 0
        expires = datetime.fromtimestamp(float(current["expires_at"]), UTC).isoformat(timespec="seconds")
        out(f"jeton valable jusqu'à {expires} (cache : {tokens.cache_path})")
        permissions = current.get("permissions") or {}
        listed = ", ".join(f"{name}: {level}" for name, level in sorted(permissions.items()))
        out(f"permissions de l'installation : {listed or 'aucune'}")
        readable = GitHubClient(tokens).repository_readable(CHECKED_REPO)
        out(f"{CHECKED_REPO} : {'accessible' if readable else 'inaccessible'}")
        return 0
    except (AppConfigError, GitHubError) as error:
        err(f"workflow-core: {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
