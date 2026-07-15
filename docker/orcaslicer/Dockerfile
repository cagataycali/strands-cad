# OrcaSlicer 2.5.0-dev (aarch64) — headless CLI slicer for strands-cad.
# Built once from source (see AGENTS notes); this image packages the prebuilt
# extracted package + runtime GTK3/WebKit libs so we never rebuild from source.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
# Runtime libraries OrcaSlicer's binary links against (GTK3 + WebKit2 + GL + gstreamer)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgtk-3-0 libwebkit2gtk-4.1-0 libglu1-mesa libgl1-mesa-dri \
    libglew2.2 libosmesa6 libegl1 libgbm1 \
    libgstreamer1.0-0 libgstreamer-plugins-base1.0-0 gstreamer1.0-gtk3 \
    libsecret-1-0 libwebkitgtk-6.0-4 \
    libsoup-3.0-0 libnotify4 libsdl2-2.0-0 \
    ca-certificates locales \
    && rm -rf /var/lib/apt/lists/* \
    && locale-gen en_US.UTF-8

ENV LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8

# Unpack the prebuilt OrcaSlicer package (binary + resources/profiles)
COPY orcaslicer-package.tar.gz /tmp/orca.tar.gz
RUN mkdir -p /opt/orcaslicer && tar xzf /tmp/orca.tar.gz -C /opt/orcaslicer && rm /tmp/orca.tar.gz

# Headless entrypoint: run the CLI under a virtual X server (GTK init needs a display)
COPY entrypoint.sh /usr/local/bin/orca
RUN chmod +x /usr/local/bin/orca

WORKDIR /work
ENTRYPOINT ["/usr/local/bin/orca"]
