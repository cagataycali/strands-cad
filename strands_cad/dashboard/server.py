#!/usr/bin/env python3
"""
🖥️  strands-cad printer dashboard — WebAuthn-gated live control + camera.

A single FastAPI app that:
  • serves a glassmorphic single-page dashboard (./frontend/index.html)
  • gates EVERYTHING behind WebAuthn passkeys (auth.py) — a printer can start
    fires and move motors, so anonymous LAN access is a safety hazard
  • streams the Bambu chamber camera (RTSPS→MJPEG, camera.py) at
    /api/camera/stream (multipart) and /api/camera/snapshot (single JPEG)
  • polls printer telemetry over MQTT (printer.py) at /api/telemetry
  • issues pause/resume/stop control commands at /api/control

Run:
    strands-cad-dashboard                        # :8099
    BAMBU_IP=192.168.1.164 BAMBU_ACCESS_CODE=xxx strands-cad-dashboard
    STRANDS_CAD_TLS=true strands-cad-dashboard   # HTTPS (needed for passkeys
                                                 #  over LAN IP / real devices)

Or from an agent / MCP:
    dashboard_start(ip="192.168.1.164", access_code="xxxx")
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("strands_cad.dashboard")

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (JSONResponse, HTMLResponse, Response,
                               StreamingResponse)

from strands_cad.dashboard import auth as _auth
from strands_cad.dashboard import camera as _camera
from strands_cad.dashboard import printer as _printer

HERE = Path(__file__).resolve().parent
FRONTEND = HERE / "frontend"

# printer creds (env; overridable at dashboard_start)
BAMBU_IP = os.getenv("BAMBU_IP", "")
BAMBU_ACCESS = os.getenv("BAMBU_ACCESS_CODE", "")
BAMBU_SERIAL = os.getenv("BAMBU_SERIAL", "")

app = FastAPI(title="strands-cad dashboard")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])


def _creds() -> tuple[str, str, str]:
    return (os.getenv("BAMBU_IP", BAMBU_IP),
            os.getenv("BAMBU_ACCESS_CODE", BAMBU_ACCESS),
            os.getenv("BAMBU_SERIAL", BAMBU_SERIAL))


# ── auth routes ────────────────────────────────────────────────────────────
@app.get("/auth/status")
async def auth_status(request: Request):
    return {**_auth.status(request), "available": True}


@app.post("/auth/register/begin")
async def auth_reg_begin(request: Request):
    body = await request.json()
    if _auth.has_credentials():
        _auth.require_auth(request)  # adding another passkey needs a session
    return _auth.begin_registration(request, label=body.get("label", "admin passkey"),
                                    bootstrap=body.get("bootstrap", ""))


@app.post("/auth/register/finish")
async def auth_reg_finish(request: Request):
    body = await request.json()
    res = _auth.finish_registration(request, body.get("challenge_id", ""),
                                    body.get("credential", {}))
    resp = JSONResponse(res)
    resp.set_cookie("strands_cad_session", res["token"], httponly=True,
                    samesite="lax", max_age=_auth.TOKEN_TTL)
    return resp


@app.post("/auth/login/begin")
async def auth_login_begin(request: Request):
    return _auth.begin_authentication(request)


@app.post("/auth/login/finish")
async def auth_login_finish(request: Request):
    body = await request.json()
    res = _auth.finish_authentication(request, body.get("challenge_id", ""),
                                      body.get("credential", {}))
    resp = JSONResponse(res)
    resp.set_cookie("strands_cad_session", res["token"], httponly=True,
                    samesite="lax", max_age=_auth.TOKEN_TTL)
    return resp


@app.post("/auth/logout")
async def auth_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("strands_cad_session")
    return resp


@app.get("/auth/credentials")
async def auth_creds(request: Request):
    _auth.require_auth(request)
    return {"credentials": _auth.list_credentials()}


# ── global auth guard: seal /api/* ─────────────────────────────────────────
_PUBLIC_PREFIXES = ("/auth/",)
_PUBLIC_EXACT = {"/", "/favicon.ico", "/api/health"}


@app.middleware("http")
async def _auth_mw(request: Request, call_next):
    if not (_auth.AUTH_ENABLED):
        return await call_next(request)
    path = request.url.path
    if path in _PUBLIC_EXACT or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
        return await call_next(request)
    if path.startswith("/api/"):
        try:
            _auth.require_auth(request)
        except HTTPException as e:
            return JSONResponse({"error": e.detail}, status_code=e.status_code)
    return await call_next(request)


# ── health / telemetry / control ───────────────────────────────────────────
@app.get("/api/health")
async def health():
    return {"ok": True, "service": "strands-cad-dashboard"}


@app.get("/api/telemetry")
async def telemetry():
    ip, access, serial = _creds()
    if not ip or not access:
        return JSONResponse({"error": "printer not configured (BAMBU_IP/ACCESS_CODE)"},
                            status_code=503)
    p = _printer.get_printer(ip, access, serial)
    return p.snapshot()


@app.post("/api/control")
async def control(request: Request):
    body = await request.json()
    action = body.get("action", "")
    ip, access, serial = _creds()
    p = _printer.get_printer(ip, access, serial)
    ok = p.control(action)
    if not ok:
        raise HTTPException(400, f"control '{action}' failed (valid: pause/resume/stop)")
    return {"ok": True, "action": action}


# ── camera ─────────────────────────────────────────────────────────────────
@app.get("/api/camera/status")
async def camera_status():
    ip, access, _ = _creds()
    if not ip or not access:
        return JSONResponse({"running": False, "error": "not configured"})
    cam = _camera.get_camera(ip, access)
    return cam.status()


@app.get("/api/camera/snapshot")
async def camera_snapshot():
    ip, access, _ = _creds()
    if not ip or not access:
        return Response(_camera.placeholder_jpeg(), media_type="image/jpeg")
    cam = _camera.get_camera(ip, access)
    jpg = cam.latest() or _camera.placeholder_jpeg()
    return Response(jpg, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/camera/stream")
async def camera_stream():
    ip, access, _ = _creds()
    boundary = "cadframe"
    cam = _camera.get_camera(ip, access) if (ip and access) else None
    fps = cam.fps if cam else 10

    async def gen():
        idle = 0
        while True:
            jpg = cam.latest() if cam else None
            if jpg is None:
                jpg = _camera.placeholder_jpeg()
                idle += 1
                if idle > 100:
                    await asyncio.sleep(0.5)
            else:
                idle = 0
            yield (b"--" + boundary.encode() + b"\r\n"
                   b"Content-Type: image/jpeg\r\n"
                   b"Content-Length: " + str(len(jpg)).encode() + b"\r\n\r\n"
                   + jpg + b"\r\n")
            await asyncio.sleep(1.0 / max(fps, 1))

    return StreamingResponse(
        gen(), media_type=f"multipart/x-mixed-replace; boundary={boundary}",
        headers={"Cache-Control": "no-store, no-cache", "Pragma": "no-cache"})


# ── static frontend ────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index():
    idx = FRONTEND / "index.html"
    if idx.exists():
        return HTMLResponse(idx.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>strands-cad dashboard</h1><p>frontend missing</p>")


def create_app() -> FastAPI:
    return app


# ── programmatic control (used by the dashboard_* agent tools) ──────────────
_server_thread: Optional[threading.Thread] = None
_server_should_run = False
_server_port = 8099


def run(host: str = "0.0.0.0", port: int = 8099, ip: str = "", access_code: str = "",
        serial: str = "", tls: Optional[bool] = None, block: bool = True):
    """Start the dashboard (uvicorn). Sets printer creds into env for handlers."""
    global BAMBU_IP, BAMBU_ACCESS, BAMBU_SERIAL, _server_port
    import uvicorn
    from strands_cad.dashboard import tls as _tls

    if ip:
        os.environ["BAMBU_IP"] = ip
        BAMBU_IP = ip
    if access_code:
        os.environ["BAMBU_ACCESS_CODE"] = access_code
        BAMBU_ACCESS = access_code
    if serial:
        os.environ["BAMBU_SERIAL"] = serial
        BAMBU_SERIAL = serial
    if tls is not None:
        os.environ["STRANDS_CAD_TLS"] = "true" if tls else "false"
    _server_port = port

    cert = _tls.ensure_cert()
    use_tls = cert is not None
    urls = _tls.access_urls(port, use_tls)

    print("🖥️  strands-cad dashboard")
    print(f"    printer : {BAMBU_IP or '(unset)'}  serial={BAMBU_SERIAL or 'auto'}")
    print(f"    auth    : {'WebAuthn passkeys' if _auth.AUTH_ENABLED else 'DISABLED'}")
    print(f"    tls     : {'on' if use_tls else 'off (passkeys need HTTPS on LAN!)'}")
    for u in urls:
        print(f"    → {u}")
    if _auth.AUTH_ENABLED and not use_tls:
        print("    ⚠️  Open via http://localhost OR set STRANDS_CAD_TLS=true for LAN passkeys")

    kw = dict(host=host, port=port, log_level="warning")
    if use_tls:
        kw["ssl_certfile"], kw["ssl_keyfile"] = cert
    uvicorn.run(app, **kw)


def main():
    ap = argparse.ArgumentParser(description="strands-cad WebAuthn printer dashboard")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=int(os.getenv("STRANDS_CAD_DASH_PORT", "8099")))
    ap.add_argument("--ip", default=os.getenv("BAMBU_IP", ""), help="printer IP")
    ap.add_argument("--access-code", default=os.getenv("BAMBU_ACCESS_CODE", ""))
    ap.add_argument("--serial", default=os.getenv("BAMBU_SERIAL", ""))
    ap.add_argument("--tls", action="store_true", help="enable HTTPS (self-signed)")
    ap.add_argument("--no-auth", action="store_true", help="disable WebAuthn (DANGER)")
    args = ap.parse_args()
    if args.no_auth:
        os.environ["STRANDS_CAD_AUTH_ENABLED"] = "false"
        _auth.AUTH_ENABLED = False
    run(host=args.host, port=args.port, ip=args.ip, access_code=args.access_code,
        serial=args.serial, tls=True if args.tls else None)


if __name__ == "__main__":
    main()
