"""Install optional git-only deps for strands-cad SDF + neural tools.

Usage:
    python -m strands_cad.install_extras           # install both
    python -m strands_cad.install_extras sdf       # SDF only
    python -m strands_cad.install_extras neural    # neural only
"""
import subprocess
import sys

EXTRAS = {
    "sdf":    "git+https://github.com/fogleman/sdf.git",
    "neural": "git+https://github.com/openai/shap-e.git",
}


def main():
    which = sys.argv[1:] or list(EXTRAS.keys())
    for name in which:
        if name not in EXTRAS:
            print(f"❌ unknown extra '{name}'. Options: {list(EXTRAS.keys())}")
            sys.exit(1)
        url = EXTRAS[name]
        print(f"→ installing {name} from {url}")
        rc = subprocess.call([sys.executable, "-m", "pip", "install", url])
        if rc != 0:
            print(f"❌ failed to install {name}")
            sys.exit(rc)
    print("✅ all requested extras installed")


if __name__ == "__main__":
    main()
