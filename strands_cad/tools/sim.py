"""Sim layer — MuJoCo bridge for physics validation of printed geometry."""
from __future__ import annotations
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any

from strands import tool
from strands_cad._common import ok, err, parse_stl, signed_volume_cm3


@tool
def sim_inertia_from_stl(
    stl_file: str,
    material: str = "PLA",
    density_g_cm3: float | None = None,
    infill: float = 0.15,
    wall_fraction: float = 0.30,
) -> dict:
    """Compute mass + inertia tensor + center of mass from an STL.

    Uses trimesh if available (full inertia tensor); falls back to bbox approximation.

    Args:
        stl_file: Path to .stl file.
        material: Material preset name (PLA / PETG / ABS / TPU / ASA / NYLON / PC).
        density_g_cm3: Override density (g/cm³). If None, uses material preset.
        infill: Sparse infill fraction.
        wall_fraction: Wall fraction.

    Returns:
        {status, content, mass_g, com:[x,y,z], inertia:[[Ixx,Ixy,Ixz],...], volume_cm3}
    """
    from strands_cad.tools.stl import DENSITY
    src = Path(stl_file).resolve()
    if not src.exists():
        return err(f"file not found: {src}")
    if density_g_cm3 is None:
        density_g_cm3 = DENSITY.get(material.upper(), 1.24)
    effective_density = density_g_cm3 * (wall_fraction + (1 - wall_fraction) * infill)

    try:
        import trimesh  # type: ignore
        import numpy as np  # type: ignore
        m = trimesh.load(src, force="mesh")
        m.density = effective_density * 1000  # trimesh uses kg/m³, we have g/cm³
        vol_mm3 = float(m.volume)
        vol_cm3 = vol_mm3 / 1000.0
        mass_g = vol_cm3 * effective_density
        com = m.center_mass.tolist()
        # trimesh moment_inertia is in kg·m² for its density; scale for our mass
        I = (m.moment_inertia * (mass_g / 1000.0) / max(m.mass, 1e-9)).tolist()
        return ok(f"mass={mass_g:.2f} g, vol={vol_cm3:.2f} cm³",
                  mass_g=mass_g, volume_cm3=vol_cm3, com=com, inertia=I,
                  effective_density_g_cm3=effective_density)
    except ImportError:
        # fallback: bbox-based approximation
        try:
            verts, tris = parse_stl(src)
        except Exception as e:
            return err(str(e))
        vol_cm3 = signed_volume_cm3(verts, tris)
        mass_g = vol_cm3 * effective_density
        xs = [v[0] for v in verts]; ys = [v[1] for v in verts]; zs = [v[2] for v in verts]
        com = [(min(xs)+max(xs))/2, (min(ys)+max(ys))/2, (min(zs)+max(zs))/2]
        dx = max(xs)-min(xs); dy = max(ys)-min(ys); dz = max(zs)-min(zs)
        # Solid box inertia (kg·mm² → kg·m²)
        m_kg = mass_g / 1000.0
        Ixx = m_kg * (dy*dy + dz*dz) / 12 / 1e6
        Iyy = m_kg * (dx*dx + dz*dz) / 12 / 1e6
        Izz = m_kg * (dx*dx + dy*dy) / 12 / 1e6
        return ok(f"mass={mass_g:.2f} g (bbox approx; install trimesh for exact)",
                  mass_g=mass_g, volume_cm3=vol_cm3, com=com,
                  inertia=[[Ixx,0,0],[0,Iyy,0],[0,0,Izz]],
                  approximation="bbox")


