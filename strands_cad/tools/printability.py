"""Printability layer — analyze meshes BEFORE wasting filament."""
from __future__ import annotations
import math
from pathlib import Path
from typing import Any

from strands import tool
from strands_cad._common import ok, err, parse_stl


# Common printer build volumes (mm)
PRINTERS = {
    "X1C":      [256, 256, 256],
    "P1S":      [256, 256, 256],
    "P1P":      [256, 256, 256],
    "A1":       [256, 256, 256],
    "A1_MINI":  [180, 180, 180],
    "H2D":      [350, 320, 325],
    "MK4":      [250, 210, 220],
    "ENDER3":   [220, 220, 250],
}


def _face_normals(verts, tris):
    """Compute unit normals per triangle (pure python)."""
    normals = []
    for a_i, b_i, c_i in tris:
        a, b, c = verts[a_i], verts[b_i], verts[c_i]
        u = (b[0]-a[0], b[1]-a[1], b[2]-a[2])
        v = (c[0]-a[0], c[1]-a[1], c[2]-a[2])
        n = (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
        mag = math.sqrt(n[0]**2 + n[1]**2 + n[2]**2)
        if mag < 1e-12:
            normals.append((0.0, 0.0, 0.0))
        else:
            normals.append((n[0]/mag, n[1]/mag, n[2]/mag))
    return normals


def _tri_area(verts, tri):
    a, b, c = verts[tri[0]], verts[tri[1]], verts[tri[2]]
    u = (b[0]-a[0], b[1]-a[1], b[2]-a[2])
    v = (c[0]-a[0], c[1]-a[1], c[2]-a[2])
    n = (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
    return math.sqrt(n[0]**2 + n[1]**2 + n[2]**2) / 2.0


@tool
def stl_printability(
    stl_file: str,
    printer: str = "X1C",
    max_overhang_deg: float = 45.0,
    build_volume: list[float] | None = None,
) -> dict:
    """Analyze an STL for FDM printability: overhangs, bed fit, bed contact.

    Args:
        stl_file: Path to .stl file.
        printer: Printer preset — one of: X1C, P1S, P1P, A1, A1_MINI, H2D, MK4, ENDER3.
        max_overhang_deg: Max printable overhang angle from vertical (default 45°).
        build_volume: Override [x, y, z] build volume in mm (ignores printer preset).

    Returns:
        {status, content, fits_bed, overhang_area_pct, needs_support,
         bed_contact_area_mm2, size, warnings}
    """
    try:
        verts, tris = parse_stl(stl_file)
    except Exception as e:
        return err(str(e))
    if not tris:
        return err("empty mesh")

    vol = build_volume or PRINTERS.get(printer.upper())
    if not vol:
        return err(f"unknown printer '{printer}'. Options: {list(PRINTERS)}")

    xs = [v[0] for v in verts]; ys = [v[1] for v in verts]; zs = [v[2] for v in verts]
    size = [max(xs)-min(xs), max(ys)-min(ys), max(zs)-min(zs)]
    fits = all(size[i] <= vol[i] for i in range(3))
    # also check rotated flat fits (any axis permutation)
    import itertools
    fits_any_orientation = any(
        all(sorted(size)[i] <= sorted(vol)[i] for i in range(3))
        for _ in [0])

    normals = _face_normals(verts, tris)
    z_min = min(zs)
    threshold = -math.cos(math.radians(max_overhang_deg))  # nz below this ⇒ overhang
    total_area = 0.0
    overhang_area = 0.0
    bed_contact = 0.0
    for i, tri in enumerate(tris):
        area = _tri_area(verts, tri)
        total_area += area
        nz = normals[i][2]
        tri_zs = [verts[j][2] for j in tri]
        on_bed = all(abs(z - z_min) < 0.5 for z in tri_zs)
        if on_bed:
            bed_contact += area
            continue  # bottom face isn't an overhang
        if nz < threshold:
            overhang_area += area

    overhang_pct = (overhang_area / total_area * 100) if total_area > 0 else 0.0
    needs_support = overhang_pct > 1.0
    warnings = []
    if not fits:
        warnings.append(f"part {size[0]:.0f}×{size[1]:.0f}×{size[2]:.0f} exceeds {printer} bed {vol}")
    if bed_contact < 25.0:
        warnings.append(f"tiny bed contact ({bed_contact:.1f} mm²) — adhesion risk, add brim")
    if overhang_pct > 25:
        warnings.append(f"heavy overhangs ({overhang_pct:.0f}% of surface) — consider reorienting")

    return ok(
        f"fits={fits}, overhangs={overhang_pct:.1f}% of surface, "
        f"bed contact={bed_contact:.0f} mm², supports={'YES' if needs_support else 'no'}",
        fits_bed=fits, size=size, build_volume=vol, printer=printer.upper(),
        overhang_area_pct=round(overhang_pct, 2),
        overhang_area_mm2=round(overhang_area, 1),
        bed_contact_area_mm2=round(bed_contact, 1),
        needs_support=needs_support,
        warnings=warnings,
    )


@tool
def stl_orient(stl_file: str, output_stl: str, printer: str = "X1C") -> dict:
    """Auto-orient an STL for printing: test 24 axis-aligned rotations, pick the one
    minimizing support (overhang area), tie-broken by max bed contact.

    Args:
        stl_file: Input .stl path.
        output_stl: Output .stl path (best orientation, translated so z_min = 0).
        printer: Printer preset for bed-fit checking.

    Returns:
        {status, content, path, rotation_deg, overhang_area_before, overhang_area_after}
    """
    try:
        import trimesh  # type: ignore
        import numpy as np  # type: ignore
    except ImportError:
        return err("trimesh + numpy required. pip install 'strands-cad[mesh]'")
    src = Path(stl_file).resolve()
    out = Path(output_stl).resolve()
    if not src.exists():
        return err(f"file not found: {src}")
    m = trimesh.load(src, force="mesh")

    def overhang_metric(mesh) -> tuple[float, float]:
        """(overhang_area, -bed_contact) — lower is better."""
        n = mesh.face_normals
        z_min = mesh.bounds[0][2]
        face_z_min = mesh.triangles[:, :, 2].min(axis=1)
        on_bed = face_z_min < (z_min + 0.5)
        overhang = (n[:, 2] < -math.cos(math.radians(45))) & (~on_bed)
        return float(mesh.area_faces[overhang].sum()), -float(mesh.area_faces[on_bed].sum())

    base_overhang, _ = overhang_metric(m)

    best = None
    best_key = None
    best_rot = (0, 0, 0)
    for rx in (0, 90, 180, 270):
        for ry in (0, 90, 180, 270):
            for rz in (0,):  # z-rotation doesn't change overhangs
                cand = m.copy()
                if rx:
                    cand.apply_transform(trimesh.transformations.rotation_matrix(math.radians(rx), [1, 0, 0]))
                if ry:
                    cand.apply_transform(trimesh.transformations.rotation_matrix(math.radians(ry), [0, 1, 0]))
                key = overhang_metric(cand)
                if best_key is None or key < best_key:
                    best_key = key
                    best = cand
                    best_rot = (rx, ry, rz)

    # drop to bed: translate so z_min = 0
    best.apply_translation([0, 0, -best.bounds[0][2]])
    out.parent.mkdir(parents=True, exist_ok=True)
    best.export(out)
    return ok(
        f"best rotation {best_rot} — overhang {base_overhang:.0f} → {best_key[0]:.0f} mm², "
        f"bed contact {-best_key[1]:.0f} mm²",
        path=str(out), rotation_deg=list(best_rot),
        overhang_area_before=round(base_overhang, 1),
        overhang_area_after=round(best_key[0], 1),
        bed_contact_area_mm2=round(-best_key[1], 1),
    )


@tool
def stl_check_clearance(stl_a: str, stl_b: str, min_gap_mm: float = 0.2) -> dict:
    """Check clearance/interference between two meshes (assembly fit).

    FDM rule of thumb: 0.2 mm gap for tight fit, 0.4 mm loose fit.

    Args:
        stl_a: First mesh path.
        stl_b: Second mesh path (in the same coordinate frame as A).
        min_gap_mm: Required minimum gap (default 0.2 mm).

    Returns:
        {status, content, min_distance_mm, intersects, gap_ok}
    """
    try:
        import trimesh  # type: ignore
        import numpy as np  # type: ignore
    except ImportError:
        return err("trimesh + numpy required. pip install 'strands-cad[mesh]'")
    pa, pb = Path(stl_a).resolve(), Path(stl_b).resolve()
    if not pa.exists():
        return err(f"file not found: {pa}")
    if not pb.exists():
        return err(f"file not found: {pb}")
    ma = trimesh.load(pa, force="mesh")
    mb = trimesh.load(pb, force="mesh")

    # Collision / intersection check
    intersects = False
    try:
        cm = trimesh.collision.CollisionManager()
        cm.add_object("a", ma)
        intersects = cm.in_collision_single(mb)
    except BaseException:
        # fallback: bbox overlap heuristic
        amin, amax = ma.bounds; bmin, bmax = mb.bounds
        intersects = all(amax[i] > bmin[i] and bmax[i] > amin[i] for i in range(3))

    # Min distance: sample B's vertices against A's surface
    sample = mb.vertices
    if len(sample) > 2000:
        idx = np.random.default_rng(0).choice(len(sample), 2000, replace=False)
        sample = sample[idx]
    try:
        closest, dist, _ = trimesh.proximity.closest_point(ma, sample)
        min_dist = float(dist.min())
    except BaseException:
        min_dist = -1.0

    gap_ok = (not intersects) and (min_dist >= min_gap_mm)
    return ok(
        f"{'INTERSECTS' if intersects else f'min gap {min_dist:.3f} mm'} — "
        f"{'OK' if gap_ok else f'FAILS {min_gap_mm} mm requirement'}",
        min_distance_mm=round(min_dist, 4), intersects=intersects,
        gap_ok=gap_ok, required_gap_mm=min_gap_mm,
    )
