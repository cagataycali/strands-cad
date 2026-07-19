#!/usr/bin/env python3
"""
🎙️ strands-cad dashboard — OpenAI Realtime voice bridge (WebRTC).

The browser talks to OpenAI's Realtime API directly over WebRTC for
lowest-latency natural-language voice. To keep the real OPENAI_API_KEY secret,
the server mints a SHORT-LIVED ephemeral client secret here (auth-gated), and
the browser uses only that to open its PeerConnection.

Endpoint used: POST https://api.openai.com/v1/realtime/client_secrets
  → returns { value: "ek_...", expires_at: ... }  (ephemeral, ~1 min)

We also hand the browser a session config: the voice model, voice name, system
instructions, and a set of TOOLS the voice agent can call. The browser wires
those tool-calls (over the Realtime data channel) back to our /api/* endpoints
so you can *say* "recolor the stem red and slice it" and it happens.

Docs pattern mirrors neon-the-g1's OpenAI Realtime voice (gpt-realtime, alloy).
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Any, Dict, List

log = logging.getLogger("strands_cad.dashboard.realtime")

OPENAI_REALTIME_SECRETS_URL = "https://api.openai.com/v1/realtime/client_secrets"


def _voice_instructions() -> str:
    from strands_cad.dashboard import config_store
    cfg = config_store.load()
    return (
        "You are the voice of the strands-cad printer cockpit. You help the user "
        "design, view, recolor, slice, and print 3D parts on their Bambu printer. "
        f"Printer: {cfg.get('ip') or 'unset'} · profile {cfg.get('slice_profile')} · "
        f"{cfg.get('printer_model')}. Speak concisely and naturally. When the user "
        "asks to recolor a part, load a model, slice, or print, CALL THE MATCHING "
        "TOOL. Always confirm out loud before starting a physical print. For any "
        "design/CAD request beyond the direct tools, call `ask_cad_agent` with the "
        "user's request and read back the result."
    )


def voice_tools() -> List[Dict[str, Any]]:
    """Realtime function-calling tool schema the browser wires to /api/*."""
    return [
        {"type": "function", "name": "load_model",
         "description": "Load an STL/3MF into the 3D viewer by filename.",
         "parameters": {"type": "object", "properties": {
             "name": {"type": "string", "description": "filename in the workdir"}},
             "required": ["name"]}},
        {"type": "function", "name": "recolor_part",
         "description": "Recolor a part/mesh currently shown in the viewer.",
         "parameters": {"type": "object", "properties": {
             "part": {"type": "string", "description": "part name or 'all'"},
             "color": {"type": "string", "description": "CSS hex like #ff3355"}},
             "required": ["color"]}},
        {"type": "function", "name": "slice_model",
         "description": "Slice a model in the workdir for printing.",
         "parameters": {"type": "object", "properties": {
             "name": {"type": "string"},
             "profile": {"type": "string", "description": "e.g. PLA_0_20 (optional)"}},
             "required": ["name"]}},
        {"type": "function", "name": "print_model",
         "description": "Upload a sliced gcode/3mf to the printer and START the print.",
         "parameters": {"type": "object", "properties": {
             "name": {"type": "string", "description": "gcode/3mf filename"}},
             "required": ["name"]}},
        {"type": "function", "name": "printer_status",
         "description": "Get current printer telemetry (temps, progress, state).",
         "parameters": {"type": "object", "properties": {}}},
        {"type": "function", "name": "control_print",
         "description": "Pause, resume, or stop the current print.",
         "parameters": {"type": "object", "properties": {
             "action": {"type": "string", "enum": ["pause", "resume", "stop"]}},
             "required": ["action"]}},
        {"type": "function", "name": "plate_add",
         "description": "Add a model (STL/3MF filename) onto the build plate.",
         "parameters": {"type": "object", "properties": {
             "source": {"type": "string"}, "color": {"type": "string", "description": "#rrggbb optional"}},
             "required": ["source"]}},
        {"type": "function", "name": "plate_recolor",
         "description": "Recolor a plate item by id/name, or 'all' items, to #rrggbb.",
         "parameters": {"type": "object", "properties": {
             "item": {"type": "string"}, "color": {"type": "string"}},
             "required": ["item", "color"]}},
        {"type": "function", "name": "plate_arrange",
         "description": "Auto-arrange all plate items so they don't overlap.",
         "parameters": {"type": "object", "properties": {}}},
        {"type": "function", "name": "plate_list",
         "description": "List everything on the build plate with colors/positions.",
         "parameters": {"type": "object", "properties": {}}},
        {"type": "function", "name": "plate_clear",
         "description": "Remove all items from the build plate.",
         "parameters": {"type": "object", "properties": {}}},
        {"type": "function", "name": "plate_print",
         "description": "Export the COLORED plate, slice it, upload and START the print. Confirm first.",
         "parameters": {"type": "object", "properties": {
             "confirm": {"type": "boolean"}}, "required": ["confirm"]}},
        {"type": "function", "name": "telegram_notify",
         "description": "Send a Telegram message to the operator.",
         "parameters": {"type": "object", "properties": {
             "text": {"type": "string"}}, "required": ["text"]}},
        {"type": "function", "name": "ask_cad_agent",
         "description": "Delegate a complex CAD/design/slice/print request to the "
                        "full strands-cad agent (all 59 tools). Returns text.",
         "parameters": {"type": "object", "properties": {
             "request": {"type": "string"}}, "required": ["request"]}},
    ]


def mint_ephemeral() -> Dict[str, Any]:
    """Create a short-lived OpenAI Realtime client secret + session config.

    Returns {ok, client_secret, model, voice, instructions, tools} or {ok:False,error}.
    """
    from strands_cad.dashboard import config_store
    cfg = config_store.load()
    api_key = cfg.get("openai_api_key") or os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return {"ok": False, "error": "OPENAI_API_KEY not set — add it in Config."}

    model = cfg.get("voice_model") or "gpt-realtime"
    voice = cfg.get("voice_name") or "alloy"
    instructions = _voice_instructions()
    tools = voice_tools()

    session = {
        "session": {
            "type": "realtime",
            "model": model,
            "audio": {"output": {"voice": voice}},
            "instructions": instructions,
            "tools": tools,
        }
    }
    body = json.dumps(session).encode()
    req = urllib.request.Request(
        OPENAI_REALTIME_SECRETS_URL, data=body, method="POST",
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:400]
        # Fallback for older API shape: /v1/realtime/sessions
        log.warning(f"client_secrets HTTP {e.code}: {detail}")
        return _mint_legacy(api_key, model, voice, instructions, tools) or \
               {"ok": False, "error": f"OpenAI {e.code}: {detail}"}
    except Exception as e:
        return {"ok": False, "error": f"mint failed: {e}"}

    # response shape: {value/client_secret, expires_at, ...}
    secret = (data.get("value") or data.get("client_secret")
              or (data.get("client_secret") or {}).get("value"))
    return {"ok": True, "client_secret": secret, "raw": data,
            "model": model, "voice": voice,
            "instructions": instructions, "tools": tools}


def _mint_legacy(api_key, model, voice, instructions, tools):
    """Fallback to the older /v1/realtime/sessions endpoint shape."""
    url = "https://api.openai.com/v1/realtime/sessions"
    body = json.dumps({"model": model, "voice": voice,
                       "instructions": instructions, "tools": tools}).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json",
                 "OpenAI-Beta": "realtime=v1"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        log.warning(f"legacy mint failed: {e}")
        return None
    secret = (data.get("client_secret") or {}).get("value")
    return {"ok": True, "client_secret": secret, "raw": data,
            "model": model, "voice": voice,
            "instructions": instructions, "tools": tools}
