"""SCAD layer — atomic OpenSCAD tools (with -D defines, visual feedback)."""
from __future__ import annotations
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from strands import tool
from strands_cad._common import ok, err


def _run_openscad(args: list[str], timeout: int = 120) -> tuple[str, str, int]:
    try:
        r = subprocess.run(["openscad", *args], capture_output=True, text=True, timeout=timeout)
        return r.stdout, r.stderr, r.returncode
    except FileNotFoundError:
        return "", "openscad binary not found in PATH", 127


def _defines_args(defines: dict | None) -> list[str]:
    """Build -D CLI args from a {name: value} dict. Strings are auto-quoted."""
    args: list[str] = []
    for k, v in (defines or {}).items():
        if isinstance(v, bool):
            args += ["-D", f"{k}={'true' if v else 'false'}"]
        elif isinstance(v, (int, float)):
            args += ["-D", f"{k}={v}"]
        else:
            args += ["-D", f'{k}="{v}"']
    return args


# Named camera presets → OpenSCAD --camera rot strings (tx,ty,tz,rx,ry,rz,dist)
CAMERAS = {
    "iso":    "0,0,0,55,0,25,140",
    "front":  "0,0,0,90,0,0,140",
    "back":   "0,0,0,90,0,180,140",
    "top":    "0,0,0,0,0,0,140",
    "bottom": "0,0,0,180,0,0,140",
    "left":   "0,0,0,90,0,270,140",
    "right":  "0,0,0,90,0,90,140",
}


@tool
def scad_probe(scad_file: str, variables: list[str], defines: dict | None = None) -> dict:
    """Extract runtime values of OpenSCAD variables via echo probe.

    Args:
        scad_file: Path to .scad file (its variables are `include`d).
        variables: List of variable names to probe (e.g. ["WHEELBASE", "TOTAL_H"]).
        defines: Optional {name: value} overrides passed as -D (parametric probing).

    Returns:
        {status, content, values: {name: float}} — echoed variable values.
    """
    src = Path(scad_file).resolve()
    if not src.exists():
        return err(f"scad file not found: {src}")
    echos = "\n".join(f'echo("__PROBE__ {v}", {v});' for v in variables)
    probe = Path(tempfile.mkstemp(suffix=".scad")[1])
    probe.write_text(f'include <{src}>;\n{echos}\ncube(1);\n')
    stl_out = Path(tempfile.mkstemp(suffix=".stl")[1])
    _, stderr, rc = _run_openscad(["-o", str(stl_out), *_defines_args(defines), str(probe)])
    probe.unlink(missing_ok=True)
    stl_out.unlink(missing_ok=True)
    if rc == 127:
        return err(stderr)
    values: dict[str, float | str] = {}
    for line in stderr.splitlines():
        m = re.search(r'ECHO:\s*"__PROBE__\s+([A-Za-z_][A-Za-z0-9_]*)",\s*(.+)$', line)
        if m:
            name, raw = m.group(1), m.group(2).strip()
            try:
                values[name] = float(raw)
            except ValueError:
                values[name] = raw
    missing = [v for v in variables if v not in values]
    return ok(f"probed {len(values)}/{len(variables)} vars" + (f", missing: {missing}" if missing else ""),
              values=values, missing=missing)


@tool
def scad_render_stl(scad_file: str, output_stl: str, format: str = "binstl",
                    defines: dict | None = None) -> dict:
    """Render one .scad file to one STL.

    Args:
        scad_file: Input .scad path.
        output_stl: Output .stl path.
        format: 'binstl' (binary, default) or 'asciistl'.
        defines: Optional {name: value} variable overrides passed as -D.
            Lets you render parametric variants without editing the file.
    """
    src = Path(scad_file).resolve()
    out = Path(output_stl).resolve()
    if not src.exists():
        return err(f"scad file not found: {src}")
    out.parent.mkdir(parents=True, exist_ok=True)
    _, stderr, rc = _run_openscad(
        ["-o", str(out), "--export-format", format, *_defines_args(defines), str(src)],
        timeout=300)
    if rc == 127:
        return err(stderr)
    if not out.exists() or out.stat().st_size == 0:
        return err(f"render failed: {stderr[:400]}")
    return ok(f"rendered → {out} ({out.stat().st_size} bytes)", path=str(out))


@tool
def scad_render_png(
    scad_file: str,
    output_png: str,
    size: tuple[int, int] = (1200, 900),
    camera: str = "0,0,0,55,0,25,120",
    colorscheme: str = "Tomorrow",
    defines: dict | None = None,
) -> dict:
    """Render one .scad file to a PNG preview.

    Args:
        scad_file: Input .scad path.
        output_png: Output .png path.
        size: (width, height) in pixels.
        camera: OpenSCAD --camera string (tx,ty,tz,rx,ry,rz,dist).
        colorscheme: OpenSCAD --colorscheme name.
        defines: Optional {name: value} variable overrides passed as -D.
    """
    src = Path(scad_file).resolve()
    out = Path(output_png).resolve()
    if not src.exists():
        return err(f"scad file not found: {src}")
    out.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "-o", str(out),
        f"--imgsize={size[0]},{size[1]}",
        "--viewall", "--autocenter",
        f"--camera={camera}",
        f"--colorscheme={colorscheme}",
        *_defines_args(defines),
        str(src),
    ]
    _, stderr, rc = _run_openscad(args, timeout=180)
    if rc == 127:
        return err(stderr)
    if not out.exists() or out.stat().st_size == 0:
        return err(f"render failed: {stderr[:400]}")
    return ok(f"rendered → {out}", path=str(out))


