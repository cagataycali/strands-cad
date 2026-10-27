"""tiny.technology endpoint-device contract (tiny.body.json): chat / telemetry / snapshot / stop shapes,
as the worker's endpoint proxy and the iOS EndpointPanel read them (fomo's endpoint.py lineage)."""
import json
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("STRANDS_CAD_AUTH_STORE", str(tmp_path / "auth.json"))
    monkeypatch.setenv("BAMBU_IP", "10.0.0.9"); monkeypatch.setenv("BAMBU_ACCESS_CODE", "x")
    from strands_cad.dashboard import auth, server, printer, chat_agent
    monkeypatch.setattr(auth, "AUTH_ENABLED", False)

    class FakePrinter:
        _connected = True
        def snapshot(self):
            return {"connected": True, "serial": "S", "gcode_state": "RUNNING", "progress": 12, "layer": 3, "total_layers": 40,
                    "remaining_min": 77, "subtask_name": "bracket", "temps": {"nozzle": 220.0, "nozzle_target": 220, "bed": 60.0, "bed_target": 60, "chamber": 30},
                    "fan": {}, "ams": [], "age_seconds": 0.5}
        def control(self, action):
            self.last = action; return action in ("pause", "resume", "stop")
    fake = FakePrinter()
    monkeypatch.setattr(printer, "get_printer", lambda ip, a, s="": fake)
    monkeypatch.setattr(chat_agent, "ask", lambda prompt: {"result": "the printer is printing bracket at 12 %", "reply": "the printer is printing bracket at 12 %",
                                                           "error": None, "tools": [], "gates": [], "preview": None})
    from fastapi.testclient import TestClient
    return TestClient(server.app), fake


def test_body_json_declares_the_contract():
    body = json.loads((Path(__file__).parent.parent / "tiny.body.json").read_text())
    assert body["platform"] == "bambu-x2d"
    assert {"chat", "telemetry", "camera", "print", "cad", "stop"} <= set(body["capabilities"])
    assert body["endpoint"]["url"]["default"].startswith("https://")
    assert any(s["kind"] == "exec" and "strands_cad.dashboard.auth" in " ".join(s["argv"]) for s in body["endpoint"]["secret"])


def test_telemetry_has_flat_keys_for_the_device_panel(client):
    c, _ = client
    t = c.get("/api/telemetry").json()
    assert t["job"] == "bracket" and t["state"] == "RUNNING" and t["progress_pct"] == 12
    assert t["nozzle_c"] == 220.0 and t["bed_c"] == 60.0 and t["chamber_c"] == 30
    assert t["temps"]["nozzle"] == 220.0  # nested shape kept for the SPA


def test_chat_reply_has_result_and_errors(client):
    c, _ = client
    r = c.post("/api/chat", json={"prompt": "what is the printer doing?"}).json()
    assert r["result"].startswith("the printer is printing bracket") and r["reply"] == r["result"]
    assert r["errors"] == [] and r["error"] is None and isinstance(r["tools"], list)


def test_stop_route_only_stops(client):
    c, fake = client
    assert c.post("/api/stop").json() == {"ok": True, "action": "stop"} and fake.last == "stop"
    # the contract has no start: /api/print is gated (see test_gates_pipeline) and /api/stop never takes a file
    assert c.post("/api/stop", json={"name": "x.gcode"}).json()["action"] == "stop"
