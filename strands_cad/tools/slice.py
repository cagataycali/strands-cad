"""Slice layer — Bambu Studio CLI bridge + G-code inspection."""
from __future__ import annotations
import re
import shutil
import subprocess
from pathlib import Path

from strands import tool
from strands_cad._common import ok, err


# Built-in generic profiles (rough estimates — override with slicer's own presets).
PROFILES = {
    "PLA_SILK_0_16": {
        "layer_height": 0.16, "walls": 3, "top_bottom": 4,
        "infill_pct": 15, "infill_pattern": "gyroid",
        "brim_mm": 4, "supports": "auto",
        "material": "PLA_SILK", "nozzle_temp": 220, "bed_temp": 55,
    },
    "PLA_0_20": {
        "layer_height": 0.20, "walls": 3, "top_bottom": 4,
        "infill_pct": 20, "infill_pattern": "gyroid",
        "brim_mm": 3, "supports": "off",
        "material": "PLA", "nozzle_temp": 210, "bed_temp": 55,
    },
    "PETG_0_20": {
        "layer_height": 0.20, "walls": 3, "top_bottom": 4,
        "infill_pct": 20, "infill_pattern": "gyroid",
        "brim_mm": 5, "supports": "auto",
        "material": "PETG", "nozzle_temp": 240, "bed_temp": 75,
    },
    "TPU_0_20": {
        "layer_height": 0.20, "walls": 2, "top_bottom": 4,
        "infill_pct": 25, "infill_pattern": "gyroid",
        "brim_mm": 6, "supports": "off",
        "material": "TPU", "nozzle_temp": 230, "bed_temp": 45,
    },
    "ABS_0_20": {
        "layer_height": 0.20, "walls": 4, "top_bottom": 5,
        "infill_pct": 25, "infill_pattern": "gyroid",
        "brim_mm": 8, "supports": "auto",
        "material": "ABS", "nozzle_temp": 250, "bed_temp": 100,
    },
}


def _find_bambu_cli() -> str | None:
    for cand in ("bambu-studio", "BambuStudio", "bambu_studio"):
        p = shutil.which(cand)
        if p:
            return p
    # macOS app bundle path
    mac_path = Path("/Applications/BambuStudio.app/Contents/MacOS/BambuStudio")
    if mac_path.exists():
        return str(mac_path)
    return None


@tool
def slice_profile_get(profile: str) -> dict:
    """Fetch a built-in generic slicing profile.

    Args:
        profile: Profile name — one of: PLA_SILK_0_16, PLA_0_20, PETG_0_20, TPU_0_20, ABS_0_20.

    Returns:
        {status, content, profile: {...}}
    """
    key = profile.upper()
    if key not in PROFILES:
        return err(f"unknown profile '{profile}'. Available: {list(PROFILES)}")
    return ok(f"profile {key}", profile=PROFILES[key], name=key)


