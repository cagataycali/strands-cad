# MCP Clients

## Claude Code

```bash
claude mcp add strands-cad -- strands-cad-mcp
# or skip heavy groups for faster startup:
claude mcp add strands-cad -- strands-cad-mcp --skip neural,sim
```

## Claude Desktop

Add to your `claude_desktop_config.json`:

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

## Cursor

In Cursor settings → MCP, add a server:

```json
{
  "mcpServers": {
    "strands-cad": {
      "command": "strands-cad-mcp"
    }
  }
}
```

## Kiro

Kiro reads the same MCP server config shape:

```json
{
  "mcpServers": {
    "strands-cad": {
      "command": "strands-cad-mcp",
      "args": ["--skip", "sim"]
    }
  }
}
```

## Any Strands agent (no MCP needed)

If you're building with Strands directly, skip MCP and import the tools:

```python
from strands import Agent
from strands_cad import ALL_TOOLS

agent = Agent(tools=ALL_TOOLS)
agent("Design a bracket, verify weight, and slice it for my X1C.")
```

!!! tip "Missing deps auto-disable"
    Don't have torch or mujoco installed? Those groups simply won't appear —
    the server (and `ALL_TOOLS`) always work with whatever is present.

Next: [HTTP Mode →](http.md)
