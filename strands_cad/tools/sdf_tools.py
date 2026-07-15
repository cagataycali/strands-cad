"""SDF layer — signed distance field composition & meshing.

Uses fogleman/sdf: pure Python SDF DSL + parallel marching cubes.
This is the "math → mesh" pipeline: build shapes analytically, then discretize.

Key insight: SDF gives you infinite resolution, smooth blending (fillets for free),
and warping (twist/bend) that would be impossible or slow in mesh-based CAD.
"""
from __future__ import annotations
import math
import re
import textwrap
import time
from pathlib import Path
from typing import Any

from strands import tool
from strands_cad._common import ok, err


# Whitelist of names available inside sdf expressions passed to sdf_render_stl.
# Everything the fogleman/sdf library exports, plus math + numpy for user math.
_SAFE_GLOBALS: dict[str, Any] = {}


def _sdf_globals() -> dict[str, Any]:
    """Lazy-build the sandbox globals for eval(). Fails clean if sdf missing."""
    global _SAFE_GLOBALS
    if _SAFE_GLOBALS:
        return _SAFE_GLOBALS
    try:
        import sdf as _sdf  # type: ignore
        import numpy as _np  # type: ignore
    except ImportError as e:
        raise RuntimeError(f"sdf/numpy not installed: {e}")
    # sdf.core uses multiprocessing.pool.ThreadPool, which spawns a
    # resource-tracker subprocess. That breaks inside long-running host
    # processes with inherited/closed FDs ("bad value(s) in fds_to_keep").
    # Swap in a pure-threading executor — same API surface, no subprocess.
    try:
        import sdf.core as _score
        from concurrent.futures import ThreadPoolExecutor as _TPE
        class _NoMPThreadPool:
            def __init__(self, workers):
                self._ex = _TPE(max_workers=max(1, int(workers)))
            def imap(self, fn, iterable):
                return self._ex.map(fn, iterable)
            def __getattr__(self, name):
                return getattr(self._ex, name)
        _score.ThreadPool = _NoMPThreadPool
    except Exception:
        pass
    g: dict[str, Any] = {"__builtins__": {}}
    # Expose every non-private sdf name
    for name in dir(_sdf):
        if not name.startswith("_"):
            g[name] = getattr(_sdf, name)
    # Math helpers
    g["math"]   = math
    g["np"]     = _np
    g["pi"]     = math.pi
    g["tau"]    = math.tau
    g["sin"]    = math.sin
    g["cos"]    = math.cos
    g["radians"] = math.radians
    g["degrees"] = math.degrees
    _SAFE_GLOBALS = g
    return g


