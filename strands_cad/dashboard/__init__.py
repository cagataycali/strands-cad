"""strands-cad printer dashboard — WebAuthn-gated live camera + control.

Modules:
  auth     — WebAuthn passkey enrollment / login / JWT session guard
  tls      — auto self-signed cert (WebAuthn secure-context over LAN)
  camera   — Bambu RTSPS → H.264 → MJPEG background streamer
  printer  — MQTT telemetry poller + pause/resume/stop control
  server   — FastAPI app tying it together (entry: strands-cad-dashboard)

Programmatic:
    from strands_cad.dashboard.server import run
    run(ip="192.168.1.164", access_code="xxxx", tls=True)
"""
__all__ = ["auth", "tls", "camera", "printer", "server"]
