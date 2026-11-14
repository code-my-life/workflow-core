"""A fake runtime command for the tests: `hermes`, put first on the PATH by the `fake_runtime` fixture (conftest.py).

It notes each call (one JSON list of arguments per line, in the file named by FAKE_RUNTIME_CALLS) and answers:
- `--version` with the file named by FAKE_RUNTIME_VERSION (the runtime 0.22.2 when unset);
- `kanban <command...> --help` with `<command words joined by ->.txt` of the folder named by FAKE_RUNTIME_HELP; a
  command without its help file is unknown: code 2, as the runtime;
- `kanban [--board <slug>] ...`: `boards create <slug>`, `boards list --json`, `boards rm --delete <slug>`, and on a
  board `create` (`--assignee`, `--idempotency-key`, `--body`, `--parent`, `--json`), `assign`, `schedule`, `unblock`,
  `block --kind`, `comment` and `complete --result`, applied to the bases of the test's runtime home, with the
  runtime's real schema.
FAKE_RUNTIME_FAIL names a subcommand (or `all`) that fails with code 1 and an error output. The tests never run the
machine's runtime.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import sqlite3
import sys
import time
from pathlib import Path

from conftest import RUNTIME_HOME_VAR, RUNTIME_SCHEMA, VERSION_LINE


def boards() -> Path:
    return Path(os.environ[RUNTIME_HOME_VAR]) / "kanban" / "boards"


def base(slug: str) -> Path:
    return boards() / slug / "kanban.db"


def option(arguments: list[str], name: str) -> str | None:
    return arguments[arguments.index(name) + 1] if name in arguments else None


def unknown(word: str) -> int:
    print(f"hermes kanban: '{word}' is not a `hermes kanban` command.", file=sys.stderr)
    return 2


def help_file(words: list[str]) -> Path | None:
    folder = os.environ.get("FAKE_RUNTIME_HELP")
    if folder is None:
        return None
    path = Path(folder) / f"{'-'.join(words)}.txt"
    return path if path.is_file() else None


def version() -> int:
    path = os.environ.get("FAKE_RUNTIME_VERSION")
    print(Path(path).read_text(encoding="utf-8") if path else VERSION_LINE, end="" if path else "\n")
    return 0


def card_command(db: sqlite3.Connection, command: list[str]) -> int | None:
    """Apply a card command to a board's base; None when the command is not one the fake knows."""
    now = int(time.time())
    if command[0] == "create":
        key = option(command, "--idempotency-key")
        if key is not None:
            found = db.execute(
                "SELECT id FROM tasks WHERE idempotency_key = ? AND status != 'archived'", (key,)
            ).fetchone()
            if found is not None:
                if "--json" in command:
                    print(json.dumps({"id": found[0]}))
                return 0
        parent = option(command, "--parent")
        status = "ready"
        if parent is not None:
            row = db.execute("SELECT status FROM tasks WHERE id = ?", (parent,)).fetchone()
            if row is None:
                print(f"no such task: {parent}", file=sys.stderr)
                return 1
            status = "ready" if row[0] == "done" else "todo"
        card = f"t_{secrets.token_hex(4)}"
        row = {
            "id": card,
            "title": command[1],
            "body": option(command, "--body"),
            "assignee": option(command, "--assignee"),
            "status": status,
            "created_at": now,
            "idempotency_key": key,
        }
        marks = ", ".join("?" * len(row))
        db.execute(f"INSERT INTO tasks ({', '.join(row)}) VALUES ({marks})", tuple(row.values()))
        if parent is not None:
            db.execute("INSERT INTO task_links (parent_id, child_id) VALUES (?, ?)", (parent, card))
        if "--json" in command:
            print(json.dumps(row))
        return 0
    if len(command) < 2:
        return None
    card = command[1]
    if db.execute("SELECT 1 FROM tasks WHERE id = ?", (card,)).fetchone() is None:
        print(f"no such task: {card}", file=sys.stderr)
        return 1
    if command[0] == "assign" and len(command) == 3:
        db.execute("UPDATE tasks SET assignee = ? WHERE id = ?", (command[2], card))
    elif command[0] == "schedule" and len(command) == 2:
        db.execute("UPDATE tasks SET status = 'scheduled' WHERE id = ?", (card,))
    elif command[0] == "unblock" and len(command) == 2:
        db.execute("UPDATE tasks SET status = 'ready', block_kind = NULL WHERE id = ?", (card,))
    elif command[0] == "block" and len(command) == 4 and command[2] == "--kind":
        db.execute("UPDATE tasks SET status = 'blocked', block_kind = ? WHERE id = ?", (command[3], card))
    elif command[0] == "comment" and len(command) == 3:
        db.execute(
            "INSERT INTO task_comments (task_id, author, body, created_at) VALUES (?, 'user', ?, ?)",
            (card, command[2], now),
        )
    elif command[0] == "complete" and len(command) == 4 and command[2] == "--result":
        db.execute(
            "UPDATE tasks SET status = 'done', result = ?, completed_at = ? WHERE id = ?", (command[3], now, card)
        )
    else:
        return None
    print(f"{command[0]} {card}: ok")
    return 0