@tool
def scad_view(
    scad_file: str,
    view: str = "iso",
    size: tuple[int, int] = (800, 600),
    colorscheme: str = "Tomorrow",
    defines: dict | None = None,
) -> dict:
    """Render a .scad file AND return the image so the agent can SEE it.

    This closes the design→look→refine loop: the model receives actual pixels
    (Converse image block), not just a file path.

    Args:
        scad_file: Input .scad path.
        view: Named view — one of: iso, front, back, top, bottom, left, right.
            Or a raw OpenSCAD camera string "tx,ty,tz,rx,ry,rz,dist".
        size: (width, height) in pixels (keep modest — it goes into context).
        colorscheme: OpenSCAD --colorscheme name.
        defines: Optional {name: value} -D overrides.

    Returns:
        {status, content: [text, image-block], path}
    """
    src = Path(scad_file).resolve()
    if not src.exists():
        return err(f"scad file not found: {src}")
    camera = CAMERAS.get(view, view)
    out = Path(tempfile.mkstemp(suffix=".png")[1])
    args = [
        "-o", str(out),
        f"--imgsize={size[0]},{size[1]}",
        "--viewall", "--autocenter",
        f"--camera={camera}",
        f"--colorscheme={colorscheme}",
        *_defines_args(defines),
        str(src),
    ]
    _, stderr, rc = _run_openscad(args, timeout=180)
    if rc == 127:
        return err(stderr)
    if not out.exists() or out.stat().st_size == 0:
        return err(f"render failed: {stderr[:400]}")
    img_bytes = out.read_bytes()
    return {
        "status": "success",
        "content": [
            {"text": f"view '{view}' of {src.name} ({len(img_bytes)} bytes)"},
            {"image": {"format": "png", "source": {"bytes": img_bytes}}},
        ],
        "path": str(out),
    }


@tool
def scad_turntable(
    scad_file: str,
    views: list[str] = ["iso", "front", "top", "right"],
    size: tuple[int, int] = (600, 450),
    colorscheme: str = "Tomorrow",
    defines: dict | None = None,
) -> dict:
    """Render multiple named views in one call and return ALL images (visual inspection).

    Args:
        scad_file: Input .scad path.
        views: List of named views (iso, front, back, top, bottom, left, right).
        size: Per-view (width, height) — keep small, all go into context.
        colorscheme: OpenSCAD --colorscheme name.
        defines: Optional {name: value} -D overrides.

    Returns:
        {status, content: [text, img, img, ...], paths}
    """
    src = Path(scad_file).resolve()
    if not src.exists():
        return err(f"scad file not found: {src}")
    content: list[dict] = []
    paths: list[str] = []
    failed: list[str] = []
    for v in views:
        camera = CAMERAS.get(v, v)
        out = Path(tempfile.mkstemp(suffix=".png")[1])
        args = [
            "-o", str(out),
            f"--imgsize={size[0]},{size[1]}",
            "--viewall", "--autocenter",
            f"--camera={camera}",
            f"--colorscheme={colorscheme}",
            *_defines_args(defines),
            str(src),
        ]
        _, stderr, rc = _run_openscad(args, timeout=180)
        if rc == 127:
            return err(stderr)
        if not out.exists() or out.stat().st_size == 0:
            failed.append(v)
            continue
        content.append({"text": f"── view: {v} ──"})
        content.append({"image": {"format": "png", "source": {"bytes": out.read_bytes()}}})
        paths.append(str(out))
    header = f"turntable of {src.name}: {len(paths)}/{len(views)} views"
    if failed:
        header += f" (failed: {failed})"
    return {"status": "success", "content": [{"text": header}, *content], "paths": paths}


@tool
def scad_validate(values: dict, constraints: list[dict]) -> dict:
    """Check probed values against constraints.

    Args:
        values: {var_name: value} dict (from scad_probe).
        constraints: List of {name, expr, description?} — where expr is a Python
            expression referencing keys in `values` (e.g. "WHEELBASE > PROP*1.4").

    Returns:
        {status, passed:bool, results:[{name, passed, description, expr}]}
    """
    results = []
    all_ok = True
    for c in constraints:
        name = c.get("name", "unnamed")
        expr = c.get("expr", "True")
        desc = c.get("description", "")
        try:
            passed = bool(eval(expr, {"__builtins__": {}}, dict(values)))
        except Exception as e:
            passed = False
            desc = f"{desc} [eval error: {e}]"
        results.append({"name": name, "passed": passed, "expr": expr, "description": desc})
        if not passed:
            all_ok = False
    summary = f"{sum(r['passed'] for r in results)}/{len(results)} constraints passed"
    return ok(summary, passed=all_ok, results=results)
