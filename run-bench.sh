#!/usr/bin/env bash
# OpenCode local-model benchmark entrypoint (Epic-10).
#
# Thin wrapper over `bench opencode`: every flag is forwarded verbatim, e.g.
#   ./run-bench.sh --model local-dflash-qwen
#   ./run-bench.sh --model local --mode thinking --engine ollama
#   ./run-bench.sh --model local --endpoint http://127.0.0.1:1234/v1
set -euo pipefail
exec uv run bench opencode "$@"
