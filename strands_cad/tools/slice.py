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


# Env override: point at any slicer CLI (Bambu/Orca AppImage/binary).
_SLICER_ENV = "STRANDS_CAD_SLICER"


def _find_bambu_cli() -> str | None:
    """Locate a Bambu-Studio-compatible slicer CLI.

    Order: explicit env override → Bambu Studio → OrcaSlicer (a Bambu Studio
    fork sharing the same `--slice/--load-settings` CLI, and the only option
    with ARM64/Linux builds) → PrusaSlicer (different CLI; handled separately).
    Returns the CLI path or None.
    """
    import os as _os
    override = _os.getenv(_SLICER_ENV)
    if override and Path(override).exists():
        return override
    # Bambu Studio (x86 Linux / macOS) and OrcaSlicer (incl. ARM64)
    for cand in ("bambu-studio", "BambuStudio", "bambu_studio",
                 "orca-slicer", "OrcaSlicer", "orcaslicer", "OrcaSlicer_ubuntu"):
        p = shutil.which(cand)
        if p:
            return p
    # common install locations (AppImage extracted / opt / user Applications)
    for cand in (
        "/Applications/BambuStudio.app/Contents/MacOS/BambuStudio",
        "/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer",
        str(Path.home() / ".local/share/OrcaSlicer/bin/orca-slicer"),
        str(Path.home() / ".local/bin/orca-slicer"),
        str(Path.home() / "Applications/OrcaSlicer.AppImage"),
        "/opt/OrcaSlicer/bin/orca-slicer",
        "/opt/OrcaSlicer/orca-slicer",
    ):
        if Path(cand).exists():
            return cand
    return None


def _find_prusa_cli() -> str | None:
    """PrusaSlicer CLI (apt package on ARM64 Ubuntu). Different arg surface."""
    import os as _os
    override = _os.getenv("STRANDS_CAD_PRUSA")
    if override and Path(override).exists():
        return override
    for cand in ("prusa-slicer", "PrusaSlicer", "prusa-slicer-console"):
        p = shutil.which(cand)
        if p:
            return p
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


def _find_orca_docker_image() -> str | None:
    """Return the OrcaSlicer docker image tag if configured & available.

    Enabled by STRANDS_CAD_SLICER_DOCKER (image tag, default
    'strands-cad/orcaslicer:2.5.0'). Returns the tag if docker + image exist,
    else None. This lets us slice inside a reproducible container so we never
    depend on a host build again.
    """
    import os as _os, shutil as _sh, subprocess as _sp
    if _os.getenv("STRANDS_CAD_SLICER_DOCKER", "").lower() in ("0", "false", "off", "no"):
        return None
    img = _os.getenv("STRANDS_CAD_SLICER_DOCKER_IMAGE", "strands-cad/orcaslicer:2.5.0")
    if not _sh.which("docker"):
        return None
    try:
        r = _sp.run(["docker", "image", "inspect", img],
                    capture_output=True, text=True, timeout=15)
        return img if r.returncode == 0 else None
    except Exception:
        return None


