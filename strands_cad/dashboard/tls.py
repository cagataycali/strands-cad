#!/usr/bin/env python3
"""
🔒 strands-cad dashboard TLS — auto self-signed cert for WebAuthn over LAN.

WebAuthn (passkeys) only run in a "secure context": HTTPS, or http://localhost.
Open the dashboard at http://192.168.1.x:PORT and the browser refuses the
passkey ceremony. So for real LAN use the dashboard must speak HTTPS. We don't
depend on Let's Encrypt (a printer host rarely has public DNS) — we mint our
OWN self-signed cert, valid for this box's hostname(s) + every local IP.

Trade-off: a self-signed cert isn't browser-trusted, so you see a one-time
"connection is not private" warning → Advanced → Proceed. That satisfies the
secure-context requirement, so passkeys work. For zero-warning use, install
mkcert (auto-detected) or drop a real cert at STRANDS_CAD_TLS_CERT/KEY.

Env
---
  STRANDS_CAD_TLS       true/false (default false) — enable HTTPS
  STRANDS_CAD_TLS_CERT  path to fullchain.pem  (operator-supplied)
  STRANDS_CAD_TLS_KEY   path to key.pem
  STRANDS_CAD_TLS_DIR   cache dir (default ./.strands_cad_tls)
  STRANDS_CAD_TLS_HOSTS extra SANs (comma/space separated)
  STRANDS_CAD_TLS_MKCERT auto|on|off (default auto — use mkcert if present)
"""
from __future__ import annotations

import datetime
import ipaddress
import os
import socket
from pathlib import Path
from typing import List, Optional, Tuple


def _local_ips() -> List[str]:
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        host = socket.gethostname()
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            ips.add(info[4][0])
    except Exception:
        pass
    ips.add("127.0.0.1")
    return sorted(ips)


def _hostnames() -> List[str]:
    names = {"localhost"}
    try:
        names.add(socket.gethostname())
        names.add(socket.getfqdn())
    except Exception:
        pass
    extra = os.getenv("STRANDS_CAD_TLS_HOSTS", "").strip()
    if extra:
        for n in extra.replace(",", " ").split():
            if n:
                names.add(n)
    return sorted(n for n in names if n)


def _generate_self_signed(cert_path: Path, key_path: Path) -> None:
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    hostnames = _hostnames()
    ips = _local_ips()
    cn = hostnames[0] if hostnames else "strands-cad.local"
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, cn),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "strands-cad"),
    ])
    san: List[x509.GeneralName] = [x509.DNSName(h) for h in hostnames]
    for ip in ips:
        try:
            san.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except ValueError:
            pass
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(subject).issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption()))
    try:
        os.chmod(key_path, 0o600)
    except Exception:
        pass


def _cert_still_valid(cert_path: Path) -> bool:
    try:
        from cryptography import x509
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        na = getattr(cert, "not_valid_after_utc", None) or \
            cert.not_valid_after.replace(tzinfo=datetime.timezone.utc)
        return na > datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=2)
    except Exception:
        return False


def _try_mkcert(tls_dir: Path) -> Optional[Tuple[str, str]]:
    import shutil, subprocess
    mode = os.getenv("STRANDS_CAD_TLS_MKCERT", "auto").strip().lower()
    if mode in ("0", "false", "no", "off"):
        return None
    mkcert = shutil.which("mkcert")
    if not mkcert:
        return None
    cert_path = tls_dir / "cad-mkcert.pem"
    key_path = tls_dir / "cad-mkcert-key.pem"
    if cert_path.exists() and key_path.exists() and _cert_still_valid(cert_path):
        return str(cert_path), str(key_path)
    names = _hostnames() + _local_ips()
    # NB: we deliberately DO NOT run `mkcert -install` — it needs sudo and would
    # hang a headless server on a password prompt. We only mint a cert if the
    # local CA is already installed (opt in explicitly via STRANDS_CAD_TLS_MKCERT=on
    # after running `mkcert -install` yourself once).
    if mode != "on" and mode not in ("1", "true", "yes"):
        return None
    try:
        cmd = [mkcert, "-cert-file", str(cert_path), "-key-file", str(key_path)] + names
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if r.returncode == 0 and cert_path.exists():
            print("🔒 mkcert: locally-trusted cert minted (no browser warning)")
            return str(cert_path), str(key_path)
    except Exception:
        pass
    return None


def ensure_cert() -> Optional[Tuple[str, str]]:
    """Return (cert_path, key_path) or None if TLS disabled."""
    if os.getenv("STRANDS_CAD_TLS", "false").strip().lower() not in ("1", "true", "yes", "on"):
        return None
    cert_env = os.getenv("STRANDS_CAD_TLS_CERT", "").strip()
    key_env = os.getenv("STRANDS_CAD_TLS_KEY", "").strip()
    if cert_env and key_env and Path(cert_env).exists() and Path(key_env).exists():
        return cert_env, key_env
    tls_dir = Path(os.getenv("STRANDS_CAD_TLS_DIR", "./.strands_cad_tls")).resolve()
    tls_dir.mkdir(parents=True, exist_ok=True)
    mk = _try_mkcert(tls_dir)
    if mk:
        return mk
    cert_path = tls_dir / "cad-cert.pem"
    key_path = tls_dir / "cad-key.pem"
    if not (cert_path.exists() and key_path.exists() and _cert_still_valid(cert_path)):
        print("🔒 generating self-signed TLS cert for WebAuthn (hostnames + LAN IPs)…")
        _generate_self_signed(cert_path, key_path)
        print(f"   SANs: {', '.join(_hostnames() + _local_ips())}")
    return str(cert_path), str(key_path)


def access_urls(port: int, tls: bool) -> List[str]:
    scheme = "https" if tls else "http"
    urls = [f"{scheme}://localhost:{port}"]
    for ip in _local_ips():
        if ip != "127.0.0.1":
            urls.append(f"{scheme}://{ip}:{port}")
    return urls
