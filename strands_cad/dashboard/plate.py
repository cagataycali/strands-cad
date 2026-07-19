#!/usr/bin/env python3
"""
🍽️ strands-cad dashboard — build-plate model (positions, transforms, colors).

The plate is a live, editable arrangement of parts on the printer bed. Both the
human (drag/recolor in the three.js viewer) and the agent (voice/chat tools)
mutate the SAME plate state here, then export a single colored 3MF that the
slicer consumes and the printer prints.

Each item:
  { id, source (stl/3mf filename in workdir), name,
    position:[x,y,z], rotation:[rx,ry,rz] (deg), scale:float,
    color:"#rrggbb" }

The 3MF we emit embeds per-object color via the 3MF *materials* extension so
Bambu/Orca/Prusa show and (for multi-AMS) print the chosen colors.

State persists to .strands_cad_plate.json (in workdir) so a reload keeps the
arrangement.
"""
from __future__ import annotations

import json
import struct
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

_lock = threading.Lock()
_PLATE: Dict[str, Any] = {"items": [], "bed": [256, 256, 256], "_updated": 0}

# Bambu P1 bed is 256×256×256; A1 mini 180. Overridable via config printer_model.
_BEDS = {
    "Bambu Lab P1S": [256, 256, 256], "Bambu Lab P1P": [256, 256, 256],
    "Bambu Lab X1 Carbon": [256, 256, 256], "Bambu Lab A1": [256, 256, 256],
    "Bambu Lab A1 mini": [180, 180, 180],
    "Bambu Lab H2D": [350, 320, 325],   # dual-nozzle, large-format
    "Bambu Lab H2D AMS": [350, 320, 325],
    "Bambu Lab X2D": [256, 256, 260],   # laser+3D, 256×256×260 build volume
    "Bambu Lab X2D AMS": [256, 256, 260],
}

DEFAULT_COLORS = ["#76b900", "#00d9ff", "#ff6b4a", "#ffce54", "#b066ff", "#39d98a"]


def _plate_path() -> Path:
    from strands_cad.dashboard import config_store
    wd = Path(config_store.get("workdir") or ".").expanduser().resolve()
    return wd / ".strands_cad_plate.json"


def _bed_for_model() -> List[int]:
    from strands_cad.dashboard import config_store
    return _BEDS.get(config_store.get("printer_model", ""), [256, 256, 256])


def _load_disk():
    p = _plate_path()
    if p.exists():
        try:
            data = json.loads(p.read_text())
            _PLATE["items"] = data.get("items", [])
            _PLATE["bed"] = data.get("bed", _bed_for_model())
            return
        except Exception:
            pass
    _PLATE["bed"] = _bed_for_model()


def _save_disk():
    try:
        _plate_path().write_text(json.dumps(_PLATE, indent=2))
    except Exception:
        pass


def palette() -> List[Dict[str, Any]]:
    """The loaded filament palette (color chips the UI/agent can snap to)."""
    from strands_cad.dashboard import config_store
    return config_store.get("filaments", []) or []


def state() -> Dict[str, Any]:
    with _lock:
        if not _PLATE.get("_loaded"):
            _load_disk(); _PLATE["_loaded"] = True
        out = json.loads(json.dumps(_PLATE))  # deep copy
    out["bed"] = _bed_for_model()   # always reflect current printer_model
    out["palette"] = palette()
    from strands_cad.dashboard import config_store
    out["model"] = config_store.get("printer_model", "Bambu Lab X2D")
    out["has_laser"] = bool(config_store.get("has_laser", False))
    out["nozzle_count"] = int(config_store.get("nozzle_count", 1) or 1)
    return out