@tool
def sdf_render_stl(
    expression: str,
    output_stl: str,
    resolution: float = 0.5,
    bounds: list | None = None,
) -> dict:
    """Evaluate a Python SDF expression and mesh it to STL via marching cubes.

    The `expression` is a Python one-liner (or multi-line string) that must
    evaluate to an SDF3 object from the `sdf` library. All fogleman/sdf
    primitives are pre-imported (sphere, box, cylinder, capsule, torus,
    ellipsoid, capped_cylinder, rounded_cylinder, rounded_box, etc.) along
    with math constants (pi, tau, sin, cos, radians).

    Args:
        expression: Python SDF expression (e.g. "sphere(20) - box(15)").
            Multiple statements ok — the last expression is used, or bind to
            a variable named `result`.
        output_stl: Output .stl path.
        resolution: Marching-cubes step size in mm (default 0.5).
            Smaller = smoother surfaces + more triangles + slower.
            0.3-0.5 gives great print quality; 1.0 is fast preview.
        bounds: Optional (xmin, ymin, zmin, xmax, ymax, zmax) list. If None,
            sdf auto-computes tight bounds from the shape.

    Returns:
        {status, content, path, size_kb, elapsed_sec, resolution}

    Example:
        # A twisted torus of teardrop cross-section:
        sdf_render_stl(
            "torus(30, 8).twist(radians(120)/60)",
            "/tmp/twisted_torus.stl",
            resolution=0.4,
        )
    """
    try:
        g = _sdf_globals()
    except RuntimeError as e:
        return err(str(e))
    out = Path(output_stl).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    expr = textwrap.dedent(expression).strip()
    shape = None
    try:
        # Detect multi-line or assignment style
        has_stmt = ("\n" in expr and expr.count("\n") > 0) or \
                   re.search(r"^\s*[A-Za-z_][A-Za-z0-9_]*\s*=", expr) is not None
        if has_stmt:
            local: dict[str, Any] = {}
            exec(expr, g, local)
            shape = (local.get("result")
                     or local.get("shape")
                     or (list(local.values())[-1] if local else None))
        else:
            shape = eval(expr, g, {})
    except Exception as e:
        return err(f"expression failed: {type(e).__name__}: {e}")

    if shape is None or not hasattr(shape, "generate"):
        return err(f"expression did not produce an SDF3 (got {type(shape).__name__})")

    t0 = time.time()
    try:
        if bounds is not None and len(bounds) == 6:
            shape.save(str(out), step=resolution,
                       bounds=((bounds[0], bounds[1], bounds[2]),
                               (bounds[3], bounds[4], bounds[5])))
        else:
            shape.save(str(out), step=resolution)
    except Exception as e:
        return err(f"meshing failed: {type(e).__name__}: {e}")
    elapsed = time.time() - t0

    size_kb = out.stat().st_size / 1024 if out.exists() else 0
    return ok(
        f"meshed in {elapsed:.1f}s → {out.name} ({size_kb:.0f} KB) @ {resolution}mm",
        path=str(out), size_kb=size_kb, elapsed_sec=elapsed, resolution=resolution,
    )


@tool
def sdf_list_primitives() -> dict:
    """List every SDF primitive & operator available inside sdf expressions.

    Returns:
        {status, content, primitives:[{name, kind, doc}]}
    """
    try:
        import sdf as _sdf  # type: ignore
    except ImportError as e:
        return err(f"sdf not installed: {e}")
    prims = []
    for name in sorted(dir(_sdf)):
        if name.startswith("_"):
            continue
        obj = getattr(_sdf, name)
        if not callable(obj):
            continue
        doc = (obj.__doc__ or "").strip().split("\n")[0][:120]
        # Rough classification by common naming
        low = name.lower()
        if any(k in low for k in ("sphere", "box", "cylinder", "capsule", "torus",
                                   "ellipsoid", "cone", "polygon", "text", "hexagon",
                                   "triangle", "vesica", "rectangle", "circle",
                                   "octahedron", "dodecahedron", "icosahedron",
                                   "tetrahedron", "pyramid", "plane", "slab", "line")):
            kind = "primitive"
        elif any(k in low for k in ("twist", "bend", "translate", "rotate", "scale",
                                     "shell", "dilate", "erode", "revolve", "extrude",
                                     "orient", "mirror", "elongate", "wrap")):
            kind = "operator"
        elif any(k in low for k in ("union", "difference", "intersection", "blend",
                                     "transition")):
            kind = "boolean"
        elif any(k in low for k in ("save", "generate", "mesh", "measure", "sample",
                                     "show")):
            kind = "io"
        else:
            kind = "helper"
        prims.append({"name": name, "kind": kind, "doc": doc})
    return ok(f"{len(prims)} SDF names available", primitives=prims)