def _slice_with_docker(image: str, input_3mf: str, output_gcode: str,
                       profile: str = "PLA_0_20",
                       printer_model: str = "Bambu Lab X2D",
                       extra_args=None) -> dict:
    """Slice a 3MF/STL using the containerized OrcaSlicer (reproducible).

    Mounts the input's parent dir at /work and uses the profiles baked into
    the image (/opt/orcaslicer/resources/profiles/BBL). Bambu-flavored gcode
    (HEADER/EXECUTABLE/CONFIG blocks) is written next to the input.
    """
    src = Path(input_3mf).resolve()
    out = Path(output_gcode).resolve()
    if not src.exists():
        return err(f"input not found: {src}")
    out.parent.mkdir(parents=True, exist_ok=True)
    # Everything must live under one mounted dir so the container can read+write.
    workdir = src.parent
    P = "/opt/orcaslicer/resources/profiles/BBL"
    prof = PROFILES.get(profile.upper())
    layer = prof["layer_height"] if prof else 0.20
    short_model = printer_model.replace("Bambu Lab ", "")
    machine = f"{P}/machine/{printer_model} 0.4 nozzle.json"
    process = f"{P}/process/{layer:.2f}mm Standard @BBL {short_model}.json"
    material = (prof or {}).get("material", "PLA")
    fil = {"PLA": f"Bambu PLA Basic @BBL {short_model} 0.4 nozzle.json",
           "PLA_SILK": "Bambu PLA Silk @base.json",
           "PETG": f"Bambu PETG Basic @BBL {short_model} 0.4 nozzle.json",
           "TPU": "Bambu TPU 95A @base.json",
           "ABS": f"Bambu ABS @BBL {short_model} 0.4 nozzle.json"}.get(material, "")
    filament = f"{P}/filament/{fil}" if fil else ""
    import os as _os
    # Export a REAL OrcaSlicer .3mf project (firmware requires a full bundle;
    # a bare gcode or hand-wrapped 3mf → error 0x05004037/46 "file invalid").
    out_3mf_name = out.stem + ".3mf"
    args = ["docker", "run", "--rm",
            "--user", f"{_os.getuid()}:{_os.getgid()}",
            "-e", "XDG_RUNTIME_DIR=/tmp", "-e", "HOME=/tmp",
            "-v", f"{workdir}:/work",
            image,
            "--load-settings", f"{machine};{process}"]
    if filament:
        args += ["--load-filaments", filament]
    args += ["--slice", "0", "--export-3mf", out_3mf_name, "--outputdir", "/work"]
    # ALWAYS force Textured PEI Plate: Bambu X2D/H2D do NOT support OrcaSlicer's
    # default "Cool Plate" → firmware fail_reason 50348044 "build plate mismatch".
    if not (extra_args and "--curr-bed-type" in extra_args):
        args += ["--curr-bed-type", "Textured PEI Plate"]
    if extra_args:
        args += list(extra_args)
    args.append(f"/work/{src.name}")
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return err("docker OrcaSlicer slicing timed out")
    log = (r.stdout + r.stderr)[-2000:]
    if r.returncode != 0:
        return err(f"docker slice failed (rc={r.returncode}): {log}")

    # Inject the printer *model code* into the exported 3mf's slice_info.config —
    # OrcaSlicer CLI leaves printer_model_id empty, which the firmware rejects.
    out_3mf = workdir / out_3mf_name
    if out_3mf.exists():
        try:
            _inject_model_code(out_3mf, printer_model)
        except Exception as e:  # non-fatal; gcode still usable
            log += f"\n(model-code inject skipped: {e})"

    # Also surface the bare gcode (for slice_estimate / preview).
    gcode_cands = sorted(workdir.glob("*.gcode"), key=lambda q: q.stat().st_mtime, reverse=True)
    if gcode_cands and str(gcode_cands[0]) != str(out):
        try:
            import shutil as _sh
            _sh.copy(str(gcode_cands[0]), str(out))
        except Exception:
            pass

    result = str(out_3mf) if out_3mf.exists() else (str(gcode_cands[0]) if gcode_cands else "")
    return ok(f"sliced (docker OrcaSlicer) → {result}",
              path=result, gcode=str(out) if out.exists() else "",
              log=log, slicer="docker")


# Bambu printer *model codes* the firmware validates in slice_info.config.
# (verified: X2D=N6; others from BambuStudio machine defs.)
_MODEL_CODE = {
    "Bambu Lab X2D": "N6",
    "Bambu Lab H2D": "O1D",
    "Bambu Lab X1 Carbon": "BL-P001",
    "Bambu Lab X1": "BL-P002",
    "Bambu Lab X1E": "C13",
    "Bambu Lab P1P": "C11",
    "Bambu Lab P1S": "C12",
    "Bambu Lab A1": "N2S",
    "Bambu Lab A1 mini": "N1",
}


