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
                               StreamingResponse, FileResponse)
from fastapi.staticfiles import StaticFiles

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
from strands_cad.dashboard import gates as _gates
from strands_cad.dashboard import pipeline as _pipeline

HERE = Path(__file__).resolve().parent
FRONTEND = HERE / "frontend"
DIST = FRONTEND / "dist"          # the Vite build (committed; see frontend/README.md)

# printer creds (env; overridable at dashboard_start)
BAMBU_IP = os.getenv("BAMBU_IP", "")
BAMBU_ACCESS = os.getenv("BAMBU_ACCESS_CODE", "")
BAMBU_SERIAL = os.getenv("BAMBU_SERIAL", "")

# /docs, /redoc and /openapi.json are OFF by default: they're unauthenticated in
# FastAPI and hand an attacker a map of every print/control/shell route. Set
# STRANDS_CAD_DASH_DOCS=1 to expose them (they still sit behind the auth guard
# below, which covers them explicitly).
_DOCS = os.getenv("STRANDS_CAD_DASH_DOCS", "").lower() in ("1", "true", "yes")
app = FastAPI(title="strands-cad dashboard",
              docs_url="/docs" if _DOCS else None,
              redoc_url="/redoc" if _DOCS else None,
              openapi_url="/openapi.json" if _DOCS else None)

# CORS: `allow_origins=["*"]` with credentials meant any web page you visited
# could drive your printer using your session cookie. Same-origin needs no CORS
# at all, so default to an empty allowlist and let deployments opt in via
# STRANDS_CAD_DASH_CORS (comma-separated origins).
_CORS = [o.strip() for o in os.getenv("STRANDS_CAD_DASH_CORS", "").split(",") if o.strip()]
if _CORS:
    app.add_middleware(CORSMiddleware, allow_origins=_CORS, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])


def _session_response(request: Request, res: dict) -> JSONResponse:
    """Return an auth result and pin the session cookie to this connection.

    `secure` is set whenever the request arrived over TLS so the cookie can't be
    replayed over a plaintext downgrade; plain-http localhost dev still works.
    """
    resp = JSONResponse(res)
    resp.set_cookie("strands_cad_session", res["token"], httponly=True,
                    samesite="lax", secure=request.url.scheme == "https",
                    max_age=_auth.TOKEN_TTL)
    return resp


def _creds() -> tuple[str, str, str]:
    return (os.getenv("BAMBU_IP", BAMBU_IP),
            os.getenv("BAMBU_ACCESS_CODE", BAMBU_ACCESS),
            os.getenv("BAMBU_SERIAL", BAMBU_SERIAL))


# ── auth routes ────────────────────────────────────────────────────────────
@app.exception_handler(_auth.AuthStoreCorrupt)
async def _store_corrupt(request: Request, exc: _auth.AuthStoreCorrupt):
    """Unreadable auth store → 503, never a reset. See auth.AuthStoreCorrupt."""
    log.error(f"auth store unreadable: {exc}")
    return JSONResponse({"error": str(exc)}, status_code=503)


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
    return _session_response(request, res)


@app.post("/auth/login/begin")
async def auth_login_begin(request: Request):
    return _auth.begin_authentication(request)


@app.post("/auth/login/finish")
async def auth_login_finish(request: Request):
    body = await request.json()
    res = _auth.finish_authentication(request, body.get("challenge_id", ""),
                                      body.get("credential", {}))
    return _session_response(request, res)


@app.post("/auth/logout")
async def auth_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("strands_cad_session")
    return resp


@app.get("/auth/credentials")
async def auth_creds(request: Request):
    _auth.require_auth(request)
    return {"credentials": _auth.list_credentials()}


@app.post("/auth/ticket")
async def auth_ticket(request: Request):
    """Mint a short-lived, camera-only token for the MJPEG <img> tag.

    An <img src> can't carry an Authorization header, and putting the session
    JWT in the URL would leak full printer control into logs and history.
    """
    session = _auth.require_auth(request)
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    scope = body.get("scope", "camera")
    return {"ticket": _auth.issue_ticket(session, scope=scope),
            "scope": scope, "expires_in": _auth.TICKET_TTL}


# ── global auth guard: seal everything except the login surface ────────────
# /assets, /fonts, /icons are the SPA's hashed static files: no data, no secrets — the shell must load so the
# passkey gate can render. Everything under /api (except health) stays sealed.
_PUBLIC_PREFIXES = ("/auth/", "/assets/", "/fonts/", "/icons/")
_PUBLIC_EXACT = {"/", "/favicon.ico", "/api/health", "/api/version", "/index.html"}
# Routes a browser can only reach by URL (no custom headers) → scoped ticket.
_TICKET_ROUTES = {"/api/camera/stream": "camera"}


