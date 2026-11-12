"""The reference outputs of the diagnosis, copied from factory-live: anonymous, and named by their source commit.

This repository is public (ADR 0002): the check runs on every copied file before it can reach `main`.
"""

import json
import re
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "diagnosis"
FICTIONAL_HOME = "/home/user"
FICTIONAL_TAILNET_HOST = "machine.tailnet.ts.net"
CHECKS = (
    ("dossier personnel", re.compile(r"/home/[A-Za-z_][\w.-]*"), FICTIONAL_HOME),
    ("dossier personnel", re.compile(r"/Users/"), None),
    ("dossier personnel", re.compile(r"C:\\{1,2}Users\\{1,2}", re.IGNORECASE), None),
    ("tailnet", re.compile(r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.ts\.net\b", re.IGNORECASE), FICTIONAL_TAILNET_HOST),
    ("adresse", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}\b", re.IGNORECASE), None),
    ("jeton", re.compile(r"\b(?:ghp_|gho_|github_pat_|sk-|xoxb-|xoxp-|AKIA)[A-Za-z0-9_-]{8,}"), None),
)


def personal_data(text: str) -> list[str]:
    """What a copied file must not hold: a home folder, a real tailnet, an address, a token."""
    return [
        f"{what} : {found.group(0)}"
        for what, pattern, allowed in CHECKS
        for found in pattern.finditer(text)
        if found.group(0) != allowed
    ]


def copied() -> list[Path]:
    return sorted(path for path in FIXTURES.glob("*.json") if path.name != "source.json")


def test_the_copied_references_hold_no_personal_data_nor_token(tmp_path):
    assert len(copied()) == 13
    for path in copied():
        assert personal_data(path.read_text(encoding="utf-8")) == [], path.name

    token = "ghp_" + "a1B2c3D4e5" * 3 + "abcdef"
    leaked = tmp_path / "leaked.json"
    leaked.write_text(json.dumps({"path": "/home/alice/projets/x", "quote": f"token {token}"}), encoding="utf-8")
    assert personal_data(leaked.read_text(encoding="utf-8")) == [
        "dossier personnel : /home/alice",
        f"jeton : {token}",
    ]


def test_the_copied_references_name_their_source_commit():
    source = json.loads((FIXTURES / "source.json").read_text(encoding="utf-8"))

    assert source["repo"] == "code-my-life/factory-live"
    assert source["path"] == "fixtures/diagnosis-references"
    assert re.fullmatch(r"[0-9a-f]{40}", source["commit"])
    names = {json.loads(path.read_text(encoding="utf-8"))["case"] for path in copied()}
    assert {path.stem for path in copied()} == names
