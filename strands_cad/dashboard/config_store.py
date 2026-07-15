#!/usr/bin/env python3
"""
⚙️ strands-cad dashboard live config store.

A tiny JSON-backed settings store (chmod 600) that lets the dashboard UI edit —
and hot-apply — everything neon-the-g1 exposes: printer creds, chat model,
voice provider/model/name, default slice profile + printer model, and camera
fps/quality. Mirrors the neon `prompts.py` / config pattern.

Secrets (access_code, openai_api_key) live here on-disk (600) and are pushed
into the process env so the existing printer/camera/agent code picks them up.
The API layer redacts secrets before sending config to the browser.

Env knobs (initial defaults; UI overrides persist to the store):
  STRANDS_CAD_CONFIG_STORE   path (default ./.strands_cad_config.json)
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, Dict

CONFIG_STORE = Path(os.getenv("STRANDS_CAD_CONFIG_STORE",
                              "./.strands_cad_config.json")).resolve()

_lock = threading.Lock()

# keys considered secret → redacted in API responses
SECRET_KEYS = {"access_code", "openai_api_key", "telegram_bot_token", "aws_bearer_token_bedrock"}

# which config keys map to process-env vars (hot-apply)
_ENV_MAP = {
    "ip": "BAMBU_IP",
    "access_code": "BAMBU_ACCESS_CODE",
    "serial": "BAMBU_SERIAL",
    "model": "STRANDS_MODEL_ID",
    "voice_provider": "VOICE_PROVIDER",
    "voice_model": "VOICE_MODEL",
    "voice_name": "VOICE_NAME",
    "openai_api_key": "OPENAI_API_KEY",
    "cam_fps": "BAMBU_CAM_FPS",
    "cam_quality": "BAMBU_CAM_QUALITY",
    "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
    "telegram_chat_id": "TELEGRAM_CHAT_ID",
    "telegram_allowed_users": "TELEGRAM_ALLOWED_USERS",
    "aws_bearer_token_bedrock": "AWS_BEARER_TOKEN_BEDROCK",
}


def _default_filaments() -> list:
    """5-color default: 4 AMS PLA slots on nozzle 0 + 1 external PETG on nozzle 1."""
    import json as _json, os as _os
    raw = _os.getenv("STRANDS_CAD_FILAMENTS")
    if raw:
        try:
            return _json.loads(raw)
        except Exception:
            pass
    return [
        {"slot": 1, "name": "PLA Basic Green",  "type": "PLA",  "color": "#76b900", "nozzle": 0},
        {"slot": 2, "name": "PLA Basic Cyan",   "type": "PLA",  "color": "#00d9ff", "nozzle": 0},
        {"slot": 3, "name": "PLA Basic Orange", "type": "PLA",  "color": "#ff6b4a", "nozzle": 0},
        {"slot": 4, "name": "PLA Basic Yellow", "type": "PLA",  "color": "#ffce54", "nozzle": 0},
        {"slot": 5, "name": "PETG Purple",      "type": "PETG", "color": "#b066ff", "nozzle": 1},
    ]


def _defaults() -> Dict[str, Any]:
    return {
        # printer
        "ip": os.getenv("BAMBU_IP", ""),
        "access_code": os.getenv("BAMBU_ACCESS_CODE", ""),
        "serial": os.getenv("BAMBU_SERIAL", ""),
        # chat agent model
        "model": os.getenv("STRANDS_MODEL_ID", ""),
        # voice (OpenAI Realtime)
        "voice_provider": os.getenv("VOICE_PROVIDER", "openai"),
        "voice_model": os.getenv("VOICE_MODEL", "gpt-realtime"),
        "voice_name": os.getenv("VOICE_NAME", "alloy"),
        "openai_api_key": os.getenv("OPENAI_API_KEY", ""),
        # telegram bot
        "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
        "telegram_allowed_users": os.getenv("TELEGRAM_ALLOWED_USERS", ""),
        # bedrock (for chat agent auth)
        "aws_bearer_token_bedrock": os.getenv("AWS_BEARER_TOKEN_BEDROCK", ""),
        # slicing
        "slice_profile": os.getenv("STRANDS_CAD_SLICE_PROFILE", "PLA_0_20"),
        "printer_model": os.getenv("STRANDS_CAD_PRINTER_MODEL", "Bambu Lab X2D"),
        # X2D = H2D platform + laser module: DUAL-nozzle 3D printing AND 2D laser
        # cut/engrave. MQTT report carries both "2D" and "3D" blocks. Bed 350x320x325.
        "nozzle_count": int(os.getenv("STRANDS_CAD_NOZZLE_COUNT", "2")),
        # X2D has a laser/2D mode in addition to 3D printing.
        "has_laser": os.getenv("STRANDS_CAD_HAS_LASER", "true").lower() == "true",
        # filaments loaded (AMS + external spools). X2D + AMS + 1 external PETG = 5.
        # Each: {slot, name, type, color:"#rrggbb", nozzle:0|1}
        "filaments": _default_filaments(),
        # working directory for STL/3MF/gcode assets served to the viewer
        "workdir": os.getenv("STRANDS_CAD_WORKDIR", str(Path.cwd())),
        # camera
        "cam_fps": int(os.getenv("BAMBU_CAM_FPS", "15")),
        "cam_quality": int(os.getenv("BAMBU_CAM_QUALITY", "5")),
        "_updated": time.time(),
    }


def _read() -> Dict[str, Any]:
    if CONFIG_STORE.exists():
        try:
            import json
            data = json.loads(CONFIG_STORE.read_text())
            base = _defaults()
            base.update(data)
            return base
        except Exception:
            pass
    return _defaults()


def _write(cfg: Dict[str, Any]) -> None:
    import json
    CONFIG_STORE.write_text(json.dumps(cfg, indent=2))
    try:
        os.chmod(CONFIG_STORE, 0o600)
    except Exception:
        pass


def _apply_env(cfg: Dict[str, Any]) -> None:
    """Push mapped keys into process env so existing code picks them up."""
    for k, env_key in _ENV_MAP.items():
        v = cfg.get(k)
        if v not in (None, ""):
            os.environ[env_key] = str(v)


def load() -> Dict[str, Any]:
    """Load full config (with secrets) and hot-apply to env. Internal use."""
    with _lock:
        cfg = _read()
        _apply_env(cfg)
        return cfg


def get(key: str, default: Any = None) -> Any:
    return load().get(key, default)


def redacted() -> Dict[str, Any]:
    """Config safe for the browser — secrets replaced with presence booleans."""
    cfg = load()
    out = dict(cfg)
    for k in SECRET_KEYS:
        present = bool(cfg.get(k))
        out[k] = ""  # never leak the value
        out[f"{k}_set"] = present
    return out


def update(patch: Dict[str, Any]) -> Dict[str, Any]:
    """Merge a patch, persist, hot-apply env, rebuild printer/camera singletons.

    Empty-string secret values are IGNORED (so a blank field in the UI doesn't
    wipe an existing key). Returns the redacted config.
    """
    with _lock:
        cfg = _read()
        for k, v in patch.items():
            if k.startswith("_") or k.endswith("_set"):
                continue
            if k in SECRET_KEYS and (v is None or v == ""):
                continue  # don't clobber existing secret with blank
            cfg[k] = v
        # coerce ints
        for ik in ("cam_fps", "cam_quality"):
            try:
                cfg[ik] = int(cfg[ik])
            except Exception:
                pass
        cfg["_updated"] = time.time()
        _write(cfg)
        _apply_env(cfg)

    _hot_apply_singletons(cfg)
    return redacted()


def _hot_apply_singletons(cfg: Dict[str, Any]) -> None:
    """Rebuild printer + camera singletons if creds changed; nudge chat agent."""
    ip, access, serial = cfg.get("ip"), cfg.get("access_code"), cfg.get("serial")
    # printer
    try:
        from strands_cad.dashboard import printer as _printer
        with _printer._PLOCK:
            if _printer._PRINTER is not None:
                try:
                    _printer._PRINTER.disconnect()
                except Exception:
                    pass
                _printer._PRINTER = None  # lazy-recreated on next telemetry poll
    except Exception:
        pass
    # camera
    try:
        from strands_cad.dashboard import camera as _camera
        _camera.stop_all()
    except Exception:
        pass
    # chat agent model change → force rebuild on next status/ask
    try:
        from strands_cad.dashboard import chat_agent as _chat
        _chat.reset(rebuild=True)
    except Exception:
        pass


# hot-apply once at import so env is seeded from the store
try:
    load()
except Exception:
    pass