@app.middleware("http")
async def _auth_mw(request: Request, call_next):
    if not (_auth.AUTH_ENABLED):
        return await call_next(request)
    path = request.url.path
    if path in _PUBLIC_EXACT or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
        return await call_next(request)
    try:
        scope = _TICKET_ROUTES.get(path)
        if scope:
            _auth.require_ticket(request, scope=scope)
        else:
            # Guard EVERY non-public path, not just /api/* — /docs and any
            # future route are sealed by default rather than by remembering to
            # add a prefix here.
            _auth.require_auth(request)
    except HTTPException as e:
        return JSONResponse({"error": e.detail}, status_code=e.status_code)
    except _auth.AuthStoreCorrupt as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    return await call_next(request)


# ── health / telemetry / control ───────────────────────────────────────────
@app.get("/api/health")
async def health():
    return {"ok": True, "service": "strands-cad-dashboard"}


@app.get("/api/version")
async def version():
    from strands_cad import __version__
    return {"version": __version__, "service": "strands-cad-dashboard", "ui": "dist" if (DIST / "index.html").exists() else "legacy"}


@app.get("/api/telemetry")
async def telemetry():
    ip, access, serial = _creds()
    if not ip or not access:
        return JSONResponse({"error": "printer not configured (BAMBU_IP/ACCESS_CODE)"},
                            status_code=503)
    p = _printer.get_printer(ip, access, serial)
    snap = p.snapshot()
    # flat keys for tiny's endpoint panel (it draws top-level scalars); nested shape stays for the SPA
    t = snap.get("temps") or {}
    snap.update({"job": snap.get("subtask_name") or ("idle" if snap.get("connected") else None),
                 "state": snap.get("gcode_state"), "progress_pct": snap.get("progress"),
                 "nozzle_c": t.get("nozzle"), "bed_c": t.get("bed"), "chamber_c": t.get("chamber")})
    return snap


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


@app.post("/api/stop")
async def stop_print():
    """The tiny device contract's `stop`: stop the running print. Safe direction only — nothing here starts one."""
    ip, access, serial = _creds()
    p = _printer.get_printer(ip, access, serial)
    ok = p.control("stop")
    if not ok:
        raise HTTPException(503, "printer not connected — stop not sent")
    return {"ok": True, "action": "stop"}


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
    """One-shot turn: {result, reply, error, tools, gates, preview} — the shape tiny's endpoint proxy and
    Telegram expect (fomo's endpoint.py contract: result/tools/errors). A print request ends with a gate
    the operator answers on the dashboard; the reply says so and carries the preview URL."""
    body = await request.json()
    prompt = (body.get("prompt") or body.get("message") or "").strip()
    if not prompt:
        raise HTTPException(400, "empty prompt")
    res = await asyncio.to_thread(_chat.ask, prompt)
    res["errors"] = [res["error"]] if res.get("error") else []
    return res


@app.post("/api/chat/stream")
async def chat_stream(request: Request):
    """The same turn as SSE: frames text{delta} · tool{id,name,input} · tool_result{id,status,text,image?} ·
    gate{id,kind,detail,preview,timeout_s | approve,answered} · done · error (expo's vocabulary)."""
    body = await request.json()
    prompt = (body.get("prompt") or body.get("message") or "").strip()
    if not prompt:
        raise HTTPException(400, "empty prompt")

    async def gen():
        import json as _json
        it = _chat.ask_stream(prompt)
        while True:
            frame = await asyncio.to_thread(next, it, None)
            if frame is None:
                break
            if frame.get("type") == "ping":
                yield ": keepalive\n\n"
                continue
            yield f"data: {_json.dumps(frame, default=str)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


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
    # NOT a direct start any more: the job opens an operator gate and prints only after approval.
    jid = _jobs.start_print(name, use_ams=bool(body.get("use_ams", True)),
                            plate_index=int(body.get("plate_index", 1)), requested_by="dashboard /api/print")
    return {"job_id": jid, "gated": True}


# ── prompt-to-print pipeline + operator gates ───────────────────────────────
@app.post("/api/pipeline")
async def pipeline_start(request: Request):
    """validate → preview → slice → GATE → print for a workdir model. Returns {job_id} at once; poll /api/job/{id}."""
    body = await request.json()
    model = (body.get("model") or body.get("name") or "").strip()
    if not model:
        raise HTTPException(400, "model required (a file in the workdir)")
    r = _pipeline.start(model=model, profile=body.get("profile", ""), title=body.get("title", ""),
                        requested_by=body.get("requested_by") or "dashboard")
    if r.get("error"):
        raise HTTPException(400, r["error"])
    return r


