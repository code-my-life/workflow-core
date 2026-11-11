"""The version of the installed Hermès, read on the first line of `hermes --version`.

Hermès 0.22.2 prints: `Hermes Agent v0.22.2 (2026.9.24) · stable · local d923a5eb487a`. The form of that line is
part of the contract (contract.py): another form is a named problem, never a guessed version.
"""

from __future__ import annotations

import re
import subprocess

VERSION_COMMAND = ("hermes", "--version")
VERSION_LINE = re.compile(r"Hermes Agent v(?P<version>\d+\.\d+\.\d+)\b")
TIMEOUT_SECONDS = 10


def read_runtime_version() -> tuple[str | None, str | None]:
    """(the installed version, None), or (None, the problem with the version output)."""
    try:
        result = subprocess.run(VERSION_COMMAND, capture_output=True, text=True, timeout=TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        return None, f"version : « {' '.join(VERSION_COMMAND)} » impossible ({error})"
    first = result.stdout.splitlines()[0].strip() if result.stdout.strip() else ""
    match = VERSION_LINE.match(first)
    if result.returncode != 0 or match is None:
        return None, f"version : première ligne inattendue « {first} » (code {result.returncode})"
    return match["version"], None
