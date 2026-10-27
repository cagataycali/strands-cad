#!/usr/bin/env python3
"""
🧠 In-dashboard printer agent — design → validate → preview → slice → GATE → print.

One shared strands Agent (built lazily in a background thread, serialized by a lock —
shared history), with live printer telemetry injected per turn from the dashboard's
cached MQTT snapshot (never a blocking call in the request path).

What changed from the first cockpit agent:
  • NO un-gated print path. `bambu_send`, `bambu_upload`, `bambu_control`, the raw
    `/api/print` and the old `plate_slice_and_print(confirm=True)` are not in the
    toolbelt. The only way to print is `print_pipeline(...)`, which validates, previews,
    slices and then opens an OPERATOR GATE on the dashboard; the model cannot approve it.
  • `shell` is gone (it could `curl` the printer or read .env).
  • Streaming: `ask_stream()` yields expo-style frames (text · tool · tool_result · gate ·
    done · error) for `POST /api/chat/stream`; `ask()` keeps the one-shot reply for
    tiny's device path and Telegram and returns `result` + `reply`.
  • "What is the printer doing?" is answered from the LIVE COCKPIT STATE block (job name,
    state, progress, temps) — pinned by a test with a telemetry fixture.
"""
from __future__ import annotations

import logging
import queue
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

log = logging.getLogger("strands_cad.dashboard.chat")

_AGENT = None
_LOCK = threading.Lock()          # serialize ask() (single agent, shared history)
_BUILD_LOCK = threading.Lock()    # guard one-time build
_ERROR: Optional[str] = None
_BUILDING = False
_static_prompt = ""

# tools that could move filament or the printer without the gate — never in the chat agent
_EXCLUDED_TOOLS = {"bambu_send", "bambu_upload", "bambu_control", "bambu_connect",
                   "dashboard_start", "dashboard_stop", "preview_serve", "preview_stop",
                   "sim_view_live"}

_BASE_PROMPT = """You are the printer agent of printer.cagatay.my — the cockpit of a Bambu Lab X2D (dual nozzle, AMS, 325×320×325 mm).
You design parts, check them, slice them and queue them for printing. Be concise; speak in plain words, no JSON.

DESIGN: write the model with cq_render_stl (CadQuery script → STL) or scad_render_stl (OpenSCAD, if installed) or sdf_render_stl;
always write into the WORKDIR (see LIVE COCKPIT STATE) with a short descriptive filename, e.g. calibration_cube_20mm.stl.
Tell the user the filename — the dashboard's Models drawer shows it in 3D.

PRINT: the ONLY way to print is print_pipeline(model=<filename in the workdir>, title=...). It validates the mesh against the
X2D bed, renders a preview, slices with the configured profile and then OPENS AN OPERATOR GATE on the dashboard.
You cannot approve a print. After calling it, say exactly what it returned: the title, the preview, the estimate, and that it is
WAITING FOR APPROVAL on printer.cagatay.my. Never claim a print started unless job_status says state=done.
If the user asks to print and no model exists yet, design it first, then call print_pipeline — in one turn, no extra questions
when the request is specific enough (a "20 mm calibration cube" is specific enough).

STATUS: when asked what the printer is doing / how the print is going / temperatures, answer FROM the LIVE COCKPIT STATE block
below (job name, state, progress %, layer, remaining time, nozzle/bed temperatures, AMS). Do not call tools for that unless the
block says the snapshot is missing. If the job name is 'idle' say the printer is idle.

NEVER: send gcode yourself, pause/stop/resume the printer (the operator has buttons for that), or read secrets.
"""


