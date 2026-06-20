#!/usr/bin/env bash
set -euo pipefail

backend="${1:-dflash}"

case "$backend" in
  dflash)
    echo "Start dflash manually, for example:"
    echo "dflash serve --model qwen3.6-27b --port 8000"
    ;;
  turboquant)
    echo "Start turboquant manually, for example:"
    echo "turboquant-serve --model qwen3.6-35b-a3b --port 8001"
    ;;
  *)
    echo "unknown backend: $backend" >&2
    exit 2
    ;;
esac
