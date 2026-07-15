# =============================================================================
# strands-cad — printer cockpit app image (dashboard + thinker + telegram).
# One image, entrypoint dispatches by first arg:
#   dashboard | thinker | telegram | mcp | bash
# Slicing is delegated to the `orcaslicer` sidecar image (docker/orcaslicer),
# invoked over a shared /work volume — so this image stays lean (no GTK/X).
# =============================================================================
FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1

# Build deps for cadquery/trimesh/rtree/numba wheels + curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libgl1 libglu1-mesa libspatialindex-dev \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY strands_cad ./strands_cad
COPY tests ./tests

# Install the package (core deps). sdf/neural extras are opt-in and skipped here.
RUN pip install --upgrade pip && pip install -e ".[dashboard]"

COPY docker/app-entrypoint.sh /usr/local/bin/strands-cad-entry
RUN chmod +x /usr/local/bin/strands-cad-entry

EXPOSE 8099
ENTRYPOINT ["/usr/local/bin/strands-cad-entry"]
CMD ["dashboard"]
