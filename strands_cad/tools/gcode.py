"""G-code layer — safety checks and toolpath preview."""
from __future__ import annotations
import re
from pathlib import Path
from typing import Any

from strands import tool
from strands_cad._common import ok, err


# Sane temp limits per material family (°C)
TEMP_LIMITS = {
    "nozzle_max": 320,
    "bed_max": 120,
    "nozzle_min_printing": 160,
}


@tool
def gcode_check(
    gcode_file: str,
    build_volume: list[float] = [256, 256, 256],
    nozzle_max_c: float = 320,
    bed_max_c: float = 120,
) -> dict:
    """Safety-check a G-code file: temperatures, bounds, cold extrusion.

    Args:
        gcode_file: Path to .gcode file.
        build_volume: [x, y, z] machine limits in mm.
        nozzle_max_c: Max allowed nozzle temp.
        bed_max_c: Max allowed bed temp.

    Returns:
        {status, content, passed, issues, stats:{max_temp, bed_temp, bounds}}
    """
    src = Path(gcode_file).resolve()
    if not src.exists():
        return err(f"gcode not found: {src}")

    issues: list[str] = []
    max_nozzle = 0.0
    max_bed = 0.0
    minx = miny = minz = float("inf")
    maxx = maxy = maxz = float("-inf")
    extrude_before_heat = False
    seen_hot = False
    line_no = 0

    with open(src, errors="ignore") as f:
        for line in f:
            line_no += 1
            s = line.split(";")[0].strip()
            if not s:
                continue
            # temps
            m = re.match(r"M10[49]\s.*S([\d.]+)", s)
            if m:
                t = float(m.group(1))
                max_nozzle = max(max_nozzle, t)
                if t >= TEMP_LIMITS["nozzle_min_printing"]:
                    seen_hot = True
            m = re.match(r"M1[49]0\s.*S([\d.]+)", s)
            if m:
                max_bed = max(max_bed, float(m.group(1)))
            # moves
            if s.startswith(("G0", "G1")):
                has_e = "E" in s
                for axis, val in re.findall(r"([XYZ])([-\d.]+)", s):
                    v = float(val)
                    if axis == "X":
                        minx, maxx = min(minx, v), max(maxx, v)
                    elif axis == "Y":
                        miny, maxy = min(miny, v), max(maxy, v)
                    else:
                        minz, maxz = min(minz, v), max(maxz, v)
                if has_e and not seen_hot and re.search(r"E[\d.]*[1-9]", s):
                    extrude_before_heat = True

    if max_nozzle > nozzle_max_c:
        issues.append(f"nozzle temp {max_nozzle}°C exceeds {nozzle_max_c}°C limit")
    if max_bed > bed_max_c:
        issues.append(f"bed temp {max_bed}°C exceeds {bed_max_c}°C limit")
    if extrude_before_heat:
        issues.append("extrusion command before nozzle reached printing temp (cold extrude)")
    bounds_ok = True
    if maxx > float("-inf"):
        if maxx > build_volume[0] or maxy > build_volume[1] or maxz > build_volume[2]:
            issues.append(f"toolpath exceeds build volume: max ({maxx:.0f},{maxy:.0f},{maxz:.0f}) vs {build_volume}")
            bounds_ok = False
        if minx < -1 or miny < -1:
            issues.append(f"negative XY moves: min ({minx:.0f},{miny:.0f})")
            bounds_ok = False

    passed = not issues
    return ok(
        f"{'PASS' if passed else 'FAIL'} — nozzle≤{max_nozzle:.0f}°C bed≤{max_bed:.0f}°C, "
        f"bounds {'OK' if bounds_ok else 'VIOLATED'}, {len(issues)} issue(s)",
        passed=passed, issues=issues,
        stats={
            "max_nozzle_c": max_nozzle, "max_bed_c": max_bed,
            "bounds_min": [minx, miny, minz] if maxx > float("-inf") else None,
            "bounds_max": [maxx, maxy, maxz] if maxx > float("-inf") else None,
            "lines": line_no,
        },
    )


@tool
def gcode_preview_png(gcode_file: str, output_png: str, max_moves: int = 200_000) -> dict:
    """Render a top-down toolpath preview PNG from G-code (extrusion moves only).

    Args:
        gcode_file: Path to .gcode file.
        output_png: Output .png path.
        max_moves: Cap on parsed extrusion moves (for huge files).

    Returns:
        {status, content: [text, image], path}
    """
    try:
        from PIL import Image, ImageDraw  # type: ignore
    except ImportError:
        return err("Pillow required. pip install pillow")
    src = Path(gcode_file).resolve()
    out = Path(output_png).resolve()
    if not src.exists():
        return err(f"gcode not found: {src}")

    segs: list[tuple[float, float, float, float]] = []
    x = y = None
    count = 0
    with open(src, errors="ignore") as f:
        for line in f:
            s = line.split(";")[0].strip()
            if not s.startswith(("G0", "G1")):
                continue
            nx, ny = x, y
            m = re.search(r"X([-\d.]+)", s)
            if m:
                nx = float(m.group(1))
            m = re.search(r"Y([-\d.]+)", s)
            if m:
                ny = float(m.group(1))
            extruding = bool(re.search(r"E[\d.]*[1-9]", s))
            if extruding and x is not None and nx is not None and (nx != x or ny != y):
                segs.append((x, y, nx, ny))
                count += 1
                if count >= max_moves:
                    break
            x, y = nx, ny

    if not segs:
        return err("no extrusion moves found")

    xs = [c for s_ in segs for c in (s_[0], s_[2])]
    ys = [c for s_ in segs for c in (s_[1], s_[3])]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    W = 900
    span = max(maxx - minx, maxy - miny, 1)
    scale = (W - 40) / span
    H = int((maxy - miny) * scale) + 40

    img = Image.new("RGB", (W, max(H, 100)), "#0a0a0a")
    d = ImageDraw.Draw(img)
    for x1, y1, x2, y2 in segs:
        d.line([
            (20 + (x1 - minx) * scale, H - 20 - (y1 - miny) * scale),
            (20 + (x2 - minx) * scale, H - 20 - (y2 - miny) * scale),
        ], fill="#ff8c1a", width=1)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    png_bytes = out.read_bytes()
    return {
        "status": "success",
        "content": [
            {"text": f"toolpath preview: {len(segs)} extrusion moves, "
                     f"XY span {maxx-minx:.0f}×{maxy-miny:.0f} mm → {out}"},
            {"image": {"format": "png", "source": {"bytes": png_bytes}}},
        ],
        "path": str(out),
    }
