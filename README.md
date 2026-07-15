# 🔧 strands-cad

**Atomic CAD, mesh, SDF, slice & print tools for [Strands](https://github.com/strands-agents) agents.** Prompt-to-print pipeline: math → mesh → 3MF → Bambu Lab.

Generic 3D asset toolkit — every tool does one job well. Compose them however you want.

## Install

```bash
pip install strands-cad
```

All dependencies are included (trimesh, sdf, mujoco, scikit-image, paho-mqtt, fast-simplification, meshio, numpy).

**External deps** (install separately):
- `openscad` — for `scad_*` tools (`brew install openscad`)
- `bambu-studio` — optional, for `slice_bambu` (download from bambulab.com)

## The 42 Atomic Tools

| Layer | Tools |
|---|---|
| **SCAD** (parametric CAD) | `scad_probe`, `scad_render_stl`, `scad_render_png`, `scad_validate` |
| **STL / Mesh** | `stl_parse`, `stl_volume`, `stl_bbox`, `stl_weight`, `stl_repair`, `stl_transform`, `stl_convert`, `mesh_decimate`, `mesh_normalize`, `mesh_boolean`, `mesh_combine`, `mesh_hollow` |
| **3MF** | `mf3_pack`, `mf3_unpack`, `mf3_read_metadata` |
| **Slice** | `slice_bambu`, `slice_profile_get`, `slice_estimate` |
| **Bambu Printer** | `bambu_connect`, `bambu_send`, `bambu_status`, `bambu_control`, `bambu_camera`, `bambu_ams` |
| **SDF (math → mesh)** | `sdf_render_stl`, `sdf_list_primitives`, `sdf_gyroid_infill`, `sdf_from_function`, `sdf_lattice_infill_stl` |
| **Sim** | `sim_build_mjcf`, `sim_run_headless`, `sim_view_live`, `sim_inertia_from_stl` |
| **Preview** | `preview_serve`, `preview_stop` |
| **Meta** | `bom_parse`, `bom_total`, `journal_append` |

## Usage

```python
from strands import Agent
from strands_cad import ALL_TOOLS

agent = Agent(tools=ALL_TOOLS)
agent("Design a twisted vase from math, decimate it, and pack for Bambu.")
```

Or hand-pick:

```python
from strands_cad import sdf_render_stl, mesh_decimate, mf3_pack

sdf_render_stl(
    expression="torus(30, 8).twist(radians(180)/60)",
    output_stl="thing.stl", resolution=0.4,
)
mesh_decimate("thing.stl", "thing_lite.stl", target_faces=100_000)
mf3_pack(items=[{"stl":"thing_lite.stl","name":"thing","position":[0,0,0]}],
         output_3mf="plate.3mf")
```

## Design Approaches

**Two paths to a printable object:**

1. **Parametric (OpenSCAD)** — `scad_render_stl(scad_file, output_stl)`.
   Mechanical parts, brackets, arms. Easy to reason about mm-precise dimensions.

2. **SDF / implicit math** — `sdf_render_stl(expression, output_stl)`.
   Organic shapes, twisted geometry, TPMS lattices, blended surfaces.
   Impossible or slow in traditional CAD.

**Then always:**
- `stl_repair` if the mesh is non-manifold
- `mesh_decimate` if it's too heavy (>500k tris)
- `mesh_normalize` to sit on the print bed
- `mf3_pack` to build a slicer-ready plate
- `bambu_send` to actually print (optional)

## Design Principles

- **Atomic** — one tool = one verb = one input shape = one output shape
- **No orchestration** inside tools; the agent composes
- **No hidden state** except the Bambu MQTT connection handle
- **Standard response**: `{status: "success"|"error", content: [{text: ...}], ...extra}`

## License

MIT
