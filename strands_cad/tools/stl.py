"""STL / mesh layer — atomic geometry tools."""
from __future__ import annotations
import math
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
        return err(f"file not found: {e}")
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
    # trimesh 4.x compatibility: prefer update_faces / update_vertices helpers,
    # but fall back to older method names if present.
    try:
        m.update_faces(m.nondegenerate_faces())
    except AttributeError:
        if hasattr(m, 'remove_degenerate_faces'):
            m.remove_degenerate_faces()
    try:
        m.update_faces(m.unique_faces())
    except AttributeError:
        if hasattr(m, 'remove_duplicate_faces'):
            m.remove_duplicate_faces()
    try:
        m.remove_unreferenced_vertices()
    except AttributeError:
        pass
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
    try:
        m.export(out)
    except BaseException as e:  # trimesh raises ImportError for missing format deps
        return err(f"export to {out.suffix} failed: {e}")
    return ok(f"converted {src.suffix} → {out.suffix}: {out}", path=str(out))


# ============================================================
# v0.2 additions — decimation, hollow, boolean, normalize, combine
# ============================================================


@tool
def mesh_decimate(
    stl_file: str,
    output_stl: str,
    target_faces: int = 100_000,
    preserve_boundary: bool = True,
) -> dict:
    """Reduce triangle count while preserving overall shape (quadric decimation).

    Great for making SDF-generated meshes slicer-friendly (SDF often yields
    millions of triangles; slicers work best with 50k-500k).

    Args:
        stl_file: Input .stl.
        output_stl: Output .stl.
        target_faces: Target face count (default 100k).
        preserve_boundary: Keep boundary edges intact (safer for open meshes).

    Returns:
        {status, content, path, faces_before, faces_after, ratio}
    """
    try:
        import trimesh  # type: ignore
    except ImportError:
        return err("trimesh required")
    src = Path(stl_file).resolve()
    out = Path(output_stl).resolve()
    if not src.exists():
        return err(f"file not found: {src}")
    m = trimesh.load(src, force="mesh")
    before = int(len(m.faces))
    if before <= target_faces:
        m.export(out)
        return ok(f"already ≤ target ({before} ≤ {target_faces}) — copied unchanged",
                  path=str(out), faces_before=before, faces_after=before, ratio=1.0)
    try:
        m2 = m.simplify_quadric_decimation(face_count=target_faces)
    except TypeError:
        # Older trimesh API
        m2 = m.simplify_quadric_decimation(target_faces)
    out.parent.mkdir(parents=True, exist_ok=True)
    m2.export(out)
    after = int(len(m2.faces))
    return ok(f"decimated {before:,} → {after:,} faces ({after/before*100:.1f}%)",
              path=str(out), faces_before=before, faces_after=after,
              ratio=after / before)


@tool
def mesh_normalize(
    stl_file: str,
    output_stl: str,
    center: bool = True,
    lay_flat: bool = True,
    z_zero: bool = True,
) -> dict:
    """Auto-orient a mesh for the print bed: center XY, drop to Z=0, optionally lay flat.

    Args:
        stl_file: Input .stl.
        output_stl: Output .stl.
        center: Center on XY origin.
        lay_flat: Orient largest face down (approximation via bbox — use with care).
        z_zero: Translate so minimum Z = 0 (sits on bed).
    """
    try:
        import trimesh  # type: ignore
        import numpy as np  # type: ignore
    except ImportError:
        return err("trimesh required")
    src = Path(stl_file).resolve()
    out = Path(output_stl).resolve()
    if not src.exists():
        return err(f"file not found: {src}")
    m = trimesh.load(src, force="mesh")

    if lay_flat:
        # Very simple: put the largest bbox dimension along the print bed by
        # rotating so that Z axis is the shortest bbox extent.
        ext = m.extents
        axes = np.argsort(ext)  # ascending
        z_axis = axes[0]
        if z_axis != 2:
            # rotate so z_axis becomes Z
            if z_axis == 0:
                m.apply_transform(trimesh.transformations.rotation_matrix(math.pi/2, [0,1,0]))
            elif z_axis == 1:
                m.apply_transform(trimesh.transformations.rotation_matrix(-math.pi/2, [1,0,0]))

    if center:
        mn, mx = m.bounds
        xc = (mn[0] + mx[0]) / 2
        yc = (mn[1] + mx[1]) / 2
        m.apply_translation([-xc, -yc, 0])

    if z_zero:
        mn = m.bounds[0]
        m.apply_translation([0, 0, -mn[2]])

    out.parent.mkdir(parents=True, exist_ok=True)
    m.export(out)
    mn, mx = m.bounds
    return ok(f"normalized → bounds {mn.tolist()} to {mx.tolist()}",
              path=str(out), bounds=[mn.tolist(), mx.tolist()])


