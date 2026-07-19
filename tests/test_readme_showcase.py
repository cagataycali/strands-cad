"""Smoke tests: every runnable README showcase snippet, executed for real.

These mirror the '🎨 What Can You Render?' section — if a snippet breaks,
the docs are lying. Fast subset only (no neural weights, no printer, no slicer).
"""
import shutil
import pytest

pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.fixture(scope="module")
def outdir(tmp_path_factory):
    return tmp_path_factory.mktemp("showcase")


def _ok(r):
    assert r["status"] == "success", r["content"][0]["text"]
    return r


# ---------- CadQuery path ----------

def test_cq_t_block(outdir):
    from strands_cad import cq_render_stl
    r = _ok(cq_render_stl(script='''
result = (
    cq.Workplane("XY").box(80, 20, 15)
    .union(cq.Workplane("XY").center(0, -30).box(20, 40, 15))
    .edges("|Z").fillet(2)
)
''', output_stl=str(outdir / "t_block.stl")))
    assert r["size_kb"] > 10


def test_cq_bracket(outdir):
    from strands_cad import cq_render_stl
    _ok(cq_render_stl(script='''
result = (
    cq.Workplane("XY").box(60, 40, 6)
    .edges("|Z").fillet(6)
    .faces(">Z").workplane()
    .rect(46, 26, forConstruction=True).vertices().hole(4.2)
    .faces(">Z").workplane().hole(20)
)
''', output_stl=str(outdir / "bracket.stl")))


def test_cq_peg_board(outdir):
    from strands_cad import cq_render_stl
    _ok(cq_render_stl(script='''
board = cq.Workplane("XY").box(120, 50, 12).edges("|Z").fillet(4)
result = (board.faces(">Z").workplane()
    .pushPoints([(-40, 0)]).hole(10.4)
    .pushPoints([(0, 0)]).hole(15.4)
    .pushPoints([(40, 0)]).hole(20.4))
''', output_stl=str(outdir / "peg_board.stl")))


# ---------- SDF path ----------

def _sdf_available():
    try:
        import sdf  # noqa
        return True
    except ImportError:
        return False


sdf_only = pytest.mark.skipif(not _sdf_available(), reason="fogleman/sdf not installed")


@sdf_only
def test_sdf_twisted_torus(outdir):
    from strands_cad import sdf_render_stl
    _ok(sdf_render_stl("torus(30, 8).twist(radians(180)/60)",
                       str(outdir / "twisted_torus.stl"), resolution=1.0))


@sdf_only
def test_sdf_csg_classic(outdir):
    from strands_cad import sdf_render_stl
    _ok(sdf_render_stl(
        "sphere(20) & box(30) - cylinder(10).orient(X) - cylinder(10).orient(Y) - cylinder(10)",
        str(outdir / "csg.stl"), resolution=1.0))


@sdf_only
def test_sdf_from_function(outdir):
    from strands_cad import sdf_from_function
    _ok(sdf_from_function(
        function_source='''
def f(p):
    x, y, z = p[:,0], p[:,1], p[:,2]
    return np.sqrt(x**2 + y**2 + z**2) - 20 + 2.5*np.sin(x*0.6)*np.sin(y*0.6)*np.sin(z*0.6)
''',
        output_stl=str(outdir / "wavy.stl"),
        bounds=[-30, -30, -30, 30, 30, 30], resolution=1.0))


@sdf_only
def test_sdf_gyroid(outdir):
    from strands_cad import sdf_gyroid_infill
    _ok(sdf_gyroid_infill(size=(30, 30, 30), period=12, thickness=1.6,
                          output_stl=str(outdir / "gyroid.stl"), resolution=1.0))


# ---------- Analysis / QA path ----------

def test_weight_printability_bbox(outdir):
    from strands_cad import cq_render_stl, stl_weight, stl_bbox
    from strands_cad.tools.printability import stl_printability
    stl = str(outdir / "qa_block.stl")
    _ok(cq_render_stl(script='''
result = (cq.Workplane("XY").box(80, 20, 15)
    .union(cq.Workplane("XY").center(0, -30).box(20, 40, 15)))
''', output_stl=stl))
    w = _ok(stl_weight(stl, material="PLA"))
    assert 10 < w["weight_g"] < 30          # ~18 g in README
    p = _ok(stl_printability(stl, printer="X1C"))
    assert p["fits_bed"] is True
    b = _ok(stl_bbox(stl))
    assert abs(b["size"][0] - 80) < 0.5


# ---------- Point cloud loop (regression: alpha-shape numpy bug) ----------

