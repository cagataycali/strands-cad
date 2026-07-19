#!/usr/bin/env python3
"""
🧠 In-dashboard strands-cad agent — full CAD/mesh/slice/print toolbelt.

Mirrors neon-the-g1's docs/dashboard/chat_agent.py: a single shared Agent built
lazily in a background thread, serialized by a lock (shared history), with live
printer telemetry injected per-turn from the dashboard's cached snapshot (never
a blocking MQTT call in the request path).

The agent has ALL_TOOLS (all 59 strands-cad tools) so from the chat box you can
say "design a 40mm bracket, slice it, and print it" and it composes the tools.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

log = logging.getLogger("strands_cad.dashboard.chat")

_AGENT = None
_LOCK = threading.Lock()          # serialize ask() (single agent, shared history)
_BUILD_LOCK = threading.Lock()    # guard one-time build
_ERROR: Optional[str] = None
_BUILDING = False
_static_prompt = ""

_BASE_PROMPT = (
    "You are the strands-cad printer cockpit agent. You have atomic CAD, mesh, "
    "SDF, CadQuery, neural, slicing, and Bambu-printer tools. Compose them to "
    "fulfil the user's request end-to-end: design → validate → slice → print. "
    "Be concise. When you create or modify an STL, tell the user its filename so "
    "the 3D viewer can load it. Prefer the configured slice profile and printer "
    "model. Confirm before starting a physical print. Every tool does one job — "
    "chain them yourself."
)


def _build():
    global _AGENT, _ERROR, _BUILDING
    if _AGENT is not None or _ERROR is not None:
        return
    with _BUILD_LOCK:
        if _AGENT is not None or _ERROR is not None or _BUILDING:
            return
        _BUILDING = True
    try:
        import os
        from strands_cad.dashboard import config_store
        config_store.load()  # seed env (model creds etc.)

        from strands import Agent, tool
        from strands_tools import shell
        from strands_cad import ALL_TOOLS

        # ── dashboard-scoped tools: plate editing, colored print, telegram ──
        from strands_cad.dashboard import plate as _plate, jobs as _jobs, telegram as _tg

        @tool
        def plate_add(source: str, color: str = "") -> dict:
            """Add a model (STL/3MF filename in the workdir) onto the build plate.
            Args: source: filename; color: optional #rrggbb."""
            return _plate.add_item(source, color=color)

        @tool
        def plate_recolor(item: str, color: str) -> dict:
            """Recolor a plate item by id/name (or 'all') to a #rrggbb color."""
            return _plate.recolor(item, color)

        @tool
        def plate_move(item_id: str, x: float, y: float, z: float = 0.0) -> dict:
            """Move a plate item to bed position x,y,z (mm, bed-centered)."""
            return _plate.update_item(item_id, position=[x, y, z])

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
        def plate_export_colored_3mf() -> dict:
            """Export the current colored plate arrangement to a printable 3MF."""
            return _plate.export_3mf()

        @tool
        def plate_slice_and_print(confirm: bool = False) -> dict:
            """Export colored plate → slice → upload → START the print on the Bambu.
            Set confirm=True to actually start the physical print."""
            exp = _plate.export_3mf()
            if not exp.get("ok"):
                return {"status": "error", "content": [{"text": exp.get("error", "export failed")}]}
            if not confirm:
                return {"status": "success", "content": [{"text":
                    f"Ready to print {exp['objects']} parts ({exp['rel']}). "
                    "Call again with confirm=True to START the physical print."}], **exp}
            sjid = _jobs.start_slice(exp["rel"], then_print=True,
                                     use_ams=bool(exp.get("use_ams", True)))
            return {"status": "success", "content": [{"text":
                    f"slicing colored plate ({exp['rel']}), job {sjid}; will auto-upload+print when slice completes"}],
                    "slice_job": sjid, "exported": exp}

        @tool
        def telegram_notify(text: str) -> dict:
            """Send a Telegram message to the operator's chat."""
            return _tg.notify(text)

        @tool
        def telegram_send_snapshot(caption: str = "chamber") -> dict:
            """Grab a chamber-camera frame and send it over Telegram."""
            return _tg.send_camera_snapshot(caption)

        _dash_tools = [plate_add, plate_recolor, plate_move, plate_arrange,
                       plate_list, plate_clear, plate_export_colored_3mf,
                       plate_slice_and_print, telegram_notify, telegram_send_snapshot]

        model_id = config_store.get("model") or None
        kwargs = {"tools": ALL_TOOLS + _dash_tools + [shell], "system_prompt": _BASE_PROMPT}
        if model_id:
            kwargs["model"] = model_id

        a = Agent(**kwargs)
        global _static_prompt
        _static_prompt = _BASE_PROMPT
        _AGENT = a
        log.info(f"🧠 strands-cad dashboard agent built ({len(a.tool_names)} tools, "
                 f"model={model_id or 'default'})")
    except Exception as e:
        _ERROR = f"agent build failed: {e}"
        log.warning(_ERROR, exc_info=True)
    finally:
        _BUILDING = False


def _live_block() -> str:
    """Live printer + config block from cached telemetry (non-blocking)."""
    try:
        from strands_cad.dashboard import config_store, printer as _printer
        cfg = config_store.load()
        block = [
            "\n\n## 🖨️ LIVE COCKPIT STATE",
            f"- Printer: {cfg.get('ip') or '?'} serial={cfg.get('serial') or 'auto'}",
            f"- Slice profile: {cfg.get('slice_profile')} · model: {cfg.get('printer_model')}",
            f"- Workdir (STLs live here): {cfg.get('workdir')}",
        ]
        # cached printer snapshot (no new MQTT roundtrip)
        p = _printer._PRINTER
        if p is not None:
            try:
                s = p.snapshot()
                block.append(
                    f"- Job: {s.get('subtask_name') or 'idle'} state={s.get('gcode_state')} "
                    f"progress={s.get('progress')}% nozzle={ (s.get('temps') or {}).get('nozzle') }°C "
                    f"bed={ (s.get('temps') or {}).get('bed') }°C")
            except Exception:
                pass
        return "\n".join(block) + "\n*(trust this cached snapshot; call bambu_status only if you must)*\n"
    except Exception:
        return ""


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


def ask(prompt: str) -> dict:
    _build()
    if _AGENT is None:
        return {"reply": None, "error": _ERROR or "agent still building — retry shortly"}
    try:
        with _LOCK:
            try:
                _AGENT.system_prompt = _static_prompt + _live_block()
            except Exception:
                pass
            result = _AGENT(prompt)
        reply = str(result)
        return {"reply": reply, "error": None}
    except Exception as e:
        log.warning(f"chat ask failed: {e}", exc_info=True)
        return {"reply": None, "error": str(e)}


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
