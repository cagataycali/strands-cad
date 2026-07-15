"""strands-cad — atomic 3D tools for Strands agents."""
__version__ = "0.3.4"

from strands_cad.tools import (
    scad_probe, scad_render_stl, scad_render_png, scad_validate,
    scad_view, scad_turntable,
    gcode_check, gcode_preview_png,
    stl_parse, stl_volume, stl_bbox, stl_weight, stl_repair,
    stl_transform, stl_convert,
    mesh_decimate, mesh_normalize, mesh_boolean, mesh_combine, mesh_hollow,
    mf3_pack, mf3_unpack, mf3_read_metadata,
    slice_bambu, slice_profile_get, slice_estimate,
    bambu_connect, bambu_send, bambu_upload, bambu_status, bambu_control,
    bambu_camera, bambu_ams,
    sim_build_mjcf, sim_run_headless, sim_view_live, sim_inertia_from_stl,
    preview_serve, preview_stop,
    bom_parse, bom_total, journal_append,
    sdf_render_stl, sdf_list_primitives, sdf_gyroid_infill,
    sdf_from_function, sdf_lattice_infill_stl,
    cq_render_stl, cq_render_step, cq_import_step, cq_render_svg,
    neural_text_to_stl, neural_image_to_stl,
    pointcloud_from_stl, pointcloud_to_stl, pointcloud_downsample,
)

ALL_TOOLS = [
    scad_probe, scad_render_stl, scad_render_png, scad_validate,
    scad_view, scad_turntable,
    gcode_check, gcode_preview_png,
    stl_parse, stl_volume, stl_bbox, stl_weight, stl_repair,
    stl_transform, stl_convert,
    mesh_decimate, mesh_normalize, mesh_boolean, mesh_combine, mesh_hollow,
    mf3_pack, mf3_unpack, mf3_read_metadata,
    slice_bambu, slice_profile_get, slice_estimate,
    bambu_connect, bambu_send, bambu_upload, bambu_status, bambu_control,
    bambu_camera, bambu_ams,
    sim_build_mjcf, sim_run_headless, sim_view_live, sim_inertia_from_stl,
    preview_serve, preview_stop,
    bom_parse, bom_total, journal_append,
    sdf_render_stl, sdf_list_primitives, sdf_gyroid_infill,
    sdf_from_function, sdf_lattice_infill_stl,
    cq_render_stl, cq_render_step, cq_import_step, cq_render_svg,
    neural_text_to_stl, neural_image_to_stl,
    pointcloud_from_stl, pointcloud_to_stl, pointcloud_downsample,
]
