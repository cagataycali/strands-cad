"""🖥️ Dashboard control tools — start/stop the WebAuthn-gated printer dashboard.

These let an agent (or the MCP server) spin up the live printer dashboard on
demand, so a human can watch the chamber camera + drive the print from a
passkey-protected web page. The dashboard runs in a background thread; the
agent stays responsive.

Requires the [dashboard] extra:  pip install 'strands-cad[dashboard]'
"""
from __future__ import annotations

import os
import threading
import time
from typing import Optional

from strands import tool
from strands_cad._common import ok, err

_DASH: dict = {"thread": None, "port": None, "ip": None, "started": 0}


def _run_uvicorn_bg(host, port, ip, access_code, serial, tls):
    from strands_cad.dashboard.server import run
    run(host=host, port=port, ip=ip, access_code=access_code,
        serial=serial, tls=tls, block=True)


@tool
def dashboard_start(ip: str = "", access_code: str = "", serial: str = "",
                    port: int = 8099, host: str = "0.0.0.0",
                    tls: bool = False) -> dict:
    """Start the WebAuthn-gated printer dashboard (live camera + control).

    Serves a passkey-protected web page that streams the Bambu chamber camera
    and shows live telemetry (temps, progress, AMS), with pause/resume/stop.
    Runs in a background thread. First visit enrolls an admin passkey; after
    that the dashboard is sealed.

    Args:
        ip: Printer LAN IP (falls back to $BAMBU_IP).
        access_code: Printer access code (falls back to $BAMBU_ACCESS_CODE).
        serial: Printer serial (optional; auto-discovered over MQTT).
        port: HTTP(S) port (default 8099).
        host: Bind address (default 0.0.0.0 = all interfaces).
        tls: Serve HTTPS with an auto self-signed cert. REQUIRED for passkeys
            when accessed over a LAN IP / from phones (WebAuthn needs a secure
            context). For localhost-only, http is fine.

    Returns:
        {status, content, url, port, tls}
    """
    try:
        import uvicorn  # noqa
        import fastapi  # noqa
    except ImportError:
        return err("dashboard extra not installed. Run: pip install 'strands-cad[dashboard]'")

    if _DASH["thread"] and _DASH["thread"].is_alive():
        return ok(f"dashboard already running on port {_DASH['port']}",
                  url=f"http://localhost:{_DASH['port']}", port=_DASH["port"])

    ip = ip or os.getenv("BAMBU_IP", "")
    access_code = access_code or os.getenv("BAMBU_ACCESS_CODE", "")
    if not ip or not access_code:
        return err("printer not configured — pass ip= and access_code= "
                   "(or set BAMBU_IP / BAMBU_ACCESS_CODE).")

    t = threading.Thread(
        target=_run_uvicorn_bg,
        args=(host, port, ip, access_code, serial, tls),
        daemon=True, name=f"cad-dashboard-{port}")
    t.start()
    _DASH.update({"thread": t, "port": port, "ip": ip, "started": time.time()})
    time.sleep(1.5)  # let uvicorn bind

    from strands_cad.dashboard import tls as _tls
    scheme = "https" if tls else "http"
    urls = _tls.access_urls(port, tls)
    return ok(
        f"dashboard started on {scheme}://<host>:{port} for printer {ip}",
        url=urls[0], all_urls=urls, port=port, tls=tls,
        note=("Open the URL, enroll a passkey (Touch/Face ID), then watch the "
              "camera + drive the print. Use tls=True for LAN/phone access."))


@tool
def dashboard_status() -> dict:
    """Report whether the printer dashboard is running and on which port."""
    alive = bool(_DASH["thread"] and _DASH["thread"].is_alive())
    return ok(
        "dashboard running" if alive else "dashboard not running",
        running=alive, port=_DASH.get("port"), ip=_DASH.get("ip"),
        uptime_s=round(time.time() - _DASH["started"], 1) if alive else 0)


@tool
def dashboard_stop() -> dict:
    """Stop the printer dashboard.

    Note: the dashboard runs in a daemon thread with uvicorn; a clean in-process
    stop is limited. This marks it stopped and stops camera streams. For a hard
    stop, end the host process. (A future version will use a uvicorn Server
    handle for graceful shutdown.)
    """
    try:
        from strands_cad.dashboard import camera as _camera
        _camera.stop_all()
    except Exception:
        pass
    _DASH.update({"thread": None, "port": None, "ip": None, "started": 0})
    return ok("dashboard camera streams stopped; server thread will idle "
              "(end the process for a hard stop).")
