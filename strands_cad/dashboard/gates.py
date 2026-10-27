#!/usr/bin/env python3
"""
🔒 Operator gate — the ONE place a physical print may start from.

Every path that would start a print (the dashboard's Print buttons, the chat agent's
pipeline tool, tiny's device chat, Telegram `/print`) opens a gate here and WAITS.
The operator answers from the dashboard dock (`POST /api/gate/{id}`), or the gate
denies itself after `timeout_s`. Nothing in this module talks to the printer.

A gate is a dict: {id, kind, detail, job, preview, t, timeout_s, decision, by, timed_out}.
`wait()` blocks the caller's thread; `answer()` wakes it. Listeners (the chat SSE
stream) get every open/answer event as an expo-style frame:
    {"type": "gate", "id", "kind", "detail", "preview", "timeout_s"}                 (opened)
    {"type": "gate", "id", "kind", "detail", "approve", "answered": True, "by"}     (answered)
    {"type": "gate", "id", ..., "approve": False, "answered": True, "timed_out": True}

Gates live in memory on purpose: a restart denies everything that was waiting.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("strands_cad.dashboard.gates")

GATE_TIMEOUT_S = int(os.getenv("STRANDS_CAD_GATE_TIMEOUT_S", "600"))

_GATES: Dict[str, Dict[str, Any]] = {}
_EVENTS: Dict[str, threading.Event] = {}
_LOCK = threading.Lock()
_LISTENERS: List[Callable[[dict], None]] = []
_KEEP = 50


def add_listener(fn: Callable[[dict], None]) -> None:
    with _LOCK:
        _LISTENERS.append(fn)


def remove_listener(fn: Callable[[dict], None]) -> None:
    with _LOCK:
        if fn in _LISTENERS:
            _LISTENERS.remove(fn)


def _emit(frame: dict) -> None:
    with _LOCK:
        fns = list(_LISTENERS)
    for fn in fns:
        try:
            fn(frame)
        except Exception as e:  # a dead SSE client must not break the gate
            log.debug(f"gate listener: {e}")


def _public(g: Dict[str, Any]) -> Dict[str, Any]:
    return {k: g.get(k) for k in ("id", "kind", "detail", "job", "preview", "t", "timeout_s",
                                  "decision", "by", "timed_out", "answered_t")}


def open_gate(kind: str, detail: str, job: Optional[str] = None, preview: Optional[str] = None,
              timeout_s: Optional[int] = None) -> str:
    """Register a gate and announce it. Returns its id; call wait() to block on it."""
    gid = uuid.uuid4().hex[:10]
    rec = {"id": gid, "kind": kind, "detail": detail, "job": job, "preview": preview,
           "t": time.time(), "timeout_s": timeout_s or GATE_TIMEOUT_S,
           "decision": None, "by": None, "timed_out": False, "answered_t": None}
    with _LOCK:
        _GATES[gid] = rec
        _EVENTS[gid] = threading.Event()
        # keep the history short
        if len(_GATES) > _KEEP:
            for old in sorted(_GATES.values(), key=lambda g: g["t"])[: len(_GATES) - _KEEP]:
                if old["decision"] is not None or old["timed_out"]:
                    _GATES.pop(old["id"], None)
                    _EVENTS.pop(old["id"], None)
    log.info(f"🔒 gate {gid} opened [{kind}] {detail[:80]!r}")
    _emit({"type": "gate", "id": gid, "kind": kind, "detail": detail, "preview": preview,
           "timeout_s": rec["timeout_s"], "job": job})
    return gid


def wait(gid: str, timeout_s: Optional[int] = None) -> bool:
    """Block until the operator answers. False on deny OR timeout (timeout → denied, announced)."""
    with _LOCK:
        rec = _GATES.get(gid)
        ev = _EVENTS.get(gid)
    if not rec or not ev:
        return False
    t = timeout_s or rec["timeout_s"]
    ev.wait(timeout=t)
    with _LOCK:
        if rec["decision"] is None:
            rec["decision"], rec["timed_out"], rec["by"] = False, True, "timeout"
            rec["answered_t"] = time.time()
            frame = {"type": "gate", "id": gid, "kind": rec["kind"], "detail": rec["detail"],
                     "approve": False, "answered": True, "timed_out": True, "by": "timeout", "job": rec.get("job")}
        else:
            frame = None
    if frame:
        log.info(f"🔒 gate {gid} timed out → denied")
        _emit(frame)
    return bool(rec["decision"])


def answer(gid: str, approve: bool, by: str = "dashboard") -> Dict[str, Any]:
    """The operator's decision. Idempotent: a second answer does not flip the first."""
    with _LOCK:
        rec = _GATES.get(gid)
        if not rec:
            return {"ok": False, "error": "gate not found"}
        if rec["decision"] is not None:
            return {"ok": True, "already": True, **_public(rec)}
        rec["decision"], rec["by"], rec["answered_t"] = bool(approve), by, time.time()
        ev = _EVENTS.get(gid)
    if ev:
        ev.set()
    log.info(f"🔒 gate {gid} {'APPROVED' if approve else 'denied'} by {by}")
    _emit({"type": "gate", "id": gid, "kind": rec["kind"], "detail": rec["detail"], "approve": bool(approve),
           "answered": True, "by": by, "job": rec.get("job")})
    return {"ok": True, **_public(rec)}


def get(gid: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        rec = _GATES.get(gid)
    return _public(rec) if rec else None


def pending() -> List[Dict[str, Any]]:
    with _LOCK:
        return [_public(g) for g in _GATES.values() if g["decision"] is None and not g["timed_out"]]


def recent(limit: int = 20) -> List[Dict[str, Any]]:
    with _LOCK:
        gs = sorted(_GATES.values(), key=lambda g: g["t"], reverse=True)
    return [_public(g) for g in gs[:limit]]


def deny_all(by: str = "shutdown") -> int:
    n = 0
    for g in pending():
        answer(g["id"], False, by=by)
        n += 1
    return n
