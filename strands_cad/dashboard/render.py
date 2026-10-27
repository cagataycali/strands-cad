#!/usr/bin/env python3
"""
🖼️ STL → PNG preview without OpenSCAD: numpy projection + PIL painter's algorithm.

Thor (the deploy host) has no openscad; CadQuery/SDF designs come out as STL. The
gate card and the tiny device reply need a picture of WHAT will be printed, so this
renders any STL: isometric-ish camera, flat Lambert shading in the Strands green,
the bed outline for scale. Good enough to recognise a part; not a ray tracer.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Optional, Tuple


def render_stl_png(stl_file: str, output_png: str, size: Tuple[int, int] = (960, 720),
                   bed_mm: Optional[Tuple[float, float]] = (325.0, 320.0), dark: bool = True) -> dict:
    import numpy as np
    from PIL import Image, ImageDraw

    from strands_cad._common import parse_stl

    verts, tris = parse_stl(stl_file)
    V = np.asarray(verts, dtype=float)
    T = np.asarray(tris, dtype=int)
    if len(T) == 0:
        return {"ok": False, "error": "empty mesh"}
    mn, mx = V.min(0), V.max(0)
    ext = mx - mn
    center = (mn + mx) / 2.0
    center[2] = mn[2]                      # sit on the bed
    Vc = V - center

    # camera: yaw 35°, pitch 30° looking down — the OpenSCAD default feel
    yaw, pitch = math.radians(-35.0), math.radians(28.0)
    cy, sy, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
    Ry = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
    Rp = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]])
    R = Rp @ Ry
    P = Vc @ R.T                           # x right, y depth, z up (after pitch: y mixes)
    # orthographic: screen x = P[:,0], screen y = -P[:,2]; depth = P[:,1]
    sx, sz, depth = P[:, 0], P[:, 2], P[:, 1]
    radius = max(float(np.max(np.abs(sx))), float(np.max(np.abs(sz))), 1.0)
    W, H = size
    scale = (min(W, H) * 0.36) / radius
    ox, oy = W / 2.0, H * 0.56

    def to_px(x, z):
        return (ox + x * scale, oy - z * scale)

    bg = (17, 18, 19) if dark else (244, 244, 244)
    fg_grid = (70, 80, 92) if dark else (182, 182, 182)
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)

    # bed outline (only if the part is a reasonable fraction of it)
    if bed_mm:
        bw, bh = bed_mm
        if max(ext[0], ext[1]) > 0.08 * max(bw, bh):
            corners = np.array([[-bw / 2, -bh / 2, 0], [bw / 2, -bh / 2, 0], [bw / 2, bh / 2, 0], [-bw / 2, bh / 2, 0]])
            Pc = corners @ R.T
            pts = [to_px(p[0], p[2]) for p in Pc]
            d.polygon(pts, outline=fg_grid)

    # flat shading per triangle, painter's algorithm back → front
    A, B, C = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    N = np.cross(B - A, C - A)
    ln = np.linalg.norm(N, axis=1, keepdims=True)
    ln[ln == 0] = 1
    N = N / ln
    light = np.array([-0.4, -0.6, 0.7]); light /= np.linalg.norm(light)
    lam = np.clip(N @ light, 0, 1)
    base = np.array([0, 204, 96]) if dark else np.array([0, 122, 61])
    shade = (0.25 + 0.75 * lam)[:, None] * base[None, :]
    tri_depth = depth[T].mean(axis=1)
    order = np.argsort(tri_depth)[::-1]     # far first (larger depth = farther? depth axis points away → sort desc)
    pts_x, pts_z = sx[T], sz[T]
    # limit draw cost on huge meshes
    step = max(1, len(order) // 60000)
    for i in order[::step]:
        col = tuple(int(c) for c in shade[i])
        poly = [to_px(pts_x[i, k], pts_z[i, k]) for k in range(3)]
        d.polygon(poly, fill=col)

    # caption
    name = Path(stl_file).name
    cap = f"{name}  ·  {ext[0]:.1f} × {ext[1]:.1f} × {ext[2]:.1f} mm"
    d.text((14, H - 26), cap, fill=(200, 200, 200) if dark else (60, 60, 60))

    out = Path(output_png)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG", optimize=True)
    return {"ok": True, "path": str(out), "size": [float(x) for x in ext], "triangles": int(len(T))}
