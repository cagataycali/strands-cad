"""Atomic tools for strands-cad."""
from strands_cad.tools.scad import scad_probe, scad_render_stl, scad_render_png, scad_validate
from strands_cad.tools.stl import (
    stl_parse, stl_volume, stl_bbox, stl_weight, stl_repair, stl_transform, stl_convert,
    mesh_decimate, mesh_normalize, mesh_boolean, mesh_combine, mesh_hollow,
)
from strands_cad.tools.mf3 import mf3_pack, mf3_unpack, mf3_read_metadata
from strands_cad.tools.slice import slice_bambu, slice_profile_get, slice_estimate
from strands_cad.tools.bambu import (
    bambu_connect, bambu_send, bambu_status, bambu_control, bambu_camera, bambu_ams
)
from strands_cad.tools.sim import sim_build_mjcf, sim_run_headless, sim_view_live, sim_inertia_from_stl
from strands_cad.tools.preview import preview_serve, preview_stop
from strands_cad.tools.meta import bom_parse, bom_total, journal_append
from strands_cad.tools.sdf_tools import (
    sdf_render_stl, sdf_list_primitives, sdf_gyroid_infill,
    sdf_from_function, sdf_lattice_infill_stl,
)

__all__ = [
    # SCAD
    "scad_probe", "scad_render_stl", "scad_render_png", "scad_validate",
    # STL / mesh
    "stl_parse", "stl_volume", "stl_bbox", "stl_weight", "stl_repair", "stl_transform", "stl_convert",
    "mesh_decimate", "mesh_normalize", "mesh_boolean", "mesh_combine", "mesh_hollow",
    # 3MF
    "mf3_pack", "mf3_unpack", "mf3_read_metadata",
    # Slice
    "slice_bambu", "slice_profile_get", "slice_estimate",
    # Bambu printer
    "bambu_connect", "bambu_send", "bambu_status", "bambu_control", "bambu_camera", "bambu_ams",
    # Sim
    "sim_build_mjcf", "sim_run_headless", "sim_view_live", "sim_inertia_from_stl",
    # Preview
    "preview_serve", "preview_stop",
    # Meta
    "bom_parse", "bom_total", "journal_append",
    # SDF
    "sdf_render_stl", "sdf_list_primitives", "sdf_gyroid_infill",
    "sdf_from_function", "sdf_lattice_infill_stl",
]