@app.get("/api/gates")
async def gates_list():
    return {"gates": _gates.recent(), "pending": _gates.pending()}


@app.get("/api/gate/{gid}")
async def gate_get(gid: str):
    g = _gates.get(gid)
    if not g:
        raise HTTPException(404, "gate not found")
    return g


@app.post("/api/gate/{gid}")
async def gate_answer(request: Request, gid: str):
    """The operator's decision. Only a human session may approve: service tokens (tiny's device path) are
    refused unless STRANDS_CAD_GATE_FROM_DEVICE=1 — the dashboard is the default and only approver."""
    body = await request.json()
    approve = bool(body.get("approve"))
    who = "dashboard"
    if _auth.AUTH_ENABLED:
        try:
            claims = _auth.require_auth(request)
            sub = str(claims.get("sub", ""))
            if sub.startswith("service:"):
                if approve and os.getenv("STRANDS_CAD_GATE_FROM_DEVICE", "").lower() not in ("1", "true", "yes"):
                    raise HTTPException(403, "a service token cannot approve a print — approve on the dashboard")
                who = sub
            else:
                who = claims.get("name") or "passkey"
        except HTTPException:
            raise
    r = _gates.answer(gid, approve, by=who)
    if not r.get("ok"):
        raise HTTPException(404, r.get("error", "gate not found"))
    return r


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
    """Export the colored plate → the gated pipeline (slice → GATE → print)."""
    exp = _plate.export_3mf()
    if not exp.get("ok"):
        raise HTTPException(400, exp.get("error", "export failed"))
    r = _pipeline.start(model=exp["rel"], title="colored plate", requested_by="dashboard plate")
    if r.get("error"):
        raise HTTPException(400, r["error"])
    return {**r, "exported": exp, "gated": True}


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
    # In split-service mode (docker/systemd), telegram + thinker run as their
    # OWN processes; set STRANDS_CAD_SPLIT_LOOPS=1 so the dashboard doesn't
    # also start them (which would double-poll Telegram getUpdates).
    _split = os.getenv("STRANDS_CAD_SPLIT_LOOPS", "").lower() in ("1", "true", "yes")
    # start the MQTT poller now so the chat agent's LIVE COCKPIT STATE (and tiny's device chat) has a
    # snapshot from the first turn — before, it only existed after someone hit /api/telemetry
    try:
        ip, access, serial = _creds()
        if ip and access:
            _printer.get_printer(ip, access, serial)
    except Exception as e:
        log.warning(f"printer autostart failed: {e}")
    # telegram command poll loop (accepts /status /snapshot /ask ... from operator)
    if _split or os.getenv("STRANDS_CAD_NO_TELEGRAM", "").lower() in ("1", "true", "yes"):
        log.info("📱 telegram autostart skipped (split-loops / disabled)")
    else:
        try:
            r = _telegram.start_polling()
            log.info(f"📱 telegram polling: {r}")
        except Exception as e:
            log.warning(f"telegram autostart failed: {e}")
    # 🧠 slow-thinker background loop
    if _split or os.getenv("STRANDS_CAD_NO_THINKER", "").lower() in ("1", "true", "yes"):
        log.info("🧠 thinker autostart skipped (split-loops / disabled)")
    else:
        try:
            r = _thinker.start()
            log.info(f"🧠 thinker autostart: {r}")
        except Exception as e:
            log.warning(f"thinker autostart failed: {e}")


@app.on_event("shutdown")
async def _deny_open_gates():
    n = _gates.deny_all(by="shutdown")
    if n:
        log.info(f"🔒 denied {n} open gate(s) on shutdown")


# ── static frontend ────────────────────────────────────────────────────────
# The cockpit is a Vite + React SPA (frontend/src → frontend/dist, committed). Hashed assets are mounted
# publicly (they carry no data); the HTML shell is public too so the passkey gate can render — every
# /api route behind it is still sealed by the middleware. Without a dist (source checkout, no build)
# the page points at the build command.
if (DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")
for _static in ("fonts", "icons"):
    if (DIST / _static).is_dir():
        app.mount(f"/{_static}", StaticFiles(directory=DIST / _static), name=_static)


@app.get("/", response_class=HTMLResponse)
async def index():
    idx = DIST / "index.html"
    if idx.exists():
        return FileResponse(idx, media_type="text/html", headers={"Cache-Control": "no-cache"})
    return HTMLResponse("<h1>strands-cad dashboard</h1><p>frontend not built: "
                        "<code>cd strands_cad/dashboard/frontend && npm ci && npm run build</code></p>")


@app.get("/favicon.ico")
async def favicon():
    ico = DIST / "icons" / "favicon.png"
    if ico.exists():
        return FileResponse(ico, media_type="image/png")
    raise HTTPException(404)


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
