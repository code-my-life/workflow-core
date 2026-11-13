"""Write to Hermès boards through its command line only: `hermes kanban --board <slug> ...` (ADR 0001, amended).

Each write is a subprocess launched with a list of arguments, never through a shell, with a delay of 30 seconds. No
base is ever opened for writing: Hermès writes its own bases. A command that fails or passes its delay is a named
error (`BoardWriteError`): the command without its values, its exit code and its error output.

The commands and options used here are declared in `contract.COMMANDS`, which the contract test checks.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from workflow_core.adapters.hermes.board import HOME_VARIABLE, HermesBoardReader, hermes_home
from workflow_core.model.board import BoardUnavailableError
from workflow_core.model.writer import BoardWriteError

COMMAND = "hermes"
WRITE_TIMEOUT_SECONDS = 30.0


class HermesBoardWriter:
    """Writes to Hermès boards by name, through the command line, for the Hermès home it holds."""

    def __init__(self, home: Path, *, timeout: float = WRITE_TIMEOUT_SECONDS) -> None:
        self.home = home
        self.timeout = timeout
        self._paths = HermesBoardReader(home)

    def _run(self, board: str, arguments: list[str], shape: str) -> str:
        """Run `hermes kanban --board <board> <arguments...>` and return its output; `shape` names the command without
        its values, for the error."""
        command = [COMMAND, "kanban", "--board", board, *arguments]
        named = f"{COMMAND} kanban --board <board> {shape}"
        environ = {**os.environ, HOME_VARIABLE: str(self.home)}
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=self.timeout, check=False, env=environ
            )
        except subprocess.TimeoutExpired as error:
            stderr = error.stderr.decode("utf-8", "replace") if isinstance(error.stderr, bytes) else error.stderr
            raise BoardWriteError(named, None, f"délai de {self.timeout:g} s dépassé. {stderr or ''}") from error
        except OSError as error:
            raise BoardWriteError(named, None, f"lancement impossible : {error}") from error
        if result.returncode != 0:
            raise BoardWriteError(named, result.returncode, result.stderr)
        return result.stdout

    def has_board(self, board: str) -> bool:
        try:
            return self._paths.base_path(board).is_file()
        except BoardUnavailableError:
            return False

    def create_board(self, board: str) -> None:
        # `boards create` ignores `--board` in Hermès 0.22.2 and is idempotent: the slug is its argument.
        self._run(board, ["boards", "create", board], "boards create <slug>")

    def create_root_card(self, board: str, *, title: str, lane: str, item: str, issue: str | None) -> str:
        arguments = ["create", title, "--assignee", lane, "--idempotency-key", item]
        if issue is not None:
            arguments += ["--body", issue]
        arguments.append("--json")
        shape = "create <title> --assignee <lane> --idempotency-key <item> --body <issue> --json"
        output = self._run(board, arguments, shape)
        try:
            card = json.loads(output).get("id")
        except (ValueError, AttributeError):
            card = None
        if not isinstance(card, str):
            raise BoardWriteError(f"{COMMAND} kanban --board <board> {shape}", 0, f"sortie illisible : {output[:200]}")
        return card

    def assign(self, board: str, card: str, lane: str) -> None:
        self._run(board, ["assign", card, lane], "assign <card> <lane>")


def make_board_writer(environ: dict[str, str] | None = None) -> HermesBoardWriter:
    """The board writer of the Hermès home named by `HERMES_HOME`, `~/.hermes` by default."""
    return HermesBoardWriter(hermes_home(environ))
