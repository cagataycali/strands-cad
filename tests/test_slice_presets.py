"""Slicer preset resolution regressions."""
from pathlib import Path
from types import SimpleNamespace

from strands_cad.tools import slice as slice_mod


def raw(fn):
    return getattr(fn, "_tool_func", None) or getattr(fn, "original_function", None) or fn


def _write(path: Path, text: str = "{}") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_p1s_resolves_x1c_process_preset(tmp_path):
    profiles = tmp_path / "profiles" / "BBL"
    expected = _write(
        profiles / "process" / "0.20mm Standard @BBL X1C.json",
        '{"compatible_printers":["Bambu Lab P1S 0.4 nozzle"]}',
    )

    resolved = slice_mod._resolve_process_preset(profiles, "Bambu Lab P1S", 0.20)

    assert resolved == expected


def test_process_resolver_falls_back_to_compatibility_metadata(tmp_path):
    profiles = tmp_path / "profiles" / "BBL"
    expected = _write(
        profiles / "process" / "0.20mm Standard @BBL Shared.json",
        '{"compatible_printers":["Bambu Lab Future 0.4 nozzle"]}',
    )

    resolved = slice_mod._resolve_process_preset(profiles, "Bambu Lab Future", 0.20)

    assert resolved == expected


def test_filament_resolver_prefers_printer_specific_preset(tmp_path):
    profiles = tmp_path / "profiles" / "BBL"
    _write(profiles / "filament" / "Bambu PLA Basic @base.json")
    expected = _write(
        profiles / "filament" / "Bambu PLA Basic @BBL P1S 0.4 nozzle.json"
    )

    resolved = slice_mod._resolve_filament_preset(
        profiles, "Bambu Lab P1S", "PLA"
    )

    assert resolved == expected


def test_slice_bambu_loads_compatible_p1s_presets(monkeypatch, tmp_path):
    app = tmp_path / "BambuStudio.app" / "Contents"
    cli = _write(app / "MacOS" / "BambuStudio")
    profiles = app / "Resources" / "profiles" / "BBL"
    machine = _write(profiles / "machine" / "Bambu Lab P1S 0.4 nozzle.json")
    process = _write(
        profiles / "process" / "0.20mm Standard @BBL X1C.json",
        '{"compatible_printers":["Bambu Lab P1S 0.4 nozzle"]}',
    )
    filament = _write(
        profiles / "filament" / "Bambu PLA Basic @BBL P1S 0.4 nozzle.json"
    )
    source = _write(tmp_path / "source.model.3mf", "model")
    output = tmp_path / "out" / "part.gcode"
    seen = {}

    def fake_run(args, **kwargs):
        seen["args"] = args
        output.parent.mkdir(parents=True, exist_ok=True)
        (output.parent / "part.3mf").write_bytes(b"sliced project")
        (output.parent / "plate_1.gcode").write_text("; gcode")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(slice_mod, "_find_orca_docker_image", lambda: None)
    monkeypatch.setattr(slice_mod, "_find_bambu_cli", lambda: str(cli))
    monkeypatch.setattr(slice_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(slice_mod, "_inject_model_code", lambda *args: None)

    result = raw(slice_mod.slice_bambu)(
        str(source), str(output), profile="PLA_0_20", printer_model="Bambu Lab P1S"
    )

    assert result["status"] == "success", result
    settings = seen["args"][seen["args"].index("--load-settings") + 1]
    loaded_filament = seen["args"][seen["args"].index("--load-filaments") + 1]
    assert settings == f"{machine};{process}"
    assert loaded_filament == str(filament)
    assert result["presets"] == {
        "machine": str(machine),
        "process": str(process),
        "filament": str(filament),
    }


def test_slice_bambu_fails_before_cli_when_machine_preset_missing(
    monkeypatch, tmp_path
):
    app = tmp_path / "BambuStudio.app" / "Contents"
    cli = _write(app / "MacOS" / "BambuStudio")
    source = _write(tmp_path / "source.model.3mf", "model")
    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("slicer must not run without complete presets")

    monkeypatch.setattr(slice_mod, "_find_orca_docker_image", lambda: None)
    monkeypatch.setattr(slice_mod, "_find_bambu_cli", lambda: str(cli))
    monkeypatch.setattr(slice_mod.subprocess, "run", fake_run)

    result = raw(slice_mod.slice_bambu)(
        str(source), str(tmp_path / "part.gcode"), printer_model="Bambu Lab P1S"
    )

    assert result["status"] == "error"
    assert "machine preset not found" in result["content"][0]["text"]
    assert called is False
