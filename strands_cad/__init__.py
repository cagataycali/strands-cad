"""strands-cad — atomic 3D tools for Strands agents.

Four independent paths to a printable asset (parametric SCAD, B-rep CadQuery,
implicit SDF, neural text/image→3D) plus mesh ops, slicing, Bambu printer
control, and a WebAuthn-gated live dashboard.

`ALL_TOOLS` is assembled dynamically from whichever tool groups successfully
imported — so a lean `pip install strands-cad` (core only) still gives you a
usable tool list; installing extras ([neural], [sim], [dashboard]) grows it.
"""
__version__ = "0.4.0"

from strands_cad import tools as _tools

# Re-export every tool that actually loaded into the tools namespace.
_g = globals()
for _name in _tools.__all__:
    _g[_name] = getattr(_tools, _name)

ALL_TOOLS = [getattr(_tools, _n) for _n in _tools.__all__]

__all__ = list(_tools.__all__) + ["ALL_TOOLS", "__version__"]
