"""Mesh generation layer — create geometry from images, SVG, text, and CSG booleans."""
from __future__ import annotations
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from strands import tool
from strands_cad._common import ok, err


def _need_trimesh():
    try:
        import trimesh  # type: ignore
        return trimesh, None
    except ImportError:
        return None, err("trimesh required. pip install 'strands-cad[mesh]'")


@tool
def mesh_boolean(
    mesh_a: str,
    mesh_b: str,
    operation: str,
    output_file: str,
) -> dict:
    """Boolean CSG between two meshes: union / difference / intersection.

    Uses trimesh with the manifold3d engine (pip install manifold3d).
    difference = A minus B.

    Args:
        mesh_a: First mesh path (STL/OBJ/PLY/GLB).
        mesh_b: Second mesh path.
        operation: 'union', 'difference', or 'intersection'.
        output_file: Output mesh path (extension picks format).

    Returns:
        {status, content, path, watertight, volume_mm3}
    """
    trimesh, e = _need_trimesh()
    if e:
        return e
    pa, pb = Path(mesh_a).resolve(), Path(mesh_b).resolve()
    out = Path(output_file).resolve()
    if not pa.exists():
        return err(f"file not found: {pa}")
    if not pb.exists():
        return err(f"file not found: {pb}")
    if operation not in ("union", "difference", "intersection"):
        return err(f"unknown operation '{operation}'. Options: union, difference, intersection")
    ma = trimesh.load(pa, force="mesh")
    mb = trimesh.load(pb, force="mesh")
    try:
        fn = getattr(trimesh.boolean, operation)
        result = fn([ma, mb])
    except BaseException as ex:
        return err(f"boolean failed: {ex}. Try: pip install manifold3d")
    if result is None or len(result.faces) == 0:
        return err("boolean produced empty mesh (objects may not overlap for intersection)")
    out.parent.mkdir(parents=True, exist_ok=True)
    result.export(out)
    return ok(
        f"{operation}({pa.name}, {pb.name}) → {out} "
        f"({len(result.faces)} faces, watertight={result.is_watertight})",
        path=str(out), watertight=bool(result.is_watertight),
        volume_mm3=float(result.volume) if result.is_watertight else None,
        faces=int(len(result.faces)),
    )


@tool
def mesh_from_image(
    image_file: str,
    output_file: str,
    max_height_mm: float = 3.0,
    base_mm: float = 0.6,
    width_mm: float = 100.0,
    invert: bool = False,
) -> dict:
    """Convert a grayscale image into a heightmap mesh (lithophane / relief).

    Bright pixels = high (or low if invert=True, which is standard for lithophanes
    where dark areas should be thick).

    Args:
        image_file: Input image path (PNG/JPG — converted to grayscale).
        output_file: Output mesh path (.stl recommended).
        max_height_mm: Relief height above base.
        base_mm: Solid base thickness.
        width_mm: Physical width of output; height scales by aspect ratio.
        invert: If True, dark pixels are raised (lithophane mode).

    Returns:
        {status, content, path, size_mm, pixels}
    """
    trimesh, e = _need_trimesh()
    if e:
        return e
    try:
        import numpy as np  # type: ignore
        from PIL import Image  # type: ignore
    except ImportError:
        return err("numpy + Pillow required. pip install numpy pillow")
    src = Path(image_file).resolve()
    out = Path(output_file).resolve()
    if not src.exists():
        return err(f"image not found: {src}")

    img = Image.open(src).convert("L")
    # Downsample huge images — cap at 300px wide for sane mesh sizes
    if img.width > 300:
        ratio = 300 / img.width
        img = img.resize((300, max(2, int(img.height * ratio))))
    h_px, w_px = img.height, img.width
    z = np.asarray(img, dtype=np.float64) / 255.0
    if invert:
        z = 1.0 - z
    z = base_mm + z * max_height_mm

    scale = width_mm / w_px
    depth_mm = h_px * scale

    # Build heightmap grid mesh (top surface + walls + bottom)
    xs = np.arange(w_px) * scale
    ys = np.arange(h_px) * scale
    verts = []
    faces = []
    # top surface vertices
    for j in range(h_px):
        for i in range(w_px):
            verts.append((xs[i], ys[j], z[j, i]))
    def vid(i, j):
        return j * w_px + i
    for j in range(h_px - 1):
        for i in range(w_px - 1):
            a, b, c, d = vid(i, j), vid(i+1, j), vid(i+1, j+1), vid(i, j+1)
            faces.append((a, b, c)); faces.append((a, c, d))
    # bottom rectangle (4 verts)
    nb = len(verts)
    verts += [(0, 0, 0), (xs[-1], 0, 0), (xs[-1], ys[-1], 0), (0, ys[-1], 0)]
    faces.append((nb, nb+2, nb+1)); faces.append((nb, nb+3, nb+2))
    # walls: stitch edges to bottom perimeter
    for i in range(w_px - 1):  # front (y=0) & back (y=max)
        faces.append((vid(i, 0), nb, vid(i+1, 0))); faces.append((vid(i+1, 0), nb, nb+1))
        faces.append((vid(i, h_px-1), vid(i+1, h_px-1), nb+3)); faces.append((vid(i+1, h_px-1), nb+2, nb+3))
    for j in range(h_px - 1):  # left (x=0) & right (x=max)
        faces.append((vid(0, j), vid(0, j+1), nb)); faces.append((vid(0, j+1), nb+3, nb))
        faces.append((vid(w_px-1, j), nb+1, vid(w_px-1, j+1))); faces.append((vid(w_px-1, j+1), nb+1, nb+2))

    m = trimesh.Trimesh(vertices=np.array(verts), faces=np.array(faces))
    trimesh.repair.fix_normals(m)
    out.parent.mkdir(parents=True, exist_ok=True)
    m.export(out)
    return ok(
        f"heightmap → {out} ({w_px}×{h_px} px → {width_mm:.0f}×{depth_mm:.0f}×{base_mm+max_height_mm:.1f} mm)",
        path=str(out), size_mm=[width_mm, depth_mm, base_mm + max_height_mm],
        pixels=[w_px, h_px], lithophane=invert,
    )


