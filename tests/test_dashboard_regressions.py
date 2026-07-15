"""Regressions in the dashboard's security-sensitive and stateful helpers.

Each test pins behaviour that was wrong at some point and is easy to break again
because the failure is silent: a Telegram stranger getting shell access, the
saved plate being wiped on the first mutation, a dict handed to the browser
where a token string was expected.
"""
import importlib

import pytest


def test_telegram_denies_strangers_without_allowlist(monkeypatch):
    """No allowlist must mean 'owner chat only', not 'anyone who finds the bot'.

    The chat handler routes free text to an agent with shell + printer control.
    """
    pytest.importorskip("fastapi")
    from strands_cad.dashboard import telegram

    monkeypatch.setattr(telegram, "_cfg", lambda: ("tok", "4242", ""))
    stranger = {"id": 999, "username": "nobody"}
    assert telegram._allowed(stranger, "999") is False
    assert telegram._allowed(stranger, "") is False
    assert telegram._allowed(stranger, "4242") is True

    # explicit allowlist takes over, by id or username
    monkeypatch.setattr(telegram, "_cfg", lambda: ("tok", "4242", "999,someone"))
    assert telegram._allowed({"id": 999}, "1") is True
    assert telegram._allowed({"id": 1, "username": "someone"}, "1") is True
    assert telegram._allowed({"id": 7, "username": "intruder"}, "1") is False


def test_telegram_no_chat_configured_denies_everyone(monkeypatch):
    pytest.importorskip("fastapi")
    from strands_cad.dashboard import telegram
    monkeypatch.setattr(telegram, "_cfg", lambda: ("tok", "", ""))
    assert telegram._allowed({"id": 1}, "1") is False


def test_start_polling_refuses_while_old_loop_alive(monkeypatch):
    """Two live getUpdates consumers make Telegram 409 and drop commands."""
    pytest.importorskip("fastapi")
    from strands_cad.dashboard import telegram

    class _Alive:
        def is_alive(self): return True

    monkeypatch.setattr(telegram, "_cfg", lambda: ("tok", "1", ""))
    monkeypatch.setitem(telegram._poll, "running", False)
    monkeypatch.setitem(telegram._poll, "thread", _Alive())
    r = telegram.start_polling()
    assert r["ok"] is False and telegram._poll["running"] is False


def test_realtime_secret_is_always_a_string():
    """All three OpenAI response shapes must yield the bare ek_ token."""
    pytest.importorskip("fastapi")
    from strands_cad.dashboard.realtime import _extract_secret
    assert _extract_secret({"value": "ek_a"}) == "ek_a"
    assert _extract_secret({"client_secret": "ek_b"}) == "ek_b"
    assert _extract_secret({"client_secret": {"value": "ek_c"}}) == "ek_c"
    assert _extract_secret({}) == ""


def test_plate_mutation_does_not_wipe_saved_state(tmp_path, monkeypatch):
    """A fresh process must load the plate from disk before mutating it.

    Previously the first add_item() in a new process appended to the in-memory
    empty list and saved, silently deleting the arrangement on disk.
    """
    pytest.importorskip("fastapi")
    monkeypatch.setenv("STRANDS_CAD_CONFIG_STORE", str(tmp_path / "cfg.json"))
    wd = tmp_path / "wd"; wd.mkdir()
    (wd / "a.stl").write_bytes(b"solid x\nendsolid x\n")

    from strands_cad.dashboard import config_store, models, plate
    importlib.reload(config_store)
    config_store.update({"workdir": str(wd)})
    importlib.reload(models); importlib.reload(plate)

    plate.clear()
    first = plate.add_item("a.stl")
    assert (wd / ".strands_cad_plate.json").exists()

    importlib.reload(plate)          # simulate a server restart
    second = plate.add_item("a.stl")  # mutate before any read
    ids = {i["id"] for i in plate.state()["items"]}
    assert ids == {first["id"], second["id"]}, "restart lost the saved item"


def test_plate_clear_is_a_deliberate_wipe(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    monkeypatch.setenv("STRANDS_CAD_CONFIG_STORE", str(tmp_path / "cfg.json"))
    wd = tmp_path / "wd"; wd.mkdir()
    (wd / "a.stl").write_bytes(b"solid x\nendsolid x\n")
    from strands_cad.dashboard import config_store, models, plate
    importlib.reload(config_store); config_store.update({"workdir": str(wd)})
    importlib.reload(models); importlib.reload(plate)
    plate.add_item("a.stl")
    importlib.reload(plate)
    plate.clear()
    assert plate.state()["items"] == []


def test_sim_inertia_is_kg_m2():
    """Inertia must be kg·m², not kg·mm² — MuJoCo bodies otherwise won't move.

    A 20 mm solid PLA cube: m ≈ 10 g, I = m·a²/6 ≈ 6.7e-7 kg·m².
    """
    trimesh = pytest.importorskip("trimesh")
    from strands_cad.tools import sim
    f = sim.sim_inertia_from_stl
    fn = getattr(f, "_tool_func", None) or getattr(f, "original_function", None) or f
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "cube.stl"
        trimesh.creation.box(extents=(20, 20, 20)).export(p)
        r = fn(str(p), material="PLA", infill=1.0, wall_fraction=1.0)
    assert r["status"] == "success"
    ixx = r["inertia"][0][0]
    assert 1e-7 < ixx < 1e-5, f"inertia {ixx} not in kg·m² range"