@tool
def sdf_gyroid_infill(
    size: tuple[float, float, float] = (60, 60, 60),
    period: float = 12.0,
    thickness: float = 1.6,
    output_stl: str = "gyroid.stl",
    resolution: float = 0.4,
) -> dict:
    """Generate a gyroid TPMS (triply-periodic minimal surface) lattice.

    Gyroids are strong, lightweight, and popular for functional 3D prints
    (heat sinks, filters, orthopedic scaffolds, lightweight parts).

    Args:
        size: (X, Y, Z) bounding box of the lattice (mm).
        period: One full gyroid cell size (mm). Smaller = finer lattice.
        thickness: Wall thickness of the surface (mm). ~2× nozzle recommended.
        output_stl: Output .stl path.
        resolution: Marching cubes step (mm).

    Returns:
        {status, content, path}
    """
    try:
        import sdf as _sdf  # type: ignore
        import numpy as np  # type: ignore
    except ImportError as e:
        return err(f"sdf/numpy not installed: {e}")

    # Gyroid: sin(x)cos(y) + sin(y)cos(z) + sin(z)cos(x) = 0
    # Wall version: |gyroid| < half-thickness  (thickened isosurface).
    w = period / (2 * math.pi)
    half_t = thickness / 2

    @_sdf.sdf3
    def gyroid_wall_factory():
        def f(p):
            x = p[:, 0] / w
            y = p[:, 1] / w
            z = p[:, 2] / w
            v = np.sin(x)*np.cos(y) + np.sin(y)*np.cos(z) + np.sin(z)*np.cos(x)
            return np.abs(v) - half_t / w
        return f

    sx, sy, sz = size
    bbox = _sdf.box((sx, sy, sz))
    shape = gyroid_wall_factory() & bbox

    out = Path(output_stl).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    try:
        shape.save(str(out), step=resolution)
    except Exception as e:
        return err(f"meshing failed: {type(e).__name__}: {e}")
    elapsed = time.time() - t0
    size_kb = out.stat().st_size / 1024
    return ok(f"gyroid {sx}×{sy}×{sz}mm, period {period}mm, thickness {thickness}mm — {size_kb:.0f} KB in {elapsed:.1f}s",
              path=str(out), size_kb=size_kb, elapsed_sec=elapsed)


@tool
def sdf_from_function(
    function_source: str,
    output_stl: str,
    bounds: list,
    resolution: float = 0.5,
) -> dict:
    """Mesh an arbitrary Python `f(p) -> distance` implicit function.

    The function receives an (N,3) numpy array of points and must return
    an (N,) array of signed distances (or scalar function of x,y,z).
    This is the escape hatch for math you can't express with sdf primitives.

    Args:
        function_source: Python source defining `def f(p):` returning
            distance values. Numpy is available as `np`.
        output_stl: Output .stl.
        bounds: [xmin, ymin, zmin, xmax, ymax, zmax] in mm.
        resolution: Marching cubes step.

    Example:
        sdf_from_function(
            function_source='''
def f(p):
    x, y, z = p[:,0], p[:,1], p[:,2]
    return np.sqrt(x**2 + y**2 + z**2) - 20 + np.sin(x)*np.sin(y)*np.sin(z)
''',
            output_stl="/tmp/wavy_sphere.stl",
            bounds=[-30,-30,-30, 30,30,30],
        )
    """
    try:
        import sdf as _sdf  # type: ignore
        import numpy as np  # type: ignore
    except ImportError as e:
        return err(f"sdf/numpy not installed: {e}")
    if len(bounds) != 6:
        return err("bounds must be [xmin,ymin,zmin, xmax,ymax,zmax]")
    local: dict[str, Any] = {"np": np, "math": math}
    try:
        exec(textwrap.dedent(function_source), local)
    except Exception as e:
        return err(f"function_source exec failed: {e}")
    f = local.get("f")
    if not callable(f):
        return err("function_source must define `def f(p): ...`")

    @_sdf.sdf3
    def wrapped_factory():
        return f  # already takes p

    out = Path(output_stl).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    try:
        wrapped_factory().save(str(out), step=resolution, bounds=((bounds[0], bounds[1], bounds[2]), (bounds[3], bounds[4], bounds[5])))
    except Exception as e:
        return err(f"meshing failed: {type(e).__name__}: {e}")
    elapsed = time.time() - t0
    size_kb = out.stat().st_size / 1024
    return ok(f"custom SDF meshed in {elapsed:.1f}s → {out} ({size_kb:.0f} KB)",
              path=str(out), size_kb=size_kb, elapsed_sec=elapsed)


