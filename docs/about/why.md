# Why strands-cad?

## The problem

Getting an AI agent from *"design me a bracket"* to a **physical printed object**
usually means gluing together five incompatible worlds: a CAD kernel, a mesh
library, a slicer, a printer protocol, and a camera stream — each with its own
quirks, auth schemes, and failure modes.

strands-cad collapses all of that into **65 atomic tools** with one consistent
response shape, so an agent can compose the whole loop in a single conversation.

## The three promises

<div class="grid cards" markdown>

-   :material-check-decagram: __Zero-shot install__

    `pip install strands-cad` works first try on Python 3.10–3.13. No
    build-from-source landmines (we pin the numba/llvmlite trap shut).

-   :material-atom: __Truly atomic__

    One tool = one verb = one input shape = one output shape. No hidden
    orchestration. The agent composes; the tools stay reliable.

-   :material-shield-lock: __Local & sealed__

    Everything runs on your LAN. The dashboard is sealed behind device passkeys
    — no passwords, no cloud, nothing to phish.

</div>

## What makes it different

- **Four modeling paths, one pipeline.** SCAD, CadQuery, SDF, and Neural all
  converge on STL → the same verify → slice → print loop.
- **We solved the hard printer bits.** Bambu FTPS TLS-session reuse, the RTSPS
  camera handshake ffmpeg can't do, the "silently rejected PrusaSlicer G-code"
  trap — all handled, all documented.
- **Robotics-native.** Print-accurate mass/inertia + MuJoCo means sim and
  reality match before you spend filament.

## Who it's for

| You are… | strands-cad gives you |
|---|---|
| An **agent builder** | 65 composable MCP verbs for model → slice → print |
| A **maker** | A headless print-farm brain + passkey-sealed live cockpit |
| A **robotics researcher** | Design → validate physics → simulate → print the twin |

Next: [Architecture →](architecture.md)
