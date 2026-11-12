"""The SSE stream: every card change reaches it in under a second, with the full item and its cursor (ADR 0011)."""

from __future__ import annotations

import contextlib
import itertools
import json
import queue
import socket
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlencode

import httpx2 as httpx
import jsonschema
import pytest
import uvicorn
from workflow_core.adapters import runtime_adapter
from workflow_core.config import Workflow
from workflow_core.events import EventJournal
from workflow_core.service import build_service, server_config

from conftest import FACTORY_ITEMS

SECRET = "test-secret-not-a-real-one"
AUTH = {"Authorization": f"Bearer {SECRET}"}
WORKFLOWS = {
    "factory": Workflow(key="factory", board="factory", sheet=Path("factory.json")),
    "studio": Workflow(key="studio", board="studio", sheet=Path("studio.json")),
}


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@contextmanager
def running_service(
    clock=time.time, spy=None, stopped: dict | None = None, checks=(), checks_every: float | None = None
) -> Iterator[str]:
    """The service as `python -m workflow_core` builds it, served by a real uvicorn on a free port."""
    extra = {"checks_every": checks_every} if checks_every is not None else {}
    app = build_service(
        secret=SECRET,
        workflows=WORKFLOWS,
        adapter=runtime_adapter(),
        clock=clock,
        spy=spy,
        checks=list(checks),
        **extra,
    )
    port = free_port()
    server = uvicorn.Server(server_config(app, "127.0.0.1", port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "the service did not start"
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        asked = time.monotonic()
        server.should_exit = True
        thread.join(timeout=10)
        if stopped is not None:
            stopped["seconds"] = time.monotonic() - asked
            stopped["alive"] = thread.is_alive()


class Stream:
    """An SSE client on a raw socket, read in a thread: each frame lands in a queue as (id, event, data). Closing it
    shuts the socket down, so the service sees the client leave, as with a browser closing its tab."""

    def __init__(self, base: str, headers: dict[str, str] | None = None, params: dict[str, str] | None = None):
        self.frames: queue.Queue = queue.Queue()
        self.status: int | None = None
        self.content_type: str | None = None
        self.opened = threading.Event()
        host, port = base.removeprefix("http://").split(":")
        target = "/api/events" + ("?" + urlencode(params) if params else "")
        lines = [f"GET {target} HTTP/1.1", f"Host: {host}:{port}", "Accept: text/event-stream"]
        lines += [f"{name}: {value}" for name, value in {**AUTH, **(headers or {})}.items()]
        self._socket = socket.create_connection((host, int(port)), timeout=10)
        self._socket.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("utf-8"))
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()
        assert self.opened.wait(10), "the stream did not open"

    def _read(self) -> None:
        reader = self._socket.makefile("rb")
        try:
            status_line = reader.readline().decode()
            self.status = int(status_line.split()[1])
            while (header := reader.readline().decode().strip()) != "":
                name, _, value = header.partition(": ")
                if name.lower() == "content-type":
                    self.content_type = value
            self.opened.set()
            frame: dict[str, str] = {}
            for raw in reader:
                line = raw.decode("utf-8").rstrip("\r\n")
                if line == "":
                    if "data" in frame:
                        self.frames.put((frame.get("id"), frame.get("event"), json.loads(frame["data"])))
                    frame = {}
                elif line.startswith(("id: ", "event: ", "data: ")):
                    field, _, value = line.partition(": ")
                    frame[field] = value
                # Other lines are the chunk sizes of the chunked transfer, or comments: skipped.
        except (OSError, ValueError, IndexError):
            pass
        finally:
            self.opened.set()

    def next(self, timeout: float = 5) -> tuple[str, str, dict]:
        return self.frames.get(timeout=timeout)

    def nothing_for(self, seconds: float) -> bool:
        try:
            self.frames.get(timeout=seconds)
        except queue.Empty:
            return True
        return False

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self._socket.shutdown(socket.SHUT_RDWR)
        self._socket.close()
        self._thread.join(5)


def write(base: Path, sql: str, *args) -> float:
    """Write in a board's base through another connection, as the runtime does; return the time of the commit."""
    with sqlite3.connect(base) as db:
        db.execute(sql, args)
    db.close()
    return time.monotonic()


def board_base(home: Path, board: str) -> Path:
    return home / "kanban" / "boards" / board / "kanban.db"


@pytest.fixture
def state_dir(tmp_path, monkeypatch) -> Path:
    folder = tmp_path / "state"
    monkeypatch.setenv("WORKFLOW_CORE_STATE_DIR", str(folder))
    return folder


def test_a_card_change_reaches_the_stream_in_under_a_second(runtime_home, state_dir):
    with running_service() as base:
        stream = Stream(base)
        assert stream.status == 200
        assert stream.content_type.startswith("text/event-stream")
        time.sleep(0.5)  # the first probe has read the boards

        written = write(board_base(runtime_home, "factory"), "UPDATE tasks SET status = 'review' WHERE id = 't_0002'")
        _, kind, event = stream.next(timeout=2)
        elapsed = time.monotonic() - written
        stream.close()

    assert kind == "changed"
    assert (event["workflow"], event["item"], event["state"]["status"]) == ("factory", "t_0002", "review")
    assert elapsed < 1.0, f"the change took {elapsed:.3f} s to reach the stream"


def test_each_event_carries_the_full_item_state_and_the_next_cursor(runtime_home, state_dir):
    with running_service() as base:
        stream = Stream(base)
        time.sleep(0.5)
        factory = board_base(runtime_home, "factory")
        write(factory, "UPDATE tasks SET status = 'review' WHERE id = 't_0002'")
        first = stream.next()
        write(
            factory, "INSERT INTO task_comments (task_id, author, body, created_at) VALUES ('t_0001', 'gate', 'ok', 5)"
        )
        second = stream.next()
        items = httpx.get(f"{base}/api/workflows/factory/items", headers=AUTH).json()["items"]
        stream.close()

    epoch, n = first[0].split(":")
    assert second[0] == f"{epoch}:{int(n) + 1}"
    for cursor, kind, event in (first, second):
        assert kind == "changed"
        assert event["cursor"] == cursor
        assert set(event) == {"cursor", "workflow", "item", "kind", "at", "state"}
        assert event["state"] == next(item for item in items if item["id"] == event["item"])
    assert second[2]["item"] == "t_0001"
    assert second[2]["state"]["comments"][-1]["body"] == "ok"


def test_an_unchanged_data_version_emits_nothing(runtime_home, state_dir):
    reads: list[str] = []
    with running_service(spy=reads.append) as base:
        stream = Stream(base)
        time.sleep(0.5)
        settled = len(reads)
        quiet = stream.nothing_for(1.5)
        stream.close()

    assert settled >= 2  # one read per board, at the first probe
    assert quiet
    assert len(reads) == settled  # about five probes of each board, and no read of the cards


def test_a_rebuilt_journal_opens_a_new_epoch_with_a_reset(runtime_home, state_dir):
    ticks = itertools.count(1_762_900_000)
    with running_service(clock=lambda: next(ticks)):
        pass
    first = EventJournal.open_existing(state_dir / "events.db")
    old_epoch = first.epoch
    first.close()

    (state_dir / "events.db").unlink()
    with running_service(clock=lambda: next(ticks)):
        pass

    journal = EventJournal.open_existing(state_dir / "events.db")
    events = journal.events_after(0)
    journal.close()
    assert journal.epoch != old_epoch
    assert events[0]["kind"] == "reset"
    assert events[0]["cursor"] == f"{journal.epoch}:1"
    assert (events[0]["workflow"], events[0]["item"], events[0]["state"]) == (None, None, None)


def test_a_cursor_of_an_old_epoch_gets_the_reset_first(runtime_home, state_dir):
    ticks = itertools.count(1_762_900_000)
    with running_service(clock=lambda: next(ticks)) as base:
        stream = Stream(base)
        time.sleep(0.5)
        write(board_base(runtime_home, "factory"), "UPDATE tasks SET status = 'review' WHERE id = 't_0002'")
        old_cursor = stream.next()[0]
        stream.close()

    (state_dir / "events.db").unlink()
    with running_service(clock=lambda: next(ticks)) as base:
        for headers, params in (({"Last-Event-ID": old_cursor}, None), (None, {"cursor": old_cursor})):
            stream = Stream(base, headers=headers, params=params)
            cursor, kind, _ = stream.next()
            time.sleep(0.4)
            write(board_base(runtime_home, "studio"), "UPDATE tasks SET status = 'done' WHERE id = 't_0100'")
            following = stream.next()
            stream.close()

            assert kind == "reset"
            assert cursor.split(":")[0] != old_cursor.split(":")[0]
            assert cursor.endswith(":1")
            assert following[1] == "changed"
            # The client rereads the full state through the API, as the reset asks.
            items = httpx.get(f"{base}/api/workflows/factory/items", headers=AUTH).json()["items"]
            assert items[1]["status"] == "review"
            write(board_base(runtime_home, "studio"), "UPDATE tasks SET status = 'running' WHERE id = 't_0100'")
            time.sleep(0.6)


def test_a_current_cursor_gets_the_missed_events_in_order(runtime_home, state_dir):
    with running_service() as base:
        stream = Stream(base)
        time.sleep(0.5)
        factory = board_base(runtime_home, "factory")
        write(factory, "UPDATE tasks SET status = 'review' WHERE id = 't_0002'")
        kept = stream.next()[0]
        stream.close()

        # Missed while away: three changes, each probed apart.
        for status in ("running", "done"):
            write(factory, f"UPDATE tasks SET status = '{status}' WHERE id = 't_0002'")
            time.sleep(0.6)
        write(factory, "UPDATE tasks SET title = 'Renamed' WHERE id = 't_0001'")
        time.sleep(0.6)

        back = Stream(base, headers={"Last-Event-ID": kept})
        missed = [back.next() for _ in range(3)]
        no_duplicate = back.nothing_for(0.8)
        back.close()

    epoch, n = kept.split(":")
    assert [cursor for cursor, _, _ in missed] == [f"{epoch}:{int(n) + k}" for k in (1, 2, 3)]
    assert [(event["item"], event["state"]["status"]) for _, _, event in missed] == [
        ("t_0002", "running"),
        ("t_0002", "done"),
        ("t_0001", FACTORY_ITEMS[0]["status"]),
    ]
    assert missed[2][2]["state"]["title"] == "Renamed"
    assert no_duplicate


def test_the_stream_refuses_a_missing_secret(runtime_home, state_dir):
    with running_service() as base:
        for headers in ({}, {"Authorization": "Bearer wrong"}):
            response = httpx.get(f"{base}/api/events", headers=headers, timeout=5)
            assert response.status_code == 401
            assert response.json()["error"] == "unauthorized"


def test_the_probe_leaves_every_base_unchanged(runtime_home, state_dir):
    bases = sorted(runtime_home.rglob("kanban.db"))
    before = {base: base.read_bytes() for base in bases}
    reads: list[str] = []

    with running_service(spy=reads.append):
        time.sleep(2)  # about seven probes of each board, without any writer

    assert {base: base.read_bytes() for base in bases} == before
    assert not list(runtime_home.rglob("events.db"))


def test_the_service_stops_within_seconds_with_an_open_stream(runtime_home, state_dir):
    # A client that keeps its stream open (factory-live) must not hold `systemctl stop` until systemd kills the core.
    stopped: dict = {}
    with running_service(stopped=stopped) as base:
        stream = Stream(base)
        time.sleep(0.5)

    assert stopped["alive"] is False
    assert stopped["seconds"] < 5
    stream.close()


def test_the_read_routes_carry_the_current_cursor(runtime_home, state_dir):
    # A client reads the state, then opens the stream at the cursor of that read: it misses nothing in between.
    with running_service() as base:
        time.sleep(0.5)
        routes = ("/api/workflows", "/api/workflows/factory/items", "/api/machine")
        cursors = {route: httpx.get(f"{base}{route}", headers=AUTH).json().get("cursor") for route in routes}
        journal = EventJournal.open_existing(state_dir / "events.db")
        current = journal.cursor
        journal.close()
        assert cursors == dict.fromkeys(routes, current)

        read = httpx.get(f"{base}/api/workflows/factory/items", headers=AUTH).json()
        write(board_base(runtime_home, "factory"), "UPDATE tasks SET status = 'review' WHERE id = 't_0002'")
        time.sleep(0.6)
        stream = Stream(base, params={"cursor": read["cursor"]})
        cursor, kind, event = stream.next()
        stream.close()

    epoch, n = read["cursor"].split(":")
    assert (cursor, kind, event["item"]) == (f"{epoch}:{int(n) + 1}", "changed", "t_0002")


def test_a_new_card_event_goes_on_the_stream_with_the_full_item(runtime_home, state_dir):
    with running_service() as base:
        stream = Stream(base)
        time.sleep(0.5)  # the first probe has read the boards

        written = write(
            board_base(runtime_home, "factory"),
            "INSERT INTO task_events (task_id, run_id, kind, payload, created_at) VALUES (?, ?, ?, ?, ?)",
            "t_0002",
            None,
            "blocked",
            '{"reason": "il manque une preuve"}',
            1762811500,
        )
        _, kind, event = stream.next(timeout=2)
        elapsed = time.monotonic() - written
        items = httpx.get(f"{base}/api/workflows/factory/items", headers=AUTH).json()["items"]
        stream.close()

    assert kind == "changed"
    assert (event["workflow"], event["item"]) == ("factory", "t_0002")
    assert event["state"]["events"][-1]["kind"] == "blocked"
    assert event["state"]["events"][-1]["payload"] == {"reason": "il manque une preuve"}
    assert event["state"] == next(item for item in items if item["id"] == "t_0002")
    assert elapsed < 1.0, f"the event took {elapsed:.3f} s to reach the stream"


def test_a_check_status_change_goes_on_the_stream_once(runtime_home, state_dir, tmp_path, monkeypatch):
    from workflow_core.model.checks import DeclaredCheck

    # No machine tool is run: only the declared tcp check matters here.
    empty = tmp_path / "no-tools"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    listening = socket.socket()
    listening.bind(("127.0.0.1", 0))
    listening.listen()
    port = listening.getsockname()[1]
    check = DeclaredCheck(id="graphify", label="MCP graphify", group="tools", kind="tcp", target=str(port))
    with running_service(checks=[check], checks_every=0.2) as base:
        machine = httpx.get(f"{base}/api/machine", headers=AUTH).json()
        assert {c["id"]: c["status"] for c in machine["checks"]}["graphify"] == "ok"
        stream = Stream(base)
        time.sleep(0.5)  # a few rounds of checks, all ok: no event

        listening.close()
        frames = []
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                frames.append(stream.next(timeout=0.2))
            except queue.Empty:
                continue
        stream.close()

    machines = [(kind, event) for _, kind, event in frames if kind == "machine"]
    assert len(machines) == 1, machines
    _, event = machines[0]
    assert (event["workflow"], event["item"]) == (None, None)
    assert {c["id"]: c["status"] for c in event["state"]}["graphify"] == "down"
    schema = json.loads((Path(__file__).resolve().parent.parent / "schema" / "event.schema.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(event)
