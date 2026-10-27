"""The operator gate and the prompt-to-print pipeline.

Pins the safety contract of the revamped cockpit: NO path starts a print without an
approved gate; a denied or timed-out gate ends the job as `denied`; the slicer's own
words reach the job on failure; the agent's toolbelt cannot reach bambu_send; the
"what is the printer doing" prompt block carries job name + temperatures; and tiny's
service token cannot approve a gate.
"""
import json
import os
import threading
import time
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")

CUBE_STL = Path(__file__).parent / "fixtures" / "cube.stl"


def _write_cube(path: Path, size: float = 20.0) -> Path:
    """A binary-less ASCII cube STL, no OpenSCAD needed."""
    s = size
    v = [(0, 0, 0), (s, 0, 0), (s, s, 0), (0, s, 0), (0, 0, s), (s, 0, s), (s, s, s), (0, s, s)]
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
             (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    lines = ["solid cube"]
    for a, b, c in faces:
        lines.append("facet normal 0 0 0\nouter loop")
        for i in (a, b, c):
            lines.append("vertex %g %g %g" % v[i])
        lines.append("endloop\nendfacet")
    lines.append("endsolid cube")
    path.write_text("\n".join(lines))
    return path


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    wd = tmp_path / "wd"
    wd.mkdir()
    monkeypatch.setenv("STRANDS_CAD_WORKDIR", str(wd))
    monkeypatch.setenv("STRANDS_CAD_JOBS_STORE", str(tmp_path / "jobs.json"))
    monkeypatch.setenv("STRANDS_CAD_CONFIG_STORE", str(tmp_path / "cfg.json"))
    from strands_cad.dashboard import config_store, jobs, gates
    monkeypatch.setattr(config_store, "CONFIG_STORE", tmp_path / "cfg.json")
    jobs._JOBS.clear(); jobs._LOADED = False
    gates._GATES.clear(); gates._EVENTS.clear()
    monkeypatch.setattr(gates, "GATE_TIMEOUT_S", 2)
    config_store.update({"workdir": str(wd)})
    return wd


def test_gate_approve_and_timeout():
    from strands_cad.dashboard import gates
    gates._GATES.clear(); gates._EVENTS.clear()
    seen = []
    gates.add_listener(seen.append)
    try:
        gid = gates.open_gate("print", "cube", job="j1", timeout_s=5)
        assert gates.pending() and gates.pending()[0]["id"] == gid
        threading.Timer(0.2, lambda: gates.answer(gid, True, by="test")).start()
        assert gates.wait(gid) is True
        assert gates.get(gid)["decision"] is True and gates.get(gid)["by"] == "test"
        # a second answer cannot flip it
        assert gates.answer(gid, False)["already"] is True
        assert gates.get(gid)["decision"] is True

        g2 = gates.open_gate("print", "cube 2", timeout_s=1)
        t0 = time.time()
        assert gates.wait(g2) is False
        assert time.time() - t0 < 3
        rec = gates.get(g2)
        assert rec["timed_out"] is True and rec["decision"] is False
        kinds = [(f["id"], f.get("answered", False), f.get("timed_out", False)) for f in seen]
        assert (gid, False, False) in kinds and (gid, True, False) in kinds and (g2, True, True) in kinds
    finally:
        gates.remove_listener(seen.append)


def test_pipeline_validates_previews_and_carries_slicer_words(workdir, monkeypatch):
    """No slicer on the test host → the job ends in `error` with the slicer's own words, AFTER validate and preview
    succeeded (preview PNG exists). No gate is opened, so nothing could have printed."""
    from strands_cad.dashboard import pipeline, jobs, gates
    from strands_cad.tools import slice as _slice
    monkeypatch.setattr(_slice, "_find_orca_docker_image", lambda: None)
    monkeypatch.setattr(_slice, "_find_bambu_cli", lambda: None)
    monkeypatch.setattr(_slice, "_find_prusa_cli", lambda: None)
    _write_cube(workdir / "cube20.stl")

    r = pipeline.start(model="cube20.stl", title="test cube", requested_by="pytest")
    jid = r["job_id"]
    for _ in range(100):
        j = jobs.get(jid)
        if j["state"] in ("error", "denied", "done", "awaiting_approval"):
            break
        time.sleep(0.1)
    assert j["state"] == "error", j
    assert "slicer" in j["error"] and "no slicer CLI found" in j["error"]
    steps = {s["name"]: s for s in j["steps"]}
    assert steps["validate"]["state"] == "done" and "20.0" in steps["validate"]["text"]
    assert steps["preview"]["state"] == "done" and (workdir / "cube20_preview.png").exists()
    assert steps["slice"]["state"] == "error"
    assert "gate" not in steps and gates.pending() == []
    assert j["preview"] == "/api/model/cube20_preview.png"
    # persisted
    data = json.loads(Path(os.environ["STRANDS_CAD_JOBS_STORE"]).read_text())
    assert data["jobs"][0]["id"] == jid

    # idempotent id: same file + profile → same id; a finished (error) job does not block a retry
    assert pipeline.job_id_for(workdir / "cube20.stl", j["profile"]) == jid


def test_pipeline_gate_denied_never_prints(workdir, monkeypatch):
    """Slicer stubbed to succeed → the job waits at the gate; a deny ends it `denied` and print_sync is never called."""
    from strands_cad.dashboard import pipeline, jobs, gates
    _write_cube(workdir / "bracket.stl")
    printed = []
    monkeypatch.setattr(jobs, "slice_sync", lambda jid, src, profile, pm: {"printable": str(src.with_suffix(".3mf")), "gcode": "x", "time": "0h12m", "filament_g": 4.2})
    monkeypatch.setattr(jobs, "print_sync", lambda *a, **k: printed.append(a) or {"started": True})

    r = pipeline.start(model="bracket.stl", title="bracket", requested_by="pytest")
    jid = r["job_id"]
    for _ in range(100):
        j = jobs.get(jid)
        if j["state"] == "awaiting_approval":
            break
        time.sleep(0.05)
    assert j["state"] == "awaiting_approval" and j["gate"]
    g = gates.get(j["gate"])
    assert g["job"] == jid and "bracket" in g["detail"] and "0h12m" in g["detail"] and "4.2 g" in g["detail"]
    # re-asking while waiting returns the SAME job, no second gate
    again = pipeline.start(model="bracket.stl", title="bracket", requested_by="pytest")
    assert again["existing"] is True and again["job_id"] == jid and len(gates.pending()) == 1

    gates.answer(j["gate"], False, by="pytest")
    for _ in range(50):
        j = jobs.get(jid)
        if j["state"] == "denied":
            break
        time.sleep(0.05)
    assert j["state"] == "denied" and "denied by pytest" in j["error"]
    assert printed == []
    assert pipeline.describe(jid).startswith("'bracket' was not printed")


def test_pipeline_approved_prints_once(workdir, monkeypatch):
    from strands_cad.dashboard import pipeline, jobs, gates
    _write_cube(workdir / "ok.stl")
    printed = []
    monkeypatch.setattr(jobs, "slice_sync", lambda jid, src, profile, pm: {"printable": str(src), "gcode": "x", "time": "1h", "filament_g": 9})
    monkeypatch.setattr(jobs, "print_sync", lambda jid, src, **k: printed.append(str(src)) or {"started": True, "job": src.name})
    jid = pipeline.start(model="ok.stl", title="ok")["job_id"]
    for _ in range(100):
        j = jobs.get(jid)
        if j["state"] == "awaiting_approval":
            break
        time.sleep(0.05)
    gates.answer(j["gate"], True, by="owner")
    for _ in range(50):
        j = jobs.get(jid)
        if j["state"] == "done":
            break
        time.sleep(0.05)
    assert j["state"] == "done" and printed == [str(workdir / "ok.stl")]
    assert {s["name"]: s["state"] for s in j["steps"]}["print"] == "done"


def test_legacy_print_route_is_gated(workdir, monkeypatch):
    """/api/print (and jobs.start_print behind Telegram's /print) opens a gate instead of uploading."""
    from strands_cad.dashboard import jobs, gates
    (workdir / "old.gcode").write_text(";gcode\n")
    printed = []
    monkeypatch.setattr(jobs, "print_sync", lambda *a, **k: printed.append(1) or {"started": True})
    jid = jobs.start_print("old.gcode", requested_by="telegram")
    for _ in range(100):
        j = jobs.get(jid)
        if j["state"] == "awaiting_approval":
            break
        time.sleep(0.05)
    assert j["state"] == "awaiting_approval" and gates.pending()[0]["job"] == jid
    gates.answer(j["gate"], False, by="pytest")
    time.sleep(0.3)
    assert jobs.get(jid)["state"] == "denied" and printed == []


def test_agent_toolbelt_has_no_direct_print():
    from strands_cad import ALL_TOOLS
    from strands_cad.dashboard import chat_agent
    names = {getattr(t, "tool_name", getattr(t, "__name__", "")) for t in ALL_TOOLS}
    assert "bambu_send" in names  # the tool exists in the package …
    kept = {n for n in names if n not in chat_agent._EXCLUDED_TOOLS}
    assert not ({"bambu_send", "bambu_upload", "bambu_control"} & kept)  # … but never in the chat agent
    assert "shell" not in kept
    assert "print_pipeline" in chat_agent._BASE_PROMPT and "cannot approve" in chat_agent._BASE_PROMPT


def test_live_block_answers_status_from_telemetry(workdir):
    from strands_cad.dashboard import chat_agent
    snap = {"connected": True, "serial": "X", "gcode_state": "RUNNING", "progress": 42, "layer": 17, "total_layers": 80,
            "remaining_min": 95, "subtask_name": "phone_stand", "temps": {"nozzle": 219.5, "nozzle_target": 220, "bed": 60.0,
            "bed_target": 60, "chamber": 31}, "ams": [{"id": 0, "slots": [{"material": "PLA", "remaining_pct": 50}]}], "age_seconds": 1.2}
    block = chat_agent.live_block(snap)
    for needle in ("phone_stand", "RUNNING", "42%", "17/80", "95 min", "219.5°C", "60.0°C", "PLA 50%"):
        assert needle in block, needle
    assert "snapshot missing" in chat_agent.live_block({}) or "Printer now" in chat_agent.live_block({})


def test_gate_route_refuses_service_token_approval(workdir, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    monkeypatch.setenv("STRANDS_CAD_AUTH_STORE", str(tmp_path / "auth.json"))
    from strands_cad.dashboard import auth, gates
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "AUTH_STORE", tmp_path / "auth.json")
    from strands_cad.dashboard import server
    client = TestClient(server.app)
    gid = gates.open_gate("print", "cube", timeout_s=30)
    svc = auth.service_token("tiny")
    r = client.post(f"/api/gate/{gid}", json={"approve": True}, headers={"Authorization": f"Bearer {svc}"})
    assert r.status_code == 403 and "dashboard" in r.text
    assert gates.get(gid)["decision"] is None
    # a service token MAY deny (safe direction)
    r = client.post(f"/api/gate/{gid}", json={"approve": False}, headers={"Authorization": f"Bearer {svc}"})
    assert r.status_code == 200 and gates.get(gid)["decision"] is False and gates.get(gid)["by"] == "service:tiny"
    # a human session approves
    g2 = gates.open_gate("print", "cube2", timeout_s=30)
    tok = auth.issue_token("cred1", name="owner passkey")
    r = client.post(f"/api/gate/{g2}", json={"approve": True}, headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and gates.get(g2)["decision"] is True and gates.get(g2)["by"] == "owner passkey"
    # unauthenticated → 401 from the middleware
    g3 = gates.open_gate("print", "cube3", timeout_s=30)
    assert client.post(f"/api/gate/{g3}", json={"approve": True}).status_code == 401
