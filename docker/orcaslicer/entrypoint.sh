#!/usr/bin/env bash
# OrcaSlicer CLI (headless). GTK CLI slicing works without an X display.
set -e
export HOME=/tmp
exec /opt/orcaslicer/bin/orca-slicer "$@"
