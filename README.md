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

## 🎨 What Can You Render? — Full Showcase

Every example below was **actually executed** with these tools (timings from an M-series Mac). Copy-paste any snippet.

### 1. Parametric Engineering — CadQuery (B-rep / NURBS)

| Object | What It Teaches | Code | Verified Output |
|---|---|---|---|
| **Calibration cube** | Boxes, chamfers | `cq.Workplane("XY").box(20,20,20).edges().chamfer(0.8)` | 2.2 KB STL in 0.01s |
| **Mounting bracket** | Fillets, hole patterns, M4 clearance | see below | 150 KB STL, 0% overhangs, fits X1C bed |
| **Peg-in-hole board** | `pushPoints`, multi-hole ops — robot manipulation training | see below | 100 KB STL |
| **T-block** ("push-T" benchmark) | `union`, robot RL objects | see below | 51 KB STL, 18.05 g PLA |

```python
# T-block — the classic "Push-T" manipulation benchmark object
cq_render_stl(script='''
result = (
    cq.Workplane("XY").box(80, 20, 15)                         # horizontal bar
    .union(cq.Workplane("XY").center(0, -30).box(20, 40, 15))  # vertical stem
    .edges("|Z").fillet(2)
)
''', output_stl="t_block.stl")

# Engineering bracket with M4 mounting holes + center bore
cq_render_stl(script='''
result = (
    cq.Workplane("XY").box(60, 40, 6)
    .edges("|Z").fillet(6)
    .faces(">Z").workplane()
    .rect(46, 26, forConstruction=True).vertices().hole(4.2)   # M4 clearance × 4
    .faces(">Z").workplane().hole(20)                          # center bore
)
''', output_stl="bracket.stl")

# Peg-in-hole board (3 tolerance sizes) — grasping/insertion training
cq_render_stl(script='''
board = cq.Workplane("XY").box(120, 50, 12).edges("|Z").fillet(4)
result = (board.faces(">Z").workplane()
    .pushPoints([(-40, 0)]).hole(10.4)
    .pushPoints([(0, 0)]).hole(15.4)
    .pushPoints([(40, 0)]).hole(20.4))
''', output_stl="peg_board.stl")
```

Need exact geometry for Fusion/SolidWorks/CNC? Swap to `cq_render_step` — same script, lossless STEP out. `cq_render_svg` gives you drawing projections for docs.

### 2. Implicit Math — SDF (organic, twisted, impossible-in-CAD)

| Object | What It Teaches | Expression | Verified Output |
|---|---|---|---|
| **Twisted torus** | `.twist()` warp operator | `torus(30, 8).twist(radians(180)/60)` | 3.7 MB STL in 0.3s |
| **CSG classic** | Booleans as `& - \|` operators | `sphere(20) & box(30) - cylinder(10).orient(X) - cylinder(10).orient(Y) - cylinder(10)` | 2.5 MB STL in 0.6s |
| **Wavy sphere** | Arbitrary `f(p)→d` math via `sdf_from_function` | see below | 2.6 MB STL in 0.2s |
| **Gyroid lattice** | TPMS structures (strong + light) | `sdf_gyroid_infill(size=(40,40,40), period=12, thickness=1.6)` | 40mm cube in 0.5s |

```python
# Any math you can write becomes a solid:
sdf_from_function(
    function_source='''
def f(p):
    x, y, z = p[:,0], p[:,1], p[:,2]
    return np.sqrt(x**2 + y**2 + z**2) - 20 + 2.5*np.sin(x*0.6)*np.sin(y*0.6)*np.sin(z*0.6)
''',
    output_stl="wavy_sphere.stl",
    bounds=[-30,-30,-30, 30,30,30],
)

# Fill ANY existing STL interior with gyroid/schwarz-p/diamond lattice:
sdf_lattice_infill_stl(input_stl="bracket.stl", output_stl="bracket_light.stl",
                       lattice="gyroid", period=10, shell_thickness=2)
```

Run `sdf_list_primitives()` to see all ~60 primitives/operators (sphere, capsule, rounded_box, twist, bend, shell, dilate, erode, smooth blends…).

### 3. Neural Generation — text/image → 3D (Shap-E)

```python
neural_text_to_stl(prompt="a stylized rocket ship", output_stl="rocket.stl", steps=64)
neural_image_to_stl(image_path="reference.jpg", output_stl="from_photo.stl")
```

First call downloads ~1 GB weights; runs on MPS/CUDA/CPU. Great for concept props and organic shapes no one wants to model by hand.

