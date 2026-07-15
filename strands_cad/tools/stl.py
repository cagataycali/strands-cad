"""STL / mesh layer — atomic geometry tools."""
from __future__ import annotations
import struct
from pathlib import Path
from typing import Any

from strands import tool
from strands_cad._common import ok, err, parse_stl, signed_volume_cm3


# ---- density presets (g/cm³) ----
DENSITY = {
    "PLA": 1.24,
    "PLA_SILK": 1.24,
    "PETG": 1.27,
    "ABS": 1.04,
    "TPU": 1.20,
    "ASA": 1.07,
    "NYLON": 1.14,
    "PC": 1.20,
}


@tool
def stl_parse(stl_file: str) -> dict:
    """Parse an STL file and return vertex/triangle counts + bounding info.

    Args:
        stl_file: Path to .stl file (binary or ASCII).

    Returns:
        {status, content, vertex_count, triangle_count, bbox: {min,max,size}}
    """
    try:
        verts, tris = parse_stl(stl_file)
    except FileNotFoundError as e:
        return err(str(e))
    except Exception as e:
        return err(f"parse failed: {e}")
    if not verts:
        return err("no vertices found in STL")
    xs = [v[0] for v in verts]; ys = [v[1] for v in verts]; zs = [v[2] for v in verts]
    bbox = {
        "min": [min(xs), min(ys), min(zs)],
        "max": [max(xs), max(ys), max(zs)],
    }
    bbox["size"] = [bbox["max"][i] - bbox["min"][i] for i in range(3)]
    return ok(f"parsed {len(verts)} verts / {len(tris)} tris",
              vertex_count=len(verts), triangle_count=len(tris), bbox=bbox)


@tool
def stl_volume(stl_file: str) -> dict:
    """Compute solid volume of an STL in cm³ (via signed-tetrahedron sum).

    Args:
        stl_file: Path to .stl file.

    Returns:
        {status, content, volume_cm3, volume_mm3}
    """
    try:
        verts, tris = parse_stl(stl_file)
    except Exception as e:
        return err(str(e))
    vol_cm3 = signed_volume_cm3(verts, tris)
    return ok(f"volume = {vol_cm3:.3f} cm³", volume_cm3=vol_cm3, volume_mm3=vol_cm3 * 1000.0)


@tool
def stl_bbox(stl_file: str) -> dict:
    """Compute axis-aligned bounding box.

    Args:
        stl_file: Path to .stl file.

    Returns:
        {status, content, min:[x,y,z], max:[x,y,z], size:[dx,dy,dz], center:[x,y,z]}
    """
    try:
        verts, _ = parse_stl(stl_file)
    except Exception as e:
        return err(str(e))
    if not verts:
        return err("empty mesh")
    xs = [v[0] for v in verts]; ys = [v[1] for v in verts]; zs = [v[2] for v in verts]
    mn = [min(xs), min(ys), min(zs)]
    mx = [max(xs), max(ys), max(zs)]
    sz = [mx[i] - mn[i] for i in range(3)]
    center = [(mn[i] + mx[i]) / 2 for i in range(3)]
    return ok(f"bbox size {sz[0]:.1f} × {sz[1]:.1f} × {sz[2]:.1f} mm",
              min=mn, max=mx, size=sz, center=center)


@tool
def stl_weight(
    stl_file: str,
    material: str = "PLA",
    infill: float = 0.15,
    wall_fraction: float = 0.30,
) -> dict:
    """Estimate printed part weight.

    Args:
        stl_file: Path to .stl file.
        material: Material name — one of: PLA, PLA_SILK, PETG, ABS, TPU, ASA, NYLON, PC.
        infill: Sparse-infill fraction (0.0–1.0). Default 0.15 = 15%.
        wall_fraction: Solid-wall fraction (0.0–1.0). Default 0.30 = 30%.

    Returns:
        {status, content, weight_g, volume_cm3, density_g_cm3, material}
    """
    mat = material.upper()
    if mat not in DENSITY:
        return err(f"unknown material '{material}'. Options: {list(DENSITY)}")
    try:
        verts, tris = parse_stl(stl_file)
    except Exception as e:
        return err(str(e))
    vol_cm3 = signed_volume_cm3(verts, tris)
    density = DENSITY[mat]
    effective = wall_fraction + (1 - wall_fraction) * infill
    weight_g = vol_cm3 * density * effective
    return ok(f"{weight_g:.2f} g ({mat}, {int(infill*100)}% infill)",
              weight_g=weight_g, volume_cm3=vol_cm3,
              density_g_cm3=density, material=mat,
              infill=infill, wall_fraction=wall_fraction)


