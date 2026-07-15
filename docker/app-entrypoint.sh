#!/usr/bin/env bash
# strands-cad container entrypoint — dispatch by first arg.
set -e
CMD="${1:-dashboard}"; shift || true
case "$CMD" in
  dashboard)
    # split-loops so the dashboard process doesn't also run thinker/telegram
    export STRANDS_CAD_SPLIT_LOOPS="${STRANDS_CAD_SPLIT_LOOPS:-1}"
    exec strands-cad-dashboard --no-auth "$@"
    ;;
  thinker)   exec strands-cad-thinker "$@" ;;
  telegram)  exec strands-cad-telegram "$@" ;;
  mcp)       exec strands-cad-mcp "$@" ;;
  bash|sh)   exec bash "$@" ;;
  *)         exec "$CMD" "$@" ;;
esac
