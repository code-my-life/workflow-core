"""The CI check that keeps the agent runtime's name inside its adapter (ADR 0001)."""

from pathlib import Path

from check_runtime_confined import main, offenses

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = "Her" + "mès"


def make_package(tmp_path: Path) -> Path:
    (tmp_path / "workflow_core" / "adapters" / "hermes").mkdir(parents=True)
    (tmp_path / "workflow_core" / "__init__.py").write_text('"""Core."""\n', encoding="utf-8")
    return tmp_path


def test_the_repository_package_names_the_runtime_only_in_its_adapter():
    assert offenses(ROOT) == []


def test_a_mention_outside_the_adapter_fails(tmp_path):
    root = make_package(tmp_path)
    (root / "workflow_core" / "engine.py").write_text(f"# reads the {RUNTIME} board\n", encoding="utf-8")
    assert offenses(root) == [f"workflow_core/engine.py:1: # reads the {RUNTIME} board"]
    assert main(["check", str(root)]) == 1


def test_a_mention_without_accent_or_in_capitals_fails(tmp_path):
    root = make_package(tmp_path)
    (root / "workflow_core" / "api.py").write_text("URL = 'HERMES_HOME'\n", encoding="utf-8")
    assert main(["check", str(root)]) == 1


def test_a_file_named_after_the_runtime_fails(tmp_path):
    root = make_package(tmp_path)
    (root / "workflow_core" / ("her" + "mes_board.py")).write_text("X = 1\n", encoding="utf-8")
    assert len(offenses(root)) == 1


def test_a_mention_inside_the_adapter_passes(tmp_path):
    root = make_package(tmp_path)
    (root / "workflow_core" / "adapters" / "hermes" / "board.py").write_text(f"# {RUNTIME} board\n", encoding="utf-8")
    assert offenses(root) == []
    assert main(["check", str(root)]) == 0
