"""Dashboard package tests — import safety, tool presence, RTSP framing logic.

These run WITHOUT a printer (no network). Live camera/telemetry are exercised
separately against real hardware. Here we assert the modules import, the tools
are registered, the FastAPI app builds with the auth guard, and the H.264
NAL/FU-A reassembly logic is correct on synthetic RTP frames.
"""
import struct
import pytest


def test_dashboard_tools_present():
    import strands_cad
    for name in ("dashboard_start", "dashboard_stop", "dashboard_status"):
        assert hasattr(strands_cad, name), f"missing tool {name}"


def test_dashboard_modules_import():
    # needs [dashboard] extra; skip cleanly if not installed
    fastapi = pytest.importorskip("fastapi")
    from strands_cad.dashboard import auth, tls, camera, printer, server
    app = server.create_app()
    assert app.title == "strands-cad dashboard"


def test_placeholder_jpeg_valid():
    pytest.importorskip("fastapi")
    from strands_cad.dashboard.camera import placeholder_jpeg
    jpg = placeholder_jpeg()
    assert jpg[:2] == b"\xff\xd8" and jpg[-2:] == b"\xff\xd9", "placeholder must be a valid JPEG"


def test_auth_gate_defaults_enabled():
    pytest.importorskip("webauthn")
    from strands_cad.dashboard import auth
    # status() with no request should report a shape we rely on in the frontend
    s = auth.status()
    assert set(("enabled", "setup_required", "credentials")).issubset(s.keys())


def test_rtsp_fua_reassembly():
    """FU-A fragmentation reassembly must rebuild a NAL from start/mid/end pieces."""
    pytest.importorskip("fastapi")
    from strands_cad.dashboard.camera import _RTSPSClient

    c = _RTSPSClient("0.0.0.0", "x")

    # craft two interleaved RTP frames carrying one FU-A NAL (type 5 = IDR)
    def rtp(payload: bytes) -> bytes:
        hdr = b"\x80\xe0\x00\x01" + b"\x00" * 8  # 12-byte RTP header
        body = hdr + payload
        return b"\x24\x00" + struct.pack(">H", len(body)) + body

    # FU indicator: F|NRI from original (0x60) | type 28; FU header: S/E/type
    fu_ind = 0x60 | 28
    start = bytes([fu_ind, 0x80 | 5]) + b"AAAA"   # start bit + type 5
    end = bytes([fu_ind, 0x40 | 5]) + b"BBBB"     # end bit
    c._buf = rtp(start) + rtp(end)

    # feed a closed socket sentinel so iter_nals stops after buffer drains
    class _Dead:
        def recv(self, n): return b""
    c._sock = _Dead()

    nals = list(c.iter_nals())
    assert len(nals) == 1
    # Annex-B start code + reconstructed NAL header (0x60|5=0x65) + payload
    assert nals[0] == b"\x00\x00\x00\x01" + bytes([0x65]) + b"AAAABBBB"


# ── new cockpit modules (config / models / jobs / chat / realtime / routes) ──
def test_config_store_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("STRANDS_CAD_CONFIG_STORE", str(tmp_path / "cfg.json"))
    import importlib
    from strands_cad.dashboard import config_store
    importlib.reload(config_store)
    r = config_store.update({"ip": "1.2.3.4", "access_code": "secret", "slice_profile": "PETG_0_20"})
    assert r["ip"] == "1.2.3.4"
    assert r["access_code"] == "" and r["access_code_set"] is True  # redacted but present
    assert r["slice_profile"] == "PETG_0_20"
    # blank secret must NOT clobber existing
    r2 = config_store.update({"access_code": ""})
    assert r2["access_code_set"] is True


def test_models_sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("STRANDS_CAD_CONFIG_STORE", str(tmp_path / "cfg.json"))
    wd = tmp_path / "wd"; wd.mkdir()
    (wd / "a.stl").write_bytes(b"solid x\nendsolid x\n")
    import importlib
    from strands_cad.dashboard import config_store, models
    importlib.reload(config_store)
    config_store.update({"workdir": str(wd)})
    importlib.reload(models)
    names = [m["name"] for m in models.list_models()]
    assert "a.stl" in names
    assert models._safe("../etc/passwd") is None      # traversal blocked
    assert models.read_bytes("a.stl") is not None


def test_new_routes_present():
    pytest.importorskip("fastapi")
    from strands_cad.dashboard import server
    paths = {r.path for r in server.create_app().routes}
    for p in ("/api/config", "/api/models", "/api/model/{name:path}",
              "/api/chat", "/api/chat/status", "/api/slice", "/api/print",
              "/api/job/{jid}", "/api/realtime/token"):
        assert p in paths, f"missing route {p}"


def test_realtime_tool_schema():
    pytest.importorskip("fastapi")
    from strands_cad.dashboard import realtime
    names = {t["name"] for t in realtime.voice_tools()}
    assert {"load_model", "recolor_part", "slice_model", "print_model",
            "printer_status", "control_print", "ask_cad_agent"}.issubset(names)


