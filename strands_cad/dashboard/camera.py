#!/usr/bin/env python3
"""
📷 Bambu Lab chamber-camera streamer — RTSPS → H.264 → MJPEG.

Why this module exists
----------------------
Bambu P1/A1 printers expose their chamber camera ONLY as an RTSPS (RTSP-over-
TLS) H.264 stream on port 322, behind LIVE555 with HTTP-Digest auth. Two things
make this painful:

  1. `ffmpeg` (both 4.x and 7.x) HANGS on the `rtsps://` scheme against Bambu's
     TLS1.3 + LIVE555 endpoint (gnutls/redirect handling bug). You cannot just
     `ffmpeg -i rtsps://...`.
  2. The advertised URL redirects confusingly; the ACTUAL working path is
     `rtsps://<ip>/streaming/live/1` (NOT `/1`, despite a 301 pointing there).

So we do the RTSP control channel OURSELVES in pure Python over a `ssl` socket
(OPTIONS → DESCRIBE[digest] → SETUP[interleaved TCP] → PLAY), pull the SPS/PPS
out of the SDP, reassemble H.264 NAL units from the interleaved RTP packets
(handling FU-A fragmentation), prepend Annex-B start codes, and pipe that raw
elementary stream to ffmpeg's STDIN (`-f h264 -i -`) which happily transcodes
it to MJPEG. The bundled static ffmpeg from `imageio-ffmpeg` is used, so there
is no system ffmpeg dependency.

One background thread per printer owns the socket + ffmpeg child and keeps the
latest JPEG frame in memory; all HTTP clients (snapshot + MJPEG multipart)
share that frame. Auto-reconnects on drop. Bambu's LIVE555 allows only ONE
concurrent live-view session, so we keep exactly one and always TEARDOWN.

Public API
----------
    cam = BambuCamera(ip, access_code)         # or .from_env()
    cam.start()
    jpg = cam.latest()                         # bytes | None
    cam.stop()

    get_camera(ip, access) -> shared singleton per (ip)
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import socket
import ssl
import subprocess
import threading
import time
from typing import Optional

log = logging.getLogger("strands_cad.dashboard.camera")

# 90×90 dark-grey "no signal" placeholder JPEG (base64), served before the
# first real frame arrives so the <img> never shows a broken icon.
_PLACEHOLDER_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAgAAAQABAAD//gAPTGF2YzYxLjMuMTAwAP/bAEMACAQEBAQEBQUFBQUFBgYGBgYGBgYGBgYGBgcHBwgICAcHBwYGBwcICAgICQkJCAgICAkJCgoKDAwLCw4ODhERFP/EAEwAAQEAAAAAAAAAAAAAAAAAAAAHAQEBAAAAAAAAAAAAAAAAAAAAAhABAAAAAAAAAAAAAAAAAAAAABEBAAAAAAAAAAAAAAAAAAAAAP/AABEIALQBQAMBIgACEQADEQD/2gAMAwEAAhEDEQA/AIgAsAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAf/9k="
)


def placeholder_jpeg() -> bytes:
    return _PLACEHOLDER_JPEG


def _ffmpeg_exe() -> str:
    """Locate an ffmpeg binary — prefer the bundled static one."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        import shutil
        exe = shutil.which("ffmpeg")
        if not exe:
            raise RuntimeError(
                "ffmpeg not found. Install the dashboard extra: "
                "pip install 'strands-cad[dashboard]' (bundles imageio-ffmpeg)."
            )
        return exe