@tool
def sdf_lattice_infill_stl(
    input_stl: str,
    output_stl: str,
    lattice: str = "gyroid",
    period: float = 10.0,
    thickness: float = 1.4,
    shell_thickness: float = 2.0,
    resolution: float = 0.4,
) -> dict:
    """Fill an existing STL's interior with a TPMS lattice (functional/lightweight).

    Takes an existing STL mesh, treats it as a solid, adds a shell wall of the
    given thickness, and fills its interior with the chosen lattice
    (gyroid/schwarz-p/diamond). Great for making printed parts stronger + lighter.

    Args:
        input_stl: Input .stl file (must be watertight for best results).
        output_stl: Output .stl.
        lattice: 'gyroid', 'schwarz_p', or 'diamond'.
        period: Cell period in mm.
        thickness: Lattice wall thickness in mm.
        shell_thickness: Outer shell thickness in mm.
        resolution: Marching cubes step in mm.
    """
    try:
        import sdf as _sdf  # type: ignore
        import trimesh  # type: ignore
        import numpy as np  # type: ignore
    except ImportError as e:
        return err(f"missing deps: {e}")

    src = Path(input_stl).resolve()
    out = Path(output_stl).resolve()
    if not src.exists():
        return err(f"input STL not found: {src}")

    mesh = trimesh.load(src, force="mesh")
    if not mesh.is_watertight:
        # Best-effort: fill holes so signed-distance queries are meaningful
        try:
            trimesh.repair.fill_holes(mesh)
        except Exception:
            pass

    # Build a signed-distance function from the mesh
    from trimesh.proximity import ProximityQuery
    pq = ProximityQuery(mesh)

    def mesh_sdf(p_np):
        # trimesh signed_distance is positive INSIDE, we need positive OUTSIDE.
        return -pq.signed_distance(p_np)

    @_sdf.sdf3
    def solid_factory():
        return mesh_sdf

    # Lattice function
    w = period / (2 * math.pi)
    half_t = thickness / 2

    if lattice == "gyroid":
        def lat(x, y, z):
            return np.sin(x)*np.cos(y) + np.sin(y)*np.cos(z) + np.sin(z)*np.cos(x)
    elif lattice == "schwarz_p":
        def lat(x, y, z):
            return np.cos(x) + np.cos(y) + np.cos(z)
    elif lattice == "diamond":
        def lat(x, y, z):
            return (np.sin(x)*np.sin(y)*np.sin(z)
                    + np.sin(x)*np.cos(y)*np.cos(z)
                    + np.cos(x)*np.sin(y)*np.cos(z)
                    + np.cos(x)*np.cos(y)*np.sin(z))
    else:
        return err(f"unknown lattice '{lattice}'. Use gyroid|schwarz_p|diamond.")

    @_sdf.sdf3
    def lattice_wall_factory():
        def f(p):
            x = p[:, 0] / w; y = p[:, 1] / w; z = p[:, 2] / w
            return np.abs(lat(x, y, z)) - half_t / w
        return f

    # Composition:
    #   shell   = solid grown by shell_thickness inside (shell of the input)
    #   infill  = lattice_wall INTERSECTED with solid shrunk by shell_thickness
    # Then union them.
    shell = solid_factory() - solid_factory().dilate(-shell_thickness)
    core  = solid_factory().dilate(-shell_thickness)
    infill = lattice_wall_factory() & core
    combined = shell | infill

    out.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    try:
        combined.save(str(out), step=resolution)
    except Exception as e:
        return err(f"meshing failed: {type(e).__name__}: {e}")
    elapsed = time.time() - t0
    size_kb = out.stat().st_size / 1024
    return ok(f"{lattice} infill @ period {period}mm — {size_kb:.0f} KB in {elapsed:.1f}s",
              path=str(out), size_kb=size_kb, lattice=lattice, elapsed_sec=elapsed)
