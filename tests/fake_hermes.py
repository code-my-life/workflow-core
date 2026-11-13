"""A fake runtime command for the tests: `hermes`, put first on the PATH by the `fake_runtime` fixture (conftest.py).

It notes each call (one JSON list of arguments per line, in the file named by FAKE_RUNTIME_CALLS), answers
`--version` as the runtime 0.22.2, and applies `kanban --board <slug> boards create <slug>`, `create` and `assign` to
the bases of the test's runtime home, with the runtime's real schema. FAKE_RUNTIME_FAIL names a subcommand (or `all`)
that fails with code 1 and an error output. The tests never run the machine's runtime.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import sys
import time
from pathlib import Path

from conftest import RUNTIME_HOME_VAR, RUNTIME_SCHEMA, VERSION_LINE


def base(slug: str) -> Path:
    return Path(os.environ[RUNTIME_HOME_VAR]) / "kanban" / "boards" / slug / "kanban.db"


def option(arguments: list[str], name: str) -> str | None:
    return arguments[arguments.index(name) + 1] if name in arguments else None


def main(argv: list[str]) -> int:
    with Path(os.environ["FAKE_RUNTIME_CALLS"]).open("a", encoding="utf-8") as calls:
        calls.write(json.dumps(argv) + "\n")
    if argv == ["--version"]:
        print(VERSION_LINE)
        return 0
    if argv[:2] != ["kanban", "--board"] or len(argv) < 4:
        print(f"fake runtime: unexpected call {argv}", file=sys.stderr)
        return 2
    board, command = argv[2], argv[3:]
    failing = os.environ.get("FAKE_RUNTIME_FAIL")
    if failing in ("all", command[0]):
        print(f"kanban: {command[0]} refusé par le faux runtime", file=sys.stderr)
        return 1
    if command[:2] == ["boards", "create"] and len(command) == 3:
        path = base(command[2])
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(path) as db:
                db.executescript(RUNTIME_SCHEMA.read_text(encoding="utf-8"))
            db.close()
        print(f"Created board {command[2]}")
        return 0
    path = base(board)
    if not path.is_file():
        print(f"kanban: no board {board}", file=sys.stderr)
        return 1
    with sqlite3.connect(path) as db:
        if command[0] == "create":
            card = f"t_{secrets.token_hex(4)}"
            row = {
                "id": card,
                "title": command[1],
                "body": option(command, "--body"),
                "assignee": option(command, "--assignee"),
                "status": "ready",
                "created_at": int(time.time()),
                "idempotency_key": option(command, "--idempotency-key"),
            }
            marks = ", ".join("?" * len(row))
            db.execute(f"INSERT INTO tasks ({', '.join(row)}) VALUES ({marks})", tuple(row.values()))
            if "--json" in command:
                print(json.dumps(row))
            return 0
        if command[0] == "assign" and len(command) == 3:
            changed = db.execute("UPDATE tasks SET assignee = ? WHERE id = ?", (command[2], command[1])).rowcount
            if not changed:
                print(f"no such task: {command[1]}", file=sys.stderr)
                return 1
            print(f"Assigned {command[1]} to {command[2]}")
            return 0
    db.close()
    print(f"fake runtime: unknown command {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
