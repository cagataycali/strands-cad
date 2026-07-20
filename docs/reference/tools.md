# Tool Index

Every tool is **atomic** — one verb, one input shape, one output shape. This
page is generated from the live tool registry, so it always matches the
installed version.

!!! info "Availability"
    Optional groups (SDF, neural, sim, dashboard) only appear once their extra
    is installed. The counts below reflect a **full** install.

Total: **65 tools** across 15 groups.

## SCAD — parametric (OpenSCAD)

| Tool | Description |
|---|---|
| `scad_probe` | Extract runtime values of OpenSCAD variables via echo probe. |
| `scad_render_png` | Render one .scad file to a PNG preview. |
| `scad_render_stl` | Render one .scad file to one STL. |
| `scad_turntable` | Render multiple named views in one call and return ALL images (visual inspection). |
| `scad_validate` | Check probed values against constraints. |
| `scad_view` | Render a .scad file AND return the image so the agent can SEE it. |

## CadQuery — B-rep (NURBS)

| Tool | Description |
|---|---|
| `cq_import_step` | Import a STEP file (from Fusion/SolidWorks/etc.) and re-export as STL. |
| `cq_render_step` | Execute a CadQuery script and export to STEP (B-rep, exact NURBS). |
| `cq_render_stl` | Execute a CadQuery script and export the resulting solid to STL. |
| `cq_render_svg` | Execute CadQuery + render an SVG projection (for docs / drawings). |

## SDF — implicit math

| Tool | Description |
|---|---|
| `sdf_from_function` | Mesh an arbitrary Python `f(p) -> distance` implicit function. |
| `sdf_gyroid_infill` | Generate a gyroid TPMS (triply-periodic minimal surface) lattice. |
| `sdf_lattice_infill_stl` | Fill an existing STL's interior with a TPMS lattice (functional/lightweight). |
| `sdf_list_primitives` | List every SDF primitive & operator available inside sdf expressions. |
| `sdf_render_stl` | Evaluate a Python SDF expression and mesh it to STL via marching cubes. |

## Neural + Point Cloud

| Tool | Description |
|---|---|
| `neural_image_to_stl` | Generate a 3D mesh from a reference image using Shap-E image-conditioned model. |
| `neural_text_to_stl` | Generate a 3D mesh from a text prompt using OpenAI Shap-E. |
| `pointcloud_downsample` | Voxel-downsample a point cloud (one representative point per voxel cube). |
| `pointcloud_from_stl` | Sample a point cloud from an STL mesh. |
| `pointcloud_to_stl` | Reconstruct a mesh from a point cloud (alpha-shape / ball-pivot style). |

## 2D → 3D (text/svg/image)

| Tool | Description |
|---|---|
| `mesh_from_image` | Convert a grayscale image into a heightmap mesh (lithophane / relief). |
| `mesh_from_svg` | Extrude an SVG's paths into a 3D mesh (logos, profiles, badges). |
| `mesh_from_text` | Create extruded 3D text (nameplates, labels, embossing stock). |

## STL / Mesh

| Tool | Description |
|---|---|
| `mesh_boolean` | Boolean op (union/difference/intersection) between two meshes. |
| `mesh_combine` | Combine multiple STL files into ONE STL as separate concatenated meshes. |
| `mesh_decimate` | Reduce triangle count while preserving overall shape (quadric decimation). |
| `mesh_hollow` | Hollow out a solid mesh (offset inward by wall_thickness, subtract). |
| `mesh_normalize` | Auto-orient a mesh for the print bed: center XY, drop to Z=0, optionally lay flat. |
| `stl_bbox` | Compute axis-aligned bounding box. |
| `stl_convert` | Convert between mesh formats (STL / OBJ / PLY / GLB / 3MF). |
| `stl_parse` | Parse an STL file and return vertex/triangle counts + bounding info. |
| `stl_repair` | Repair an STL (fill holes, fix normals, remove degenerate faces) using trimesh. |
| `stl_transform` | Apply affine transform (translate/rotate/scale) and write new STL. |
| `stl_volume` | Compute solid volume of an STL in cm³ (via signed-tetrahedron sum). |
| `stl_weight` | Estimate printed part weight. |

## Printability / QA

| Tool | Description |
|---|---|
| `stl_check_clearance` | Check clearance/interference between two meshes (assembly fit). |
| `stl_orient` | Auto-orient an STL for printing: test 24 axis-aligned rotations, pick the one |
| `stl_printability` | Analyze an STL for FDM printability: overhangs, bed fit, bed contact. |

## 3MF

| Tool | Description |
|---|---|
| `mf3_pack` | Pack one or more STLs into a single .3mf file. |
| `mf3_read_metadata` | Read metadata + object listing from a 3MF without loading geometry. |
| `mf3_unpack` | Unpack a .3mf archive to a directory. |

## Slice

| Tool | Description |
|---|---|
| `slice_bambu` | Slice a 3MF using Bambu Studio CLI. |
| `slice_estimate` | Estimate print time + filament from G-code header comments. |
| `slice_profile_get` | Fetch a built-in generic slicing profile. |

## G-code

| Tool | Description |
|---|---|
| `gcode_check` | Safety-check a G-code file: temperatures, bounds, cold extrusion. |
| `gcode_preview_png` | Render a top-down toolpath preview PNG from G-code (extrusion moves only). |

## Bambu Printer

| Tool | Description |
|---|---|
| `bambu_ams` | Get AMS (Automatic Material System) filament status. |
| `bambu_camera` | Fetch a JPEG snapshot from the printer's chamber camera. |
| `bambu_connect` | Connect to a Bambu Lab printer over LAN MQTT. |
| `bambu_control` | Control the print job (pause/resume/stop). |
| `bambu_send` | Upload a sliced 3MF/G-code and start the print job. |
| `bambu_status` | Get current printer state (poll cached MQTT report). |
| `bambu_upload` | Upload a file to the printer's SD card via FTPS (implicit TLS, port 990). |

## Simulation (MuJoCo)

| Tool | Description |
|---|---|
| `sim_build_mjcf` | Compose a MuJoCo MJCF (XML) referencing one or more mesh files. |
| `sim_inertia_from_stl` | Compute mass + inertia tensor + center of mass from an STL. |
| `sim_run_headless` | Run a MuJoCo simulation headless and return final state + summary metrics. |
| `sim_view_live` | Launch an interactive MuJoCo viewer (non-blocking). |

## Preview

| Tool | Description |
|---|---|
| `preview_serve` | Start a live-refresh HTTP server serving PNG renders from a directory. |
| `preview_stop` | Stop a previously-started preview server. |

## Meta / BOM

| Tool | Description |
|---|---|
| `bom_parse` | Parse a BOM (bill of materials) CSV. |
| `bom_total` | Compute total cost of a BOM. |
| `journal_append` | Append a dated entry to a Markdown design journal (creates file if missing). |

## Dashboard (WebAuthn + camera)

| Tool | Description |
|---|---|
| `dashboard_start` | Start the WebAuthn-gated printer dashboard (live camera + control). |
| `dashboard_status` | Report whether the printer dashboard is running and on which port. |
| `dashboard_stop` | Stop the printer dashboard. |