@tool
def slice_bambu(
    input_3mf: str,
    output_gcode: str,
    profile: str = "PLA_0_20",
    printer_model: str = "Bambu Lab X2D",
    extra_args: list[str] | None = None,
) -> dict:
    """Slice a 3MF using Bambu Studio CLI.

    Args:
        input_3mf: Input .3mf plate.
        output_gcode: Output .gcode or .3mf (with G-code) path.
        profile: Built-in profile name (PLA_0_20, PETG_0_20, TPU_0_20, ABS_0_20, PLA_SILK_0_16).
        printer_model: Bambu machine preset (e.g. "Bambu Lab X2D", "Bambu Lab X1 Carbon",
            "Bambu Lab A1", "Bambu Lab P1S").
        extra_args: Additional CLI args passed to Bambu Studio.

    Returns:
        {status, content, path, log}
    """
    cli = _find_bambu_cli()
    if not cli:
        return err("bambu-studio CLI not found. Install Bambu Studio and ensure it's on PATH.")
    src = Path(input_3mf).resolve()
    out = Path(output_gcode).resolve()
    if not src.exists():
        return err(f"3mf not found: {src}")
    out.parent.mkdir(parents=True, exist_ok=True)
    args = [cli, "--slice", "0", "--outputdir", str(out.parent)]
    # Use official Bambu Studio machine/process/filament presets.
    # The CLI requires real preset JSONs (with type/name/from fields) —
    # ad-hoc key/value JSON files are rejected ("from unsupported").
    profiles_dir = Path(cli).parent.parent / "Resources" / "profiles" / "BBL"
    if not profiles_dir.exists():
        for cand in (
            Path("/usr/share/BambuStudio/profiles/BBL"),
            Path.home() / ".config/BambuStudio/system/BBL",
        ):
            if cand.exists():
                profiles_dir = cand
                break
    prof = PROFILES.get(profile.upper())
    machine_json = profiles_dir / "machine" / f"{printer_model} 0.4 nozzle.json"
    layer = prof["layer_height"] if prof else 0.20
    short_model = printer_model.replace("Bambu Lab ", "")
    process_json = profiles_dir / "process" / f"{layer:.2f}mm Standard @BBL {short_model}.json"
    material = (prof or {}).get("material", "PLA")
    fil_name = {"PLA": "Bambu PLA Basic @base.json", "PLA_SILK": "Bambu PLA Silk @base.json",
                "PETG": "Bambu PETG Basic @base.json", "TPU": "Bambu TPU 95A @base.json",
                "ABS": "Bambu ABS @base.json"}.get(material, "Bambu PLA Basic @base.json")
    filament_json = profiles_dir / "filament" / fil_name
    if machine_json.exists() and process_json.exists():
        args += ["--load-settings", f"{machine_json};{process_json}"]
        if filament_json.exists():
            args += ["--load-filaments", str(filament_json)]
    # Non-PLA materials aren't allowed on the default Cool Plate — pick PEI
    if material != "PLA" and not (extra_args and "--curr-bed-type" in extra_args):
        args += ["--curr-bed-type", "Textured PEI Plate"]
    if extra_args:
        args.extend(extra_args)
    args.append(str(src))
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return err("bambu-studio slicing timed out")
    log = (r.stdout + r.stderr)[-2000:]
    # Bambu writes gcode/3mf into outputdir; we return the newest matching file
    candidates = sorted(out.parent.glob("*.gcode"), key=lambda p: p.stat().st_mtime, reverse=True)
    candidates += sorted(out.parent.glob("*.3mf"), key=lambda p: p.stat().st_mtime, reverse=True)
    result = str(candidates[0]) if candidates else ""
    if r.returncode != 0:
        return err(f"slice failed (rc={r.returncode}): {log}")
    return ok(f"sliced → {result}", path=result, log=log)


@tool
def slice_estimate(gcode_file: str) -> dict:
    """Estimate print time + filament from G-code header comments.

    Args:
        gcode_file: Path to .gcode file.

    Returns:
        {status, content, estimated_seconds, estimated_time_hms, filament_g, filament_mm}
    """
    src = Path(gcode_file).resolve()
    if not src.exists():
        return err(f"gcode not found: {src}")
    text = src.read_text(errors="ignore")[:200_000]  # header only
    est_sec = None
    fil_g = None
    fil_mm = None
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith(";"):
            continue
        # PrusaSlicer / Bambu style
        m = re.search(r"estimated printing time.*?=\s*([0-9dhms\s]+)", s, re.I)
        if m:
            hms = m.group(1)
            hours = re.search(r"(\d+)\s*h", hms)
            mins = re.search(r"(\d+)\s*m", hms)
            secs = re.search(r"(\d+)\s*s", hms)
            days = re.search(r"(\d+)\s*d", hms)
            est_sec = (int(days.group(1))*86400 if days else 0) \
                    + (int(hours.group(1))*3600 if hours else 0) \
                    + (int(mins.group(1))*60 if mins else 0) \
                    + (int(secs.group(1)) if secs else 0)
        # PrusaSlicer: "filament used [g] = 12.3" / Bambu: "total filament weight [g] : 12.3"
        m = re.search(r"filament (?:used|weight) \[g\]\s*[=:]\s*([\d.]+)", s, re.I)
        if m:
            val = float(m.group(1))
            if val > 0 or fil_g is None:
                fil_g = val
        m = re.search(r"filament (?:used|length) \[mm\]\s*[=:]\s*([\d.]+)", s, re.I)
        if m:
            val = float(m.group(1))
            if val > 0 or fil_mm is None:
                fil_mm = val
    hms = ""
    if est_sec is not None:
        h, r = divmod(int(est_sec), 3600)
        m, s = divmod(r, 60)
        hms = f"{h}h{m:02d}m{s:02d}s"
    # Bambu base profiles ship density=0 → weight header reads 0.00.
    # Fall back to computing from filament length (1.75mm dia, PLA 1.24 g/cm³).
    fil_g_estimated = False
    if (fil_g is None or fil_g == 0) and fil_mm:
        area_mm2 = 3.14159265 * (1.75 / 2) ** 2
        fil_g = round(fil_mm * area_mm2 / 1000 * 1.24, 2)
        fil_g_estimated = True
    fil_note = "~" if fil_g_estimated else ""
    return ok(f"time={hms or '?'}, filament={fil_note}{fil_g or '?'} g",
              estimated_seconds=est_sec, estimated_time_hms=hms,
              filament_g=fil_g, filament_mm=fil_mm,
              filament_g_estimated=fil_g_estimated)
