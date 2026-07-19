"""Atomic tools for strands-cad.

Imports are resilient: core groups (scad, stl, mf3, slice, bambu, meta, gcode)
always load. Heavy/optional groups (neural→torch, sim→mujoco, sdf→fogleman-sdf,
cadquery→OCP, dashboard→fastapi) are imported best-effort so that a plain
`pip install strands-cad` yields a working `import strands_cad` even before the
optional extras are installed. Missing a group only drops its tools.
"""
import logging as _log

_logger = _log.getLogger("strands_cad.tools")
__all__ = []


def _pull(module: str, names: list[str]) -> None:
    """Best-effort import of `names` from `strands_cad.tools.<module>`."""
    import importlib
    try:
        mod = importlib.import_module(f"strands_cad.tools.{module}")
    except Exception as e:  # optional dep missing → skip the whole group
        _logger.debug(f"tool group '{module}' unavailable ({type(e).__name__}: {e})")
        return
    g = globals()
    for n in names:
        fn = getattr(mod, n, None)
        if fn is not None:
            g[n] = fn
            __all__.append(n)


# ── always-available core groups ──────────────────────────────────────────
_pull("scad", ["scad_probe", "scad_render_stl", "scad_render_png",
               "scad_validate", "scad_view", "scad_turntable"])
_pull("gcode", ["gcode_check", "gcode_preview_png"])
_pull("stl", ["stl_parse", "stl_volume", "stl_bbox", "stl_weight", "stl_repair",
              "stl_transform", "stl_convert", "mesh_decimate", "mesh_normalize",
              "mesh_boolean", "mesh_combine", "mesh_hollow"])
_pull("mf3", ["mf3_pack", "mf3_unpack", "mf3_read_metadata"])
_pull("slice", ["slice_bambu", "slice_profile_get", "slice_estimate"])
_pull("bambu", ["bambu_connect", "bambu_send", "bambu_upload", "bambu_status",
                "bambu_control", "bambu_camera", "bambu_ams"])
_pull("meta", ["bom_parse", "bom_total", "journal_append"])
_pull("preview", ["preview_serve", "preview_stop"])

# ── optional groups (need extras) ─────────────────────────────────────────
_pull("cadquery_tools", ["cq_render_stl", "cq_render_step", "cq_import_step",
                         "cq_render_svg"])
_pull("sdf_tools", ["sdf_render_stl", "sdf_list_primitives", "sdf_gyroid_infill",
                    "sdf_from_function", "sdf_lattice_infill_stl"])
_pull("sim", ["sim_build_mjcf", "sim_run_headless", "sim_view_live",
              "sim_inertia_from_stl"])
_pull("neural_tools", ["neural_text_to_stl", "neural_image_to_stl",
                       "pointcloud_from_stl", "pointcloud_to_stl",
                       "pointcloud_downsample"])
# dashboard control tools (need [dashboard] extra)
_pull("dashboard", ["dashboard_start", "dashboard_stop", "dashboard_status"])
