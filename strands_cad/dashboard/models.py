#!/usr/bin/env python3
"""
🧊 strands-cad dashboard model registry — list & serve 3D assets to the viewer.

Serves STL / 3MF / G-code files from the configured workdir to the browser's
three.js viewer. Also computes lightweight metadata (size, bbox, triangle
count, PLA weight) so the UI can label parts without re-parsing client-side.

All paths are sandboxed to the workdir (no traversal). Read-only.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

_EXTS = {".stl", ".3mf", ".gcode", ".obj", ".step", ".stp"}


def _workdir() -> Path:
    from strands_cad.dashboard import config_store
    wd = config_store.get("workdir") or str(Path.cwd())
    p = Path(wd).expanduser().resolve()
    return p


def _safe(name: str) -> Optional[Path]:
    """Resolve `name` inside the workdir; None if it escapes or missing."""
    wd = _workdir()
    try:
        p = (wd / name).resolve()
        p.relative_to(wd)  # raises if traversal
    except Exception:
        return None
    return p if p.exists() and p.is_file() else None


def list_models() -> List[Dict[str, Any]]:
    """List all 3D asset files in the workdir (recursive, sandboxed)."""
    wd = _workdir()
    if not wd.exists():
        return []
    out = []
    for p in sorted(wd.rglob("*")):
        if p.is_file() and p.suffix.lower() in _EXTS:
            try:
                rel = str(p.relative_to(wd))
            except Exception:
                continue
            out.append({
                "name": rel,
                "ext": p.suffix.lower().lstrip("."),
                "size": p.stat().st_size,
                "mtime": p.stat().st_mtime,
            })
    return out


def meta(name: str) -> Dict[str, Any]:
    """Compute bbox / triangle count / PLA weight for an STL (best-effort)."""
    p = _safe(name)
    if not p:
        return {"error": "not found"}
    info: Dict[str, Any] = {"name": name, "size": p.stat().st_size,
                            "ext": p.suffix.lower().lstrip(".")}
    if p.suffix.lower() == ".stl":
        try:
            from strands_cad._common import parse_stl, signed_volume_cm3
            verts, tris = parse_stl(p)
            xs = [v[0] for v in verts]; ys = [v[1] for v in verts]; zs = [v[2] for v in verts]
            info["triangles"] = len(tris)
            info["vertices"] = len(verts)
            info["bbox"] = {
                "min": [min(xs), min(ys), min(zs)],
                "max": [max(xs), max(ys), max(zs)],
                "size": [max(xs)-min(xs), max(ys)-min(ys), max(zs)-min(zs)],
            }
            vol_cm3 = signed_volume_cm3(verts, tris)
            info["volume_cm3"] = round(vol_cm3, 3)
            info["weight_pla_g"] = round(vol_cm3 * 1.24, 2)  # solid PLA density
        except Exception as e:
            info["meta_error"] = str(e)
    return info


def read_bytes(name: str) -> Optional[bytes]:
    """Return raw file bytes for viewer download (sandboxed)."""
    p = _safe(name)
    if not p:
        return None
    return p.read_bytes()


def content_type(name: str) -> str:
    ext = Path(name).suffix.lower()
    return {
        ".stl": "model/stl",
        ".3mf": "model/3mf",
        ".obj": "text/plain",
        ".gcode": "text/plain",
        ".step": "application/step",
        ".stp": "application/step",
    }.get(ext, "application/octet-stream")
