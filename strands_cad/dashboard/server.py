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

# Load .env (printer creds, telegram, thinker knobs) before anything reads env
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv()
except Exception:
    pass
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
from strands_cad.dashboard import config_store as _config
from strands_cad.dashboard import models as _models
from strands_cad.dashboard import jobs as _jobs
from strands_cad.dashboard import chat_agent as _chat
from strands_cad.dashboard import realtime as _realtime
from strands_cad.dashboard import plate as _plate
from strands_cad.dashboard import telegram as _telegram
from strands_cad.dashboard import thinker as _thinker

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


# ── config (live settings, neon-the-g1 parity) ─────────────────────────────
@app.get("/api/config")
async def config_get():
    return _config.redacted()


@app.post("/api/config")
async def config_post(request: Request):
    patch = await request.json()
    return _config.update(patch)


# ── models (3D assets for the viewer) ──────────────────────────────────────
@app.get("/api/models")
async def models_list():
    return {"workdir": _config.get("workdir"), "models": _models.list_models()}


@app.get("/api/model/meta/{name:path}")
async def model_meta(name: str):
    return _models.meta(name)


@app.get("/api/model/{name:path}")
async def model_file(name: str):
    data = _models.read_bytes(name)
    if data is None:
        raise HTTPException(404, f"model not found: {name}")
    return Response(data, media_type=_models.content_type(name),
                    headers={"Cache-Control": "no-store"})


# ── chat (in-dashboard CAD agent) ──────────────────────────────────────────
@app.get("/api/chat/status")
async def chat_status():
    return _chat.status()


@app.post("/api/chat")
async def chat_post(request: Request):
    body = await request.json()
    prompt = (body.get("prompt") or body.get("message") or "").strip()
    if not prompt:
        raise HTTPException(400, "empty prompt")
    return await asyncio.to_thread(_chat.ask, prompt)


@app.post("/api/chat/reset")
async def chat_reset(request: Request):
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    return _chat.reset(rebuild=bool(body.get("rebuild")))


# ── jobs (async slice / print) ─────────────────────────────────────────────
@app.post("/api/slice")
async def slice_start(request: Request):
    body = await request.json()
    name = body.get("name", "")
    if not name:
        raise HTTPException(400, "name required")
    jid = _jobs.start_slice(name, profile=body.get("profile", ""),
                            printer_model=body.get("printer_model", ""))
    return {"job_id": jid}


@app.post("/api/print")
async def print_start(request: Request):
    body = await request.json()
    name = body.get("name", "")
    if not name:
        raise HTTPException(400, "name required")
    jid = _jobs.start_print(name, use_ams=bool(body.get("use_ams", True)),
                            plate_index=int(body.get("plate_index", 1)))
    return {"job_id": jid}


@app.get("/api/job/{jid}")
async def job_status(jid: str):
    j = _jobs.get(jid)
    if not j:
        raise HTTPException(404, "job not found")
    return j


@app.get("/api/jobs")
async def jobs_recent():
    return {"jobs": _jobs.recent()}


# ── realtime voice (OpenAI ephemeral token) ────────────────────────────────
@app.post("/api/realtime/token")
async def realtime_token():
    res = await asyncio.to_thread(_realtime.mint_ephemeral)
    if not res.get("ok"):
        return JSONResponse({"error": res.get("error", "mint failed")}, status_code=503)
    return res


# ── plate (editable build-plate: positions, transforms, colors) ────────────
@app.get("/api/plate")
async def plate_get():
    return _plate.state()


@app.get("/api/filaments")
async def filaments_get():
    return {"filaments": _config.get("filaments", []),
            "nozzle_count": _config.get("nozzle_count", 1),
            "printer_model": _config.get("printer_model", "")}


@app.get("/api/filaments/live")
async def filaments_live():
    """Read ACTUAL loaded filaments from the printer's AMS + external spool.
    Only returns slots that have real filament (non-empty color/type), so the
    palette shows exactly what you can print with right now."""
    ip, access, serial = _creds()
    if not ip or not access:
        return {"filaments": [], "error": "printer not configured"}
    p = _printer.get_printer(ip, access, serial)
    snap = p.snapshot()
    out = []
    def norm(c):
        if not c: return None
        c = str(c).lstrip("#")
        if len(c) >= 6 and c[:6].upper() != "000000":  # drop empty/black-placeholder
            return "#" + c[:6].upper()
        # keep pure black only if type is set (real black filament)
        return "#" + c[:6].upper() if len(c) >= 6 else None
    # AMS units → nozzle 0 (AMS feeds the main extruder on X2D/H2D)
    for unit in snap.get("ams", []):
        for slot in unit.get("slots", []):
            typ = slot.get("material"); col = slot.get("color")
            if not typ:  # empty slot
                continue
            hexc = norm(col) or "#888888"
            out.append({
                "slot": f"AMS{unit.get('id')}-{slot.get('id')}",
                "name": f"{typ} ({hexc})",
                "type": typ, "color": hexc, "nozzle": 0,
                "remaining_pct": slot.get("remaining_pct"),
                "source": "ams",
            })
    # external spool (nozzle 1 on X2D) from live state vt_tray
    ext = getattr(p, "_state", {}).get("vt_tray") or []
    if isinstance(ext, list):
        for vt in ext:
            typ = vt.get("tray_type"); col = vt.get("tray_color")
            if typ and str(col).upper() not in ("", "00000000"):
                out.append({
                    "slot": f"EXT-{vt.get('id')}", "name": f"{typ} (ext)",
                    "type": typ, "color": "#" + str(col)[:6].upper(),
                    "nozzle": 1, "source": "external",
                })
    return {"filaments": out, "count": len(out),
            "nozzle_count": snap.get("nozzle_count", _config.get("nozzle_count", 2))}


