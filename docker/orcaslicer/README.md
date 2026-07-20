# OrcaSlicer container (strands-cad slicer)

Reproducible headless OrcaSlicer 2.5.0-dev (aarch64) so we never rebuild from
source again. Emits **Bambu-flavored gcode** (HEADER/EXECUTABLE/CONFIG blocks)
that the Bambu X2D/H2D/P1 firmware accepts — plain PrusaSlicer output does not.

## The package tarball (NOT in git — 263 MB)
`orcaslicer-package.tar.gz` is the prebuilt extracted OrcaSlicer package
(binary + resources/profiles). Regenerate it from a source build:

```bash
# one-time source build (containerized, ~32 min, aarch64) — DO NOT pass -L (lld)
cd ~/OrcaSlicer && ./build_linux.sh -g -dsic
# package it
cd ~/.local/share/OrcaSlicer && \
  tar czf ~/strands-cad/docker/orcaslicer/orcaslicer-package.tar.gz .
```

Or copy the tarball from another machine that already has it.

## Build the image
```bash
make orca-image        # or: docker build -t strands-cad/orcaslicer:2.5.0 .
```

## Use
`strands_cad/tools/slice.py` auto-detects the image and routes slicing through
it. Controlled by env:
- `STRANDS_CAD_SLICER_DOCKER=0`  → disable docker mode (use host CLI)
- `STRANDS_CAD_SLICER_DOCKER_IMAGE=<tag>`  → override image tag

Manual slice:
```bash
docker run --rm --user $(id -u):$(id -g) -v /path/to/work:/work \
  strands-cad/orcaslicer:2.5.0 \
  --load-settings "/opt/orcaslicer/resources/profiles/BBL/machine/Bambu Lab X2D 0.4 nozzle.json;/opt/orcaslicer/resources/profiles/BBL/process/0.20mm Standard @BBL X2D.json" \
  --load-filaments "/opt/orcaslicer/resources/profiles/BBL/filament/Bambu PLA Basic @BBL X2D 0.4 nozzle.json" \
  --slice 0 --outputdir /work /work/model.stl
```