@tool
def sim_build_mjcf(
    meshes: list[dict],
    output_mjcf: str,
    gravity: tuple[float, float, float] = (0.0, 0.0, -9.81),
    timestep: float = 0.002,
) -> dict:
    """Compose a MuJoCo MJCF (XML) referencing one or more mesh files.

    Args:
        meshes: List of {name, path, mass_g?, pos?, rgba?} entries. Each becomes a body
            with a mesh geom.
        output_mjcf: Output .xml path (MJCF).
        gravity: World gravity vector (m/s²).
        timestep: Simulation timestep (seconds).

    Returns:
        {status, content, path}
    """
    if not meshes:
        return err("meshes list is empty")
    out = Path(output_mjcf).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    mesh_assets = []
    bodies = []
    for i, mspec in enumerate(meshes):
        name = mspec.get("name") or f"mesh_{i}"
        path = mspec.get("path")
        if not path or not Path(path).exists():
            return err(f"mesh path missing for {name}: {path}")
        pos = mspec.get("pos", [0, 0, 0])
        rgba = mspec.get("rgba", [0.6, 0.6, 0.6, 1])
        mass_g = mspec.get("mass_g", 1.0)
        mass_kg = mass_g / 1000.0
        mesh_assets.append(f'    <mesh name="{name}" file="{path}" scale="0.001 0.001 0.001"/>')
        bodies.append(f'''    <body name="{name}" pos="{pos[0]} {pos[1]} {pos[2]}">
      <freejoint/>
      <inertial pos="0 0 0" mass="{mass_kg:.6f}" diaginertia="1e-4 1e-4 1e-4"/>
      <geom type="mesh" mesh="{name}" rgba="{rgba[0]} {rgba[1]} {rgba[2]} {rgba[3]}"/>
    </body>''')

    xml = f'''<?xml version="1.0" ?>
<mujoco model="strands-cad">
  <option timestep="{timestep}" gravity="{gravity[0]} {gravity[1]} {gravity[2]}"/>
  <asset>
{chr(10).join(mesh_assets)}
    <texture type="skybox" builtin="gradient" rgb1="0.3 0.5 0.7" rgb2="0 0 0" width="512" height="512"/>
    <material name="grid" rgba="0.9 0.9 0.9 1"/>
  </asset>
  <worldbody>
    <light pos="0 0 3" dir="0 0 -1" diffuse="1 1 1"/>
    <geom name="floor" type="plane" size="2 2 0.05" material="grid"/>
{chr(10).join(bodies)}
  </worldbody>
</mujoco>
'''
    out.write_text(xml)
    return ok(f"MJCF written → {out} ({len(meshes)} body/bodies)", path=str(out))


@tool
def sim_run_headless(
    mjcf_file: str,
    duration_sec: float = 2.0,
    control_callback: str | None = None,
) -> dict:
    """Run a MuJoCo simulation headless and return final state + summary metrics.

    Args:
        mjcf_file: Path to MJCF XML.
        duration_sec: How long to simulate (real seconds).
        control_callback: (unused for now — placeholder for future callable ref)

    Returns:
        {status, content, steps, final_pos, final_time}
    """
    try:
        import mujoco  # type: ignore
    except ImportError:
        return err("mujoco not installed. pip install 'strands-cad[sim]'")
    src = Path(mjcf_file).resolve()
    if not src.exists():
        return err(f"MJCF not found: {src}")
    try:
        m = mujoco.MjModel.from_xml_path(str(src))
        d = mujoco.MjData(m)
    except Exception as e:
        return err(f"MJCF load failed: {e}")
    steps = int(duration_sec / m.opt.timestep)
    for _ in range(steps):
        mujoco.mj_step(m, d)
    final_pos = d.qpos[:3].tolist() if len(d.qpos) >= 3 else d.qpos.tolist()
    return ok(f"ran {steps} steps ({duration_sec}s), t={d.time:.3f}s",
              steps=steps, final_time=float(d.time), final_pos=final_pos)


@tool
def sim_view_live(mjcf_file: str) -> dict:
    """Launch an interactive MuJoCo viewer (non-blocking).

    Args:
        mjcf_file: Path to MJCF XML.

    Returns:
        {status, content, pid}
    """
    src = Path(mjcf_file).resolve()
    if not src.exists():
        return err(f"MJCF not found: {src}")
    try:
        # Prefer python -m mujoco.viewer (blocking) via subprocess so it doesn't kill the agent.
        p = subprocess.Popen(
            ["python", "-m", "mujoco.viewer", "--mjcf", str(src)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        return err(f"viewer launch failed: {e}")
    return ok(f"viewer launched (pid {p.pid}). Close window to end.", pid=p.pid)
