# Install

## Zero-shot — one command

Works on **Python 3.10 – 3.13**:

```bash
pip install strands-cad
```

That's it. Core gives you SCAD, CadQuery (B-rep), all mesh/STL/3MF ops, slicing,
Bambu printer control, and the MCP server — **no build-from-source landmines.**

!!! info "Why *zero-shot* matters here"
    cadquery pulls in `numba`, and naive resolvers used to drag in the ancient
    `numba 0.53 / llvmlite 0.36` combo that *fails to compile* on modern Python.
    strands-cad pins `numba>=0.59` / `llvmlite>=0.42` so `pip install` just…
    works. First try. Every time.

## Optional extras

Kept out of core so the base install stays lean & fast — opt in to what you need.

=== "Dashboard"

    ```bash
    pip install "strands-cad[dashboard]"
    ```
    🖥️ WebAuthn-gated live printer dashboard + RTSPS chamber camera
    (FastAPI + passkeys + bundled ffmpeg).

=== "Simulation"

    ```bash
    pip install "strands-cad[sim]"
    ```
    🦿 MuJoCo physics — `sim_*` tools for robot-prop validation.

=== "Neural"

    ```bash
    pip install "strands-cad[neural]"
    ```
    🧠 torch/torchvision — required for shap-e text/image → 3D.

=== "Everything"

    ```bash
    pip install "strands-cad[all]"
    ```
    neural + sim + dashboard in one shot.

## Git-only extras (SDF + neural weights)

PyPI forbids direct git URLs, so these ship via a resolver-safe helper:

```bash
python -m strands_cad.install_extras            # both
python -m strands_cad.install_extras sdf        # fogleman/sdf → sdf_* tools
python -m strands_cad.install_extras neural     # openai/shap-e → neural_* tools
```

!!! tip "The shap-e trap, handled"
    The helper installs shap-e with `--no-deps` (its `setup.py` pins an ancient
    numba) then satisfies its *real* runtime deps — so it installs cleanly on
    3.10–3.13.

## External system tools

Only needed for the specific tools that shell out to them:

| Tool group | Needs | Install |
|---|---|---|
| `scad_*` | OpenSCAD | `brew install openscad` / `apt install openscad` |
| `slice_bambu` | OrcaSlicer (or Bambu Studio) | see [Slicing](../pipeline/slice.md) |
| Dashboard camera | ffmpeg | **bundled** via `imageio-ffmpeg` — no system install |

!!! note "Slicer is auto-managed"
    strands-cad ships a Dockerized OrcaSlicer and a `strands-cad-install-slicer`
    helper. See [Plate → Slice](../pipeline/slice.md) for details.

## Verify the install

```bash
python -c "from strands_cad.tools import __all__; print(len(__all__), 'tools loaded')"
```

You should see **≥ 50 tools** with core only, growing as you add extras.

Next: [Quickstart →](quickstart.md)
