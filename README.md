# 🔧 strands-cad

**Atomic CAD, mesh, slice & print tools for [Strands](https://github.com/strands-agents) agents.** Prompt-to-print pipeline: OpenSCAD → STL → 3MF → Bambu Lab.

Generic 3D asset toolkit — every tool does one job well. Compose them however you want.

## Install

```bash
pip install strands-cad                    # core (no heavy deps)
pip install 'strands-cad[mesh]'            # + trimesh, numpy (STL repair/transform)
pip install 'strands-cad[sim]'             # + mujoco (physics validation)
pip install 'strands-cad[bambu]'           # + paho-mqtt, requests (printer control)
pip install 'strands-cad[all]'             # everything
```

**External deps** (install separately):
- `openscad` — for `scad_*` tools (`brew install openscad`)
- `bambu-studio` — for `slice_bambu` (download from bambulab.com)

## The 32 Atomic Tools

| Layer | Tools |
|---|---|
| **SCAD** | `scad_probe`, `scad_render_stl`, `scad_render_png`, `scad_validate` |
| **STL / Mesh** | `stl_parse`, `stl_volume`, `stl_bbox`, `stl_weight`, `stl_repair`, `stl_transform`, `stl_convert` |
| **3MF** | `mf3_pack`, `mf3_unpack`, `mf3_read_metadata` |
| **Slice** | `slice_bambu`, `slice_profile_get`, `slice_estimate` |
| **Bambu Printer** | `bambu_connect`, `bambu_send`, `bambu_status`, `bambu_control`, `bambu_camera`, `bambu_ams` |
| **Sim** | `sim_build_mjcf`, `sim_run_headless`, `sim_view_live`, `sim_inertia_from_stl` |
| **Preview** | `preview_serve`, `preview_stop` |
| **Meta** | `bom_parse`, `bom_total`, `journal_append` |

## Usage

```python
from strands import Agent
from strands_cad import ALL_TOOLS

agent = Agent(tools=ALL_TOOLS)
agent("Render frame.scad to STL, check volume, pack into 3mf and slice for PLA.")
```

Or hand-pick:

```python
from strands_cad import scad_render_stl, stl_weight, mf3_pack

result = scad_render_stl(scad_file="frame.scad", output_stl="frame.stl")
weight = stl_weight(stl_file="frame.stl", material="PLA_SILK", infill=0.15)
plate  = mf3_pack(items=[{"stl": "frame.stl", "name": "frame", "position": [0,0,0]}],
                  output_3mf="plate.3mf")
```

## Design Principles

- **Atomic** — one tool = one verb = one input shape = one output shape
- **No orchestration** inside tools; the agent composes
- **No hidden state** except the Bambu MQTT connection handle
- **Standard response**: `{status: "success"|"error", content: [{text: ...}], ...extra}`

## License

MIT