def boards_command(command: list[str]) -> int | None:
    """Apply a `boards` command; None when the command is not one the fake knows."""
    if command[1:2] == ["create"] and len(command) == 3:
        path = base(command[2])
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(path) as db:
                db.executescript(RUNTIME_SCHEMA.read_text(encoding="utf-8"))
            db.close()
        print(f"Created board {command[2]}")
        return 0
    if command[1:] == ["list", "--json"]:
        found = sorted(folder.name for folder in boards().iterdir()) if boards().is_dir() else []
        print(json.dumps([{"slug": slug, "name": slug} for slug in found]))
        return 0
    if command[1:3] == ["rm", "--delete"] and len(command) == 4:
        folder = boards() / command[3]
        if not folder.is_dir():
            print(f"no board {command[3]}", file=sys.stderr)
            return 1
        shutil.rmtree(folder)
        print(f"Deleted board {command[3]}")
        return 0
    return None


def main(argv: list[str]) -> int:
    with Path(os.environ["FAKE_RUNTIME_CALLS"]).open("a", encoding="utf-8") as calls:
        calls.write(json.dumps(argv) + "\n")
    if argv == ["--version"]:
        return version()
    if argv[:1] != ["kanban"] or len(argv) < 2:
        print(f"fake runtime: unexpected call {argv}", file=sys.stderr)
        return 2
    rest = argv[1:]
    board = None
    if rest[:1] == ["--board"]:
        board, rest = rest[1], rest[2:]
    if not rest:
        print(f"fake runtime: unexpected call {argv}", file=sys.stderr)
        return 2
    words = rest[:2] if rest[0] == "boards" else rest[:1]
    if os.environ.get("FAKE_RUNTIME_HELP") is not None and help_file(words) is None:
        return unknown(words[-1])
    if rest[-1] == "--help":
        path = help_file(words)
        print(path.read_text(encoding="utf-8") if path else "", end="")
        return 0
    failing = os.environ.get("FAKE_RUNTIME_FAIL")
    if failing in ("all", rest[0]):
        print(f"kanban: {rest[0]} refusé par le faux runtime", file=sys.stderr)
        return 1
    if rest[0] == "boards":
        answered = boards_command(rest)
    elif board is None:
        answered = None
    elif not base(board).is_file():
        print(f"kanban: no board {board}", file=sys.stderr)
        return 1
    else:
        with sqlite3.connect(base(board)) as db:
            answered = card_command(db, rest)
        db.close()
    if answered is None:
        print(f"fake runtime: unknown command {rest}", file=sys.stderr)
        return 2
    return answered


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
