# 🔧 strands-cad

**Atomic CAD, mesh, SDF, cadquery, neural & print tools for [Strands](https://github.com/strands-agents) agents.**

Four independent paths to a printable 3D asset:

| Path | Tool | Best For |
|---|---|---|
| **Parametric SCAD** | `scad_render_stl` | Mechanical, brackets, parametric families |
| **B-rep CAD (NURBS)** | `cq_render_stl` / `cq_render_step` | Fillets, chamfers, engineering-grade parts |
| **SDF / implicit math** | `sdf_render_stl` | Organic, twisted, TPMS, blended surfaces |
| **Neural (text/image → 3D)** | `neural_text_to_stl` | AI generation from prompt or reference photo |

All roads lead to STL → 3MF → Bambu Lab.

## Install

```bash
pip install strands-cad
```

Everything is a hard dependency — no extras needed. Includes:
`trimesh, sdf (fogleman), cadquery, shap-e, torch, scikit-image, fast-simplification, mujoco, paho-mqtt, scipy, numpy, meshio`.

**External** (system):
- `openscad` for `scad_*` tools (`brew install openscad`)
- `bambu-studio` for `slice_bambu` (bambulab.com)

## The 51 Atomic Tools

| Layer | Tools |
|---|---|
| **SCAD** (parametric) | `scad_probe`, `scad_render_stl`, `scad_render_png`, `scad_validate` |
| **CadQuery** (B-rep, NURBS) | `cq_render_stl`, `cq_render_step`, `cq_import_step`, `cq_render_svg` |
| **SDF** (implicit math) | `sdf_render_stl`, `sdf_list_primitives`, `sdf_gyroid_infill`, `sdf_from_function`, `sdf_lattice_infill_stl` |
| **Neural** (AI generation) | `neural_text_to_stl`, `neural_image_to_stl` |
| **Point Cloud** | `pointcloud_from_stl`, `pointcloud_to_stl`, `pointcloud_downsample` |
| **STL / Mesh** | `stl_parse`, `stl_volume`, `stl_bbox`, `stl_weight`, `stl_repair`, `stl_transform`, `stl_convert`, `mesh_decimate`, `mesh_normalize`, `mesh_boolean`, `mesh_combine`, `mesh_hollow` |
| **3MF** | `mf3_pack`, `mf3_unpack`, `mf3_read_metadata` |
| **Slice** | `slice_bambu`, `slice_profile_get`, `slice_estimate` |
| **Bambu Printer** | `bambu_connect`, `bambu_send`, `bambu_status`, `bambu_control`, `bambu_camera`, `bambu_ams` |
| **Sim** | `sim_build_mjcf`, `sim_run_headless`, `sim_view_live`, `sim_inertia_from_stl` |
| **Preview** | `preview_serve`, `preview_stop` |
| **Meta** | `bom_parse`, `bom_total`, `journal_append` |

## Quick Examples

### Parametric CAD (CadQuery)
```python
from strands_cad import cq_render_stl

cq_render_stl(script='''
result = (
    cq.Workplane("XY").box(60, 40, 5)
    .edges().fillet(2)
    .faces(">Z").hole(20)
)
''', output_stl="bracket.stl")
```

### Implicit Math (SDF)
```python
from strands_cad import sdf_render_stl

sdf_render_stl(
    expression="torus(30, 8).twist(radians(180)/60)",
    output_stl="twisted_torus.stl",
    resolution=0.4,
)
```

### AI Text → 3D (Shap-E)
```python
from strands_cad import neural_text_to_stl

neural_text_to_stl(
    prompt="a stylized rocket ship",
    output_stl="rocket.stl",
    steps=64,
)
```

### Full agent
```python
from strands import Agent
from strands_cad import ALL_TOOLS

agent = Agent(tools=ALL_TOOLS)
agent("Generate a mechanical bracket with M4 mounting holes, "
      "verify weight in PLA, and pack it for my Bambu P1S.")
```

## Design Principles

- **Atomic** — one tool = one verb = one input shape = one output shape
- **No orchestration** inside tools; the agent composes
- **No hidden state** except the Bambu MQTT connection handle
- **Standard response**: `{status, content: [{text}], ...extras}`

## License

MIT
