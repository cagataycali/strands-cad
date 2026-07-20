# Architecture

## Package layout

```
strands_cad/
├── __init__.py          # ALL_TOOLS assembled from whatever loaded
├── mcp.py               # strands-cad-mcp entrypoint (15 tool groups)
├── install_extras.py    # resolver-safe sdf + neural installer
├── install_slicer.py    # PrusaSlicer/OrcaSlicer helper
├── tools/               # the 65 atomic tools
│   ├── scad.py          # parametric (OpenSCAD)
│   ├── cadquery_tools.py# B-rep / NURBS
│   ├── sdf_tools.py     # implicit math
│   ├── neural_tools.py  # shap-e + point clouds
│   ├── mesh_gen.py      # text/svg/image → 3D
│   ├── stl.py           # mesh ops
│   ├── printability.py  # QA (weight/overhang/clearance)
│   ├── mf3.py           # 3MF plates
│   ├── slice.py         # slicing (docker/host OrcaSlicer)
│   ├── gcode.py         # gcode check + preview
│   ├── bambu.py         # printer MQTT/FTPS/camera
│   ├── sim.py           # MuJoCo
│   ├── preview.py       # live PNG server
│   └── meta.py          # BOM + journal
└── dashboard/           # [dashboard] extra
    ├── server.py        # FastAPI app (:8099)
    ├── auth.py          # WebAuthn / passkeys
    ├── camera.py        # RTSPS → MJPEG (pure-python handshake)
    ├── plate.py         # 3D build-plate state
    ├── jobs.py          # slice + print jobs
    ├── chat_agent.py    # on-page agent
    ├── realtime.py      # OpenAI Realtime voice tokens
    ├── telegram.py      # notify/control bridge
    ├── thinker.py       # autonomous background loop
    └── frontend/index.html  # single-file SPA
```

## Resilient tool loading

Both `strands_cad.tools` and `strands_cad.mcp` import each group **best-effort**.
A missing optional dep (torch, mujoco, cadquery, fastapi) drops only that group
— `import strands_cad` always succeeds.

```mermaid
flowchart TB
    IMPORT[import strands_cad] --> PULL{_pull each group}
    PULL -->|ok| ADD[add to ALL_TOOLS]
    PULL -->|ImportError| SKIP[log + skip group]
    ADD --> DONE([usable tool list])
    SKIP --> DONE
```

## Two consumption surfaces

The same tool functions power both surfaces — no duplication:

```mermaid
flowchart LR
    TOOLS[strands_cad.tools\n65 @tool functions]
    TOOLS --> A["Direct import\nAgent(tools=ALL_TOOLS)"]
    TOOLS --> B["MCP server\nstrands-cad-mcp"]
    B --> STDIO[stdio]
    B --> HTTP[StreamableHTTP]
```

## The interchange formats

```mermaid
flowchart LR
    subgraph design [design]
      SCAD & CQ & SDF & NEURAL & TWOD[2D→3D]
    end
    design --> STL[[STL]]
    STL --> STEP[[STEP]]
    STL --> MF3[[3MF]]
    MF3 --> GCODE[[G-code]]
    GCODE --> HW[🖨️ Bambu Lab]
    HW -.MQTT/FTPS/RTSPS.-> DASH[Dashboard]
```

## Design principles (recap)

- **Atomic** — one tool, one job.
- **No orchestration inside tools** — the agent composes.
- **No hidden state** — except the Bambu MQTT handle.
- **Standard response** — `{status, content:[{text}], **extras}`.

Next: [Related Projects →](related.md)