def test_pointcloud_roundtrip(outdir):
    from strands_cad import cq_render_stl, pointcloud_from_stl, pointcloud_to_stl
    stl = str(outdir / "pc_src.stl")
    _ok(cq_render_stl(script='result = cq.Workplane("XY").box(40, 40, 40)',
                      output_stl=stl))
    xyz = str(outdir / "scan.xyz")
    _ok(pointcloud_from_stl(stl, xyz, n_points=2000))
    # alpha method crashed pre-32609d6 with "field 'a' occurs more than once"
    r = _ok(pointcloud_to_stl(xyz, str(outdir / "recon.stl"), method="alpha", alpha=8))
    assert r["faces"] > 100
    _ok(pointcloud_to_stl(xyz, str(outdir / "recon_cvx.stl"), method="convex"))


# ---------- Sim path ----------

def _mujoco_available():
    try:
        import mujoco  # noqa
        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _mujoco_available(), reason="mujoco not installed")
def test_sim_pipeline(outdir):
    from strands_cad import cq_render_stl, sim_inertia_from_stl, sim_build_mjcf, sim_run_headless
    stl = str(outdir / "sim_block.stl")
    _ok(cq_render_stl(script='result = cq.Workplane("XY").box(30, 30, 30)',
                      output_stl=stl))
    inertia = _ok(sim_inertia_from_stl(stl, material="PLA"))
    assert inertia["mass_g"] > 0
    mjcf = str(outdir / "world.xml")
    _ok(sim_build_mjcf(meshes=[{"name": "block", "path": stl,
                                "mass_g": inertia["mass_g"], "pos": [0, 0, 0.05]}],
                       output_mjcf=mjcf))
    r = _ok(sim_run_headless(mjcf, duration_sec=0.2))
    assert r["steps"] > 0


# ---------- 3MF + estimate path ----------

def test_mf3_pack_and_meta(outdir):
    from strands_cad import cq_render_stl, mf3_pack, mf3_read_metadata
    a = str(outdir / "a.stl"); b = str(outdir / "b.stl")
    _ok(cq_render_stl(script='result = cq.Workplane("XY").box(20, 20, 20)', output_stl=a))
    _ok(cq_render_stl(script='result = cq.Workplane("XY").box(10, 10, 10)', output_stl=b))
    plate = str(outdir / "plate.3mf")
    r = _ok(mf3_pack(items=[
        {"stl": a, "name": "A", "position": [0, 0, 0]},
        {"stl": b, "name": "B", "position": [40, 0, 0]},
    ], output_3mf=plate, title="test plate"))
    assert len(r["objects"]) == 2
    meta = _ok(mf3_read_metadata(plate))
    assert len(meta["objects"]) == 2


def test_slice_estimate_filament_fallback(tmp_path):
    """Regression: Bambu density=0 header → grams computed from length."""
    from strands_cad import slice_estimate
    g = tmp_path / "fake.gcode"
    g.write_text(
        "; estimated printing time (normal mode) = 2h 18m 21s\n"
        "; total filament length [mm] : 6414.11\n"
        "; total filament weight [g] : 0.00\n")
    r = _ok(slice_estimate(str(g)))
    assert r["estimated_seconds"] == 2*3600 + 18*60 + 21
    assert r["filament_g"] and r["filament_g"] > 10   # ~19.1 g
    assert r["filament_g_estimated"] is True


# ---------- SCAD path (needs openscad binary) ----------

scad_only = pytest.mark.skipif(shutil.which("openscad") is None,
                               reason="openscad not in PATH")


@scad_only
def test_scad_probe_and_defines(outdir):
    from strands_cad import scad_probe, scad_render_stl, stl_bbox
    scad = outdir / "box.scad"
    scad.write_text('W = 30; H = 20;\ncube([W, W, H], center=true);\n')
    p = _ok(scad_probe(str(scad), ["W", "H"]))
    assert p["values"]["W"] == 30
    stl = str(outdir / "box50.stl")
    _ok(scad_render_stl(str(scad), stl, defines={"W": 50}))
    b = _ok(stl_bbox(stl))
    assert abs(b["size"][0] - 50) < 0.5


# ---------- Example scripts stay runnable ----------

@pytest.mark.skipif(not _mujoco_available(), reason="mujoco not installed")
def test_example_robot_training_props(tmp_path, monkeypatch):
    """examples/robot_training_props.py must run end-to-end (README links it)."""
    import runpy
    import sys
    from pathlib import Path
    script = Path(__file__).parent.parent / "examples" / "robot_training_props.py"
    assert script.exists()
    # Redirect its output dir to tmp by faking __file__ location? Simpler:
    # run it as-is; it writes to examples/props/ which is gitignored.
    monkeypatch.setattr(sys, "argv", [str(script)])
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as e:
        assert e.code == 0
