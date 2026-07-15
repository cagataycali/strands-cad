# Related Projects

strands-cad is part of the [Strands](https://github.com/strands-agents)
ecosystem.

## Built on

| Project | Role |
|---|---|
| [strands-agents](https://github.com/strands-agents) | The agent framework — `@tool`, `Agent` |
| [strands-agents-tools](https://pypi.org/project/strands-agents-tools/) | Shared tool utilities & model providers |
| [strands-mcp-server](https://github.com/cagataycali/strands-mcp-server) | Powers the `strands-cad-mcp` entrypoint |

## Uses

| Project | Role in strands-cad |
|---|---|
| [CadQuery](https://github.com/CadQuery/cadquery) | B-rep / NURBS modeling |
| [OpenSCAD](https://openscad.org/) | Parametric modeling |
| [fogleman/sdf](https://github.com/fogleman/sdf) | Implicit-math meshing |
| [openai/shap-e](https://github.com/openai/shap-e) | Neural text/image → 3D |
| [trimesh](https://github.com/mikedh/trimesh) | Mesh ops / QA |
| [MuJoCo](https://mujoco.org/) | Physics simulation |
| [OrcaSlicer](https://github.com/SoftFever/OrcaSlicer) | Bambu-compatible slicing |
| [FastAPI](https://fastapi.tiangolo.com/) + [py_webauthn](https://github.com/duo-labs/py_webauthn) | The dashboard + passkeys |

## Downstream

| Project | How it uses strands-cad |
|---|---|
| [strands-labs/robots](https://github.com/strands-labs/robots) | Design → validate → simulate → print manipulation props (rover mounts, drone frames, gripper fingers, T-blocks, peg boards) |

## Learn more

- [Model Context Protocol](https://modelcontextprotocol.io/)
- [Bambu Lab](https://bambulab.com/)
