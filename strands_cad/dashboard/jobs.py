#!/usr/bin/env python3
"""
⚙️ strands-cad dashboard job runner — async slice / print with status polling.

The UI's "Slice" and "Print" buttons kick off background jobs (slicing can take
minutes; upload+start is a multi-step FTPS+MQTT dance). Each job gets an id the
browser polls at /api/job/{id}. Keeps the request path non-blocking.

Job lifecycle:  queued → running → [awaiting_approval] → done | error | denied

Jobs are persisted to `.strands_cad_jobs.json` next to the config store so the Jobs
drawer survives a restart (a job that was running when the process died is marked
error on load — its thread is gone). A print NEVER starts from here without a gate:
`start_print` opens one and waits (gates.py); `print_sync` is the post-approval half
the pipeline uses.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger("strands_cad.dashboard.jobs")

_JOBS: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()
_KEEP = 200
_LOADED = False


def _store_path() -> Path:
    return Path(os.getenv("STRANDS_CAD_JOBS_STORE", "./.strands_cad_jobs.json")).resolve()


def _load_locked() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    p = _store_path()
    if not p.exists():
        return
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        for j in data.get("jobs", []):
            if j.get("state") in ("queued", "running", "awaiting_approval"):
                j["state"] = "error"
                j["error"] = j.get("error") or "dashboard restarted while the job was in flight"
            _JOBS[j["id"]] = j
    except Exception as e:
        log.warning(f"jobs store unreadable ({p}): {e}")


def _save_locked() -> None:
    try:
        js = sorted(_JOBS.values(), key=lambda j: j["created"], reverse=True)[:_KEEP]
        tmp = _store_path().with_suffix(".tmp")
        tmp.write_text(json.dumps({"jobs": js}, default=str), encoding="utf-8")
        os.replace(tmp, _store_path())
    except Exception as e:
        log.debug(f"jobs store write failed: {e}")


def _new_with_id(jid: str, kind: str, meta: Dict[str, Any]) -> str:
    with _LOCK:
        _load_locked()
        _JOBS[jid] = {
            "id": jid, "kind": kind, "state": "queued",
            "created": time.time(), "log": [], "result": None,
            "error": None, **meta,
        }
        _save_locked()
    return jid


def _new(kind: str, meta: Dict[str, Any]) -> str:
    return _new_with_id(uuid.uuid4().hex[:12], kind, meta)


def _set(jid: str, **patch):
    with _LOCK:
        if jid in _JOBS:
            _JOBS[jid].update(patch)
            _save_locked()


def _log(jid: str, msg: str):
    with _LOCK:
        if jid in _JOBS:
            _JOBS[jid]["log"].append(f"{time.strftime('%H:%M:%S')} {msg}")
            _save_locked()


def get(jid: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        _load_locked()
        return json.loads(json.dumps(_JOBS[jid], default=str)) if jid in _JOBS else None


def recent(limit: int = 40) -> list:
    with _LOCK:
        _load_locked()
        js = sorted(_JOBS.values(), key=lambda j: j["created"], reverse=True)
    return [json.loads(json.dumps(j, default=str)) for j in js[:limit]]


# ── synchronous halves (the pipeline runs them inside its own thread) ────────
def slice_sync(jid: str, src: Path, profile: str, printer_model: str) -> Dict[str, Any]:
    """Slice `src` (STL → packed 3MF → Orca) logging onto job `jid`.
    Returns {printable, gcode, time, filament_g} or {error, result}."""
    from strands_cad.dashboard import models
    wd = models._workdir()
    three_mf = src
    if src.suffix.lower() == ".stl":
        from strands_cad.tools.mf3 import mf3_pack
        plate = wd / (src.stem + "_plate.3mf")
        _log(jid, f"packing STL → {plate.name}")
        r = mf3_pack(items=[{"stl": str(src), "name": src.stem, "position": [0, 0, 0]}],
                     output_3mf=str(plate), title=src.stem)
        if r.get("status") != "success":
            return {"error": f"3MF pack failed: {r.get('content', [{}])[0].get('text', r)}", "result": r}
        three_mf = plate
    out_gcode = wd / (src.stem + ".gcode")
    from strands_cad.tools.slice import slice_bambu
    _log(jid, f"slicing {three_mf.name} [{profile} · {printer_model}]")
    r = slice_bambu(input_3mf=str(three_mf), output_gcode=str(out_gcode),
                    profile=profile, printer_model=printer_model)
    if r.get("status") != "success":
        words = r.get("content", [{}])[0].get("text", "slice failed")
        return {"error": f"slicer: {words}", "result": r}
    gpath = r.get("path", str(out_gcode))
    est: Dict[str, Any] = {}
    try:
        from strands_cad.tools.slice import slice_estimate
        gc = r.get("gcode") or (str(out_gcode) if out_gcode.exists() else gpath)
        est = slice_estimate(gcode_file=gc) or {}
    except Exception:
        pass
    printable = r.get("path", gpath)
    _log(jid, f"sliced → {Path(printable).name} ({est.get('estimated_time_hms', '?')}, {est.get('filament_g', '?')} g)")
    return {"printable": printable, "gcode": gpath, "time": est.get("estimated_time_hms"),
            "filament_g": est.get("filament_g")}


def print_sync(jid: str, src: Path, use_ams: bool = True, plate_index: int = 1) -> Dict[str, Any]:
    """Upload + start `src` on the printer, logging onto job `jid`. ONLY called after an approved gate.
    Returns {started: True, job} or {error}."""
    from strands_cad.dashboard import config_store
    cfg = config_store.load()
    ip, access, serial = cfg.get("ip"), cfg.get("access_code"), cfg.get("serial")
    if not ip or not access:
        return {"error": "printer not configured"}
    from strands_cad.tools.bambu import (bambu_connect, bambu_upload, bambu_send)
    pm = cfg.get("printer_model") or "Bambu Lab X2D"
    if src.suffix.lower() == ".gcode":
        sibling = src.with_suffix(".3mf")
        if sibling.exists():
            src = sibling
            _log(jid, f"using sliced project bundle → {src.name}")
        else:
            from strands_cad.tools.mf3 import bambu_gcode_3mf
            wrapped = bambu_gcode_3mf(str(src), plate_index=plate_index, printer_model=pm)
            if wrapped.get("status") != "success":
                return {"error": f"wrap failed: {wrapped}"}
            src = Path(wrapped["path"])
            _log(jid, f"wrapped gcode → {src.name} (Bambu project bundle)")
    if src.suffix.lower() == ".3mf":
        try:
            from strands_cad.tools.slice import _inject_model_code
            _inject_model_code(str(src), pm)
        except Exception as _e:
            _log(jid, f"model-code inject skipped: {_e}")
    _log(jid, f"connecting {ip} …")
    rc = bambu_connect(ip=ip, access_code=access, serial=serial or "")
    if rc.get("status") != "success":
        return {"error": f"connect failed: {rc.get('content', [{}])[0].get('text', rc)}"}
    _log(jid, f"uploading {src.name} via FTPS …")
    ru = bambu_upload(file_path=str(src))
    if ru.get("status") != "success":
        return {"error": ru.get("content", [{}])[0].get("text", "upload failed")}
    _log(jid, "starting print …")
    rs = bambu_send(file_path=str(src), plate_index=plate_index, use_ams=use_ams)
    if rs.get("status") != "success":
        return {"error": rs.get("content", [{}])[0].get("text", "send failed")}
    _log(jid, f"🖨️ print started: {src.name}")
    return {"started": True, "job": src.name}


# ── slice job ───────────────────────────────────────────────────────────────
def start_slice(input_name: str, profile: str = "", printer_model: str = "",
                then_print: bool = False, use_ams: bool = True, plate_index: int = 1) -> str:
    """Slice an STL/3MF from the workdir → gcode/3mf in the same dir."""
    from strands_cad.dashboard import config_store, models
    cfg = config_store.load()
    profile = profile or cfg.get("slice_profile", "PLA_0_20")
    # coalesce empty-string too (persisted config may have "") → default X2D
    printer_model = printer_model or cfg.get("printer_model") or "Bambu Lab X2D"

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
            # estimate reads the bare gcode (r['gcode']), not the .3mf bundle
            est = {}
            try:
                from strands_cad.tools.slice import slice_estimate
                _gc = r.get("gcode") or (str(out_gcode) if out_gcode.exists() else gpath)
                est = slice_estimate(gcode_file=_gc)
            except Exception:
                pass
            _set(jid, state="done", result={"gcode": gpath,
                 "rel": str(Path(gpath).name),
                 "time": est.get("estimated_time_hms"),
                 "filament_g": est.get("filament_g")})
            _log(jid, f"sliced → {Path(gpath).name} "
                      f"({est.get('estimated_time_hms','?')}, {est.get('filament_g','?')}g)")
            if then_print:
                # Prefer the real OrcaSlicer .3mf project bundle (path) over the
                # bare gcode — firmware accepts the proper bundle directly.
                printable = r.get("path", gpath)
                gname = Path(printable).name
                _log(jid, f"→ chaining to print: {gname}")
                pjid = start_print(gname, use_ams=use_ams, plate_index=plate_index)
                _set(jid, result={"gcode": gpath, "rel": gname,
                     "time": est.get("estimated_time_hms"),
                     "filament_g": est.get("filament_g"), "print_job": pjid})
                _log(jid, f"print job queued: {pjid}")
        except Exception as e:
            _set(jid, state="error", error=str(e))
            _log(jid, f"exception: {e}")

    threading.Thread(target=_run, daemon=True, name=f"slice-{jid}").start()
    return jid


# ── print job (gate → upload + start) ───────────────────────────────────────
def start_print(gcode_name: str, use_ams: bool = True, plate_index: int = 1,
                requested_by: str = "dashboard") -> str:
    """Print an already-sliced gcode/3mf from the workdir — AFTER the operator approves the gate.
    The job sits in `awaiting_approval` with its gate id until then; denial/timeout → `denied`."""
    from strands_cad.dashboard import models, gates as _gates
    jid = _new("print", {"gcode": gcode_name, "title": Path(gcode_name).stem.replace("_", " "),
                         "requested_by": requested_by, "gate": None, "steps": []})

    def _run():
        _set(jid, state="running")
        try:
            src = models._safe(gcode_name)
            if not src:
                _set(jid, state="error", error=f"file not found: {gcode_name}")
                return
            detail = f"{src.name}\nprint an already-sliced file as is\nrequested by {requested_by}"
            gid = _gates.open_gate("print", detail, job=jid)
            _set(jid, state="awaiting_approval", gate=gid,
                 steps=[{"name": "gate", "state": "waiting", "t": time.time(), "text": f"gate {gid}"}])
            _log(jid, f"waiting for approval (gate {gid})")
            if not _gates.wait(gid):
                g = _gates.get(gid) or {}
                why = "timed out → denied" if g.get("timed_out") else f"denied by {g.get('by') or 'operator'}"
                _set(jid, state="denied", error=f"print not approved: {why}",
                     steps=[{"name": "gate", "state": "denied", "t": time.time(), "text": why}])
                return
            _set(jid, state="running", steps=[{"name": "gate", "state": "done", "t": time.time(), "text": "approved"},
                                              {"name": "print", "state": "running", "t": time.time()}])
            r = print_sync(jid, src, use_ams=use_ams, plate_index=plate_index)
            if r.get("error"):
                _set(jid, state="error", error=r["error"])
                return
            _set(jid, state="done", result={"job": r.get("job"), "started": True},
                 steps=[{"name": "gate", "state": "done", "t": time.time(), "text": "approved"},
                        {"name": "print", "state": "done", "t": time.time()}])
        except Exception as e:
            _set(jid, state="error", error=str(e))
            _log(jid, f"exception: {e}")

    threading.Thread(target=_run, daemon=True, name=f"print-{jid}").start()
    return jid
