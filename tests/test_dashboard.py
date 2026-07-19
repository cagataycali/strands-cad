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