class _RTSPSClient:
    """Minimal RTSP-over-TLS client that yields raw Annex-B H.264 bytes."""

    def __init__(self, ip: str, access_code: str, user: str = "bblp",
                 port: int = 322, path: str = "/streaming/live/1"):
        self.ip = ip
        self.access = access_code
        self.user = user
        self.port = port
        self.path = path
        self.base = f"rtsps://{ip}{path}"
        self._sock: Optional[ssl.SSLSocket] = None
        self._buf = b""
        self._cseq = 0
        self._realm: Optional[str] = None
        self._nonce: Optional[str] = None
        self._session: Optional[str] = None

    # ── RTSP plumbing ──────────────────────────────────────────────────
    def _digest(self, method: str, uri: str) -> str:
        ha1 = hashlib.md5(f"{self.user}:{self._realm}:{self.access}".encode()).hexdigest()
        ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
        resp = hashlib.md5(f"{ha1}:{self._nonce}:{ha2}".encode()).hexdigest()
        return (f'Digest username="{self.user}", realm="{self._realm}", '
                f'nonce="{self._nonce}", uri="{uri}", response="{resp}"')

    def _read_headers(self) -> str:
        while b"\r\n\r\n" not in self._buf:
            c = self._sock.recv(4096)
            if not c:
                raise ConnectionError("RTSP socket closed during header read")
            self._buf += c
        head, _, rest = self._buf.partition(b"\r\n\r\n")
        self._buf = rest
        return head.decode(errors="replace")

    def _request(self, method: str, uri: str, extra: str = "") -> tuple[str, str]:
        self._cseq += 1
        auth = f"Authorization: {self._digest(method, uri)}\r\n" if self._nonce else ""
        sess = f"Session: {self._session}\r\n" if self._session else ""
        msg = (f"{method} {uri} RTSP/1.0\r\nCSeq: {self._cseq}\r\n"
               f"User-Agent: strands-cad\r\n{auth}{sess}{extra}\r\n")
        self._sock.sendall(msg.encode())
        head = self._read_headers()
        body = ""
        cl = re.search(r"Content-[Ll]ength:\s*(\d+)", head)
        if cl:
            n = int(cl.group(1))
            while len(self._buf) < n:
                c = self._sock.recv(4096)
                if not c:
                    break
                self._buf += c
            body = self._buf[:n].decode(errors="replace")
            self._buf = self._buf[n:]
        return head, body

    def connect(self, timeout: float = 10.0) -> tuple[bytes, bytes]:
        """Handshake through PLAY. Returns (sps, pps) raw bytes."""
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        raw = socket.create_connection((self.ip, self.port), timeout=timeout)
        self._sock = ctx.wrap_socket(raw, server_hostname=self.ip)
        self._sock.settimeout(timeout)

        self._request("OPTIONS", self.base)
        # first DESCRIBE → 401 carrying realm+nonce
        head, _ = self._request("DESCRIBE", self.base, "Accept: application/sdp\r\n")
        m = re.search(r'realm="([^"]+)".*?nonce="([^"]+)"', head, re.S)
        if m:
            self._realm, self._nonce = m.group(1), m.group(2)
        # authed DESCRIBE (retry a few times — LIVE555 can 404 transiently if a
        # previous session hasn't been reaped yet).
        body = ""
        for _ in range(6):
            head, body = self._request("DESCRIBE", self.base, "Accept: application/sdp\r\n")
            if "200" in head and "sprop-parameter-sets" in body:
                break
            mm = re.search(r'nonce="([^"]+)"', head)
            if mm:
                self._nonce = mm.group(1)
            time.sleep(0.4)
        sm = re.search(r"sprop-parameter-sets=([^;\r\n]+)", body)
        if not sm:
            raise ConnectionError(f"no SPS/PPS in SDP (last status: {head.splitlines()[0] if head else '?'})")
        sps_b64, pps_b64 = sm.group(1).split(",")

        # control track from SDP. There are usually TWO a=control lines:
        # a session-level "*" and a media-level "trackN" (under m=video). We
        # want the media-level one — the "*" would 404 on SETUP.
        tracks = re.findall(r"a=control:(\S+)", body)
        track = next((t for t in tracks if t != "*"), "track1")
        track_uri = track if track.startswith("rtsp") else f"{self.base}/{track}"

        head, _ = self._request(
            "SETUP", track_uri,
            "Transport: RTP/AVP/TCP;unicast;interleaved=0-1\r\n")
        sess = re.search(r"Session:\s*([^;\r\n]+)", head)
        if not sess:
            raise ConnectionError("SETUP returned no Session")
        self._session = sess.group(1).strip()

        head, _ = self._request("PLAY", self.base, "Range: npt=0.000-\r\n")
        if "200" not in head:
            raise ConnectionError(f"PLAY failed: {head.splitlines()[0] if head else '?'}")
        return base64.b64decode(sps_b64), base64.b64decode(pps_b64)

    def _need(self, n: int) -> bool:
        while len(self._buf) < n:
            c = self._sock.recv(65536)
            if not c:
                return False
            self._buf += c
        return True

    def iter_nals(self):
        """Yield Annex-B framed NAL units (with 00 00 00 01 start codes)."""
        start = b"\x00\x00\x00\x01"
        fu = b""
        while True:
            if not self._need(4):
                return
            if self._buf[0] != 0x24:  # not a '$' interleaved frame
                self._buf = self._buf[1:]
                continue
            ch = self._buf[1]
            ln = (self._buf[2] << 8) | self._buf[3]
            if not self._need(4 + ln):
                return
            pkt = self._buf[4:4 + ln]
            self._buf = self._buf[4 + ln:]
            if ch != 0 or len(pkt) < 12:
                continue
            payload = pkt[12:]  # strip 12-byte RTP header
            if not payload:
                continue
            ntype = payload[0] & 0x1F
            if ntype == 28:  # FU-A fragment
                fh = payload[1]
                start_bit, end_bit, otype = fh & 0x80, fh & 0x40, fh & 0x1F
                if start_bit:
                    fu = bytes([(payload[0] & 0xE0) | otype]) + payload[2:]
                else:
                    fu += payload[2:]
                if end_bit and fu:
                    yield start + fu
                    fu = b""
            elif ntype in (1, 5, 7, 8):  # single NAL (slice/IDR/SPS/PPS)
                yield start + payload

    def teardown(self):
        try:
            if self._sock and self._session:
                self._request("TEARDOWN", self.base)
        except Exception:
            pass
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        self._sock = None