def _build():
    global _AGENT, _ERROR, _BUILDING
    if _AGENT is not None or _ERROR is not None:
        return
    with _BUILD_LOCK:
        if _AGENT is not None or _ERROR is not None or _BUILDING:
            return
        _BUILDING = True
    try:
        from strands_cad.dashboard import config_store
        config_store.load()  # seed env (model creds etc.)

        from strands import Agent, tool
        from strands_cad import ALL_TOOLS
        from strands_cad.dashboard import plate as _plate, jobs as _jobs, pipeline as _pipeline
        from strands_cad.dashboard import telegram as _tg

        base_tools = [t for t in ALL_TOOLS if getattr(t, "tool_name", getattr(t, "__name__", "")) not in _EXCLUDED_TOOLS]

        # ── the gated print path ────────────────────────────────────────────
        @tool
        def print_pipeline(model: str, title: str = "", profile: str = "", designed_by: str = "") -> dict:
            """Queue a model for printing: validate (X2D bed, overhangs) → preview PNG → slice (Orca) → OPERATOR GATE → print.
            The print only starts after the operator approves the gate on the dashboard; this call returns immediately.
            Args: model: STL/3MF filename inside the workdir. title: short human title shown on the gate card.
            profile: slice profile (default: the configured one, e.g. PLA_0_20). designed_by: one line on how it was designed."""
            r = _pipeline.start(model=model, profile=profile, title=title, designed_by=designed_by, requested_by="chat agent")
            if r.get("error"):
                return {"status": "error", "content": [{"text": r["error"]}]}
            jid = r["job_id"]
            # give the fast steps a moment so the reply can carry the preview + estimate
            deadline = time.time() + 75
            while time.time() < deadline:
                j = _jobs.get(jid) or {}
                if j.get("state") in ("awaiting_approval", "error", "denied", "done"):
                    break
                time.sleep(0.5)
            j = _jobs.get(jid) or {}
            text = _pipeline.describe(jid)
            if j.get("state") == "running":
                text = f"'{j.get('title')}' is being prepared (step {j.get('step')}); the gate will appear on the dashboard when slicing is done."
            return {"status": "success" if j.get("state") not in ("error",) else "error",
                    "content": [{"text": text}], "job_id": jid, "state": j.get("state"), "step": j.get("step"),
                    "gate": j.get("gate"), "preview": j.get("preview"), "title": j.get("title"),
                    "steps": j.get("steps"), "existing": r.get("existing", False)}

        @tool
        def job_status(job_id: str = "") -> dict:
            """Where a pipeline/print job stands (state, step, gate, preview, error). Empty id → the most recent jobs."""
            if job_id:
                j = _jobs.get(job_id)
                if not j:
                    return {"status": "error", "content": [{"text": f"job {job_id} not found"}]}
                return {"status": "success", "content": [{"text": _pipeline.describe(job_id)}], "job": j}
            js = _jobs.recent(8)
            lines = [f"{j['id']} · {j['kind']} · {j['state']} · {j.get('title') or j.get('input') or j.get('gcode') or ''}" for j in js]
            return {"status": "success", "content": [{"text": "\n".join(lines) or "no jobs yet"}], "jobs": js}

        @tool
        def printer_status() -> dict:
            """The live printer snapshot (job, state, progress, layers, remaining minutes, temps, AMS) from the cockpit's MQTT cache."""
            s = _snapshot()
            if not s:
                return {"status": "error", "content": [{"text": "no telemetry yet — the printer connection is not up"}]}
            return {"status": "success", "content": [{"text": _snapshot_words(s)}], "telemetry": s}

        @tool
        def list_models() -> dict:
            """The printable files in the workdir (STL/3MF/G-code) with sizes."""
            from strands_cad.dashboard import models as _models
            ms = _models.list_models()
            return {"status": "success", "content": [{"text": "\n".join(f"{m['name']} ({m['size']} B)" for m in ms) or "workdir is empty"}],
                    "workdir": str(_models._workdir()), "models": ms}

        # ── plate tools (colour / arrange); printing a plate goes through the pipeline ──
        @tool
        def plate_add(source: str, color: str = "") -> dict:
            """Add a model (STL/3MF filename in the workdir) onto the build plate. color: optional #rrggbb."""
            return _plate.add_item(source, color=color)

        @tool
        def plate_recolor(item: str, color: str) -> dict:
            """Recolor a plate item by id/name (or 'all') to a #rrggbb color."""
            return _plate.recolor(item, color)

        @tool
        def plate_arrange() -> dict:
            """Auto-arrange all plate items in a non-overlapping grid on the bed."""
            return _plate.auto_arrange()

        @tool
        def plate_list() -> dict:
            """List everything currently on the build plate with colors/positions."""
            return {"status": "success", "content": [{"text": "plate"}], **_plate.state()}

        @tool
        def plate_clear() -> dict:
            """Remove all items from the build plate."""
            return _plate.clear()

        @tool
        def plate_print(title: str = "colored plate") -> dict:
            """Export the colored plate to a 3MF and queue it through the gated print pipeline (validate → slice → GATE → print)."""
            exp = _plate.export_3mf()
            if not exp.get("ok"):
                return {"status": "error", "content": [{"text": exp.get("error", "export failed")}]}
            return print_pipeline(model=exp["rel"], title=title, designed_by="colored plate export")

        @tool
        def telegram_notify(text: str) -> dict:
            """Send a Telegram message to the operator's chat."""
            return _tg.notify(text)

        @tool
        def telegram_send_snapshot(caption: str = "chamber") -> dict:
            """Grab a chamber-camera frame and send it over Telegram."""
            return _tg.send_camera_snapshot(caption)

        _dash_tools = [print_pipeline, job_status, printer_status, list_models,
                       plate_add, plate_recolor, plate_arrange, plate_list, plate_clear, plate_print,
                       telegram_notify, telegram_send_snapshot]

        model_id = config_store.get("model") or None
        kwargs: Dict[str, Any] = {"tools": base_tools + _dash_tools, "system_prompt": _BASE_PROMPT,
                                  "callback_handler": None}
        if model_id:
            kwargs["model"] = model_id

        a = Agent(**kwargs)
        global _static_prompt
        _static_prompt = _BASE_PROMPT
        _AGENT = a
        log.info(f"🧠 printer agent built ({len(a.tool_names)} tools, model={model_id or 'default'}, "
                 f"excluded={sorted(_EXCLUDED_TOOLS & set(getattr(t, 'tool_name', '') for t in ALL_TOOLS))})")
    except Exception as e:
        _ERROR = f"agent build failed: {e}"
        log.warning(_ERROR, exc_info=True)
    finally:
        _BUILDING = False


