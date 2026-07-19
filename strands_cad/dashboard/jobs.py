#!/usr/bin/env python3
"""
⚙️ strands-cad dashboard job runner — async slice / print with status polling.

The UI's "Slice" and "Print" buttons kick off background jobs (slicing can take
minutes; upload+start is a multi-step FTPS+MQTT dance). Each job gets an id the
browser polls at /api/job/{id}. Keeps the request path non-blocking.

Job lifecycle:  queued → running → done | error
"""
from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

_JOBS: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()


def _new(kind: str, meta: Dict[str, Any]) -> str:
    jid = uuid.uuid4().hex[:12]
    with _LOCK:
        _JOBS[jid] = {
            "id": jid, "kind": kind, "state": "queued",
            "created": time.time(), "log": [], "result": None,
            "error": None, **meta,
        }
    return jid


def _set(jid: str, **patch):
    with _LOCK:
        if jid in _JOBS:
            _JOBS[jid].update(patch)


def _log(jid: str, msg: str):
    with _LOCK:
        if jid in _JOBS:
            _JOBS[jid]["log"].append(f"{time.strftime('%H:%M:%S')} {msg}")


def get(jid: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        return dict(_JOBS[jid]) if jid in _JOBS else None


def recent(limit: int = 20) -> list:
    with _LOCK:
        js = sorted(_JOBS.values(), key=lambda j: j["created"], reverse=True)
    return [dict(j) for j in js[:limit]]


# ── slice job ───────────────────────────────────────────────────────────────
def start_slice(input_name: str, profile: str = "", printer_model: str = "") -> str:
    """Slice an STL/3MF from the workdir → gcode/3mf in the same dir."""
    from strands_cad.dashboard import config_store, models
    cfg = config_store.load()
    profile = profile or cfg.get("slice_profile", "PLA_0_20")
    printer_model = printer_model or cfg.get("printer_model", "Bambu Lab P1S")

    jid = _new("slice", {"input": input_name, "profile": profile,
                         "printer_model": printer_model})

    def _run():
        _set(jid, state="running")
        _log(jid, f"slicing {input_name} [{profile} · {printer_model}]")
        try:
            src = models._safe(input_name)
            if not src:
                _set(jid, state="error", error=f"input not found: {input_name}")
                return
            wd = models._workdir()
            # if input is STL, wrap into a 3mf plate first (slice_bambu needs 3mf)
            three_mf = src
            if src.suffix.lower() == ".stl":
                from strands_cad.tools.mf3 import mf3_pack
                plate = wd / (src.stem + "_plate.3mf")
                _log(jid, f"packing STL → {plate.name}")
                r = mf3_pack(items=[{"stl": str(src), "name": src.stem,
                                     "position": [0, 0, 0]}],
                             output_3mf=str(plate), title=src.stem)
                if r.get("status") != "success":
                    _set(jid, state="error", error=f"pack failed: {r}")
                    return
                three_mf = plate
            out_gcode = wd / (src.stem + ".gcode")
            from strands_cad.tools.slice import slice_bambu
            r = slice_bambu(input_3mf=str(three_mf), output_gcode=str(out_gcode),
                            profile=profile, printer_model=printer_model)
            if r.get("status") != "success":
                _set(jid, state="error", error=r.get("content", [{}])[0].get("text", "slice failed"),
                     result=r)
                _log(jid, "slice FAILED")
                return
            gpath = r.get("path", str(out_gcode))
            # estimate
            est = {}
            try:
                from strands_cad.tools.slice import slice_estimate
                est = slice_estimate(gcode_file=gpath)
            except Exception:
                pass
            _set(jid, state="done", result={"gcode": gpath,
                 "rel": str(Path(gpath).name),
                 "time": est.get("estimated_time_hms"),
                 "filament_g": est.get("filament_g")})
            _log(jid, f"sliced → {Path(gpath).name} "
                      f"({est.get('estimated_time_hms','?')}, {est.get('filament_g','?')}g)")
        except Exception as e:
            _set(jid, state="error", error=str(e))
            _log(jid, f"exception: {e}")

    threading.Thread(target=_run, daemon=True, name=f"slice-{jid}").start()
    return jid


# ── print job (upload + start) ───────────────────────────────────────────────
def start_print(gcode_name: str, use_ams: bool = True, plate_index: int = 1) -> str:
    """Upload a gcode/3mf from workdir to the printer SD then start the job."""
    from strands_cad.dashboard import config_store, models
    cfg = config_store.load()
    jid = _new("print", {"gcode": gcode_name})

    def _run():
        _set(jid, state="running")
        try:
            src = models._safe(gcode_name)
            if not src:
                _set(jid, state="error", error=f"file not found: {gcode_name}")
                return
            ip, access, serial = cfg.get("ip"), cfg.get("access_code"), cfg.get("serial")
            if not ip or not access:
                _set(jid, state="error", error="printer not configured")
                return
            from strands_cad.tools.bambu import (bambu_connect, bambu_upload, bambu_send)
            _log(jid, f"connecting {ip} …")
            rc = bambu_connect(ip=ip, access_code=access, serial=serial or "")
            if rc.get("status") != "success":
                _set(jid, state="error", error=f"connect failed: {rc}")
                return
            _log(jid, f"uploading {src.name} via FTPS …")
            ru = bambu_upload(file_path=str(src))
            if ru.get("status") != "success":
                _set(jid, state="error", error=ru.get("content", [{}])[0].get("text", "upload failed"))
                _log(jid, "upload FAILED")
                return
            _log(jid, "starting print …")
            rs = bambu_send(file_path=str(src), plate_index=plate_index, use_ams=use_ams)
            if rs.get("status") != "success":
                _set(jid, state="error", error=rs.get("content", [{}])[0].get("text", "send failed"))
                return
            _set(jid, state="done", result={"job": src.name, "started": True})
            _log(jid, f"🖨️ print started: {src.name}")
        except Exception as e:
            _set(jid, state="error", error=str(e))
            _log(jid, f"exception: {e}")

    threading.Thread(target=_run, daemon=True, name=f"print-{jid}").start()
    return jid