class BambuCamera:
    """Background RTSPS→MJPEG streamer for one Bambu printer.

    Owns a worker thread that keeps the RTSPS session + an ffmpeg child alive,
    decoding H.264 to a rolling latest-JPEG that all HTTP clients share.
    """

    def __init__(self, ip: str, access_code: str, *, fps: int = 15,
                 quality: int = 5, width: int = 0):
        self.ip = ip
        self.access = access_code
        self.fps = max(1, min(fps, 30))
        self.quality = quality      # ffmpeg -q:v (2=best … 31=worst)
        self.width = width          # 0 = native (1920); else scale down
        self._latest: Optional[bytes] = None
        self._latest_ts = 0.0
        self._frames = 0
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._err: Optional[str] = None

    @classmethod
    def from_env(cls) -> "BambuCamera":
        ip = os.getenv("BAMBU_IP", "")
        access = os.getenv("BAMBU_ACCESS_CODE", "")
        if not ip or not access:
            raise RuntimeError("Set BAMBU_IP and BAMBU_ACCESS_CODE env vars.")
        return cls(ip, access,
                   fps=int(os.getenv("BAMBU_CAM_FPS", "15")),
                   quality=int(os.getenv("BAMBU_CAM_QUALITY", "5")))

    # ── lifecycle ──────────────────────────────────────────────────────
    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name=f"bambu-cam-{self.ip}")
        self._thread.start()
        log.info(f"📷 BambuCamera started for {self.ip}")

    def stop(self):
        self._running = False
        log.info(f"📷 BambuCamera stopping for {self.ip}")

    def latest(self) -> Optional[bytes]:
        with self._lock:
            return self._latest

    def status(self) -> dict:
        with self._lock:
            age = time.time() - self._latest_ts if self._latest_ts else None
            return {
                "ip": self.ip,
                "running": self._running,
                "has_frame": self._latest is not None,
                "frames": self._frames,
                "last_frame_age_s": round(age, 2) if age is not None else None,
                "fps": self.fps,
                "error": self._err,
            }

    # ── worker ─────────────────────────────────────────────────────────
    def _loop(self):
        backoff = 1.0
        while self._running:
            client = _RTSPSClient(self.ip, self.access)
            ff = None
            try:
                sps, pps = client.connect()
                self._err = None
                backoff = 1.0
                ff = self._spawn_ffmpeg()

                # writer: RTSP NALs → ffmpeg stdin
                start = b"\x00\x00\x00\x01"
                ff.stdin.write(start + sps + start + pps)
                ff.stdin.flush()

                # reader: ffmpeg stdout MJPEG → latest frame
                reader = threading.Thread(target=self._read_mjpeg, args=(ff,),
                                          daemon=True)
                reader.start()

                for nal in client.iter_nals():
                    if not self._running:
                        break
                    try:
                        ff.stdin.write(nal)
                    except (BrokenPipeError, OSError):
                        break
                # flush loop ended → connection dropped
            except Exception as e:
                self._err = f"{type(e).__name__}: {e}"
                log.warning(f"📷 {self.ip} stream error: {self._err}")
            finally:
                client.teardown()
                if ff:
                    try:
                        ff.stdin.close()
                    except Exception:
                        pass
                    try:
                        ff.terminate()
                        ff.wait(timeout=3)
                    except Exception:
                        try:
                            ff.kill()
                        except Exception:
                            pass
            if self._running:
                time.sleep(backoff)
                backoff = min(backoff * 1.5, 10.0)
        log.info(f"📷 BambuCamera loop exited for {self.ip}")

    def _spawn_ffmpeg(self) -> subprocess.Popen:
        vf = f"-vf scale={self.width}:-2 " if self.width else ""
        cmd = [
            _ffmpeg_exe(),
            "-loglevel", "error",
            "-fflags", "nobuffer", "-flags", "low_delay",
            "-f", "h264", "-i", "pipe:0",
            "-r", str(self.fps),
            *(vf.split()),
            "-f", "mjpeg", "-q:v", str(self.quality),
            "pipe:1",
        ]
        return subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                bufsize=0)

    def _read_mjpeg(self, ff: subprocess.Popen):
        """Split ffmpeg's MJPEG stdout into individual JPEG frames."""
        buf = b""
        SOI, EOI = b"\xff\xd8", b"\xff\xd9"
        while self._running:
            chunk = ff.stdout.read(65536)
            if not chunk:
                break
            buf += chunk
            while True:
                i = buf.find(SOI)
                if i < 0:
                    if len(buf) > 4_000_000:
                        buf = buf[-1024:]
                    break
                j = buf.find(EOI, i + 2)
                if j < 0:
                    break
                jpg = buf[i:j + 2]
                buf = buf[j + 2:]
                with self._lock:
                    self._latest = jpg
                    self._latest_ts = time.time()
                    self._frames += 1


# ── shared singletons (one camera per printer IP) ──────────────────────────
_CAMERAS: dict[str, BambuCamera] = {}
_CAM_LOCK = threading.Lock()


def get_camera(ip: str, access_code: str, **kw) -> BambuCamera:
    """Return (and lazily start) the shared BambuCamera for this IP."""
    with _CAM_LOCK:
        cam = _CAMERAS.get(ip)
        if cam is None:
            cam = BambuCamera(ip, access_code, **kw)
            _CAMERAS[ip] = cam
        cam.start()
        return cam


def stop_all():
    with _CAM_LOCK:
        for cam in _CAMERAS.values():
            cam.stop()
        _CAMERAS.clear()
