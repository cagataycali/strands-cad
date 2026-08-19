"""Bambu Lab printer control (LAN MQTT).

Uses bambulabs-api or paho-mqtt. Requires:
  - Printer IP (LAN mode enabled in Bambu Handy / Studio)
  - Access code (printer settings)
  - Serial number

Stateless-ish: a single global connection handle is cached in-process.
"""
from __future__ import annotations
import base64
import json
import os
import ssl
import threading
import time
from pathlib import Path
from typing import Any

from strands import tool
from strands_cad._common import ok, err


# Cached connection state — keyed by (ip, serial).
_CONN: dict[str, Any] = {
    "client": None,
    "ip": None,
    "serial": None,
    "access_code": None,
    "last_state": {},
    "last_update": 0,
}
_LOCK = threading.Lock()


def _mqtt_client(ip: str, access_code: str, serial: str):
    try:
        import paho.mqtt.client as mqtt  # type: ignore
    except ImportError:
        raise RuntimeError("paho-mqtt not installed. Install with: pip install 'strands-cad[bambu]'")

    client = mqtt.Client(client_id=f"strands-cad-{serial}", protocol=mqtt.MQTTv311)
    client.username_pw_set("bblp", access_code)
    client.tls_set(cert_reqs=ssl.CERT_NONE, tls_version=ssl.PROTOCOL_TLSv1_2)
    client.tls_insecure_set(True)

    def on_message(_c, _u, msg):
        try:
            data = json.loads(msg.payload)
        except Exception:
            return
        with _LOCK:
            _CONN["last_state"].update(data.get("print", data))
            _CONN["last_update"] = time.time()

    def on_connect(c, _u, _f, rc):
        if rc == 0:
            c.subscribe(f"device/{serial}/report")
            c.publish(f"device/{serial}/request",
                      json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}}))

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(ip, 8883, keepalive=60)
    client.loop_start()
    return client


@tool
def bambu_connect(ip: str, access_code: str, serial: str) -> dict:
    """Connect to a Bambu Lab printer over LAN MQTT.

    Args:
        ip: Printer IP address on your LAN.
        access_code: Access code from printer settings (Network → LAN mode).
        serial: Printer serial number (e.g. 01P00A123456789).

    Returns:
        {status, content, connected:bool, printer:{ip, serial}}
    """
    with _LOCK:
        if _CONN["client"] is not None:
            try:
                _CONN["client"].loop_stop()
                _CONN["client"].disconnect()
            except Exception:
                pass
    try:
        client = _mqtt_client(ip, access_code, serial)
    except Exception as e:
        return err(f"connect failed: {e}")
    with _LOCK:
        _CONN["client"] = client
        _CONN["ip"] = ip
        _CONN["serial"] = serial
        _CONN["access_code"] = access_code
        _CONN["last_state"] = {}
    time.sleep(1.0)  # let initial pushall arrive
    return ok(f"connected to Bambu @ {ip}", connected=True,
              printer={"ip": ip, "serial": serial})


def _env_autoconnect() -> bool:
    """Connect from BAMBU_IP / BAMBU_ACCESS_CODE / BAMBU_SERIAL env if set.

    Lets every bambu tool work without an explicit bambu_connect() call when
    the process carries printer credentials (MCP server env, dashboard .env),
    so an agent that only knows "look at the printer" isn't stopped by
    credentials it was never told.
    """
    ip = os.getenv("BAMBU_IP", "")
    access = os.getenv("BAMBU_ACCESS_CODE", "")
    serial = os.getenv("BAMBU_SERIAL", "")
    if not (ip and access and serial):
        return False
    try:
        client = _mqtt_client(ip, access, serial)
    except Exception:
        return False
    with _LOCK:
        _CONN["client"] = client
        _CONN["ip"] = ip
        _CONN["serial"] = serial
        _CONN["access_code"] = access
        _CONN["last_state"] = {}
    time.sleep(1.0)  # let initial pushall arrive
    return True


def _require_conn() -> tuple[Any, str] | None:
    with _LOCK:
        connected = _CONN["client"] is not None and _CONN["serial"] is not None
    if not connected and not _env_autoconnect():
        return None
    with _LOCK:
        if _CONN["client"] is None or _CONN["serial"] is None:
            return None
        return _CONN["client"], _CONN["serial"]