# ── live state ──────────────────────────────────────────────────────────────
def _snapshot() -> Optional[Dict[str, Any]]:
    """The cached printer snapshot, starting the MQTT poller from config if nobody did yet."""
    try:
        from strands_cad.dashboard import config_store, printer as _printer
        p = _printer._PRINTER
        if p is None:
            cfg = config_store.load()
            if cfg.get("ip") and cfg.get("access_code"):
                p = _printer.get_printer(cfg["ip"], cfg["access_code"], cfg.get("serial") or "")
        return p.snapshot() if p is not None else None
    except Exception:
        return None


def _snapshot_words(s: Dict[str, Any]) -> str:
    t = s.get("temps") or {}
    job = s.get("subtask_name") or "idle"
    state = s.get("gcode_state") or "unknown"
    ams = []
    for u in s.get("ams") or []:
        for sl in u.get("slots") or []:
            if sl.get("material"):
                ams.append(f"{sl['material']}{' ' + str(sl['remaining_pct']) + '%' if sl.get('remaining_pct') is not None else ''}")
    return (f"job: {job} · state: {state} · progress: {s.get('progress')}% · layer {s.get('layer')}/{s.get('total_layers')} · "
            f"remaining: {s.get('remaining_min')} min · nozzle {t.get('nozzle')}°C (target {t.get('nozzle_target')}) · "
            f"bed {t.get('bed')}°C (target {t.get('bed_target')}) · chamber {t.get('chamber')}°C · "
            f"AMS: {', '.join(ams) or 'none'} · connected: {s.get('connected')} · report age {s.get('age_seconds')} s")


def live_block(snapshot: Optional[Dict[str, Any]] = None) -> str:
    """Live printer + config block (pure: pass a snapshot to test it)."""
    try:
        from strands_cad.dashboard import config_store
        cfg = config_store.load()
        s = snapshot if snapshot is not None else _snapshot()
        block = [
            "\n\n## LIVE COCKPIT STATE (trust this; it is the printer's own MQTT report)",
            f"- WORKDIR (write every STL/PNG here): {cfg.get('workdir')}",
            f"- Slice profile: {cfg.get('slice_profile')} · printer model: {cfg.get('printer_model') or 'Bambu Lab X2D'}",
        ]
        if s:
            block.append("- Printer now: " + _snapshot_words(s))
        else:
            block.append("- Printer now: snapshot missing (MQTT not connected yet) — call printer_status if asked.")
        return "\n".join(block) + "\n"
    except Exception:
        return ""


_live_block = live_block  # backwards-compatible name


# ── streaming ────────────────────────────────────────────────────────────────
_PNG_RE = re.compile(r"([\w./-]+\.png)")


