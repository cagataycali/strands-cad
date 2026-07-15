# 🔧 strands-cad

[![PyPI version](https://badge.fury.io/py/strands-cad.svg)](https://pypi.org/project/strands-cad/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

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

Core includes: `trimesh, cadquery, torch, scipy, scikit-image, mujoco, paho-mqtt, fast-simplification, meshio, numpy`.

### Optional git-only extras

PyPI disallows direct git URLs, so two libraries install separately:

```bash
python -m strands_cad.install_extras            # both
# OR selective:
python -m strands_cad.install_extras sdf        # fogleman/sdf → sdf_render_stl
python -m strands_cad.install_extras neural     # openai/shap-e → neural_text_to_stl
```

### External system tools

- `openscad` for `scad_*` tools — `brew install openscad`
- `bambu-studio` for `slice_bambu` — download from bambulab.com

## The 56 Atomic Tools

| Layer | Tools |
|---|---|
| **SCAD** (parametric) | `scad_probe`, `scad_render_stl`, `scad_render_png`, `scad_validate`, `scad_view`, `scad_turntable` |
| **G-code** | `gcode_check`, `gcode_preview_png` |
| **CadQuery** (B-rep, NURBS) | `cq_render_stl`, `cq_render_step`, `cq_import_step`, `cq_render_svg` |
| **SDF** (implicit math) | `sdf_render_stl`, `sdf_list_primitives`, `sdf_gyroid_infill`, `sdf_from_function`, `sdf_lattice_infill_stl` |
| **Neural** (AI generation) | `neural_text_to_stl`, `neural_image_to_stl` |
| **Point Cloud** | `pointcloud_from_stl`, `pointcloud_to_stl`, `pointcloud_downsample` |
| **STL / Mesh** | `stl_parse`, `stl_volume`, `stl_bbox`, `stl_weight`, `stl_repair`, `stl_transform`, `stl_convert`, `mesh_decimate`, `mesh_normalize`, `mesh_boolean`, `mesh_combine`, `mesh_hollow` |
| **3MF** | `mf3_pack`, `mf3_unpack`, `mf3_read_metadata` |
| **Slice** | `slice_bambu`, `slice_profile_get`, `slice_estimate` |
| **Bambu Printer** | `bambu_connect`, `bambu_send`, `bambu_upload`, `bambu_status`, `bambu_control`, `bambu_camera`, `bambu_ams` |
| **Sim** | `sim_build_mjcf`, `sim_run_headless`, `sim_view_live`, `sim_inertia_from_stl` |
| **Preview** | `preview_serve`, `preview_stop` |
| **Meta** | `bom_parse`, `bom_total`, `journal_append` |

## MCP Server (Claude Code, Claude Desktop, Cursor, Kiro)

All 56 tools are exposable over the [Model Context Protocol](https://modelcontextprotocol.io/) via the `strands-cad-mcp` entrypoint (built on [strands-mcp-server](https://github.com/cagataycali/strands-mcp-server)).

### Claude Code

```bash
claude mcp add strands-cad -- strands-cad-mcp
# or skip heavy groups for faster startup:
claude mcp add strands-cad -- strands-cad-mcp --skip neural,sim
```

### Claude Desktop

```json
{
  "mcpServers": {
    "strands-cad": {
      "command": "strands-cad-mcp",
      "args": ["--skip", "neural"]
    }
  }
}
```

### HTTP mode (multi-client / remote)

```bash
strands-cad-mcp --http --port 8000            # → http://localhost:8000/mcp
strands-cad-mcp --http --port 8000 --stateless  # multi-node scalable
```

### Options

| Flag | Effect |
|---|---|
| *(default)* | stdio transport, all available tool groups |
| `--http --port N` | StreamableHTTP transport instead of stdio |
| `--stateless` | Fresh transport per request (horizontal scaling) |
| `--tools a,b,c` | Expose only named tools |
| `--skip neural,sim,...` | Skip tool groups (`scad,stl,mf3,slice,bambu,sim,preview,meta,sdf,cadquery,neural`) |
| `--agent-invocation` | Also expose `invoke_agent` for full conversations |
| `--debug` | Verbose logging (stderr) |

Missing optional deps (torch, mujoco, cadquery, sdf) auto-disable their group — the server always boots with whatever is installed.

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