@tool
def bambu_send(file_path: str, plate_index: int = 1, use_ams: bool = True,
               ams_mapping: list[int] | None = None) -> dict:
    """Upload a sliced 3MF/G-code and start the print job.

    Args:
        file_path: Local path to sliced .3mf (with G-code) or .gcode.
        plate_index: Which plate in the 3MF to print (default 1).
        use_ams: If True, use AMS filament mapping from the file.
        ams_mapping: AMS slot (0-based) for each filament in the sliced file,
            in filament order. A 2-color plate whose filaments live in AMS
            slots 1 and 4 (1-based, as the UI shows) needs [0, 3]. Defaults to
            [0] — correct only for single-filament plates; leaving that default
            on a multi-color job makes every filament resolve to slot 0 and the
            whole model prints in one color.

    Returns:
        {status, content, job:{file, plate}}
    """
    conn = _require_conn()
    if not conn:
        return err("not connected. Call bambu_connect() first, or set BAMBU_IP, BAMBU_ACCESS_CODE and BAMBU_SERIAL in the environment.")
    client, serial = conn
    src = Path(file_path).resolve()
    if not src.exists():
        return err(f"file not found: {src}")

    # BUSY GUARD — must run BEFORE any dispatch. Verified live on a real X2D
    # (2026-08-14): the firmware does NOT refuse a project_file while printing,
    # it silently REPLACES the running job (a print at 10% was killed by a
    # second send). Refusing here is the only protection the running job has.
    with _LOCK:
        busy = dict(_CONN.get("last_state") or {})
    if busy.get("gcode_state") in ("RUNNING", "PREPARE", "SLICING", "PAUSE"):
        return err(f"printer is busy: '{busy.get('subtask_name')}' is "
                   f"{busy.get('gcode_state')} at {busy.get('mc_percent', '?')}% — "
                   "sending now would REPLACE it. Stop it first (bambu_control) "
                   "or wait for it to finish.")

    # UPLOAD FIRST — the docstring promises "upload and start", so do both.
    # (Live failure mode on a real X2D, 2026-08-14: skipping the upload and
    # sending project_file for a file not on the SD card latches
    # print_error 0x05004002 and gcode_state=FAILED until cleared.)
    up = bambu_upload(str(src))
    if up.get("status") != "success":
        return err(f"upload before print failed: {up.get('content')}")

    is_gcode = src.suffix.lower() == ".gcode"
    # Bambu firmware: a 3MF *project* references its internal
    # Metadata/plate_N.gcode; a bare .gcode on SD must reference the file
    # itself (Metadata/plate_N.gcode does NOT exist inside a plain gcode →
    # the printer silently rejects the job). AMS mapping only exists inside a
    # 3MF, so use_ams is meaningful only for 3MF projects.
    param = src.name if is_gcode else f"Metadata/plate_{plate_index}.gcode"
    # Bambu firmware validation (verified against real X2D + BambuTools/bambulabs_api):
    #  • url MUST be "ftp:///<name>" (SD root). "file:///mnt/sdcard/..." → error
    #    0x05004002 "Unsupported print file path or name".
    #  • the printed file should be a REAL OrcaSlicer .3mf project bundle whose
    #    slice_info.config carries the printer *model code* (e.g. X2D=N6). A bare
    #    gcode or a hand-wrapped 3mf → 0x05004037/46 "file invalid / incompatible".
    #  • send both "file" and "url"; clear any latched error first.
    client.publish(f"device/{serial}/request",
                   json.dumps({"print": {"sequence_id": str(int(time.time())),
                                          "command": "clean_print_error"}}))
    time.sleep(1.0)
    # Snapshot pre-dispatch state so stale values can't fool the verifier below
    # (e.g. a print already RUNNING, or a latched print_error not yet re-pushed).
    with _LOCK:
        pre = dict(_CONN.get("last_state") or {})
    pre_err = int(pre.get("print_error") or 0)
    subtask = src.stem.removesuffix(".gcode")  # foo.gcode.3mf → "foo"
    payload = {
        "print": {
            "sequence_id": str(int(time.time())),
            "command": "project_file",
            "param": param,
            "file": src.name,
            "url": f"ftp:///{src.name}",
            "subtask_name": subtask,
            "bed_type": "textured_plate",
            "bed_leveling": True,
            "flow_cali": True,
            "vibration_cali": True,
            "layer_inspect": True,
            "timelapse": False,
            "use_ams": use_ams,
            "ams_mapping": list(ams_mapping) if ams_mapping else [0],
            "skip_objects": None,
        }
    }
    client.publish(f"device/{serial}/request", json.dumps(payload))

    # VERIFY — don't claim success on a fire-and-forget publish. Poll the
    # cached MQTT report: RUNNING/PREPARE = started; a fresh print_error or
    # FAILED = the firmware rejected the job (report the code in hex, it's
    # what Bambu's wiki indexes on).
    deadline = time.time() + 20.0
    while time.time() < deadline:
        time.sleep(1.0)
        with _LOCK:
            state = dict(_CONN.get("last_state") or {})
        gs = state.get("gcode_state")
        pe = int(state.get("print_error") or 0)
        if pe and pe != pre_err:
            return err(f"printer rejected job: print_error={pe} (0x{pe:08X}), "
                       f"gcode_state={gs}")
        if gs in ("RUNNING", "PREPARE", "SLICING") \
                and state.get("subtask_name") == subtask:
            return ok(f"print started: {src.name} (plate {plate_index}, state {gs})",
                      job={"file": src.name, "plate": plate_index, "use_ams": use_ams})
    gs = state.get("gcode_state")
    if gs in ("RUNNING", "PREPARE") and state.get("subtask_name") != subtask:
        return err(f"printer is busy with '{state.get('subtask_name')}' "
                   f"(state {gs}) — this job was not started")
    return ok(f"job dispatched: {src.name} (plate {plate_index}) — state still "
              f"'{gs}' after 20s; poll bambu_status()",
              job={"file": src.name, "plate": plate_index, "use_ams": use_ams},
              verified=False)


