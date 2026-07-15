"""Shared utilities (STL parsing, response format)."""
from __future__ import annotations
import struct
from pathlib import Path
from typing import Any


def ok(text: str, **extra: Any) -> dict:
    """Standard success response."""
    r = {"status": "success", "content": [{"text": text}]}
    r.update(extra)
    return r


def err(text: str) -> dict:
    """Standard error response."""
    return {"status": "error", "content": [{"text": text}]}


def parse_stl(path: str | Path):
    """Parse binary or ASCII STL → (verts:list[tuple[float,float,float]], tris:list[tuple[int,int,int]]).

    Deduplicates verts (rounded to 4 decimals).
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    with open(p, "rb") as f:
        head = f.read(80)
        rest = f.read(200).decode("latin-1", "ignore")
        f.seek(0)
        is_ascii = head.startswith(b"solid") and "facet" in rest
        if is_ascii:
            text = f.read().decode("ascii", errors="ignore")
            verts, tris, v_map, current = [], [], {}, []
            for line in text.splitlines():
                s = line.strip()
                if s.startswith("vertex"):
                    x, y, z = map(float, s.split()[1:4])
                    key = (round(x, 4), round(y, 4), round(z, 4))
                    if key not in v_map:
                        v_map[key] = len(verts)
                        verts.append(key)
                    current.append(v_map[key])
                elif s.startswith("endfacet"):
                    if len(current) == 3:
                        tris.append(tuple(current))
                    current = []
            return verts, tris
        # binary
        f.seek(80)
        n = struct.unpack("<I", f.read(4))[0]
        verts, tris, v_map = [], [], {}
        for _ in range(n):
            f.read(12)  # normal
            idx = []
            for _ in range(3):
                x, y, z = struct.unpack("<fff", f.read(12))
                key = (round(x, 4), round(y, 4), round(z, 4))
                if key not in v_map:
                    v_map[key] = len(verts)
                    verts.append(key)
                idx.append(v_map[key])
            f.read(2)  # attribute byte count
            tris.append(tuple(idx))
        return verts, tris


def signed_volume_cm3(verts, tris) -> float:
    """Signed-tetrahedron volume in cm³ (STL is in mm)."""
    vol = 0.0
    for a_i, b_i, c_i in tris:
        a, b, c = verts[a_i], verts[b_i], verts[c_i]
        vol += (a[0] * (b[1] * c[2] - b[2] * c[1])
                - a[1] * (b[0] * c[2] - b[2] * c[0])
                + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6.0
    return abs(vol) / 1000.0
