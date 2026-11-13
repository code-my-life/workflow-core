"""The small helpers shared since the end-of-epic cleanup (factory-live#70): each keeps the behaviour of the copies it
replaced."""

from workflow_core.adapters.hermes.values import as_number, as_record, as_strings, as_text, dotted
from workflow_core.model.checks import check_time, machine_check
from workflow_core.text import utf16_length


def test_the_loose_value_readers_keep_only_the_expected_shape():
    assert as_record({"a": 1}) == {"a": 1}
    assert as_record(["a"]) == {}
    assert as_strings(["a", 1, None, "b"]) == ["a", "b"]
    assert as_strings("a") == []
    assert as_text("  coder  ") == "coder"
    assert as_text("   ") is None
    assert as_text(3) is None
    assert as_number(2) == 2.0
    assert as_number(True) == 0.0
    assert as_number("2") == 0.0
    assert dotted({"plugins": {"enabled": ["x"]}}, "plugins.enabled") == ["x"]
    assert dotted({"plugins": ["x"]}, "plugins.enabled") is None


def test_a_text_is_measured_in_utf16_code_units():
    assert utf16_length("abc") == 3
    assert utf16_length("é") == 1
    assert utf16_length("😀") == 2


def test_a_machine_tile_writes_its_time_and_its_readings_as_text():
    tile = machine_check("disk", "Disque", "tools", "ok", "12 Go libres", 0.5, libre=0.42, pid=12)

    assert check_time(0.5) == "1970-01-01T00:00:00.500000Z"
    assert tile.checked_at == "1970-01-01T00:00:00.500000Z"
    assert tile.metrics == {"libre": "0.42", "pid": "12"}
    assert machine_check("gateway", "Gateway", "runtime", "down", "Aucun battement", 60).metrics == {}
    assert machine_check("gateway", "Gateway", "runtime", "down", "Aucun battement", 60).checked_at == (
        "1970-01-01T00:01:00Z"
    )
