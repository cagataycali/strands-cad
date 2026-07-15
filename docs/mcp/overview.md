# MCP Server

All 65 tools are exposable over the
[Model Context Protocol](https://modelcontextprotocol.io/) via the
`strands-cad-mcp` entrypoint (built on
[strands-mcp-server](https://github.com/cagataycali/strands-mcp-server)).

Drop them into Claude Code / Claude Desktop / Cursor / Kiro / any MCP client and
your model can model, simulate, slice, and print in one conversation.

```mermaid
flowchart LR
    subgraph clients [MCP clients]
      CC[Claude Code]
      CD[Claude Desktop]
      CU[Cursor]
      KI[Kiro]
    end
    clients <-->|stdio / HTTP| MCP["strands-cad-mcp<br/>65 tools, 15 groups"]
    MCP --> CAD[strands_cad tools]
    CAD --> PRINTER[🖨️ Bambu Lab]
```

## Quick add

```bash
claude mcp add strands-cad -- strands-cad-mcp
```

## Tool groups

The server imports groups **lazily** — a missing optional dep (torch, mujoco,
cadquery, sdf) only disables its group, never the whole server. It always boots
with whatever is installed.

| Group | Tools | Needs |
|---|---|---|
| `scad` | 6 | openscad |
| `gcode` | 2 | — |
| `stl` | 12 | — |
| `printability` | 3 | — |
| `mesh_gen` | 3 | — |
| `mf3` | 3 | — |
| `slice` | 3 | slicer |
| `bambu` | 7 | — |
| `sim` | 4 | mujoco |
| `preview` | 2 | — |
| `meta` | 3 | — |
| `sdf` | 5 | fogleman/sdf |
| `cadquery` | 4 | cadquery |
| `neural` | 5 | torch |
| `dashboard` | 3 | fastapi |

## Options

| Flag | Effect |
|---|---|
| *(default)* | stdio transport, all available tool groups |
| `--http --port N` | StreamableHTTP transport instead of stdio |
| `--stateless` | Fresh transport per request (horizontal scaling) |
| `--tools a,b,c` | Expose only named tools |
| `--skip neural,sim,…` | Skip tool groups |
| `--agent-invocation` | Also expose `invoke_agent` for full conversations |
| `--debug` | Verbose logging (stderr) |

```bash
# Skip heavy groups for faster startup:
strands-cad-mcp --skip neural,sim

# Expose only a handful of tools:
strands-cad-mcp --tools scad_render_stl,stl_parse,slice_bambu
```

Read on:

- [Clients (Claude / Cursor / Kiro) →](clients.md)
- [HTTP Mode →](http.md)
