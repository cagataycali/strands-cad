"""Preview layer — auto-refresh HTTP server for renders."""
from __future__ import annotations
import http.server
import socketserver
import threading
from pathlib import Path
from typing import Any

from strands import tool
from strands_cad._common import ok, err


_SERVERS: dict[int, dict] = {}
_LOCK = threading.Lock()


_INDEX_TEMPLATE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>strands-cad preview</title>
<meta http-equiv="refresh" content="{refresh}">
<style>
 *{{box-sizing:border-box}} body{{background:#0a0a0a;color:#fff;font-family:-apple-system,sans-serif;margin:0;padding:24px}}
 h1{{color:#ff8c1a;margin:0 0 16px}} .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:14px}}
 .card{{background:#141414;border:1px solid #2a2a2a;border-radius:10px;overflow:hidden}}
 .card img{{width:100%;display:block;background:#f5f5f5}} .card h3{{margin:0;padding:10px 14px;font-size:12px;color:#ff8c1a;background:#1a1a1a}}
</style></head><body>
<h1>🔧 strands-cad · live preview · refresh {refresh}s</h1>
<div class="grid">
{cards}
</div></body></html>"""


def _make_handler(root: Path):
    class _H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(root), **kw)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                png_paths = sorted(set(list(root.glob("*.png")) + list(root.glob("**/*.png"))))
                cards = "\n".join(
                    f'<div class="card"><h3>{p.relative_to(root)}</h3>'
                    f'<img src="{p.relative_to(root)}?t={int(p.stat().st_mtime)}"></div>'
                    for p in png_paths[:24]
                )
                body = _INDEX_TEMPLATE.format(refresh=4, cards=cards or "<p style='color:#888'>no PNGs yet</p>")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body.encode())))
                self.end_headers()
                self.wfile.write(body.encode())
            else:
                super().do_GET()

        def log_message(self, *a, **k):  # silence
            pass
    return _H


class _ReuseServer(socketserver.TCPServer):
    allow_reuse_address = True


@tool
def preview_serve(directory: str, port: int = 8765) -> dict:
    """Start a live-refresh HTTP server serving PNG renders from a directory.

    Args:
        directory: Directory to serve (should contain .png previews).
        port: TCP port to bind (default 8765).

    Returns:
        {status, content, port, url, pid}
    """
    root = Path(directory).resolve()
    if not root.exists():
        return err(f"directory not found: {root}")
    with _LOCK:
        if port in _SERVERS:
            return err(f"port {port} already serving. Call preview_stop({port}) first.")
        try:
            httpd = _ReuseServer(("", port), _make_handler(root))
        except OSError as e:
            return err(f"bind port {port} failed: {e}")
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        _SERVERS[port] = {"httpd": httpd, "thread": t, "root": root}
    return ok(f"serving {root} @ http://localhost:{port}", port=port,
              url=f"http://localhost:{port}", directory=str(root))


@tool
def preview_stop(port: int = 8765) -> dict:
    """Stop a previously-started preview server.

    Args:
        port: The port to stop.
    """
    with _LOCK:
        srv = _SERVERS.pop(port, None)
    if not srv:
        return err(f"no server on port {port}")
    try:
        srv["httpd"].shutdown()
        srv["httpd"].server_close()
    except Exception as e:
        return err(f"shutdown failed: {e}")
    return ok(f"stopped port {port}", port=port)
