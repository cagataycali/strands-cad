"""SCAD layer — atomic OpenSCAD tools."""
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


@tool
def scad_probe(scad_file: str, variables: list[str]) -> dict:
    """Extract runtime values of OpenSCAD variables via echo probe.

    Args:
        scad_file: Path to .scad file (its variables are `include`d).
        variables: List of variable names to probe (e.g. ["WHEELBASE", "TOTAL_H"]).

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
    _, stderr, rc = _run_openscad(["-o", str(stl_out), str(probe)])
    probe.unlink(missing_ok=True)
    stl_out.unlink(missing_ok=True)
    if rc != 0 and rc != 127:
        # openscad may return non-zero but still emit echoes; only fail on missing binary
        pass
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
def scad_render_stl(scad_file: str, output_stl: str, format: str = "binstl") -> dict:
    """Render one .scad file to one STL.

    Args:
        scad_file: Input .scad path.
        output_stl: Output .stl path.
        format: 'binstl' (binary, default) or 'asciistl'.
    """
    src = Path(scad_file).resolve()
    out = Path(output_stl).resolve()
    if not src.exists():
        return err(f"scad file not found: {src}")
    out.parent.mkdir(parents=True, exist_ok=True)
    _, stderr, rc = _run_openscad(["-o", str(out), "--export-format", format, str(src)], timeout=300)
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
) -> dict:
    """Render one .scad file to a PNG preview.

    Args:
        scad_file: Input .scad path.
        output_png: Output .png path.
        size: (width, height) in pixels.
        camera: OpenSCAD --camera string (tx,ty,tz,rx,ry,rz,dist).
        colorscheme: OpenSCAD --colorscheme name.
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
        str(src),
    ]
    _, stderr, rc = _run_openscad(args, timeout=180)
    if rc == 127:
        return err(stderr)
    if not out.exists() or out.stat().st_size == 0:
        return err(f"render failed: {stderr[:400]}")
    return ok(f"rendered → {out}", path=str(out))


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