def _image_url(text: str) -> Optional[str]:
    """A PNG path inside the workdir mentioned by a tool result → its /api/model URL."""
    try:
        from strands_cad.dashboard import models as _models
        wd = _models._workdir()
        for m in _PNG_RE.findall(text or ""):
            p = Path(m)
            if not p.is_absolute():
                p = wd / p
            try:
                rel = p.resolve().relative_to(wd)
            except Exception:
                continue
            if p.exists():
                return f"/api/model/{rel}"
    except Exception:
        pass
    return None


def _frames_from_callback(q: "queue.Queue[dict]"):
    def cb(**kw):
        try:
            if kw.get("data"):
                q.put({"type": "text", "delta": kw["data"]})
            msg = kw.get("message")
            if msg and isinstance(msg, dict):
                for blk in msg.get("content") or []:
                    if "toolUse" in blk:
                        tu = blk["toolUse"]
                        q.put({"type": "tool", "id": tu.get("toolUseId"), "name": tu.get("name"), "input": tu.get("input")})
                    elif "toolResult" in blk:
                        tr = blk["toolResult"]
                        texts = [c.get("text", "") for c in tr.get("content") or [] if isinstance(c, dict) and "text" in c]
                        text = "\n".join(texts)
                        frame = {"type": "tool_result", "id": tr.get("toolUseId"), "status": tr.get("status"),
                                 "text": text[:600]}
                        img = _image_url(text)
                        if img:
                            frame["image"] = img
                        q.put(frame)
        except Exception as e:
            log.debug(f"callback frame: {e}")
    return cb


def ask_stream(prompt: str) -> Iterator[dict]:
    """Run one turn and yield expo-style frames as they happen (blocking generator)."""
    from strands_cad.dashboard import gates as _gates
    _build()
    if _AGENT is None:
        yield {"type": "error", "error": _ERROR or "agent still building — retry shortly"}
        return
    q: "queue.Queue[Any]" = queue.Queue()
    done = object()

    def run():
        try:
            with _LOCK:
                try:
                    _AGENT.system_prompt = _static_prompt + live_block()
                except Exception:
                    pass
                _AGENT.callback_handler = _frames_from_callback(q)
                _gates.add_listener(q.put)
                try:
                    _AGENT(prompt)
                finally:
                    _gates.remove_listener(q.put)
                    _AGENT.callback_handler = lambda **kw: None
            q.put({"type": "done"})
        except Exception as e:
            log.warning(f"chat turn failed: {e}", exc_info=True)
            q.put({"type": "error", "error": str(e)})
        finally:
            q.put(done)

    threading.Thread(target=run, daemon=True, name="cad-chat-turn").start()
    while True:
        try:
            item = q.get(timeout=15)
        except queue.Empty:
            yield {"type": "ping"}
            continue
        if item is done:
            return
        yield item


def ask(prompt: str) -> dict:
    """One-shot turn for tiny's device path and Telegram: {result, reply, error, tools[], gates[], preview}."""
    text, tools, gates_seen, preview, error = [], [], [], None, None
    for f in ask_stream(prompt):
        t = f.get("type")
        if t == "text":
            text.append(f["delta"])
        elif t == "tool":
            tools.append(f.get("name"))
        elif t == "tool_result" and f.get("image") and not preview:
            preview = f["image"]
        elif t == "gate":
            gates_seen.append({k: f.get(k) for k in ("id", "kind", "detail", "approve", "answered", "preview", "job")})
            if f.get("preview") and not preview:
                preview = f["preview"]
        elif t == "error":
            error = f.get("error")
    reply = "".join(text).strip() or None
    return {"result": reply, "reply": reply, "error": error, "tools": tools, "gates": gates_seen, "preview": preview}


def status() -> dict:
    if _AGENT is not None:
        return {"ready": True, "error": None,
                "tools": len(_AGENT.tool_names),
                "turns": len(_AGENT.messages) if hasattr(_AGENT, "messages") else 0}
    if _ERROR:
        return {"ready": False, "error": _ERROR, "tools": 0, "turns": 0}
    if not _BUILDING:
        threading.Thread(target=_build, daemon=True, name="cad-agent-build").start()
    return {"ready": False, "error": None, "tools": 0, "turns": 0, "building": True}


def reset(rebuild: bool = False) -> dict:
    """Clear history (and optionally drop the agent so model change takes effect)."""
    global _AGENT, _ERROR
    with _LOCK:
        if rebuild:
            _AGENT = None
            _ERROR = None
        elif _AGENT is not None and hasattr(_AGENT, "messages"):
            _AGENT.messages.clear()
    return {"ok": True, "rebuilt": rebuild}
