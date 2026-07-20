#!/usr/bin/env bash
# Run OrcaSlicer CLI headless under Xvfb. All args are forwarded to the slicer.
set -e
export HOME=/tmp
export ORCA_BIN=/opt/orcaslicer/bin/orca-slicer
# xvfb-run gives GTK a virtual display so --slice works without a real X server
exec xvfb-run -a --server-args="-screen 0 1280x1024x24" "$ORCA_BIN" "$@"
