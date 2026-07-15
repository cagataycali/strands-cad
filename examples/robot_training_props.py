#!/usr/bin/env python3
"""Generate physical robot-training props with strands-cad, then drop them
into a strands-labs/robots MuJoCo scene.

The bridge between the two packages:

    strands-cad  → designs printable objects + computes real mass/inertia
    strands-robots → simulates robots (Go2, G1, SO-101...) interacting with them

Workflow shown here:
  1. CadQuery-model three classic manipulation-benchmark props:
       - T-block  (the "Push-T" task object)
       - peg-in-hole board (3 tolerance grades)
       - graded cube set (30/40/50 mm — curriculum grasping)
  2. Compute print-accurate mass + inertia (PLA @ 15% infill).
  3. Emit a MuJoCo MJCF scene with correct physics for each prop.
  4. (Optional, needs `pip install "strands-robots[sim-mujoco]"`) — attach the
     scene assets to a Robot() sim and author a declarative benchmark on them.
  5. The same STLs are print-ready: pack to 3MF and send to a Bambu printer.

Runtime: ~5 s. Dependencies: pip install strands-cad
"""
from pathlib import Path

from strands_cad import (
    cq_render_stl,
    sim_inertia_from_stl,
    sim_build_mjcf,
    sim_run_headless,
    mf3_pack,
    stl_weight,
)

OUT = Path(__file__).parent / "props"
OUT.mkdir(exist_ok=True)


def build_props() -> dict[str, Path]:
    """CadQuery-model the three prop families. Returns {name: stl_path}."""
    props: dict[str, Path] = {}

    # 1. T-block — the Push-T manipulation benchmark object
    props["t_block"] = OUT / "t_block.stl"
    cq_render_stl(script='''
result = (
    cq.Workplane("XY").box(80, 20, 15)
    .union(cq.Workplane("XY").center(0, -30).box(20, 40, 15))
    .edges("|Z").fillet(2)
)
''', output_stl=str(props["t_block"]))

    # 2. Peg-in-hole board — insertion tasks, 3 tolerance grades
    props["peg_board"] = OUT / "peg_board.stl"
    cq_render_stl(script='''
board = cq.Workplane("XY").box(120, 50, 12).edges("|Z").fillet(4)
result = (board.faces(">Z").workplane()
    .pushPoints([(-40, 0)]).hole(10.4)
    .pushPoints([(0, 0)]).hole(15.4)
    .pushPoints([(40, 0)]).hole(20.4))
''', output_stl=str(props["peg_board"]))

    # 3. Graded cube set — curriculum grasping (bigger = easier)
    for size in (30, 40, 50):
        name = f"cube_{size}"
        props[name] = OUT / f"{name}.stl"
        cq_render_stl(
            script=f'result = cq.Workplane("XY").box({size}, {size}, {size}).edges().fillet(2)',
            output_stl=str(props[name]))

    return props


def main() -> int:
    props = build_props()
    print(f"built {len(props)} props → {OUT}")

    # Print-accurate physics: PLA @ 15% infill, like the real printed object.
    meshes = []
    x = 0.0
    for name, stl in props.items():
        inertia = sim_inertia_from_stl(str(stl), material="PLA", infill=0.15)
        mass = inertia["mass_g"]
        print(f"  {name:<10} {mass:7.2f} g")
        meshes.append({"name": name, "path": str(stl),
                       "mass_g": mass, "pos": [x, 0, 0.10]})
        x += 0.15  # 150 mm spacing on the sim floor

    # One MJCF world with every prop — mergeable into any strands-robots scene.
    mjcf = OUT / "props_world.xml"
    sim_build_mjcf(meshes=meshes, output_mjcf=str(mjcf))
    r = sim_run_headless(str(mjcf), duration_sec=0.5)
    print(f"sim sanity: {r['content'][0]['text']}")

    # The exact same assets are print-ready:
    plate = OUT / "props_plate.3mf"
    mf3_pack(items=[
        {"stl": str(props["t_block"]), "name": "T-block", "position": [0, 0, 0]},
        {"stl": str(props["cube_30"]), "name": "cube30", "position": [90, 0, 0]},
    ], output_3mf=str(plate), title="robot training props")
    print(f"print plate → {plate}")

    # ------------------------------------------------------------------
    # Going further with strands-labs/robots (separate install):
    #
    #   pip install "strands-robots[sim-mujoco]"
    #
    #   from strands_robots import Robot
    #   sim = Robot("unitree_go2", mesh=False)      # or "so101", "unitree_g1"
    #   # merge props_world.xml bodies into the robot scene, then author a
    #   # declarative benchmark on them (see robots/examples/11_author_a_benchmark.py):
    #   spec = {
    #       "name": "push_t_to_goal",
    #       "instruction": "Push the T-block beyond x = 0.5 m.",
    #       "default_robot": "so101",
    #       "max_steps": 600,
    #       "success": {"all": [{"predicate": "base_beyond_x", "x": 0.5}]},
    #   }
    # ------------------------------------------------------------------
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
