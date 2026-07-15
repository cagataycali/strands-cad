# Troubleshooting

## Install

??? failure "`pip install` fails building numba / llvmlite"
    You're likely on an old resolver that pulled `numba 0.53 / llvmlite 0.36`.
    strands-cad pins `numba>=0.59` / `llvmlite>=0.42` — upgrade pip
    (`pip install -U pip`) and retry. First try should just work on py3.10–3.13.

??? failure "shap-e install fails on numba"
    Don't `pip install shap-e` directly (its `setup.py` pins ancient numba). Use
    the resolver-safe helper:
    ```bash
    python -m strands_cad.install_extras neural
    ```

??? question "A tool group is missing"
    Optional groups auto-disable when their dep isn't installed. Check what
    loaded:
    ```bash
    python -c "from strands_cad.tools import __all__; print(sorted(__all__))"
    ```
    Then install the extra: `[neural]`, `[sim]`, `[dashboard]`, or the SDF git
    extra.

## Slicing

??? failure "Print starts then instantly fails (empty gcode_file, state FAILED)"
    **You sliced with PrusaSlicer.** Its output is generic Marlin G-code with no
    Bambu markers, which the firmware silently rejects. Use **OrcaSlicer** (the
    Dockerized image ships with strands-cad) or Bambu Studio. See
    [Plate → Slice](../pipeline/slice.md).

??? question "Where does the slicer come from?"
    `slice_bambu` prefers the Dockerized OrcaSlicer, then a host OrcaSlicer,
    then PrusaSlicer (fallback, not Bambu-compatible). Pin with
    `STRANDS_CAD_SLICER` / `STRANDS_CAD_SLICER_DOCKER_IMAGE`.

## Printer upload (FTPS)

??? failure "553 \"Could not create file\" on bambu_upload"
    Two causes:

    1. **No removable storage.** vsFTPd chroots to the SD/USB mount — with
       nothing inserted, every write 553s. **Insert an SD card or USB stick.**
       `bambu_upload` now pre-flight-checks this.
    2. **TLS session reuse.** Handled by strands-cad's `ImplicitFTPS` (reuses the
       control connection's TLS session on the data channel).

??? failure "Uploads >1KB fail with 451/EOF after a printer reboot"
    The Bambu FTPS daemon can wedge after heavy connection churn — small writes
    succeed, big ones fail. **Reboot the printer, wait, and don't hammer
    connections.** TLS handshake is slow (~9s); the client timeout is 45s.

## Camera

??? failure "Camera won't stream / ffmpeg hangs"
    Plain `ffmpeg -i rtsps://…` **hangs** on Bambu's LIVE555 server — that's
    expected. strands-cad does the RTSP/TLS handshake in pure Python instead. If
    it still won't connect, verify the printer reports `liveview_preview:true`
    over MQTT and that port **322** is reachable. See
    [Live Camera](../dashboard/camera.md).

??? failure "Camera drops when a second viewer connects"
    The P1 has a **single** live-view slot. strands-cad shares one frame across
    viewers and always issues TEARDOWN — if you patched the camera code, make
    sure aborted sessions are torn down.

## WebAuthn / dashboard

??? failure "Passkey enrollment button does nothing / \"not a secure context\""
    WebAuthn needs HTTPS or `localhost`. Start with `--tls` so passkeys work
    from your phone at `https://192.168.1.x:8099`. Install `mkcert` for a
    zero-warning trusted cert.

??? question "How do I reset enrolled passkeys?"
    Delete `.strands_cad_auth.json` in the working directory and restart — the
    dashboard returns to the *unsealed* first-visit state.

## Still stuck?

Open an issue: [github.com/cagataycali/strands-cad/issues](https://github.com/cagataycali/strands-cad/issues)
