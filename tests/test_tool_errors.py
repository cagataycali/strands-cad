"""Error-path regression tests.

Every tool must return {"status": "error"} for bad input — never raise. An
exception escaping a @tool aborts the agent's turn instead of letting the model
read the message and retry, so these paths are part of the contract.
"""
import zipfile
from pathlib import Path

import pytest

from strands_cad.tools import gcode, meta, mf3, printability, slice as slice_mod, stl

MISSING = "/tmp/strands_cad_definitely_missing_xyz.stl"


def raw(fn):
    """Unwrap a strands @tool back to the plain python function."""
    return getattr(fn, "_tool_func", None) or getattr(fn, "original_function", None) or fn


@pytest.mark.parametrize("fn", [
    stl.stl_parse, stl.stl_volume, stl.stl_bbox, stl.stl_weight,
    printability.stl_printability, mf3.mf3_read_metadata,
    gcode.gcode_check, slice_mod.slice_estimate, meta.bom_parse,
])
def test_missing_file_returns_error(fn):
    r = raw(fn)(MISSING)
    assert r["status"] == "error"


def test_junk_stl_returns_error(tmp_path):
    junk = tmp_path / "junk.stl"
    junk.write_bytes(b"not an stl at all\n" * 4)
    assert raw(stl.stl_parse)(str(junk))["status"] == "error"


def test_bad_zip_does_not_raise(tmp_path):
    """mf3_unpack used to let BadZipFile escape and abort the agent turn."""
    fake = tmp_path / "fake.3mf"
    fake.write_text("plain text pretending to be a 3mf")
    for fn in (mf3.mf3_read_metadata,):
        assert raw(fn)(str(fake))["status"] == "error"
    assert raw(mf3.mf3_unpack)(str(fake), str(tmp_path / "out"))["status"] == "error"


def test_unknown_enum_values_return_error():
    assert raw(slice_mod.slice_profile_get)("NOPE")["status"] == "error"
    assert raw(stl.stl_weight)(MISSING, material="UNOBTANIUM")["status"] == "error"


def test_mesh_boolean_rejects_unknown_op(tmp_path):
    a = tmp_path / "a.stl"
    a.write_bytes(b"\0" * 84)
    r = raw(stl.mesh_boolean)(str(a), str(a), str(tmp_path / "o.stl"), op="nonsense")
    assert r["status"] == "error" and "nonsense" in r["content"][0]["text"]


def test_scad_validate_blocks_builtins(tmp_path):
    """Constraint expressions are eval'd — builtins must stay unreachable."""
    from strands_cad.tools import scad
    target = tmp_path / "pwned"
    r = raw(scad.scad_validate)(
        {"A": 1}, [{"name": "escape", "expr": f"open({str(target)!r}, 'w')"}])
    assert r["results"][0]["passed"] is False
    assert not target.exists()


def test_gcode_check_flags_unsafe_gcode(tmp_path):
    g = tmp_path / "hot.gcode"
    g.write_text("M104 S400\nM140 S200\nG1 X999 Y999 Z999 E5\n")
    r = raw(gcode.gcode_check)(str(g))
    assert r["status"] == "success" and r["passed"] is False
    joined = " ".join(r["issues"])
    assert "nozzle temp" in joined and "bed temp" in joined and "build volume" in joined


def test_gcode_check_flags_cold_extrusion(tmp_path):
    g = tmp_path / "cold.gcode"
    g.write_text("G1 X10 Y10 E5\nM104 S210\n")
    r = raw(gcode.gcode_check)(str(g))
    assert any("cold" in i for i in r["issues"])


def test_mf3_pack_group_builds_assembly(tmp_path):
    """Grouped items become component objects + one build item per group."""
    from strands_cad.tools import scad
    src = tmp_path / "c.scad"
    src.write_text("cube(10);")
    part = tmp_path / "c.stl"
    if raw(scad.scad_render_stl)(str(src), str(part))["status"] != "success":
        pytest.skip("openscad not installed")
    out = tmp_path / "asm.3mf"
    r = raw(mf3.mf3_pack)(
        [{"stl": str(part), "name": "a", "group": "asm"},
         {"stl": str(part), "name": "b", "group": "asm", "position": [40, 0, 0]}],
        str(out))
    assert r["status"] == "success"
    with zipfile.ZipFile(out) as z:
        xml = z.read("3D/3dmodel.model").decode()
    assert xml.count("<component ") == 2   # both parts inside one assembly
    assert xml.count("<item ") == 1        # ...placed as a single build item
