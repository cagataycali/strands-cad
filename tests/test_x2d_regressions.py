"""Regression tests for bugs found live against a real X2D (2026-08-14).

The session that found them: tiny printed the wisp nicla-sandwich lower shell.
Three failures, one printer stuck in gcode_state=FAILED with 0x05004002:

1. bambu_send claimed "job dispatched" without ever uploading the file — the
   printer then failed with print_error 0x05004002 (file not found on SD).
2. bambu_send never verified the outcome — a fire-and-forget MQTT publish was
   reported as success.
3. cq_render_stl(script=<path>) exploded with "SyntaxError: invalid syntax
   (<string>, line 1)" because a path is not a program.
"""
import json
import time
import types
import threading
from pathlib import Path

import pytest


def _text(r):
    c = r["content"]
    if isinstance(c, list):
        return " ".join(part.get("text", "") for part in c)
    return c


# ── cq: script accepts a path ────────────────────────────────────────

def test_cq_script_source_inline_passthrough():
    from strands_cad.tools.cadquery_tools import _script_source
    src = 'result = cq.Workplane("XY").box(1, 1, 1)'
    assert _script_source(src) == src


def test_cq_script_source_reads_py_file(tmp_path):
    from strands_cad.tools.cadquery_tools import _script_source
    f = tmp_path / "part.py"
    f.write_text('result = cq.Workplane("XY").box(2, 2, 2)')
    assert _script_source(str(f)) == f.read_text()


def test_cq_script_source_missing_path_is_actionable():
    from strands_cad.tools.cadquery_tools import _script_source
    with pytest.raises(FileNotFoundError):
        _script_source("/no/such/dir/part.py")


def test_cq_render_stl_accepts_path(tmp_path):
    pytest.importorskip("cadquery")
    from strands_cad.tools.cadquery_tools import cq_render_stl
    script = tmp_path / "cube.py"
    script.write_text('result = cq.Workplane("XY").box(5, 5, 5)')
    out = tmp_path / "cube.stl"
    r = cq_render_stl(script=str(script), output_stl=str(out))
    assert r["status"] == "success", r
    assert out.exists() and out.stat().st_size > 0


# ── bambu_send: uploads first, verifies after ────────────────────────

class _FakeClient:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload):
        self.published.append((topic, json.loads(payload)))


def _wire_fake_conn(monkeypatch, bambu, state):
    client = _FakeClient()
    monkeypatch.setitem(bambu._CONN, "client", client)
    monkeypatch.setitem(bambu._CONN, "serial", "TESTSERIAL")
    monkeypatch.setitem(bambu._CONN, "ip", "127.0.0.1")
    monkeypatch.setitem(bambu._CONN, "access_code", "x")
    monkeypatch.setitem(bambu._CONN, "last_state", state)
    return client


def test_bambu_send_uploads_before_dispatch(monkeypatch, tmp_path):
    from strands_cad.tools import bambu
    calls = []
    monkeypatch.setattr(bambu, "bambu_upload",
                        lambda p, remote_name="": (calls.append(p),
                                                   {"status": "success",
                                                    "content": "up"})[1])
    monkeypatch.setattr(bambu.time, "sleep", lambda s: None)
    state = {"gcode_state": "IDLE", "print_error": 0}
    client = _wire_fake_conn(monkeypatch, bambu, state)
    real_publish = client.publish
    def firmware(topic, payload):
        real_publish(topic, payload)
        if json.loads(payload).get("print", {}).get("command") == "project_file":
            state["gcode_state"] = "RUNNING"
            state["subtask_name"] = "part"
    client.publish = firmware
    f = tmp_path / "part.gcode.3mf"
    f.write_bytes(b"x")
    r = bambu.bambu_send(str(f))
    assert calls, "bambu_send must upload the file before dispatching"
    assert r["status"] == "success"
    assert "print started" in _text(r)


def test_bambu_send_fails_when_upload_fails(monkeypatch, tmp_path):
    from strands_cad.tools import bambu
    monkeypatch.setattr(bambu, "bambu_upload",
                        lambda p, remote_name="": {"status": "error",
                                                   "content": "553 no card"})
    _wire_fake_conn(monkeypatch, bambu, {})
    f = tmp_path / "part.gcode.3mf"
    f.write_bytes(b"x")
    r = bambu.bambu_send(str(f))
    assert r["status"] == "error"
    assert "553" in _text(r)


def test_bambu_send_reports_firmware_rejection(monkeypatch, tmp_path):
    """A print_error appearing AFTER dispatch = rejection, surfaced in hex.

    The error must post-date the project_file command: a latched pre-existing
    code is snapshotted away, exactly like the real clean_print_error flow.
    """
    from strands_cad.tools import bambu
    monkeypatch.setattr(bambu, "bambu_upload",
                        lambda p, remote_name="": {"status": "success",
                                                   "content": "up"})
    monkeypatch.setattr(bambu.time, "sleep", lambda s: None)
    state = {"gcode_state": "IDLE", "print_error": 0}
    client = _wire_fake_conn(monkeypatch, bambu, state)

    real_publish = client.publish
    def firmware(topic, payload):
        real_publish(topic, payload)
        if json.loads(payload).get("print", {}).get("command") == "project_file":
            state["gcode_state"] = "FAILED"
            state["print_error"] = 83902466
    client.publish = firmware
    f = tmp_path / "part.gcode.3mf"
    f.write_bytes(b"x")
    r = bambu.bambu_send(str(f))
    assert r["status"] == "error"
    assert "0x05004002" in _text(r)


def test_bambu_send_busy_printer_is_not_success(monkeypatch, tmp_path):
    """RUNNING on a DIFFERENT subtask means our job did NOT start."""
    from strands_cad.tools import bambu
    monkeypatch.setattr(bambu, "bambu_upload",
                        lambda p, remote_name="": {"status": "success",
                                                   "content": "up"})
    monkeypatch.setattr(bambu.time, "sleep", lambda s: None)
    monkeypatch.setattr(bambu.time, "time",
                        _fake_clock())
    state = {"gcode_state": "RUNNING", "print_error": 0,
             "subtask_name": "someone_elses_print"}
    client = _wire_fake_conn(monkeypatch, bambu, state)
    f = tmp_path / "part.gcode.3mf"
    f.write_bytes(b"x")
    r = bambu.bambu_send(str(f))
    assert r["status"] == "error"
    assert "busy" in _text(r)
    # the guard must fire BEFORE dispatch — the real X2D REPLACES a running
    # job instead of refusing (verified live, killed a print at 10%)
    cmds = [pl.get("print", {}).get("command") for _, pl in client.published]
    assert "project_file" not in cmds


def _fake_clock():
    t = [1000.0]

    def clock():
        t[0] += 2.0
        return t[0]
    return clock