@tool
def bambu_upload(file_path: str, remote_name: str = "") -> dict:
    """Upload a file to the printer's SD card via FTPS (implicit TLS, port 990).

    This closes the full print loop: slice → upload → bambu_send.

    Args:
        file_path: Local path to .3mf or .gcode file.
        remote_name: Filename on the SD card (defaults to local filename).

    Returns:
        {status, content, remote_path}
    """
    conn = _require_conn()
    if not conn:
        return err("not connected. Call bambu_connect() first, or set BAMBU_IP, BAMBU_ACCESS_CODE and BAMBU_SERIAL in the environment.")
    with _LOCK:
        ip = _CONN["ip"]
        access = _CONN["access_code"]
    src = Path(file_path).resolve()
    if not src.exists():
        return err(f"file not found: {src}")
    name = remote_name or src.name

    # Pre-flight: Bambu's FTPS server chroots into the SD card mount. If no card
    # is inserted (sdcard=False in MQTT report), EVERY STOR fails with
    # "553 Could not create file". Surface this as an actionable error.
    with _LOCK:
        last = _CONN.get("last_state") or {}
    sd = last.get("sdcard")
    if sd is False:
        return err("553 would fail: printer reports NO SD/microSD card inserted "
                   "(sdcard=False). Insert a FAT32 microSD/USB into the printer "
                   "to enable file upload + LAN printing.")

    import ftplib

    class ImplicitFTPS(ftplib.FTP_TLS):
        """FTP_TLS for implicit TLS (Bambu port 990) with data-channel TLS
        session reuse — Bambu\'s server REQUIRES the data connection to reuse
        the control connection\'s TLS session, otherwise STOR fails with
        "553 Could not create file". Stock ftplib does not do this."""
        def connect(self, host="", port=0, timeout=-999, source_address=None):
            import socket as _socket
            if host:
                self.host = host
            if port:
                self.port = port
            if timeout != -999:
                self.timeout = timeout
            self.sock = _socket.create_connection((self.host, self.port), self.timeout)
            self.af = self.sock.family
            self.sock = self.context.wrap_socket(self.sock, server_hostname=self.host)
            self.file = self.sock.makefile("r", encoding=self.encoding)
            self.welcome = self.getresp()
            return self.welcome

        def ntransfercmd(self, cmd, rest=None):
            # Reuse the control connection\'s TLS session for the data channel.
            conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
            if self._prot_p:
                session = self.sock.session
                conn = self.context.wrap_socket(
                    conn, server_hostname=self.host, session=session)
            return conn, size

    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ftps = ImplicitFTPS(context=ctx)
        ftps.connect(ip, 990, timeout=45)
        ftps.login("bblp", access)
        ftps.prot_p()
        with open(src, "rb") as f:
            ftps.storbinary(f"STOR {name}", f, blocksize=65536)
        try:
            size = ftps.size(name)
        except Exception:
            size = src.stat().st_size
        ftps.quit()
    except Exception as e:
        return err(f"FTPS upload failed: {e}")
    return ok(f"uploaded {src.name} -> printer SD as '{name}' ({size} bytes)",
              remote_path=name, size_bytes=size)


