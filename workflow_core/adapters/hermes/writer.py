"""Write to Hermès boards through its command line only: `hermes kanban --board <slug> ...` (ADR 0001, amended).

Each write is a subprocess launched with a list of arguments, never through a shell, with a delay of 30 seconds. No
base is ever opened for writing: Hermès writes its own bases. A command that fails or passes its delay is a named
error (`BoardWriteError`): the command without its values, its exit code and its error output. A writer given a
`gate` asks it before each command: a reason refuses the write (`BoardWriteRefusedError`) and `hermes` is not run.

The commands and options used here are declared in `contract.COMMANDS`, which the contract test checks.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

from workflow_core.adapters.hermes.board import HOME_VARIABLE, HermesBoardReader, hermes_home
from workflow_core.model.board import BoardUnavailableError
from workflow_core.model.writer import BoardWriteError, BoardWriteRefusedError

COMMAND = "hermes"
WRITE_TIMEOUT_SECONDS = 30.0


class HermesBoardWriter:
    """Writes to Hermès boards by name, through the command line, for the Hermès home it holds. `gate`, when given,
    returns the reason to refuse any write, or None."""

    def __init__(
        self,
        home: Path,
        *,
        timeout: float = WRITE_TIMEOUT_SECONDS,
        gate: Callable[[], str | None] | None = None,
    ) -> None:
        self.home = home
        self.timeout = timeout
        self.gate = gate
        self._paths = HermesBoardReader(home)

    def _run(self, board: str | None, arguments: list[str], shape: str) -> str:
        """Run `hermes kanban --board <board> <arguments...>` (without `--board` when `board` is None) and return its
        output; `shape` names the command without its values, for the error."""
        named_board = [] if board is None else ["--board", "<board>"]
        named = " ".join([COMMAND, "kanban", *named_board, shape])
        reason = self.gate() if self.gate is not None else None
        if reason is not None:
            raise BoardWriteRefusedError(named, reason)
        command = [COMMAND, "kanban", *([] if board is None else ["--board", board]), *arguments]
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

    def _create(self, board: str, arguments: list[str], shape: str) -> str:
        output = self._run(board, arguments, shape)
        try:
            card = json.loads(output).get("id")
        except (ValueError, AttributeError):
            card = None
        if not isinstance(card, str):
            raise BoardWriteError(f"{COMMAND} kanban --board <board> {shape}", 0, f"sortie illisible : {output[:200]}")
        return card

    def create_root_card(self, board: str, *, title: str, lane: str, item: str, issue: str | None) -> str:
        arguments = ["create", title, "--assignee", lane, "--idempotency-key", item]
        if issue is not None:
            arguments += ["--body", issue]
        arguments.append("--json")
        shape = "create <title> --assignee <lane> --idempotency-key <item> --body <issue> --json"
        return self._create(board, arguments, shape)

    def create_card(
        self,
        board: str,
        *,
        title: str,
        assignee: str,
        idempotency_key: str | None = None,
        body: str | None = None,
        parent: str | None = None,
    ) -> str:
        """Create a card and return its id. `--parent` is used by the write cycle only (write_cycle.py)."""
        arguments, shape = ["create", title, "--assignee", assignee], ["create <title> --assignee <assignee>"]
        for name, value in (("--idempotency-key", idempotency_key), ("--body", body), ("--parent", parent)):
            if value is not None:
                arguments += [name, value]
                shape.append(f"{name} <{name.removeprefix('--')}>")
        arguments.append("--json")
        shape.append("--json")
        return self._create(board, arguments, " ".join(shape))

    def assign(self, board: str, card: str, lane: str) -> None:
        self._run(board, ["assign", card, lane], "assign <card> <lane>")

    def schedule(self, board: str, card: str) -> None:
        self._run(board, ["schedule", card], "schedule <card>")

    def unblock(self, board: str, card: str) -> None:
        self._run(board, ["unblock", card], "unblock <card>")

    def block(self, board: str, card: str, kind: str) -> None:
        self._run(board, ["block", card, "--kind", kind], "block <card> --kind <kind>")

    def comment(self, board: str, card: str, text: str) -> None:
        self._run(board, ["comment", card, text], "comment <card> <text>")

    def complete(self, board: str, card: str, result: str) -> None:
        self._run(board, ["complete", card, "--result", result], "complete <card> --result <result>")

    def remove_board(self, board: str) -> None:
        # `boards rm` ignores `--board` in Hermès 0.22.2; `--delete` removes the board's folder instead of archiving it.
        self._run(board, ["boards", "rm", "--delete", board], "boards rm --delete <slug>")

    def list_boards(self) -> list[str]:
        """The slugs of the boards, read from `boards list --json` (a list of objects keyed by `slug`)."""
        shape = "boards list --json"
        output = self._run(None, ["boards", "list", "--json"], shape)
        try:
            boards = json.loads(output)
        except ValueError:
            boards = None
        if not isinstance(boards, list) or not all(
            isinstance(board, dict) and isinstance(board.get("slug"), str) for board in boards
        ):
            raise BoardWriteError(f"{COMMAND} kanban {shape}", 0, f"sortie illisible : {output[:200]}")
        return [board["slug"] for board in boards]


def make_board_writer(
    environ: dict[str, str] | None = None, gate: Callable[[], str | None] | None = None
) -> HermesBoardWriter:
    """The board writer of the Hermès home named by `HERMES_HOME`, `~/.hermes` by default; `gate`, when given, may
    refuse each write."""
    return HermesBoardWriter(hermes_home(environ), gate=gate)