@tool
def mesh_boolean(
    stl_a: str,
    stl_b: str,
    output_stl: str,
    op: str = "union",
) -> dict:
    """Boolean op (union/difference/intersection) between two meshes.

    Uses trimesh's boolean engine (manifold3d if available, else blender/scad).
    Both inputs should be watertight.

    Args:
        stl_a: First mesh.
        stl_b: Second mesh.
        output_stl: Output .stl.
        op: 'union', 'difference' (a - b), or 'intersection'.
    """
    try:
        import trimesh  # type: ignore
    except ImportError:
        return err("trimesh required")
    for p in (stl_a, stl_b):
        if not Path(p).exists():
            return err(f"file not found: {p}")
    a = trimesh.load(stl_a, force="mesh")
    b = trimesh.load(stl_b, force="mesh")
    if op not in ("union", "difference", "intersection"):
        return err(f"unknown op '{op}'. Use union|difference|intersection.")
    try:
        r = getattr(a, op)(b)
    except BaseException as e:  # trimesh raises ImportError when no boolean backend
        return err(f"{op} failed: {e}. Try: pip install manifold3d")
    out = Path(output_stl).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    r.export(out)
    return ok(f"{op} → {out.name} ({len(r.faces):,} faces)",
              path=str(out), faces=int(len(r.faces)))


@tool
def mesh_combine(
    stl_files: list[str],
    output_stl: str,
) -> dict:
    """Combine multiple STL files into ONE STL as separate concatenated meshes.

    Unlike mesh_boolean, this does NOT fuse geometry — objects stay disjoint.
    Useful for print plates where you want one file with many parts.

    Args:
        stl_files: List of .stl paths.
        output_stl: Output .stl.
    """
    try:
        import trimesh  # type: ignore
    except ImportError:
        return err("trimesh required")
    meshes = []
    for p in stl_files:
        if not Path(p).exists():
            return err(f"file not found: {p}")
        meshes.append(trimesh.load(p, force="mesh"))
    combined = trimesh.util.concatenate(meshes)
    out = Path(output_stl).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    combined.export(out)
    return ok(f"combined {len(meshes)} meshes → {out.name} ({len(combined.faces):,} faces)",
              path=str(out), source_count=len(meshes), faces=int(len(combined.faces)))


@tool
def mesh_hollow(
    stl_file: str,
    output_stl: str,
    wall_thickness: float = 2.0,
    drain_hole_diameter: float = 0.0,
) -> dict:
    """Hollow out a solid mesh (offset inward by wall_thickness, subtract).

    Useful for making solid SCAD/SDF prints lighter. Optionally drills a drain
    hole on the bottom (needed for resin, or to reduce print time on FDM).

    Args:
        stl_file: Input .stl (should be watertight).
        output_stl: Output .stl.
        wall_thickness: Desired wall thickness in mm.
        drain_hole_diameter: If > 0, drill a hole this wide through the bottom.
    """
    try:
        import trimesh  # type: ignore
    except ImportError:
        return err("trimesh required")
    src = Path(stl_file).resolve()
    out = Path(output_stl).resolve()
    if not src.exists():
        return err(f"file not found: {src}")
    m = trimesh.load(src, force="mesh")
    if not m.is_watertight:
        try:
            trimesh.repair.fill_holes(m)
        except Exception:
            pass

    # Inner shell via voxel erosion: voxelize the solid, erode by
    # wall_thickness, and mesh the eroded core. Unlike naive scaling this is
    # a true inward offset — uniform walls regardless of shape or origin.
    try:
        from scipy import ndimage  # type: ignore
        pitch = min(wall_thickness / 2, m.extents.min() / 100)
        vox = m.voxelized(pitch=pitch).fill()
        erode_cells = max(1, int(round(wall_thickness / pitch)))
        inner_matrix = ndimage.binary_erosion(vox.matrix, iterations=erode_cells)
        if not inner_matrix.any():
            return err(f"wall_thickness {wall_thickness}mm consumes the whole part — nothing to hollow")
        # marching_cubes returns index-space coords; map back to world space
        inner = trimesh.voxel.VoxelGrid(inner_matrix).marching_cubes
        inner.apply_transform(vox.transform)
        hollow = m.difference(inner)
    except Exception as e:
        return err(f"hollow failed: {e}")

    if drain_hole_diameter > 0:
        mn = m.bounds[0]; mx = m.bounds[1]
        cx = (mn[0] + mx[0]) / 2
        cy = (mn[1] + mx[1]) / 2
        # Pierce only the bottom wall into the cavity (drain), not the top.
        drill = trimesh.creation.cylinder(radius=drain_hole_diameter/2,
                                          height=wall_thickness * 4)
        drill.apply_translation([cx, cy, mn[2] + wall_thickness])
        try:
            hollow = hollow.difference(drill)
        except Exception:
            pass

    out.parent.mkdir(parents=True, exist_ok=True)
    hollow.export(out)
    return ok(f"hollowed with {wall_thickness}mm walls → {out.name}",
              path=str(out), wall_thickness=wall_thickness,
              drain_hole_diameter=drain_hole_diameter)