@tool
def mesh_from_svg(
    svg_file: str,
    output_file: str,
    height_mm: float = 3.0,
    scale: float = 1.0,
) -> dict:
    """Extrude an SVG's paths into a 3D mesh (logos, profiles, badges).

    Args:
        svg_file: Input .svg path.
        output_file: Output mesh path (.stl recommended).
        height_mm: Extrusion height.
        scale: Uniform XY scale factor applied to the SVG geometry.

    Returns:
        {status, content, path, size}
    """
    trimesh, e = _need_trimesh()
    if e:
        return e
    src = Path(svg_file).resolve()
    out = Path(output_file).resolve()
    if not src.exists():
        return err(f"svg not found: {src}")
    try:
        path2d = trimesh.load(str(src))
        if scale != 1.0:
            path2d.apply_scale(scale)
        mesh = path2d.extrude(height_mm)
        if isinstance(mesh, list):
            mesh = trimesh.util.concatenate(mesh)
    except BaseException as ex:
        return err(f"svg extrude failed: {ex}. May need: pip install shapely networkx")
    out.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(out)
    sz = (mesh.bounds[1] - mesh.bounds[0]).tolist()
    return ok(f"extruded {src.name} → {out} ({sz[0]:.1f}×{sz[1]:.1f}×{sz[2]:.1f} mm)",
              path=str(out), size=sz)


@tool
def mesh_from_text(
    text: str,
    output_file: str,
    font_size_mm: float = 20.0,
    height_mm: float = 3.0,
    font: str = "Liberation Sans",
) -> dict:
    """Create extruded 3D text (nameplates, labels, embossing stock).

    Uses OpenSCAD's text() primitive under the hood (most reliable path).

    Args:
        text: The text string to extrude.
        output_file: Output .stl path.
        font_size_mm: Font size (cap height ≈ this value in mm).
        height_mm: Extrusion depth.
        font: Font name (any installed system font).

    Returns:
        {status, content, path}
    """
    out = Path(output_file).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    esc = text.replace('\\', '\\\\').replace('"', '\\"')
    scad_src = (
        f'linear_extrude(height={height_mm}) '
        f'text("{esc}", size={font_size_mm}, font="{font}", halign="center", valign="center");\n'
    )
    tmp = Path(tempfile.mkstemp(suffix=".scad")[1])
    tmp.write_text(scad_src)
    try:
        r = subprocess.run(["openscad", "-o", str(out), str(tmp)],
                           capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        return err("openscad binary not found in PATH")
    finally:
        tmp.unlink(missing_ok=True)
    if not out.exists() or out.stat().st_size == 0:
        return err(f"text render failed: {r.stderr[:400]}")
    return ok(f'3D text "{text}" → {out}', path=str(out), text_value=text)


@tool
def mesh_decimate(
    input_file: str,
    output_file: str,
    target_faces: int = 20000,
) -> dict:
    """Simplify a heavy mesh to a target face count (for gen-AI meshes, scans).

    Args:
        input_file: Input mesh path.
        output_file: Output mesh path.
        target_faces: Target triangle count (default 20k).

    Returns:
        {status, content, path, faces_before, faces_after}
    """
    trimesh, e = _need_trimesh()
    if e:
        return e
    src = Path(input_file).resolve()
    out = Path(output_file).resolve()
    if not src.exists():
        return err(f"file not found: {src}")
    m = trimesh.load(src, force="mesh")
    before = len(m.faces)
    if before <= target_faces:
        out.parent.mkdir(parents=True, exist_ok=True)
        m.export(out)
        return ok(f"already ≤ {target_faces} faces ({before}), copied → {out}",
                  path=str(out), faces_before=before, faces_after=before)
    try:
        simplified = m.simplify_quadric_decimation(face_count=target_faces)
    except BaseException:
        try:
            simplified = m.simplify_quadric_decimation(target_faces)
        except BaseException as ex:
            return err(f"decimation failed: {ex}. Try: pip install fast-simplification")
    out.parent.mkdir(parents=True, exist_ok=True)
    simplified.export(out)
    return ok(f"decimated {before} → {len(simplified.faces)} faces → {out}",
              path=str(out), faces_before=before, faces_after=int(len(simplified.faces)))
