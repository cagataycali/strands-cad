"""Install a slicer CLI for strands-cad.

Bambu Studio ships NO ARM64/Linux CLI, so on aarch64 we use:
  • PrusaSlicer  — in Ubuntu apt (`prusa-slicer`), simplest, arm64 ✓
  • OrcaSlicer   — a Bambu Studio fork (same --slice CLI); AppImage releases,
                   but arm64 Linux AppImages are intermittent → prefer Prusa here.

On x86-64 you can still install Bambu Studio yourself; set $STRANDS_CAD_SLICER
to its CLI path and strands-cad will prefer it.

Usage:
    python -m strands_cad.install_slicer            # apt prusa-slicer (needs sudo)
    python -m strands_cad.install_slicer --orca     # try OrcaSlicer AppImage
    sudo python -m strands_cad.install_slicer       # non-interactive apt
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def install_prusa() -> int:
    if _have("prusa-slicer"):
        print("✓ prusa-slicer already installed:", shutil.which("prusa-slicer"))
        return 0
    print("→ installing prusa-slicer via apt (requires sudo) …")
    sudo = [] if os.geteuid() == 0 else ["sudo"]
    rc = subprocess.call(sudo + ["apt-get", "update"])
    rc |= subprocess.call(sudo + ["apt-get", "install", "-y", "prusa-slicer"])
    if rc == 0 and _have("prusa-slicer"):
        print("✅ prusa-slicer installed:", shutil.which("prusa-slicer"))
    else:
        print("❌ apt install failed. Run manually: sudo apt-get install -y prusa-slicer")
    return rc


def install_orca() -> int:
    """Best-effort OrcaSlicer AppImage fetch (Bambu-compatible CLI)."""
    arch = platform.machine()
    dest_dir = Path.home() / "Applications"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "OrcaSlicer.AppImage"
    if dest.exists():
        print("✓ OrcaSlicer AppImage already present:", dest)
        os.chmod(dest, 0o755)
        return 0
    # Orca publishes x86_64 Linux AppImages reliably; arm64 is rare.
    if arch not in ("x86_64", "amd64"):
        print(f"⚠ OrcaSlicer has no reliable {arch} Linux build. Use PrusaSlicer instead:")
        print("    python -m strands_cad.install_slicer")
        return 1
    url = ("https://github.com/SoftFever/OrcaSlicer/releases/latest/download/"
           "OrcaSlicer_Linux_AppImage_Ubuntu2404_V2.3.0.AppImage")
    print(f"→ downloading OrcaSlicer AppImage → {dest} …")
    rc = subprocess.call(["curl", "-fL", "-o", str(dest), url])
    if rc == 0:
        os.chmod(dest, 0o755)
        print("✅ OrcaSlicer AppImage saved. It'll be auto-detected.")
    return rc


def main():
    args = sys.argv[1:]
    if "--orca" in args:
        sys.exit(install_orca())
    rc = install_prusa()
    if rc != 0 and platform.machine() in ("x86_64", "amd64"):
        print("… falling back to OrcaSlicer AppImage")
        rc = install_orca()
    sys.exit(rc)


if __name__ == "__main__":
    main()
