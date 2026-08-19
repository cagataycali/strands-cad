#!/usr/bin/env python3
"""
🖨️ Bambu printer telemetry poller for the dashboard.

Owns ONE MQTT connection (TLS, port 8883) to the printer and keeps the latest
`print` report cached. The dashboard polls `snapshot()` (instant, non-blocking)
for temps / progress / AMS. Also issues control commands (pause/resume/stop) and
records the serial auto-discovered from the report topic.

Resilience (three layers — a network blip must never require a manual restart):
  1. `connect_async` + paho auto-reconnect (`reconnect_delay_set`): the initial
     connection AND any dropped connection are retried by paho's own loop.
  2. `on_disconnect` marks the cached state honest (`connected: False`).
  3. A watchdog thread rebuilds the whole client from scratch when telemetry
     goes stale — covering the wedge paho can't see (half-open TLS after the
     printer power-cycles, a reconnect loop stuck on a dead socket, etc.).

Env
---
  BAMBU_IP                  printer LAN IP
  BAMBU_ACCESS_CODE         access code (Settings → Network → LAN mode)
  BAMBU_SERIAL              (optional) serial; auto-discovered from topic otherwise
  BAMBU_WATCHDOG_INTERVAL   seconds between watchdog checks (default 20)
  BAMBU_STALE_PUSHALL_S     connected-but-silent threshold: nudge a pushall (default 120)
  BAMBU_STALE_RECONNECT_S   stale threshold: tear down + rebuild the client (default 300)
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

WATCHDOG_INTERVAL = float(os.getenv("BAMBU_WATCHDOG_INTERVAL", "20"))
STALE_PUSHALL_S = float(os.getenv("BAMBU_STALE_PUSHALL_S", "120"))
STALE_RECONNECT_S = float(os.getenv("BAMBU_STALE_RECONNECT_S", "300"))


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
        self._closing = False
        self._reconnects = 0
        self._started = time.time()
        self._watchdog_thread: Optional[threading.Thread] = None

    @classmethod
    def from_env(cls) -> "BambuPrinter":
        ip = os.getenv("BAMBU_IP", "")
        access = os.getenv("BAMBU_ACCESS_CODE", "")
        if not ip or not access:
            raise RuntimeError("Set BAMBU_IP and BAMBU_ACCESS_CODE.")
        return cls(ip, access, os.getenv("BAMBU_SERIAL", ""))

    # ── connection ──────────────────────────────────────────────────────────

    def connect(self) -> bool:
        """Start the MQTT client and the watchdog. Always succeeds in starting
        the machinery; the actual connection is established (and re-established)
        asynchronously by paho + the watchdog."""
        ok = self._build_client()
        self._ensure_watchdog()
        return ok

    def _build_client(self) -> bool:
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
        # paho retries dropped/failed connections itself, with backoff
        client.reconnect_delay_set(min_delay=1, max_delay=60)

        def on_connect(c, u, f, rc, *a):
            if rc == 0:
                self._connected = True
                log.info(f"MQTT connected to {self.ip}")
                c.subscribe("device/+/report")
                # request a full push
                if self.serial:
                    c.publish(f"device/{self.serial}/request",
                              json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}}))
            else:
                log.warning(f"MQTT connect refused (rc={rc})")

        def on_disconnect(c, u, rc, *a):
            self._connected = False
            if not self._closing:
                log.warning(f"MQTT disconnected (rc={rc}) — paho will retry; "
                            f"watchdog rebuilds if it stays down")

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
        client.on_disconnect = on_disconnect
        client.on_message = on_message
        try:
            # async: an unreachable printer at startup is retried by the loop
            # instead of permanently failing the singleton
            client.connect_async(self.ip, 8883, keepalive=60)
        except Exception as e:
            log.error(f"MQTT connect_async failed: {e}")
            return False
        client.loop_start()
        self._client = client
        time.sleep(1.2)
        return True

    def request_pushall(self):
        """Ask the printer to push its full state (no-op if not connected)."""
        if self._client and self.serial:
            try:
                self._client.publish(
                    f"device/{self.serial}/request",
                    json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}}))
            except Exception as e:
                log.debug(f"pushall failed: {e}")

    # ── watchdog ────────────────────────────────────────────────────────────

    def _ensure_watchdog(self):
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            return
        t = threading.Thread(target=self._watchdog, name="bambu-watchdog", daemon=True)
        t.start()
        self._watchdog_thread = t

    def _watchdog(self):
        log.info(f"watchdog started (check={WATCHDOG_INTERVAL:.0f}s, "
                 f"pushall>{STALE_PUSHALL_S:.0f}s, rebuild>{STALE_RECONNECT_S:.0f}s)")
        while not self._closing:
            time.sleep(WATCHDOG_INTERVAL)
            if self._closing:
                return
            try:
                self._watchdog_check()
            except Exception as e:  # a watchdog that dies is a zombie factory
                log.warning(f"watchdog check error: {e}")

    def _watchdog_check(self):
        now = time.time()
        # age of last telemetry; before any message, age since client birth
        age = now - (self._last_update or self._started)
        if self._connected and STALE_PUSHALL_S < age <= STALE_RECONNECT_S:
            # connected but silent — the printer pushes on change, so idle can
            # be quiet; a pushall proves the pipe is alive (and refreshes age)
            log.info(f"watchdog: connected but silent for {age:.0f}s — requesting pushall")
            self.request_pushall()
            return
        if age > STALE_RECONNECT_S:
            # stale regardless of what the client believes: half-open TLS after
            # a printer power-cycle keeps _connected True forever. Rebuild.
            log.warning(f"watchdog: telemetry stale for {age:.0f}s "
                        f"(connected={self._connected}) — rebuilding MQTT client")
            self._rebuild()

    def _rebuild(self):
        self._reconnects += 1
        old, self._client = self._client, None
        self._connected = False
        if old:
            try:
                old.loop_stop()
            except Exception:
                pass
            try:
                old.disconnect()
            except Exception:
                pass
        self._build_client()

    # ── state ───────────────────────────────────────────────────────────────

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
            "sdcard": s.get("sdcard"),
            "nozzle_count": 2 if s.get("2D") is not None else 1,
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
            "reconnects": self._reconnects,
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
        self._closing = True
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
