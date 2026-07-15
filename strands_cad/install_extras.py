"""Install optional git-only deps for strands-cad SDF + neural tools.

Why a helper?  PyPI forbids direct-git dependencies, so `sdf` (fogleman) and
`shap-e` (openai) can't live in pyproject. Worse, shap-e's setup.py pins an
ancient `numba`/`llvmlite` (0.53/0.36) that FAILS to build on Python ≥3.10 —
so we must install it with `--no-deps` and satisfy its real runtime deps
ourselves (they're already covered by the [neural] extra + a couple extras).

Usage:
    python -m strands_cad.install_extras            # install both (sdf+neural)
    python -m strands_cad.install_extras sdf        # SDF only
    python -m strands_cad.install_extras neural     # neural (shap-e) only
"""
import importlib.util
import shutil
import subprocess
import sys


def _pip(*args) -> int:
    # uv-created venvs ship without pip — fall back to `uv pip` there.
    if importlib.util.find_spec("pip") is not None:
        return subprocess.call([sys.executable, "-m", "pip", "install", *args])
    uv = shutil.which("uv")
    if uv:
        return subprocess.call([uv, "pip", "install", "-p", sys.executable, *args])
    print("❌ neither pip nor uv found for this interpreter", file=sys.stderr)
    return 1


def install_sdf() -> int:
    print("→ installing sdf (fogleman) …")
    return _pip("git+https://github.com/fogleman/sdf.git")


def install_neural() -> int:
    # shap-e --no-deps to dodge its ancient numba pin; then its real runtime deps.
    print("→ installing shap-e (openai) with --no-deps (avoids ancient numba pin) …")
    rc = _pip("--no-deps", "git+https://github.com/openai/shap-e.git")
    if rc != 0:
        return rc
    print("→ installing shap-e runtime deps …")
    rc = _pip("torch>=2.0", "torchvision>=0.15", "Pillow>=10.0",
              "tqdm>=4.60", "pyyaml", "ipywidgets>=8.0")
    if rc != 0:
        return rc
    print("→ installing CLIP (openai) …")
    return _pip("git+https://github.com/openai/CLIP.git")


def main():
    which = sys.argv[1:] or ["sdf", "neural"]
    handlers = {"sdf": install_sdf, "neural": install_neural}
    for name in which:
        if name not in handlers:
            print(f"❌ unknown extra '{name}'. Options: {list(handlers)}")
            sys.exit(1)
        rc = handlers[name]()
        if rc != 0:
            print(f"❌ failed to install {name}")
            sys.exit(rc)
    print("✅ all requested extras installed")


if __name__ == "__main__":
    main()
