# Quickstart

From zero to a printable STL in about a minute.

## 1. Install

```bash
pip install strands-cad
```

## 2. Your first part (three ways)

=== "Parametric CAD"

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

=== "Implicit Math (SDF)"

    ```python
    from strands_cad import sdf_render_stl

    sdf_render_stl(
        expression="torus(30, 8).twist(radians(180)/60)",
        output_stl="twisted_torus.stl",
        resolution=0.4,
    )
    ```

=== "AI Text → 3D"

    ```python
    from strands_cad import neural_text_to_stl

    neural_text_to_stl(
        prompt="a stylized rocket ship",
        output_stl="rocket.stl",
        steps=64,
    )
    ```

## 3. Verify before you print

```python
from strands_cad import stl_weight, stl_printability

stl_weight("bracket.stl", material="PLA")        # → grams @ 15% infill
stl_printability("bracket.stl", printer="X1C")   # → fits bed? overhangs? supports?
```

## 4. Let an agent do the whole loop

```python
from strands import Agent
from strands_cad import ALL_TOOLS

agent = Agent(tools=ALL_TOOLS)
agent("Generate a mechanical bracket with M4 mounting holes, "
      "verify its weight in PLA, and pack it for my Bambu P1S.")
```

The agent composes the atomic tools itself: design → verify → plate → slice →
(optionally) print.

## 5. Watch it print (dashboard)

```bash
pip install "strands-cad[dashboard]"

BAMBU_IP=192.168.1.164 BAMBU_ACCESS_CODE=xxxxxxxx \
  strands-cad-dashboard --tls          # → https://localhost:8099
```

Open the URL, tap **Create passkey**, and you're watching the chamber camera live.

## 6. Wire it into your editor (MCP)

```bash
claude mcp add strands-cad -- strands-cad-mcp
```

All 65 tools become available inside Claude Code / Cursor / Kiro.

---

Where to go next:

- [The Four Paths →](../paths/overview.md) — pick the right modeling approach
- [Prompt-to-Print Pipeline →](../pipeline/overview.md) — the full closed loop
- [Dashboard →](../dashboard/overview.md) — the passkey-sealed cockpit
