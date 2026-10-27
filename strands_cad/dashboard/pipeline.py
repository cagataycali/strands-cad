#!/usr/bin/env python3
"""
🧵 Prompt-to-print pipeline — one job, six steps, one gate.

    design → validate → preview → slice → gate → print

`start(model=...)` takes an STL/3MF the agent (or the Models drawer) already produced
and runs the rest in a background thread, recording every step on the job
(`jobs.py`, kind "pipeline") so the dashboard's Jobs drawer and tiny's device reply
can show where it stands. The `gate` step blocks on `gates.wait()` — a print NEVER
starts unless the operator approved that exact gate on the dashboard (or it was
denied/timed out → job state `denied`). Failures carry the tool's own words.

Idempotent: the job id is a hash of (file, mtime, profile); re-asking for the same
print while a job is queued/running/waiting returns the existing job instead of a
second gate.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from strands_cad.dashboard import jobs as _jobs
from strands_cad.dashboard import gates as _gates

log = logging.getLogger("strands_cad.dashboard.pipeline")

STEPS = ("design", "validate", "preview", "slice", "gate", "print")
X2D_BED = [325.0, 320.0, 325.0]      # Bambu Lab X2D build volume (mm), single-nozzle region


def _step(jid: str, name: str, state: str, text: str = "") -> None:
    j = _jobs.get(jid)
    if not j:
        return
    steps = list(j.get("steps") or [])
    for s in steps:
        if s["name"] == name:
            s.update(state=state, t=time.time(), text=text[:400])
            break
    else:
        steps.append({"name": name, "state": state, "t": time.time(), "text": text[:400]})
    _jobs._set(jid, steps=steps, step=name)
    if text:
        _jobs._log(jid, f"{name}: {text[:200]}")


def job_id_for(path: Path, profile: str) -> str:
    try:
        key = f"{path.resolve()}|{int(path.stat().st_mtime)}|{profile}"
    except OSError:
        key = f"{path}|{profile}"
    return "p" + hashlib.sha1(key.encode()).hexdigest()[:11]


def existing_live(jid: str) -> Optional[Dict[str, Any]]:
    j = _jobs.get(jid)
    if j and j.get("state") in ("queued", "running", "awaiting_approval"):
        return j
    return None


def start(model: str, profile: str = "", title: str = "", designed_by: str = "",
          use_ams: bool = True, plate_index: int = 1, requested_by: str = "dashboard") -> Dict[str, Any]:
    """Queue validate → preview → slice → gate → print for `model` (a workdir file).
    Returns {job_id, existing?, error?} immediately; poll /api/job/{id}."""
    from strands_cad.dashboard import config_store, models
    cfg = config_store.load()
    profile = profile or cfg.get("slice_profile") or "PLA_0_20"
    printer_model = cfg.get("printer_model") or "Bambu Lab X2D"
    src = models._safe(model)
    if not src:
        return {"error": f"model not found in the workdir: {model}"}
    if src.suffix.lower() not in (".stl", ".3mf"):
        return {"error": f"not printable as a model: {src.name} (need .stl or .3mf)"}

    jid = job_id_for(src, profile)
    live = existing_live(jid)
    if live:
        return {"job_id": jid, "existing": True, "state": live["state"], "gate": live.get("gate")}

    title = title or src.stem.replace("_", " ")
    _jobs._new_with_id(jid, "pipeline", {
        "model": str(src.relative_to(models._workdir())), "profile": profile, "printer_model": printer_model,
        "title": title, "requested_by": requested_by, "steps": [], "preview": None, "gate": None,
    })
    if designed_by:
        _step(jid, "design", "done", designed_by)

    def _run():
        _jobs._set(jid, state="running")
        try:
            # ── validate ────────────────────────────────────────────────────
            _step(jid, "validate", "running")
            size = None
            if src.suffix.lower() == ".stl":
                from strands_cad.tools.printability import stl_printability
                r = stl_printability(stl_file=str(src), printer="X2D")
                if r.get("status") != "success":
                    _step(jid, "validate", "error", r.get("content", [{}])[0].get("text", "printability check failed"))
                    _jobs._set(jid, state="error", error=r.get("content", [{}])[0].get("text", "printability check failed"))
                    return
                size = r.get("size")
                if not r.get("fits_bed", True):
                    msg = f"does not fit the X2D bed: {size} mm vs {X2D_BED}"
                    _step(jid, "validate", "error", msg)
                    _jobs._set(jid, state="error", error=msg)
                    return
                warn = "; ".join(r.get("warnings") or [])
                _step(jid, "validate", "done", f"{[round(x, 1) for x in size]} mm · supports {'needed' if r.get('needs_support') else 'not needed'}"
                      + (f" · {warn}" if warn else ""))
            else:
                _step(jid, "validate", "done", "3MF plate — sliced as is")

            # ── preview ─────────────────────────────────────────────────────
            _step(jid, "preview", "running")
            preview_rel = None
            if src.suffix.lower() == ".stl":
                try:
                    from strands_cad.dashboard.render import render_stl_png
                    png = src.with_name(src.stem + "_preview.png")
                    pr = render_stl_png(str(src), str(png))
                    if pr.get("ok"):
                        preview_rel = str(png.relative_to(models._workdir()))
                        _jobs._set(jid, preview=f"/api/model/{preview_rel}")
                        _step(jid, "preview", "done", png.name)
                    else:
                        _step(jid, "preview", "skipped", pr.get("error", "no preview"))
                except Exception as e:
                    _step(jid, "preview", "skipped", f"preview failed: {e}")
            else:
                _step(jid, "preview", "skipped", "no preview for a 3MF plate")

            # ── slice (synchronous, reusing the slice job's logic) ──────────
            _step(jid, "slice", "running", f"{profile} · {printer_model}")
            sl = _jobs.slice_sync(jid, src, profile, printer_model)
            if sl.get("error"):
                _step(jid, "slice", "error", sl["error"])
                _jobs._set(jid, state="error", error=sl["error"], result=sl.get("result"))
                return
            est = sl.get("time") or "?"
            _step(jid, "slice", "done", f"{Path(sl['printable']).name} · {est} · {sl.get('filament_g') or '?'} g")

            # ── gate ────────────────────────────────────────────────────────
            detail = (f"{title}\n{Path(sl['printable']).name} · {profile} on {printer_model}\n"
                      f"size {[round(x, 1) for x in size] if size else '?'} mm · est. {est} · {sl.get('filament_g') or '?'} g filament\n"
                      f"requested by {requested_by}")
            gid = _gates.open_gate("print", detail, job=jid, preview=_jobs.get(jid).get("preview"))
            _jobs._set(jid, state="awaiting_approval", gate=gid)
            _step(jid, "gate", "waiting", f"gate {gid} — approve on the dashboard")
            ok = _gates.wait(gid)
            g = _gates.get(gid) or {}
            if not ok:
                why = "timed out → denied" if g.get("timed_out") else f"denied by {g.get('by') or 'operator'}"
                _step(jid, "gate", "denied", why)
                _jobs._set(jid, state="denied", error=f"print not approved: {why}")
                return
            _step(jid, "gate", "done", f"approved by {g.get('by') or 'operator'}")

            # ── print ───────────────────────────────────────────────────────
            _step(jid, "print", "running")
            pr = _jobs.print_sync(jid, Path(sl["printable"]), use_ams=use_ams, plate_index=plate_index)
            if pr.get("error"):
                _step(jid, "print", "error", pr["error"])
                _jobs._set(jid, state="error", error=pr["error"])
                return
            _step(jid, "print", "done", f"started {Path(sl['printable']).name}")
            _jobs._set(jid, state="done", result={"printable": sl["printable"], "time": est,
                                                   "filament_g": sl.get("filament_g"), "started": True})
        except Exception as e:
            log.warning(f"pipeline {jid} crashed: {e}", exc_info=True)
            _jobs._set(jid, state="error", error=str(e))
            _jobs._log(jid, f"exception: {e}")

    threading.Thread(target=_run, daemon=True, name=f"pipeline-{jid}").start()
    return {"job_id": jid, "existing": False}


def describe(jid: str) -> str:
    """One paragraph a chat reply can carry: where the job stands and what the operator must do."""
    j = _jobs.get(jid)
    if not j:
        return f"job {jid} not found"
    st = j.get("state")
    if st == "awaiting_approval":
        return (f"'{j.get('title')}' is sliced and WAITING FOR APPROVAL (gate {j.get('gate')}). "
                f"Approve or deny it on the dashboard dock — nothing prints until then.")
    if st == "denied":
        return f"'{j.get('title')}' was not printed: {j.get('error')}"
    if st == "error":
        return f"'{j.get('title')}' failed at {j.get('step')}: {j.get('error')}"
    if st == "done":
        return f"'{j.get('title')}' was approved and the print started."
    return f"'{j.get('title')}' is {st} at step {j.get('step') or 'queued'}."