@app.post("/api/filaments/sync")
async def filaments_sync():
    """Pull live AMS colors and SAVE them as the working filament palette."""
    live = await filaments_live()
    fils = live.get("filaments", [])
    if fils:
        _config.update({"filaments": fils})
    return {"ok": bool(fils), "filaments": fils, "count": len(fils)}


@app.post("/api/filaments")
async def filaments_set(request: Request):
    b = await request.json()
    patch = {}
    if "filaments" in b: patch["filaments"] = b["filaments"]
    if "nozzle_count" in b: patch["nozzle_count"] = int(b["nozzle_count"])
    _config.update(patch)
    return {"ok": True, "filaments": _config.get("filaments", [])}


@app.post("/api/plate/add")
async def plate_add(request: Request):
    b = await request.json()
    return _plate.add_item(b.get("source", ""), name=b.get("name", ""),
                           position=b.get("position"), color=b.get("color", ""))


@app.post("/api/plate/update")
async def plate_update(request: Request):
    b = await request.json()
    return _plate.update_item(b.get("id", ""), position=b.get("position"),
                              rotation=b.get("rotation"), scale=b.get("scale"),
                              color=b.get("color"), name=b.get("name"))


@app.post("/api/plate/recolor")
async def plate_recolor(request: Request):
    b = await request.json()
    return _plate.recolor(b.get("id", "all"), b.get("color", "#cccccc"))


@app.post("/api/plate/remove")
async def plate_remove(request: Request):
    b = await request.json()
    return _plate.remove_item(b.get("id", ""))


@app.post("/api/plate/clear")
async def plate_clear():
    return _plate.clear()


@app.post("/api/plate/arrange")
async def plate_arrange(request: Request):
    b = {}
    try: b = await request.json()
    except Exception: pass
    return _plate.auto_arrange(gap=float(b.get("gap", 10.0)))


@app.post("/api/plate/export")
async def plate_export():
    return _plate.export_3mf()


@app.post("/api/plate/print")
async def plate_print(request: Request):
    """Export the colored plate → slice → upload → START print (agent can call)."""
    b = {}
    try: b = await request.json()
    except Exception: pass
    exp = _plate.export_3mf()
    if not exp.get("ok"):
        return JSONResponse({"error": exp.get("error", "export failed")}, status_code=400)
    # slice then print the exported colored 3mf
    jid = _jobs.start_slice(exp["rel"])
    return {"job_id": jid, "exported": exp["rel"], "then": "print",
            "auto_print": bool(b.get("auto_print", True))}


# ── telegram bridge ─────────────────────────────────────────────────────────
@app.get("/api/telegram/status")
async def telegram_status():
    return _telegram.status()


@app.post("/api/telegram/notify")
async def telegram_notify(request: Request):
    b = await request.json()
    return _telegram.notify(b.get("text", ""))


@app.post("/api/telegram/snapshot")
async def telegram_snapshot(request: Request):
    b = {}
    try: b = await request.json()
    except Exception: pass
    return await asyncio.to_thread(_telegram.send_camera_snapshot, b.get("caption", "📷 chamber"))


@app.get("/api/telegram/detect")
async def telegram_detect():
    return _telegram.detect_chat_id()


@app.post("/api/telegram/poll")
async def telegram_poll(request: Request):
    b = {}
    try: b = await request.json()
    except Exception: pass
    return _telegram.start_polling() if b.get("start", True) else _telegram.stop_polling()


# ── thinker (background print watchdog) ─────────────────────────────────────
@app.get("/api/thinker/status")
async def thinker_status():
    return _thinker.status()


@app.post("/api/thinker/control")
async def thinker_control(request: Request):
    b = {}
    try: b = await request.json()
    except Exception: pass
    return _thinker.start() if b.get("start", True) else _thinker.stop()


# ── startup: auto-begin telegram polling + thinker loop ─────────────────────
@app.on_event("startup")
async def _autostart_bg():
    # seed env from config store (telegram token/chat etc.) then start bg loops
    try:
        _config.load()
    except Exception as e:
        log.warning(f"config load at startup failed: {e}")
    # telegram command poll loop (accepts /status /snapshot /ask ... from operator)
    try:
        r = _telegram.start_polling()
        log.info(f"📱 telegram polling: {r}")
    except Exception as e:
        log.warning(f"telegram autostart failed: {e}")
    # 🧠 slow-thinker background loop
    try:
        r = _thinker.start()
        log.info(f"🧠 thinker autostart: {r}")
    except Exception as e:
        log.warning(f"thinker autostart failed: {e}")


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
