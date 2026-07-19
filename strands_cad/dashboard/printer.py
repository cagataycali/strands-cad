#!/usr/bin/env python3
"""
🖨️ Bambu printer telemetry poller for the dashboard.

Owns ONE MQTT connection (TLS, port 8883) to the printer and keeps the latest
`print` report cached. The dashboard polls `snapshot()` (instant, non-blocking)
for temps / progress / AMS. Also issues control commands (pause/resume/stop) and
records the serial auto-discovered from the report topic.

Env
---
  BAMBU_IP           printer LAN IP
  BAMBU_ACCESS_CODE  access code (Settings → Network → LAN mode)
  BAMBU_SERIAL       (optional) serial; auto-discovered from topic otherwise
"""
from __future__ import annotations

import json
import logging
import os
import ssl
import threading
import time
from typing import Any, Dict, Optional

log = logging.getLogger("strands_cad.dashboard.printer")


class BambuPrinter:
    def __init__(self, ip: str, access_code: str, serial: str = ""):
        self.ip = ip
        self.access = access_code
        self.serial = serial
        self._client = None
        self._state: Dict[str, Any] = {}
        self._last_update = 0.0
        self._lock = threading.Lock()
        self._connected = False

    @classmethod
    def from_env(cls) -> "BambuPrinter":
        ip = os.getenv("BAMBU_IP", "")
        access = os.getenv("BAMBU_ACCESS_CODE", "")
        if not ip or not access:
            raise RuntimeError("Set BAMBU_IP and BAMBU_ACCESS_CODE.")
        return cls(ip, access, os.getenv("BAMBU_SERIAL", ""))

    def connect(self) -> bool:
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            log.error("paho-mqtt not installed")
            return False

        try:
            client = mqtt.Client(client_id=f"strands-cad-dash-{int(time.time())}",
                                 protocol=mqtt.MQTTv311)
        except Exception:
            # paho 2.x callback API
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1,
                                 client_id=f"strands-cad-dash-{int(time.time())}")
        client.username_pw_set("bblp", self.access)
        client.tls_set(cert_reqs=ssl.CERT_NONE, tls_version=ssl.PROTOCOL_TLSv1_2)
        client.tls_insecure_set(True)

        def on_connect(c, u, f, rc, *a):
            if rc == 0:
                self._connected = True
                c.subscribe("device/+/report")
                # request a full push
                if self.serial:
                    c.publish(f"device/{self.serial}/request",
                              json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}}))

        def on_message(c, u, msg):
            try:
                data = json.loads(msg.payload)
            except Exception:
                return
            # auto-discover serial from topic: device/<serial>/report
            parts = msg.topic.split("/")
            if len(parts) >= 2 and not self.serial:
                self.serial = parts[1]
                c.publish(f"device/{self.serial}/request",
                          json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}}))
            with self._lock:
                self._state.update(data.get("print", data))
                self._last_update = time.time()

        client.on_connect = on_connect
        client.on_message = on_message
        try:
            client.connect(self.ip, 8883, keepalive=60)
        except Exception as e:
            log.error(f"MQTT connect failed: {e}")
            return False
        client.loop_start()
        self._client = client
        time.sleep(1.2)
        return True

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            s = dict(self._state)
            age = time.time() - self._last_update if self._last_update else None
        ams = self._parse_ams(s.get("ams", {}))
        return {
            "connected": self._connected,
            "serial": self.serial,
            "gcode_state": s.get("gcode_state"),
            "progress": s.get("mc_percent"),
            "layer": s.get("layer_num"),
            "total_layers": s.get("total_layer_num"),
            "remaining_min": s.get("mc_remaining_time"),
            "subtask_name": s.get("subtask_name"),
            "temps": {
                "nozzle": s.get("nozzle_temper"),
                "nozzle_target": s.get("nozzle_target_temper"),
                "bed": s.get("bed_temper"),
                "bed_target": s.get("bed_target_temper"),
                "chamber": s.get("chamber_temper"),
            },
            "fan": {
                "cooling": s.get("cooling_fan_speed"),
                "big_fan1": s.get("big_fan1_speed"),
                "big_fan2": s.get("big_fan2_speed"),
            },
            "speed_level": s.get("spd_lvl"),
            "ams": ams,
            "wifi_signal": s.get("wifi_signal"),
            "age_seconds": round(age, 1) if age is not None else None,
        }

    @staticmethod
    def _parse_ams(ams_raw: dict) -> list:
        out = []
        for unit in (ams_raw.get("ams", []) if isinstance(ams_raw, dict) else []):
            slots = []
            for tray in unit.get("tray", []):
                slots.append({
                    "id": tray.get("id"),
                    "material": tray.get("tray_type"),
                    "color": tray.get("tray_color"),
                    "remaining_pct": tray.get("remain"),
                })
            out.append({"id": unit.get("id"), "humidity": unit.get("humidity"),
                        "temp": unit.get("temp"), "slots": slots})
        return out

    def control(self, action: str) -> bool:
        if not self._client or not self.serial:
            return False
        cmd = {"pause": "pause", "resume": "resume", "stop": "stop"}.get(action)
        if not cmd:
            return False
        self._client.publish(f"device/{self.serial}/request",
                             json.dumps({"print": {"sequence_id": str(int(time.time())),
                                                    "command": cmd}}))
        return True

    def disconnect(self):
        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass
        self._connected = False


_PRINTER: Optional[BambuPrinter] = None
_PLOCK = threading.Lock()


def get_printer(ip: str, access_code: str, serial: str = "") -> BambuPrinter:
    global _PRINTER
    with _PLOCK:
        if _PRINTER is None:
            _PRINTER = BambuPrinter(ip, access_code, serial)
            _PRINTER.connect()
        return _PRINTER