def _inject_model_code(three_mf, printer_model: str) -> None:
    """Patch printer_model_id in a sliced .3mf's slice_info.config to the
    firmware model code (OrcaSlicer CLI leaves it empty → firmware rejects)."""
    import zipfile, re
    code = _MODEL_CODE.get(printer_model)
    if not code:
        return
    zin = zipfile.ZipFile(str(three_mf))
    items = {n: zin.read(n) for n in zin.namelist()}
    zin.close()
    key = "Metadata/slice_info.config"
    if key not in items:
        return
    si = items[key].decode()
    si2 = re.sub(r'(key="printer_model_id" value=")[^"]*(")',
                 rf'\g<1>{code}\g<2>', si)
    if si2 == si:
        return
    items[key] = si2.encode()
    with zipfile.ZipFile(str(three_mf), "w", zipfile.ZIP_DEFLATED) as z:
        for n, data in items.items():
            z.writestr(n, data)


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
    # Prefer the reproducible containerized OrcaSlicer if available.
    _img = _find_orca_docker_image()
    if _img:
        return _slice_with_docker(_img, input_3mf, output_gcode, profile,
                                  printer_model, extra_args)
    cli = _find_bambu_cli()
    if not cli:
        # No Bambu/Orca CLI (e.g. ARM64 where Bambu ships no build) → try Prusa.
        prusa = _find_prusa_cli()
        if prusa:
            return _slice_with_prusa(prusa, input_3mf, output_gcode, profile, extra_args)
        return err("no slicer CLI found. Install one: `python -m strands_cad.install_slicer` "
                   "(OrcaSlicer/PrusaSlicer), or set $STRANDS_CAD_SLICER to a CLI path.")
    src = Path(input_3mf).resolve()
    out = Path(output_gcode).resolve()
    if not src.exists():
        return err(f"3mf not found: {src}")
    out.parent.mkdir(parents=True, exist_ok=True)
    args = [cli, "--slice", "0", "--outputdir", str(out.parent)]
    # Use official Bambu Studio machine/process/filament presets.
    # The CLI requires real preset JSONs (with type/name/from fields) —
    # ad-hoc key/value JSON files are rejected ("from unsupported").
    # Resolve the bundled profiles dir. Layouts differ:
    #  macOS app : <cli>/../Resources/profiles/BBL
    #  Linux pkg : <cli>/../../resources/profiles/BBL  (bin/orca-slicer)
    _clip = Path(cli)
    _cands = [
        _clip.parent.parent / "Resources" / "profiles" / "BBL",   # macOS
        _clip.parent.parent / "resources" / "profiles" / "BBL",   # Linux extracted (bin/)
        _clip.parent / "resources" / "profiles" / "BBL",          # Linux (top-level)
        Path.home() / ".local/share/OrcaSlicer/resources/profiles/BBL",
        Path("/opt/OrcaSlicer/resources/profiles/BBL"),
        Path("/usr/share/BambuStudio/profiles/BBL"),
        Path.home() / ".config/BambuStudio/system/BBL",
    ]
    profiles_dir = next((c for c in _cands if c.exists()), _cands[0])
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
    # ALWAYS force Textured PEI Plate: Bambu X2D/H2D do NOT support OrcaSlicer's
    # default "Cool Plate" → firmware fail_reason 50348044 "build plate mismatch".
    if not (extra_args and "--curr-bed-type" in extra_args):
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
    # Bambu writes estimates in the header; PrusaSlicer writes them in a
    # config block at the END of the file. Read both head and tail.
    raw = src.read_bytes()
    if len(raw) > 400_000:
        text = (raw[:200_000] + raw[-200_000:]).decode("utf-8", errors="ignore")
    else:
        text = raw.decode("utf-8", errors="ignore")
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


# ── PrusaSlicer fallback (ARM64-friendly; different CLI surface) ─────────────
def _slice_with_prusa(cli: str, input_file: str, output_gcode: str,
                      profile: str = "PLA_0_20",
                      extra_args: list[str] | None = None) -> dict:
    """Slice via PrusaSlicer console using our generic PROFILES as CLI overrides.

    PrusaSlicer can ingest STL or 3MF directly and takes flat --key=value
    overrides (unlike Bambu's preset JSONs), so we translate our PROFILES dict.
    """
    src = Path(input_file).resolve()
    out = Path(output_gcode).resolve()
    if not src.exists():
        return err(f"input not found: {src}")
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() == ".3mf":
        out = out.with_suffix(".gcode")
    prof = PROFILES.get(profile.upper(), PROFILES["PLA_0_20"])
    fill = {"gyroid": "gyroid", "grid": "grid"}.get(prof.get("infill_pattern"), "gyroid")
    args = [
        cli, "--export-gcode", "-o", str(out),
        f"--layer-height={prof['layer_height']}",
        f"--fill-density={prof['infill_pct']}%",
        f"--fill-pattern={fill}",
        f"--perimeters={prof['walls']}",
        f"--top-solid-layers={prof['top_bottom']}",
        f"--bottom-solid-layers={prof['top_bottom']}",
        f"--temperature={prof['nozzle_temp']}",
        f"--bed-temperature={prof['bed_temp']}",
        f"--first-layer-temperature={prof['nozzle_temp']}",
        f"--first-layer-bed-temperature={prof['bed_temp']}",
        "--nozzle-diameter=0.4",
    ]
    if prof.get("brim_mm"):
        args.append(f"--brim-width={prof['brim_mm']}")
    if prof.get("supports") == "auto":
        args.append("--support-material")
    if extra_args:
        args.extend(extra_args)
    args.append(str(src))
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return err("prusa-slicer slicing timed out")
    log = (r.stdout + r.stderr)[-2000:]
    if r.returncode != 0 or not out.exists():
        return err(f"prusa slice failed (rc={r.returncode}): {log}")
    return ok(f"sliced (PrusaSlicer) → {out}", path=str(out), log=log, slicer="prusa")