@tool
def bambu_status() -> dict:
    """Get current printer state (poll cached MQTT report).

    Returns:
        {status, content, state, progress, layer, temps:{nozzle, bed}, remaining_min}
    """
    conn = _require_conn()
    if not conn:
        return err("not connected. Call bambu_connect() first, or set BAMBU_IP, BAMBU_ACCESS_CODE and BAMBU_SERIAL in the environment.")
    with _LOCK:
        s = dict(_CONN["last_state"])
        age = time.time() - _CONN["last_update"]
    if not s:
        return err(f"no status received yet (age {age:.1f}s). Printer may be off or unreachable.")
    return ok(
        f"gcode_state={s.get('gcode_state')}, progress={s.get('mc_percent', 0)}%",
        state=s.get("gcode_state"),
        progress=s.get("mc_percent"),
        layer=s.get("layer_num"),
        total_layers=s.get("total_layer_num"),
        remaining_min=s.get("mc_remaining_time"),
        sdcard=s.get("sdcard"),
        subtask_name=s.get("subtask_name"),
        nozzle_count=(2 if s.get("2D") is not None else 1),
        temps={
            "nozzle": s.get("nozzle_temper"),
            "nozzle_target": s.get("nozzle_target_temper"),
            "bed": s.get("bed_temper"),
            "bed_target": s.get("bed_target_temper"),
            "chamber": s.get("chamber_temper"),
        },
        age_seconds=age,
    )


@tool
def bambu_control(action: str) -> dict:
    """Control the print job (pause/resume/stop).

    Args:
        action: One of 'pause', 'resume', 'stop'.
    """
    conn = _require_conn()
    if not conn:
        return err("not connected. Call bambu_connect() first, or set BAMBU_IP, BAMBU_ACCESS_CODE and BAMBU_SERIAL in the environment.")
    client, serial = conn
    cmd_map = {"pause": "pause", "resume": "resume", "stop": "stop"}
    if action not in cmd_map:
        return err(f"unknown action '{action}'. Options: pause, resume, stop.")
    payload = {"print": {"sequence_id": str(int(time.time())), "command": cmd_map[action]}}
    client.publish(f"device/{serial}/request", json.dumps(payload))
    return ok(f"sent {action}", action=action)


def _camera_rtsps_frame(ip: str, access: str, timeout: int = 25) -> bytes | None:
    """Grab one JPEG frame from the RTSPS liveview (X1/X2/H2 series, port 322).

    Verified against a real X-series printer: the port-6000 JPEG protocol is
    P1/A1-only — X-series replies 0xffffffff and closes, so ffmpeg + RTSPS is
    the working path here.
    """
    import shutil as _sh
    import subprocess as _sp
    import tempfile as _tf
    import urllib.parse as _up
    ffmpeg = _sh.which("ffmpeg")
    if not ffmpeg:
        try:
            import imageio_ffmpeg  # type: ignore
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return None
    url = f"rtsps://bblp:{_up.quote(access, safe='')}@{ip}:322/streaming/live/1"
    tmp = Path(_tf.mkstemp(suffix=".jpg")[1])
    try:
        r = _sp.run([ffmpeg, "-y", "-loglevel", "error", "-rtsp_transport", "tcp",
                     "-i", url, "-frames:v", "1", str(tmp)],
                    capture_output=True, timeout=timeout)
        if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            return tmp.read_bytes()
        return None
    except Exception:
        return None
    finally:
        tmp.unlink(missing_ok=True)


