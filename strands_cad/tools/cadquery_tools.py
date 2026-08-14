"""CadQuery layer — real B-rep / NURBS CAD.

CadQuery is a Python DSL on top of OpenCascade — the industry-strength CAD kernel.
Best for: mechanical parts, fillets/chamfers, threads, sheet-metal, assemblies.
Exports to STL, STEP, DXF, SVG.
"""
from __future__ import annotations
import textwrap
import time
from pathlib import Path
from typing import Any

from strands import tool
from strands_cad._common import ok, err


_SAFE: dict[str, Any] = {}


def _script_source(script: str) -> str:
    """Accept either inline CadQuery source or a PATH to a .py file.

    Live failure mode (2026-08-14): an agent passed a path to a perfectly good
    script and got "SyntaxError: invalid syntax (<string>, line 1)" — a path is
    not a program. If the string names an existing file, read it.
    """
    s = script.strip()
    if "\n" not in s and s.endswith(".py"):
        p = Path(s)
        if p.is_file():
            return p.read_text()
        raise FileNotFoundError(
            f"script looks like a path but no such file: {s}")
    return script


def _cq_globals() -> dict[str, Any]:
    """Sandbox globals with cadquery + math for expressions."""
    global _SAFE
    if _SAFE:
        return _SAFE
    try:
        import cadquery as cq  # type: ignore
        import math as _m
    except ImportError as e:
        raise RuntimeError(f"cadquery not installed: {e}")
    import builtins as _bi
    g: dict[str, Any] = {"__builtins__": _bi}
    g["cq"] = cq
    g["Workplane"] = cq.Workplane
    g["Sketch"] = cq.Sketch
    g["Location"] = cq.Location
    g["Vector"] = cq.Vector
    g["Assembly"] = cq.Assembly
    g["exporters"] = cq.exporters
    g["math"] = _m
    g["pi"] = _m.pi
    _SAFE = g
    return g


@tool
def cq_render_stl(
    script: str,
    output_stl: str,
    tolerance: float = 0.1,
    angular_tolerance: float = 0.1,
) -> dict:
    """Execute a CadQuery script and export the resulting solid to STL.

    The script must produce a variable named `result` (a Workplane/Solid/Compound).
    All of cadquery is imported as `cq`, plus `Workplane`, `Sketch`, `Location`,
    `Vector`, `Assembly`, `exporters`, `math`, `pi`.

    Args:
        script: Python code that produces `result = <cq object>`.
        output_stl: Output .stl path.
        tolerance: Linear deflection for STL tessellation (mm). Smaller = smoother.
        angular_tolerance: Angular deflection (radians). Smaller = smoother.

    Returns:
        {status, content, path, size_kb}

    Example:
        cq_render_stl(script='''
result = (cq.Workplane("XY")
    .box(60, 40, 20)
    .edges("|Z").fillet(6)
    .faces(">Z").hole(12))
''', output_stl="/tmp/box.stl")
    """
    try:
        g = _cq_globals()
    except RuntimeError as e:
        return err(str(e))
    local: dict[str, Any] = {}
    try:
        exec(textwrap.dedent(_script_source(script)), g, local)
    except Exception as e:
        return err(f"script failed: {type(e).__name__}: {e}")
    obj = local.get("result")
    if obj is None:
        return err("script must set a variable named `result`")

    out = Path(output_stl).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    try:
        import cadquery as cq  # type: ignore
        cq.exporters.export(obj, str(out), exportType="STL",
                            tolerance=tolerance, angularTolerance=angular_tolerance)
    except Exception as e:
        return err(f"export failed: {type(e).__name__}: {e}")
    elapsed = time.time() - t0
    if not out.exists():
        return err("export produced no file")
    size_kb = out.stat().st_size / 1024
    return ok(f"exported STL in {elapsed:.2f}s → {out.name} ({size_kb:.1f} KB)",
              path=str(out), size_kb=size_kb, elapsed_sec=elapsed)


@tool
def cq_render_step(script: str, output_step: str) -> dict:
    """Execute a CadQuery script and export to STEP (B-rep, exact NURBS).

    STEP is the interchange format for real engineering CAD — losslessly
    preserves splines, fillets, and precise geometry. Use for sending to
    Fusion / SolidWorks / FreeCAD or CNC toolchains.

    Args:
        script: Python code that produces `result = <cq object>`.
        output_step: Output .step / .stp path.
    """
    try:
        g = _cq_globals()
    except RuntimeError as e:
        return err(str(e))
    local: dict[str, Any] = {}
    try:
        exec(textwrap.dedent(_script_source(script)), g, local)
    except Exception as e:
        return err(f"script failed: {type(e).__name__}: {e}")
    obj = local.get("result")
    if obj is None:
        return err("script must set a variable named `result`")
    out = Path(output_step).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        import cadquery as cq  # type: ignore
        cq.exporters.export(obj, str(out), exportType="STEP")
    except Exception as e:
        return err(f"export failed: {type(e).__name__}: {e}")
    if not out.exists():
        return err("export produced no file")
    return ok(f"exported STEP → {out.name} ({out.stat().st_size/1024:.1f} KB)",
              path=str(out))


@tool
def cq_import_step(step_file: str, output_stl: str, tolerance: float = 0.1) -> dict:
    """Import a STEP file (from Fusion/SolidWorks/etc.) and re-export as STL.

    Useful for bringing in vendor models, then tessellating for slicers.

    Args:
        step_file: Input .step / .stp file.
        output_stl: Output .stl file.
        tolerance: STL tessellation tolerance (mm).
    """
    try:
        import cadquery as cq  # type: ignore
    except ImportError as e:
        return err(f"cadquery not installed: {e}")
    src = Path(step_file).resolve()
    if not src.exists():
        return err(f"STEP file not found: {src}")
    try:
        shape = cq.importers.importStep(str(src))
    except Exception as e:
        return err(f"import failed: {e}")
    out = Path(output_stl).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        cq.exporters.export(shape, str(out), exportType="STL", tolerance=tolerance)
    except Exception as e:
        return err(f"export failed: {e}")
    return ok(f"STEP → STL: {out.name} ({out.stat().st_size/1024:.1f} KB)",
              path=str(out))


@tool
def cq_render_svg(script: str, output_svg: str, view: str = "iso") -> dict:
    """Execute CadQuery + render an SVG projection (for docs / drawings).

    Args:
        script: Python code producing `result`.
        output_svg: Output .svg path.
        view: 'iso', 'top', 'front', 'side'.
    """
    try:
        g = _cq_globals()
    except RuntimeError as e:
        return err(str(e))
    local: dict[str, Any] = {}
    try:
        exec(textwrap.dedent(_script_source(script)), g, local)
    except Exception as e:
        return err(f"script failed: {type(e).__name__}: {e}")
    obj = local.get("result")
    if obj is None:
        return err("script must set `result`")
    projection_dir = {
        "iso":   (1, 1, 1),
        "top":   (0, 0, 1),
        "front": (0, -1, 0),
        "side":  (1, 0, 0),
    }.get(view, (1, 1, 1))
    out = Path(output_svg).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        import cadquery as cq  # type: ignore
        cq.exporters.export(obj, str(out), exportType="SVG",
                            opt={"projectionDir": projection_dir,
                                 "showAxes": True,
                                 "strokeWidth": 0.5})
    except Exception as e:
        return err(f"SVG export failed: {e}")
    return ok(f"SVG rendered → {out.name}", path=str(out))