### 4. 2D → 3D — text, logos, images

| Tool | Input | Output |
|---|---|---|
| `mesh_from_text` | `"STRANDS"`, any system font | Extruded nameplate STL (verified: 253 KB) |
| `mesh_from_svg` | Logo .svg | Extruded badge/profile |
| `mesh_from_image` | Photo (grayscale) | Lithophane / relief heightmap |

### 5. Robot Training Objects — the strands-labs/robots workflow

Generate physical props for RL/manipulation research, then simulate them **before** printing:

```python
# 1. Design the object (T-block, peg board, cubes — see §1)
# 2. Get real physical properties (PLA @ 15% infill):
sim_inertia_from_stl(stl_file="t_block.stl", material="PLA")
#    → mass=18.05 g, COM, full inertia tensor

# 3. Build a MuJoCo world with correct mass/inertia:
sim_build_mjcf(meshes=[{"name": "t_block", "path": "t_block.stl",
                        "mass_g": 18.05, "pos": [0, 0, 0.05]}],
               output_mjcf="world.xml")

# 4. Simulate headless (verified: 500 steps / 1.0s sim):
sim_run_headless(mjcf_file="world.xml", duration_sec=1.0)
# ... or watch it live:
sim_view_live(mjcf_file="world.xml")

# 5. Synthetic scan data for perception training:
pointcloud_from_stl(stl_file="t_block.stl", output_xyz="scan.xyz", n_points=5000)
pointcloud_to_stl(pointcloud_file="scan.xyz", output_stl="reconstructed.stl")  # closes the loop
```

Same pipeline powers parts for [strands-labs/robots](https://github.com/strands-labs/robots) — rover mounts, drone frames, gripper fingers: design → validate mass/inertia → simulate → print.

### 6. Verify Before You Print

Every mesh gets a free QA pass (all verified on the assets above):

```python
stl_printability("bracket.stl", printer="X1C")   # → fits bed, 0% overhangs, no supports
stl_weight("t_block.stl", material="PLA")        # → 18.05 g @ 15% infill
stl_orient("part.stl", "oriented.stl")           # auto-rotate to minimize supports
stl_check_clearance("peg.stl", "hole.stl")       # 0.2mm tight / 0.4mm loose FDM fits
mesh_decimate("gyroid.stl", "lite.stl", target_faces=80_000)  # 420k → 80k faces (19%)
stl_repair("broken.stl", "fixed.stl")            # fill holes, fix normals, watertight
mesh_hollow("statue.stl", "shell.stl", wall_thickness=2, drain_hole_diameter=4)
```

### 7. Plate → Slice → Print (Bambu Lab, fully closed loop)

```python
# Pack multiple parts on one plate (groups = multi-material assemblies):
mf3_pack(items=[
    {"stl": "t_block.stl", "name": "T-block", "position": [0, 0, 0]},
    {"stl": "calibration_cube.stl", "name": "cube", "position": [80, 0, 0]},
], output_3mf="plate.3mf", title="robot training objects")

slice_bambu(input_3mf="plate.3mf", output_gcode="plate.gcode",
            printer_model="Bambu Lab X1 Carbon", profile="PLA_0_20")
gcode_check("plate.gcode")                        # temps, bounds, cold extrusion
slice_estimate("plate.gcode")                     # print time + filament grams

bambu_connect(ip="192.168.1.x", access_code="...", serial="01P00A...")
bambu_ams()                                       # check loaded filament
bambu_upload(file_path="plate.gcode")             # FTPS → SD card
bambu_send(file_path="plate.gcode")               # start the job
bambu_status(); bambu_camera()                    # watch progress + chamber cam
```

### Cheat Sheet — Which Path When?

| You want… | Use | Why |
|---|---|---|
| Brackets, mounts, enclosures | **CadQuery** | Fillets/chamfers/holes are first-class |
| Parametric families (`-D` overrides) | **OpenSCAD** | `scad_render_stl(defines={"W": 50})` |
| Twisted / blended / organic | **SDF** | Warps & smooth booleans are free |
| Lightweight functional parts | **SDF gyroid infill** | TPMS = max stiffness/gram |
| "Make me a dragon" | **Neural** | Text/image → mesh |
| Logos, nameplates, lithophanes | **mesh_from_text / svg / image** | 2D → extrusion |
| RL / manipulation props | **CadQuery + sim_*** | Design + physics validation |
| Vendor STEP files | **cq_import_step** | STEP → STL for slicing |
| See it before printing | **scad_view / scad_turntable** | Agent receives actual pixels |

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