@tool
def stl_repair(stl_file: str, output_stl: str) -> dict:
    """Repair an STL (fill holes, fix normals, remove degenerate faces) using trimesh.

    Args:
        stl_file: Input .stl path.
        output_stl: Output .stl path.

    Returns:
        {status, content, path, watertight:bool, changes:{...}}
    """
    try:
        import trimesh  # type: ignore
    except ImportError:
        return err("trimesh not installed. Install with: pip install 'strands-cad[mesh]'")
    src = Path(stl_file).resolve()
    out = Path(output_stl).resolve()
    if not src.exists():
        return err(f"file not found: {src}")
    m = trimesh.load(src, force="mesh")
    before = {
        "watertight": bool(m.is_watertight),
        "faces": int(len(m.faces)),
        "vertices": int(len(m.vertices)),
    }
    m.process(validate=True)
    m.remove_degenerate_faces()
    m.remove_duplicate_faces()
    m.remove_unreferenced_vertices()
    trimesh.repair.fill_holes(m)
    trimesh.repair.fix_normals(m)
    out.parent.mkdir(parents=True, exist_ok=True)
    m.export(out)
    after = {
        "watertight": bool(m.is_watertight),
        "faces": int(len(m.faces)),
        "vertices": int(len(m.vertices)),
    }
    return ok(
        f"repaired → {out} (watertight: {before['watertight']} → {after['watertight']})",
        path=str(out), watertight=after["watertight"],
        before=before, after=after,
    )


@tool
def stl_transform(
    stl_file: str,
    output_stl: str,
    translate: tuple[float, float, float] = (0.0, 0.0, 0.0),
    rotate_deg: tuple[float, float, float] = (0.0, 0.0, 0.0),
    scale: tuple[float, float, float] | float = 1.0,
) -> dict:
    """Apply affine transform (translate/rotate/scale) and write new STL.

    Args:
        stl_file: Input .stl path.
        output_stl: Output .stl path.
        translate: (tx, ty, tz) in mm.
        rotate_deg: (rx, ry, rz) Euler angles in degrees (XYZ order).
        scale: Uniform scalar or (sx, sy, sz) tuple.

    Returns:
        {status, content, path}
    """
    try:
        import trimesh  # type: ignore
        import numpy as np  # type: ignore
    except ImportError:
        return err("trimesh + numpy required. Install with: pip install 'strands-cad[mesh]'")
    src = Path(stl_file).resolve()
    out = Path(output_stl).resolve()
    if not src.exists():
        return err(f"file not found: {src}")
    m = trimesh.load(src, force="mesh")
    if isinstance(scale, (int, float)):
        sx = sy = sz = float(scale)
    else:
        sx, sy, sz = scale
    S = np.diag([sx, sy, sz, 1.0])
    m.apply_transform(S)
    rx, ry, rz = [np.radians(a) for a in rotate_deg]
    for ax, angle in ((0, rx), (1, ry), (2, rz)):
        if angle != 0.0:
            m.apply_transform(trimesh.transformations.rotation_matrix(angle, [1 if ax == 0 else 0,
                                                                                1 if ax == 1 else 0,
                                                                                1 if ax == 2 else 0]))
    m.apply_translation(translate)
    out.parent.mkdir(parents=True, exist_ok=True)
    m.export(out)
    return ok(f"transformed → {out}", path=str(out))


@tool
def stl_convert(input_file: str, output_file: str) -> dict:
    """Convert between mesh formats (STL / OBJ / PLY / GLB / 3MF).

    Format is inferred from output_file extension.

    Args:
        input_file: Input mesh path.
        output_file: Output mesh path (extension picks format).
    """
    try:
        import trimesh  # type: ignore
    except ImportError:
        return err("trimesh required. Install with: pip install 'strands-cad[mesh]'")
    src = Path(input_file).resolve()
    out = Path(output_file).resolve()
    if not src.exists():
        return err(f"file not found: {src}")
    m = trimesh.load(src, force="mesh")
    out.parent.mkdir(parents=True, exist_ok=True)
    m.export(out)
    return ok(f"converted {src.suffix} → {out.suffix}: {out}", path=str(out))