def _camera_p1_frame(ip: str, access: str, timeout: int = 10) -> bytes | None:
    """Grab one JPEG frame from the port-6000 TLS stream (P1/A1 series).

    Protocol: send a 0x40-byte auth packet (magic, "bblp", access code), then
    read 16-byte headers + JPEG payloads. An 8-byte 0xffffffff payload means
    auth rejected / unsupported model (e.g. X-series → use RTSPS instead).
    """
    import socket
    import struct
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        raw = socket.create_connection((ip, 6000), timeout=timeout)
        s = ctx.wrap_socket(raw, server_hostname=ip)
        s.settimeout(timeout)
        auth = struct.pack("<IIII", 0x40, 0x3000, 0, 0)
        auth += b"bblp".ljust(32, b"\0") + access.encode().ljust(32, b"\0")
        s.write(auth)

        def read_n(n: int) -> bytes:
            buf = b""
            while len(buf) < n:
                c = s.recv(n - len(buf))
                if not c:
                    raise EOFError(f"stream closed at {len(buf)}/{n} bytes")
                buf += c
            return buf

        for _ in range(4):  # first packets may be control frames
            plen = struct.unpack("<I", read_n(16)[:4])[0]
            payload = read_n(plen) if plen else b""
            if payload[:2] == b"\xff\xd8":
                s.close()
                return payload
        s.close()
        return None
    except Exception:
        return None


@tool
def bambu_camera(save_path: str = "") -> dict:
    """Fetch a JPEG snapshot from the printer's chamber camera (LAN mode).

    Tries the RTSPS liveview (X1/X2/H2 series, port 322, via ffmpeg) first,
    then the port-6000 TLS JPEG stream (P1/A1 series).

    Args:
        save_path: Optional path to save the JPEG. If empty, returns base64 in payload.
    """
    conn = _require_conn()
    if not conn:
        return err("not connected. Call bambu_connect() first, or set BAMBU_IP, BAMBU_ACCESS_CODE and BAMBU_SERIAL in the environment.")
    with _LOCK:
        ip = _CONN["ip"]
        access = _CONN["access_code"]
    jpeg = _camera_rtsps_frame(ip, access) or _camera_p1_frame(ip, access)
    if not jpeg:
        return err("camera fetch failed: neither RTSPS (port 322, needs ffmpeg — "
                   "`pip install imageio-ffmpeg`) nor the P1-series port-6000 stream "
                   "returned a frame. Check LAN-mode liveview is enabled on the printer.")
    if save_path:
        p = Path(save_path).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(jpeg)
        return ok(f"saved snapshot → {p} ({len(jpeg)} bytes)", path=str(p))
    return ok(f"snapshot ({len(jpeg)} bytes)", jpeg_base64=base64.b64encode(jpeg).decode())


@tool
def bambu_ams() -> dict:
    """Get AMS (Automatic Material System) filament status.

    Returns:
        {status, content, ams:[{id, humidity, temp, slots:[{material, color, remaining_pct}]}]}
    """
    conn = _require_conn()
    if not conn:
        return err("not connected. Call bambu_connect() first, or set BAMBU_IP, BAMBU_ACCESS_CODE and BAMBU_SERIAL in the environment.")
    with _LOCK:
        s = dict(_CONN["last_state"])
    ams_raw = s.get("ams", {})
    if not ams_raw:
        return ok("no AMS attached", ams=[])
    result = []
    for unit in ams_raw.get("ams", []):
        slots = []
        for tray in unit.get("tray", []):
            slots.append({
                "id": tray.get("id"),
                "material": tray.get("tray_type"),
                "color": tray.get("tray_color"),
                "remaining_pct": tray.get("remain"),
                "tray_sub_brands": tray.get("tray_sub_brands"),
            })
        result.append({
            "id": unit.get("id"),
            "humidity": unit.get("humidity"),
            "temp": unit.get("temp"),
            "slots": slots,
        })
    return ok(f"AMS: {len(result)} unit(s)", ams=result)
