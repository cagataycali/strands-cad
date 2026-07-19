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


def _require_conn() -> tuple[Any, str] | None:
    with _LOCK:
        if _CONN["client"] is None or _CONN["serial"] is None:
            return None
        return _CONN["client"], _CONN["serial"]


@tool
def bambu_send(file_path: str, plate_index: int = 1, use_ams: bool = True) -> dict:
    """Upload a sliced 3MF/G-code and start the print job.

    Args:
        file_path: Local path to sliced .3mf (with G-code) or .gcode.
        plate_index: Which plate in the 3MF to print (default 1).
        use_ams: If True, use AMS filament mapping from the file.

    Returns:
        {status, content, job:{file, plate}}
    """
    conn = _require_conn()
    if not conn:
        return err("not connected. Call bambu_connect() first.")
    client, serial = conn
    src = Path(file_path).resolve()
    if not src.exists():
        return err(f"file not found: {src}")

    # Bambu supports FTP over TLS for file upload (port 990). For simplicity,
    # we require the file to already exist on the printer's SD, or use the
    # Bambu Handy app to upload. Here we send the "start print" command.
    is_gcode = src.suffix.lower() == ".gcode"
    # Bambu firmware: a 3MF *project* references its internal
    # Metadata/plate_N.gcode; a bare .gcode on SD must reference the file
    # itself (Metadata/plate_N.gcode does NOT exist inside a plain gcode →
    # the printer silently rejects the job). AMS mapping only exists inside a
    # 3MF, so use_ams is meaningful only for 3MF projects.
    param = src.name if is_gcode else f"Metadata/plate_{plate_index}.gcode"
    payload = {
        "print": {
            "sequence_id": str(int(time.time())),
            "command": "project_file",
            "param": param,
            "subtask_name": src.stem,
            "url": f"file:///mnt/sdcard/{src.name}",
            "bed_type": "auto",
            "timelapse": True,
            "flow_cali": False,
            "use_ams": (use_ams and not is_gcode),
        }
    }
    client.publish(f"device/{serial}/request", json.dumps(payload))
    return ok(f"job dispatched: {src.name} (plate {plate_index})",
              job={"file": src.name, "plate": plate_index, "use_ams": use_ams},
              note="Use bambu_upload() first if the file is not yet on the SD card.")


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
        return err("not connected. Call bambu_connect() first.")
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
                   "(sdcard=False). Insert a FAT32 microSD card into the printer "
                   "to enable file upload + LAN printing.",
                   sdcard=False, hint="insert_sd_card")

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
        return err("not connected. Call bambu_connect() first.")
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
        return err("not connected.")
    client, serial = conn
    cmd_map = {"pause": "pause", "resume": "resume", "stop": "stop"}
    if action not in cmd_map:
        return err(f"unknown action '{action}'. Options: pause, resume, stop.")
    payload = {"print": {"sequence_id": str(int(time.time())), "command": cmd_map[action]}}
    client.publish(f"device/{serial}/request", json.dumps(payload))
    return ok(f"sent {action}", action=action)


@tool
def bambu_camera(save_path: str = "") -> dict:
    """Fetch a JPEG snapshot from the printer's chamber camera.

    Requires the printer's chamber camera to be enabled in LAN mode.
    Uses the printer's authenticated JPEG stream (port 6000 rtsp or 8080 http).

    Args:
        save_path: Optional path to save the JPEG. If empty, returns base64 in payload.
    """
    conn = _require_conn()
    if not conn:
        return err("not connected.")
    with _LOCK:
        ip = _CONN["ip"]
        access = _CONN["access_code"]
    try:
        import requests  # type: ignore
    except ImportError:
        return err("requests required. pip install 'strands-cad[bambu]'")
    # Bambu exposes: rtsps://bblp:<access>@<ip>:322/streaming/live/1
    # HTTP snapshot on newer firmware:
    url = f"http://{ip}:6000/snapshot.jpg"
    try:
        r = requests.get(url, timeout=5, auth=("bblp", access))
        if r.status_code != 200 or not r.content:
            return err(f"camera returned {r.status_code} (may need RTSP-only firmware)")
    except Exception as e:
        return err(f"camera fetch failed: {e}")
    if save_path:
        p = Path(save_path).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(r.content)
        return ok(f"saved snapshot → {p} ({len(r.content)} bytes)", path=str(p))
    return ok(f"snapshot ({len(r.content)} bytes)", jpeg_base64=base64.b64encode(r.content).decode())


@tool
def bambu_ams() -> dict:
    """Get AMS (Automatic Material System) filament status.

    Returns:
        {status, content, ams:[{id, humidity, temp, slots:[{material, color, remaining_pct}]}]}
    """
    conn = _require_conn()
    if not conn:
        return err("not connected.")
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
