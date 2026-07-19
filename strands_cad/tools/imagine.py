"""imagine — natural language → printable STL. The vibe-CAD layer.

Wraps neural_text_to_stl with a full NL-driven pipeline:

    imagine("a cute low-poly dragon, palm-sized, high quality")

does ALL of this automatically:
  1. Parse the prompt for size / quality / style / material hints
  2. Enhance the prompt with style-preset modifiers
  3. Generate via Shap-E
  4. Repair the mesh (fill holes, fix normals)
  5. Normalize (center XY, drop to bed)
  6. Scale to the requested real-world size
  7. Printability report + weight estimate
  8. Return one clean summary

Also: imagine_variations() for seed exploration ranked by printability.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from strands import tool
from strands_cad._common import ok, err


# ================================================================
# Natural-language parsing
# ================================================================

# --- semantic size vocabulary (target max dimension, mm) ---
SIZE_WORDS = {
    "microscopic": 10, "tiny": 20, "mini": 25, "miniature": 25,
    "keychain": 30, "keyring": 30, "small": 40, "pocket": 50,
    "pocket-sized": 50, "palm": 80, "palm-sized": 80, "medium": 90,
    "hand": 100, "hand-sized": 100, "desk": 120, "desk-sized": 120,
    "desktop": 120, "large": 150, "big": 150, "shelf": 180,
    "huge": 200, "giant": 220, "massive": 240,
}

# --- style presets: prompt suffix + generation param tweaks ---
STYLE_PRESETS = {
    "lowpoly":    {"suffix": "low poly style, faceted geometric surfaces, simple clean shapes",
                   "guidance": 16.0},
    "voxel":      {"suffix": "voxel art style, blocky cubes, minecraft-like",
                   "guidance": 16.0},
    "organic":    {"suffix": "organic smooth flowing curved surfaces, natural form",
                   "guidance": 14.0},
    "mechanical": {"suffix": "mechanical hard-surface design, precise engineering, functional part",
                   "guidance": 15.0},
    "cute":       {"suffix": "cute chibi style, rounded soft shapes, kawaii, big proportions",
                   "guidance": 15.0},
    "realistic":  {"suffix": "highly detailed realistic sculpture, accurate proportions",
                   "guidance": 17.0},
    "abstract":   {"suffix": "abstract sculptural art form, artistic interpretation",
                   "guidance": 12.0},
    "toy":        {"suffix": "toy figurine style, chunky durable simple shapes, no thin parts",
                   "guidance": 15.0},
    "ornament":   {"suffix": "decorative ornament, elegant, symmetric",
                   "guidance": 14.0},
    "statue":     {"suffix": "classical statue on a base, sculptural, standing pose",
                   "guidance": 15.0},
}

STYLE_ALIASES = {
    "low poly": "lowpoly", "low-poly": "lowpoly", "polygonal": "lowpoly",
    "blocky": "voxel", "minecraft": "voxel", "8-bit": "voxel", "pixel": "voxel",
    "smooth": "organic", "natural": "organic", "flowing": "organic",
    "hard surface": "mechanical", "hard-surface": "mechanical",
    "engineering": "mechanical", "industrial": "mechanical", "functional": "mechanical",
    "kawaii": "cute", "chibi": "cute", "adorable": "cute",
    "detailed": "realistic", "lifelike": "realistic", "high detail": "realistic",
    "sculptural": "abstract", "artistic": "abstract", "modern art": "abstract",
    "figurine": "toy", "chunky": "toy", "kid-friendly": "toy", "kids": "toy",
    "decoration": "ornament", "decorative": "ornament", "christmas": "ornament",
    "monument": "statue", "bust": "statue",
}

QUALITY_PRESETS = {
    "draft":   {"steps": 24},
    "fast":    {"steps": 24},
    "quick":   {"steps": 24},
    "normal":  {"steps": 64},
    "good":    {"steps": 64},
    "high":    {"steps": 96},
    "detailed":{"steps": 96},
    "best":    {"steps": 128},
    "ultra":   {"steps": 128},
    "max":     {"steps": 128},
}

# filament densities g/cm^3
MATERIALS = {
    "pla": 1.24, "petg": 1.27, "abs": 1.04, "asa": 1.07,
    "tpu": 1.21, "nylon": 1.14, "pc": 1.20, "resin": 1.10,
    "wood": 1.28, "carbon": 1.30, "cf": 1.30,
}

_MM_PER = {"mm": 1.0, "millimeter": 1.0, "millimeters": 1.0,
           "cm": 10.0, "centimeter": 10.0, "centimeters": 10.0,
           "in": 25.4, "inch": 25.4, "inches": 25.4, '"': 25.4,
           "m": 1000.0, "meter": 1000.0, "meters": 1000.0}

_DIM_AXIS = {"tall": "z", "high": "z", "height": "z", "wide": "x",
             "width": "x", "long": "y", "length": "y", "deep": "y",
             "depth": "y", "diameter": "max", "across": "max"}


def parse_intent(prompt: str) -> dict:
    """Parse a natural-language CAD prompt into structured generation props.

    Extracts and STRIPS from the prompt:
      - explicit sizes:  "5cm tall", "50 mm wide", "2 inch", "80mm"
      - semantic sizes:  "palm-sized", "keychain", "huge", "tiny"
      - quality:         "draft", "high quality", "ultra"
      - style:           "low poly", "cute", "mechanical", ... (presets)
      - material:        "in PETG", "PLA", "printed in TPU"
      - seed:            "seed 42"
      - count:           "4 variations", "3 versions"

    Returns dict: {clean_prompt, size_mm, size_axis, quality, steps,
                   style, style_suffix, guidance, material, density,
                   seed, count, notes}
    """
    text = prompt.strip()
    notes = []
    out = {
        "size_mm": None, "size_axis": "max", "quality": "normal",
        "steps": 64, "style": None, "style_suffix": "", "guidance": 15.0,
        "material": "pla", "density": 1.24, "seed": None, "count": 1,
    }

    low = text.lower()

    # --- explicit size: "5 cm tall", "50mm wide", '2" diameter', "80 mm" ---
    m = re.search(
        r'(\d+(?:\.\d+)?)\s*(millimeters?|centimeters?|inches|inch|meters?|mm|cm|in\b|m\b|")'
        r'(?:\s+(tall|high|wide|long|deep|across|diameter|height|width|length|depth))?',
        low)
    if m:
        val, unit, axis_word = float(m.group(1)), m.group(2), m.group(3)
        out["size_mm"] = val * _MM_PER.get(unit, 1.0)
        if axis_word:
            out["size_axis"] = _DIM_AXIS.get(axis_word, "max")
        notes.append(f"size: {out['size_mm']:.0f}mm ({out['size_axis']})")
        text = (text[:m.start()] + text[m.end():])
        low = text.lower()

    # --- semantic size words ---
    if out["size_mm"] is None:
        for word, mm in sorted(SIZE_WORDS.items(), key=lambda kv: -len(kv[0])):
            pat = r'\b' + re.escape(word) + r'(?:-sized|\s+sized|\s+size)?\b'
            m = re.search(pat, low)
            if m:
                out["size_mm"] = float(mm)
                notes.append(f"size: '{word}' → {mm}mm")
                text = re.sub(pat, "", text, count=1, flags=re.IGNORECASE)
                low = text.lower()
                break

    # --- quality ---
    m = re.search(r'\b(draft|fast|quick|normal|good|high|detailed|best|ultra|max)'
                  r'(?:\s+(?:quality|res|resolution|detail))?\b', low)
    if m:
        q = m.group(1)
        # only strip if clearly about quality (has 'quality' etc, or is draft/ultra/best)
        explicit = m.group(0) != q or q in ("draft", "ultra", "best", "fast", "quick")
        if explicit:
            out["quality"] = q
            out["steps"] = QUALITY_PRESETS[q]["steps"]
            notes.append(f"quality: {q} → {out['steps']} steps")
            text = text[:m.start()] + text[m.end():]
            low = text.lower()

    # --- style (aliases first — longer phrases) ---
    for phrase, preset in sorted(STYLE_ALIASES.items(), key=lambda kv: -len(kv[0])):
        m = re.search(r'\b' + re.escape(phrase) + r'\b', low)
        if m:
            out["style"] = preset
            break
    if out["style"] is None:
        for preset in STYLE_PRESETS:
            if re.search(r'\b' + preset + r'\b', low):
                out["style"] = preset
                break
    if out["style"]:
        sp = STYLE_PRESETS[out["style"]]
        out["style_suffix"] = sp["suffix"]
        out["guidance"] = sp["guidance"]
        notes.append(f"style: {out['style']} (guidance {out['guidance']})")

    # --- material: "in petg", "printed in tpu", bare "pla" ---
    m = re.search(r'\b(?:in|printed in|using|with)\s+(' + "|".join(MATERIALS) + r')\b', low)
    if not m:
        m = re.search(r'\b(' + "|".join(MATERIALS) + r')\b', low)
    if m:
        out["material"] = m.group(1)
        out["density"] = MATERIALS[out["material"]]
        notes.append(f"material: {out['material'].upper()} ({out['density']} g/cm³)")
        text = text[:m.start()] + text[m.end():]
        low = text.lower()

    # --- seed ---
    m = re.search(r'\bseed\s*[:=]?\s*(\d+)\b', low)
    if m:
        out["seed"] = int(m.group(1))
        notes.append(f"seed: {out['seed']}")
        text = text[:m.start()] + text[m.end():]
        low = text.lower()

    # --- count: "4 variations", "3 versions", "x5" ---
    m = re.search(r'\b(\d+)\s*(?:variations?|versions?|options?|candidates?)\b', low)
    if not m:
        m = re.search(r'\bx\s*(\d+)\b', low)
    if m:
        out["count"] = max(1, min(8, int(m.group(1))))
        notes.append(f"count: {out['count']}")
        text = text[:m.start()] + text[m.end():]

    # --- cleanup ---
    clean = re.sub(r'\s{2,}', ' ', text).strip(" ,.-")
    clean = re.sub(r'\s+,', ',', clean)
    out["clean_prompt"] = clean
    out["notes"] = notes
    return out


def _build_full_prompt(intent: dict) -> str:
    p = intent["clean_prompt"]
    if intent["style_suffix"]:
        p = f"{p}, {intent['style_suffix']}"
    return p


# ================================================================
# Pipeline helpers
# ================================================================

def _post_process(raw_stl: Path, out_stl: Path, intent: dict) -> dict:
    """Repair → normalize → scale to target size. Returns info dict."""
    import numpy as np  # type: ignore
    import trimesh  # type: ignore

    m = trimesh.load(str(raw_stl), force="mesh")
    info = {"faces_raw": int(len(m.faces))}

    # repair
    try:
        trimesh.repair.fix_normals(m)
        trimesh.repair.fill_holes(m)
        m.remove_degenerate_faces() if hasattr(m, "remove_degenerate_faces") else m.update_faces(m.nondegenerate_faces())
        m.remove_unreferenced_vertices()
    except Exception:
        pass
    info["watertight"] = bool(m.is_watertight)

    # scale to requested size
    ext = m.extents
    if intent["size_mm"]:
        axis = intent["size_axis"]
        cur = {"x": ext[0], "y": ext[1], "z": ext[2], "max": float(max(ext))}[axis]
        if cur > 1e-9:
            s = intent["size_mm"] / cur
            m.apply_scale(s)
            info["scale_factor"] = round(s, 4)
    else:
        # default: 80mm max dim if the raw mesh is unitless/small
        mx = float(max(ext))
        if mx < 5.0 or mx > 400.0:
            m.apply_scale(80.0 / mx)
            info["scale_factor"] = round(80.0 / mx, 4)
            info["auto_sized"] = "80mm (default)"

    # normalize onto bed
    mn, mx_ = m.bounds
    m.apply_translation([-(mn[0]+mx_[0])/2, -(mn[1]+mx_[1])/2, -mn[2]])

    out_stl.parent.mkdir(parents=True, exist_ok=True)
    m.export(str(out_stl))

    mn, mx_ = m.bounds
    dims = (mx_ - mn)
    info["dims_mm"] = [round(float(d), 1) for d in dims]
    info["faces"] = int(len(m.faces))
    try:
        vol_cm3 = float(abs(m.volume)) / 1000.0
        info["volume_cm3"] = round(vol_cm3, 2)
        info["weight_g"] = round(vol_cm3 * intent["density"], 1)
        info["weight_g_20pct_infill"] = round(vol_cm3 * intent["density"] * 0.35, 1)
    except Exception:
        pass
    return info


def _printability_score(stl_path: str) -> dict:
    """Quick printability heuristics: overhangs, bed contact, watertight."""
    try:
        import numpy as np  # type: ignore
        import trimesh  # type: ignore
        m = trimesh.load(stl_path, force="mesh")
        normals = m.face_normals
        areas = m.area_faces
        total = float(areas.sum()) or 1.0
        # faces pointing down more than 45° from vertical = overhang
        overhang_mask = normals[:, 2] < -0.7071
        overhang_pct = float(areas[overhang_mask].sum() / total * 100)
        # bed contact: faces within 0.5mm of z-min facing down
        zmin = m.bounds[0][2]
        centroids = m.triangles_center
        bed_mask = (centroids[:, 2] < zmin + 0.5) & (normals[:, 2] < -0.9)
        bed_pct = float(areas[bed_mask].sum() / total * 100)
        score = 100.0
        score -= min(40, overhang_pct * 1.5)
        score -= 0 if m.is_watertight else 20
        score -= 15 if bed_pct < 0.5 else 0
        return {"score": round(max(0, score), 1),
                "overhang_pct": round(overhang_pct, 1),
                "bed_contact_pct": round(bed_pct, 2),
                "watertight": bool(m.is_watertight)}
    except Exception as e:
        return {"score": -1, "error": str(e)}


# ================================================================
# Tools
# ================================================================


@tool
def imagine_parse(prompt: str) -> dict:
    """Dry-run the natural-language CAD parser — see what imagine() would do.

    Shows extracted size, quality, style, material, seed, count and the
    enhanced prompt WITHOUT generating anything. Free and instant.

    Args:
        prompt: Natural language description, e.g.
                "a cute low-poly dragon, palm-sized, high quality, in PETG, seed 7"
    """
    intent = parse_intent(prompt)
    full = _build_full_prompt(intent)
    lines = [f"🧠 parsed: {prompt!r}",
             f"  clean prompt : {intent['clean_prompt']}",
             f"  full prompt  : {full}",
             f"  size         : {intent['size_mm'] or 'auto (80mm)'} mm on '{intent['size_axis']}'",
             f"  quality      : {intent['quality']} ({intent['steps']} steps)",
             f"  style        : {intent['style'] or '—'} (guidance {intent['guidance']})",
             f"  material     : {intent['material'].upper()} ({intent['density']} g/cm³)",
             f"  seed         : {intent['seed'] if intent['seed'] is not None else 'auto'}",
             f"  count        : {intent['count']}"]
    return ok("\n".join(lines), intent=intent, full_prompt=full)


@tool
def imagine(
    prompt: str,
    output_stl: str = "",
    skip_postprocess: bool = False,
) -> dict:
    """🪄 Natural language → print-ready STL. The full vibe-CAD pipeline.

    ONE call does everything:
      parse NL intent → enhance prompt → Shap-E generate → repair mesh →
      scale to real-world size → drop onto print bed → printability report →
      weight estimate.

    The prompt understands (all optional, just write naturally):
      • sizes:    "5cm tall", "50mm wide", "2 inch", "palm-sized",
                  "keychain", "tiny", "huge", "desk-sized"
      • quality:  "draft" (24 steps), "high quality" (96), "ultra" (128)
      • styles:   "low poly", "voxel", "cute"/"chibi", "mechanical",
                  "organic", "realistic", "toy", "ornament", "statue"
      • material: "in PETG", "PLA", "TPU" (for weight estimate)
      • seed:     "seed 42" (reproducibility)

    Examples:
        imagine("a cute low-poly dragon, palm-sized")
        imagine("mechanical gear knob, 30mm diameter, in PETG, draft")
        imagine("majestic wizard statue, 12cm tall, ultra quality, seed 3")

    Args:
        prompt: Natural language description with optional size/style/quality hints.
        output_stl: Output path (default: ./imagined/<slug>.stl).
        skip_postprocess: If True, return raw Shap-E mesh without repair/scale.

    Returns:
        {status, content, path, intent, dims_mm, weight_g, printability, elapsed_sec}
    """
    from strands_cad.tools.neural_tools import neural_text_to_stl

    intent = parse_intent(prompt)
    full_prompt = _build_full_prompt(intent)
    seed = intent["seed"] if intent["seed"] is not None else int(time.time()) % 100000

    if not output_stl:
        slug = re.sub(r'[^a-z0-9]+', '_', intent["clean_prompt"].lower())[:40].strip("_") or "imagined"
        output_stl = str(Path("imagined") / f"{slug}_s{seed}.stl")
    out = Path(output_stl).resolve()
    raw = out.with_suffix(".raw.stl")

    t0 = time.time()
    gen = neural_text_to_stl(
        prompt=full_prompt,
        output_stl=str(raw if not skip_postprocess else out),
        guidance_scale=intent["guidance"],
        steps=intent["steps"],
        seed=seed,
    )
    if gen.get("status") != "success":
        return gen

    if skip_postprocess:
        return ok(f"🪄 imagined (raw) → {out.name}", path=str(out),
                  intent=intent, elapsed_sec=round(time.time()-t0, 1))

    try:
        info = _post_process(raw, out, intent)
    except Exception as e:
        return err(f"post-process failed: {type(e).__name__}: {e} (raw mesh at {raw})")
    finally:
        pass
    try:
        raw.unlink(missing_ok=True)
    except Exception:
        pass

    pr = _printability_score(str(out))
    elapsed = time.time() - t0

    d = info.get("dims_mm", ["?"]*3)
    lines = [f"🪄 imagined: {intent['clean_prompt']!r}",
             f"  → {out}",
             f"  dims     : {d[0]} × {d[1]} × {d[2]} mm",
             f"  faces    : {info.get('faces', '?')} (watertight: {info.get('watertight')})",
             f"  weight   : ~{info.get('weight_g','?')}g solid / ~{info.get('weight_g_20pct_infill','?')}g @20% infill ({intent['material'].upper()})",
             f"  print    : score {pr.get('score')}/100, overhangs {pr.get('overhang_pct','?')}%",
             f"  gen      : {intent['steps']} steps, guidance {intent['guidance']}, seed {seed}",
             f"  elapsed  : {elapsed:.1f}s"]
    if intent["notes"]:
        lines.append(f"  parsed   : {'; '.join(intent['notes'])}")
    return ok("\n".join(lines), path=str(out), intent=intent, seed=seed,
              dims_mm=info.get("dims_mm"), weight_g=info.get("weight_g"),
              printability=pr, elapsed_sec=round(elapsed, 1))


@tool
def imagine_variations(
    prompt: str,
    n: int = 3,
    output_dir: str = "imagined",
    base_seed: int = 0,
) -> dict:
    """🎲 Generate N seed-variations of a prompt, ranked by printability.

    Runs the full imagine() pipeline N times with different seeds and
    returns a leaderboard (best print score first). Slow: ~60-90s each on CPU.

    Prompt supports the same NL hints as imagine(). You can also embed the
    count in the prompt itself: "a chess knight, 4 variations".

    Args:
        prompt: Natural language description.
        n: Number of variations (1-8). Overridden by count in prompt.
        output_dir: Directory for outputs.
        base_seed: Starting seed (variations use base_seed, +1, +2, ...).
    """
    intent = parse_intent(prompt)
    count = intent["count"] if intent["count"] > 1 else max(1, min(8, n))

    results = []
    for i in range(count):
        seed = (intent["seed"] if intent["seed"] is not None else base_seed) + i
        slug = re.sub(r'[^a-z0-9]+', '_', intent["clean_prompt"].lower())[:32].strip("_")
        out = str(Path(output_dir) / f"{slug}_v{i}_s{seed}.stl")
        r = imagine(prompt=f"{intent['clean_prompt']} seed {seed}", output_stl=out)
        if r.get("status") == "success":
            results.append({"seed": seed, "path": r.get("path"),
                            "score": r.get("printability", {}).get("score", -1),
                            "dims_mm": r.get("dims_mm"),
                            "weight_g": r.get("weight_g")})
        else:
            results.append({"seed": seed, "error": r.get("content", [{}])[0].get("text", "?")})

    ranked = sorted([r for r in results if "score" in r], key=lambda r: -r["score"])
    lines = [f"🎲 {len(results)} variations of {intent['clean_prompt']!r}:"]
    for i, r in enumerate(ranked):
        medal = ["🥇", "🥈", "🥉"][i] if i < 3 else f"{i+1}."
        lines.append(f"  {medal} seed {r['seed']}: score {r['score']}/100, "
                     f"{r.get('weight_g','?')}g → {Path(r['path']).name}")
    for r in results:
        if "error" in r:
            lines.append(f"  ❌ seed {r['seed']}: {r['error'][:80]}")
    best = ranked[0] if ranked else None
    return ok("\n".join(lines), results=results,
              best=best, best_path=best["path"] if best else None)
