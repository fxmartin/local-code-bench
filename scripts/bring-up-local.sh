#!/usr/bin/env bash
set -euo pipefail

backend="${1:-dflash}"

port=""
command_var=""
example=""
case "$backend" in
  dflash)
    port="${DFLASH_PORT:-8000}"
    command_var="DFLASH_COMMAND"
    example="dflash serve --model qwen3.6-27b --port ${port}"
    ;;
  turboquant)
    port="${TURBOQUANT_PORT:-8001}"
    command_var="TURBOQUANT_COMMAND"
    example="turboquant-serve --model qwen3.6-35b-a3b --port ${port}"
    ;;
  *)
    echo "unknown backend: $backend" >&2
    exit 2
    ;;
esac

is_ready() {
  python3 - "$port" <<'PY'
import socket
import sys

port = int(sys.argv[1])
with socket.socket() as sock:
    sock.settimeout(0.5)
    raise SystemExit(0 if sock.connect_ex(("127.0.0.1", port)) == 0 else 1)
PY
}

if is_ready; then
  echo "$backend ready on port $port"
  exit 0
fi

command="${!command_var:-}"
if [[ -z "$command" ]]; then
  echo "$backend is not listening on port $port." >&2
  echo "Set $command_var to start it idempotently, for example:" >&2
  echo "  export $command_var='$example'" >&2
  exit 1
fi

log_file="${TMPDIR:-/tmp}/local-code-bench-${backend}.log"
echo "starting $backend: $command"
nohup bash -lc "$command" >"$log_file" 2>&1 &

for _ in {1..60}; do
  if is_ready; then
    echo "$backend ready on port $port"
    exit 0
  fi
  sleep 1
done

echo "$backend did not become ready on port $port; see $log_file" >&2
exit 1
