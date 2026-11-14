#!/usr/bin/env python3
"""Put an owner's answer in the engine's answer box (`engine.db` of a state folder): the tool of the engine's test
machine (docs/RUNBOOK.md, « La machine de test »). The engine of the core that uses that state folder hands it, at its
next pass, to the command of the `owner` station where the item waits. Prints the answer's id.

    uv run python scripts/inject_answer.py --state-dir <folder> --item <item> --gesture <gesture> [--note <text>]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from workflow_core.engine.answers import ENGINE_DB, AnswerBox


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Put an owner's answer in the engine's answer box.")
    parser.add_argument("--state-dir", required=True, type=Path, help="the core's state folder")
    parser.add_argument("--item", required=True, help="the item's id, <key>:<local id>")
    parser.add_argument("--gesture", required=True, help="the owner's gesture")
    parser.add_argument("--note", default="", help="the gesture's note")
    arguments = parser.parse_args(argv)
    box = AnswerBox(arguments.state_dir.expanduser() / ENGINE_DB)
    print(box.receive(arguments.item, arguments.gesture, arguments.note))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