def add_item(source: str, name: str = "", position=None, color: str = "") -> Dict[str, Any]:
    from strands_cad.dashboard import models
    if models._safe(source) is None:
        return {"error": f"source not in workdir: {source}"}
    with _lock:
        if not _PLATE.get("_loaded"):
            _load_disk(); _PLATE["_loaded"] = True
        n = len(_PLATE["items"])
        item = {
            "id": uuid.uuid4().hex[:8],
            "source": source,
            "name": name or Path(source).stem,
            "position": position or [0, 0, 0],
            "rotation": [0, 0, 0],
            "scale": 1.0,
            "color": color or DEFAULT_COLORS[n % len(DEFAULT_COLORS)],
        }
        _PLATE["items"].append(item)
        _PLATE["_updated"] = time.time()
        _save_disk()
        return dict(item)


def update_item(item_id: str, **patch) -> Dict[str, Any]:
    with _lock:
        it = next((i for i in _PLATE["items"] if i["id"] == item_id), None)
        if not it:
            return {"error": "item not found"}
        for k in ("position", "rotation", "scale", "color", "name"):
            if k in patch and patch[k] is not None:
                it[k] = patch[k]
        _PLATE["_updated"] = time.time()
        _save_disk()
        return dict(it)


def recolor(item_id: str, color: str) -> Dict[str, Any]:
    """Recolor one item, or ALL items if item_id in ('all','*')."""
    with _lock:
        if item_id in ("all", "*", ""):
            for it in _PLATE["items"]:
                it["color"] = color
            changed = len(_PLATE["items"])
        else:
            it = next((i for i in _PLATE["items"] if i["id"] == item_id
                       or i["name"] == item_id or i["source"] == item_id), None)
            if not it:
                return {"error": "item not found"}
            it["color"] = color
            changed = 1
        _PLATE["_updated"] = time.time()
        _save_disk()
        return {"ok": True, "changed": changed, "color": color}


def remove_item(item_id: str) -> Dict[str, Any]:
    with _lock:
        before = len(_PLATE["items"])
        _PLATE["items"] = [i for i in _PLATE["items"] if i["id"] != item_id]
        _PLATE["_updated"] = time.time()
        _save_disk()
        return {"ok": True, "removed": before - len(_PLATE["items"])}


def clear() -> Dict[str, Any]:
    with _lock:
        _PLATE["items"] = []
        _PLATE["_updated"] = time.time()
        _save_disk()
        return {"ok": True}


def auto_arrange(gap: float = 10.0) -> Dict[str, Any]:
    """Grid-arrange items on the bed so nothing overlaps (uses STL bbox)."""
    from strands_cad.dashboard import models
    with _lock:
        items = _PLATE["items"]
        bed = _PLATE.get("bed", [256, 256, 256])
        # compute footprints
        foot = []
        for it in items:
            m = models.meta(it["source"])
            sz = (m.get("bbox") or {}).get("size", [30, 30, 30])
            s = it.get("scale", 1.0)
            foot.append((sz[0]*s, sz[1]*s))
        # simple row packing centered on bed
        x = -bed[0]/2 + gap
        y = -bed[1]/2 + gap
        row_h = 0
        for it, (w, d) in zip(items, foot):
            if x + w > bed[0]/2 - gap:
                x = -bed[0]/2 + gap
                y += row_h + gap
                row_h = 0
            it["position"] = [round(x + w/2, 2), round(y + d/2, 2), 0]
            x += w + gap
            row_h = max(row_h, d)
        _PLATE["_updated"] = time.time()
        _save_disk()
        return {"ok": True, "arranged": len(items)}


# ── 3MF export with per-object color ────────────────────────────────────────
_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
_NSM = "http://schemas.microsoft.com/3dmanufacturing/material/2015/02"


def _read_stl(path: Path):
    from strands_cad._common import parse_stl
    return parse_stl(path)


def _hex_to_3mf(color: str) -> str:
    c = color.lstrip("#")
    if len(c) == 6:
        c += "FF"
    return "#" + c.upper()