def test_jobs_registry():
    pytest.importorskip("fastapi")
    from strands_cad.dashboard import jobs
    assert isinstance(jobs.recent(), list)
    assert jobs.get("nonexistent") is None


# ── plate model (per-item colors, transforms, colored 3MF export) ────────────
def test_plate_lifecycle(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    monkeypatch.setenv("STRANDS_CAD_CONFIG_STORE", str(tmp_path / "cfg.json"))
    wd = tmp_path / "wd"; wd.mkdir()
    # copy a real STL fixture from examples
    import shutil, pathlib
    ex = pathlib.Path(__file__).parent.parent / "examples" / "props" / "t_block.stl"
    if not ex.exists():
        pytest.skip("no STL fixture")
    shutil.copy(ex, wd / "t_block.stl")
    import importlib
    from strands_cad.dashboard import config_store, plate, models
    importlib.reload(config_store); config_store.update({"workdir": str(wd)})
    importlib.reload(models); importlib.reload(plate)
    plate.clear()
    a = plate.add_item("t_block.stl", color="#ff0000")
    assert a["color"] == "#ff0000"
    b = plate.add_item("t_block.stl")
    assert b["color"] != a["color"]  # auto-assigned distinct
    plate.recolor(b["id"], "#00ff00")
    assert next(i for i in plate.state()["items"] if i["id"] == b["id"])["color"] == "#00ff00"
    plate.auto_arrange()
    exp = plate.export_3mf()
    assert exp["ok"] and exp["objects"] == 2 and len(exp["colors"]) == 2
    # exported 3mf must be valid xml with 2 colors
    import zipfile, xml.etree.ElementTree as ET
    xmlb = zipfile.ZipFile(exp["path"]).read("3D/3dmodel.model")
    ET.fromstring(xmlb)  # raises if malformed
    plate.remove_item(a["id"])
    assert len(plate.state()["items"]) == 1


def test_plate_recolor_all():
    pytest.importorskip("fastapi")
    from strands_cad.dashboard import plate
    r = plate.recolor("all", "#123456")
    assert r["ok"]


def test_telegram_status_shape():
    pytest.importorskip("fastapi")
    from strands_cad.dashboard import telegram
    s = telegram.status()
    assert set(("configured", "chat_id", "polling")).issubset(s.keys())


def test_slicer_finders_exist():
    from strands_cad.tools.slice import _find_bambu_cli, _find_prusa_cli, _slice_with_prusa
    # just assert callable / no crash
    _find_bambu_cli(); _find_prusa_cli()


def test_plate_telegram_routes_present():
    pytest.importorskip("fastapi")
    from strands_cad.dashboard import server
    paths = {r.path for r in server.create_app().routes}
    for p in ("/api/plate", "/api/plate/add", "/api/plate/recolor",
              "/api/plate/export", "/api/plate/print",
              "/api/telegram/notify", "/api/telegram/status"):
        assert p in paths, f"missing {p}"


# ── H2D config + filaments + sdcard-aware upload ─────────────────────────────
def test_h2d_config_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("STRANDS_CAD_CONFIG_STORE", str(tmp_path / "cfg.json"))
    monkeypatch.delenv("STRANDS_CAD_PRINTER_MODEL", raising=False)
    monkeypatch.delenv("STRANDS_CAD_FILAMENTS", raising=False)
    import importlib
    from strands_cad.dashboard import config_store
    importlib.reload(config_store)
    d = config_store._defaults()
    assert d["printer_model"] == "Bambu Lab X2D"
    assert d["nozzle_count"] == 2
    assert len(d["filaments"]) == 5
    # nozzle 1 has the PETG
    petg = [f for f in d["filaments"] if f["type"] == "PETG"]
    assert petg and petg[0]["nozzle"] == 1


def test_h2d_bed_and_palette(monkeypatch, tmp_path):
    monkeypatch.setenv("STRANDS_CAD_CONFIG_STORE", str(tmp_path / "cfg.json"))
    import importlib
    from strands_cad.dashboard import config_store, plate
    importlib.reload(config_store); config_store.update({"printer_model": "Bambu Lab X2D"})
    importlib.reload(plate)
    st = plate.state()
    assert st["bed"] == [256, 256, 260]   # X2D build volume
    assert len(st["palette"]) == 5


def test_ftps_session_reuse_present():
    # The 553 fix: ntransfercmd override must exist in bambu_upload
    import inspect
    from strands_cad.tools import bambu
    src = inspect.getsource(bambu.bambu_upload)
    assert "ntransfercmd" in src
    assert "session=" in src
    assert "sdcard" in src  # pre-flight check


def test_filaments_routes_present():
    pytest.importorskip("fastapi")
    from strands_cad.dashboard import server
    paths = {r.path for r in server.create_app().routes}
    assert "/api/filaments" in paths