def _mat3(v):
    """3x4 transform matrix string for 3MF (translation + uniform scale + rot Z only for simplicity)."""
    import math
    px, py, pz = v.get("position", [0, 0, 0])
    s = v.get("scale", 1.0)
    rz = math.radians((v.get("rotation") or [0, 0, 0])[2])
    cz, sz = math.cos(rz), math.sin(rz)
    # column-major 3MF matrix "m00 m01 m02 m10 m11 m12 m20 m21 m22 m30 m31 m32"
    m = [ s*cz, s*sz, 0,
         -s*sz, s*cz, 0,
             0,    0, s,
            px,   py, pz]
    return " ".join(f"{x:.6f}" for x in m)


def export_3mf(output_path: str = "") -> Dict[str, Any]:
    """Export the current plate to a colored 3MF ready to slice/print.

    Embeds per-object base materials (color) via the 3MF material extension.
    """
    from strands_cad.dashboard import models
    st = state()
    items = st["items"]
    if not items:
        return {"error": "plate is empty"}
    from strands_cad.dashboard import config_store
    wd = Path(config_store.get("workdir") or ".").expanduser().resolve()
    out = Path(output_path).resolve() if output_path else (wd / "plate_colored.3mf")

    # build XML
    import xml.etree.ElementTree as ET
    ET.register_namespace("", _NS)
    ET.register_namespace("m", _NSM)
    model = ET.Element(f"{{{_NS}}}model", unit="millimeter")
    md = ET.SubElement(model, f"{{{_NS}}}metadata", name="Title")
    md.text = "strands-cad colored plate"
    resources = ET.SubElement(model, f"{{{_NS}}}resources")

    # color group (material extension)
    colorgroup = ET.SubElement(resources, f"{{{_NSM}}}colorgroup", id="1")
    color_index = {}
    for it in items:
        col = _hex_to_3mf(it.get("color", "#cccccc"))
        if col not in color_index:
            idx = len(color_index)
            ET.SubElement(colorgroup, f"{{{_NSM}}}color", color=col)
            color_index[col] = idx

    build = ET.SubElement(model, f"{{{_NS}}}build")
    obj_id = 2  # 1 is colorgroup
    packed = 0
    for it in items:
        src = models._safe(it["source"])
        if not src or src.suffix.lower() != ".stl":
            continue  # only STL meshes embed directly; 3mf sources skipped here
        verts, tris = _read_stl(src)
        col = _hex_to_3mf(it.get("color", "#cccccc"))
        pid, pindex = "1", color_index[col]
        obj = ET.SubElement(resources, f"{{{_NS}}}object", id=str(obj_id),
                            type="model", pid=pid, pindex=str(pindex))
        mesh = ET.SubElement(obj, f"{{{_NS}}}mesh")
        vs = ET.SubElement(mesh, f"{{{_NS}}}vertices")
        for (x, y, z) in verts:
            ET.SubElement(vs, f"{{{_NS}}}vertex", x=f"{x:.4f}", y=f"{y:.4f}", z=f"{z:.4f}")
        ts = ET.SubElement(mesh, f"{{{_NS}}}triangles")
        for (a, b, c) in tris:
            ET.SubElement(ts, f"{{{_NS}}}triangle", v1=str(a), v2=str(b), v3=str(c),
                          pid=pid, p1=str(pindex))
        ET.SubElement(build, f"{{{_NS}}}item", objectid=str(obj_id), transform=_mat3(it))
        obj_id += 1
        packed += 1

    if packed == 0:
        return {"error": "no STL items to export (3mf-source items not yet supported)"}

    xml_bytes = ET.tostring(model, encoding="utf-8", xml_declaration=True)
    content_types = ('<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
        '</Types>').encode()
    rels = ('<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rel-1" Target="/3D/3dmodel.model" '
        'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
        '</Relationships>').encode()

    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", xml_bytes)

    return {"ok": True, "path": str(out), "rel": out.name, "objects": packed,
            "colors": list(color_index.keys())}
