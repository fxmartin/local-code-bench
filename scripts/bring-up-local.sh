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
    port="${TURBOQUANT_PORT:-8002}"
    command_var="TURBOQUANT_COMMAND"
    example="turboquant-serve --model qwen3.6-35b-a3b --port ${port}"
    ;;
  *)
    echo "unknown backend: $backend" >&2
    exit 2
    ;;
esac

is_ready() {
  curl -fsS --max-time 2 "http://127.0.0.1:${port}/v1/models" >/dev/null
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
if command -v launchctl >/dev/null 2>&1; then
  label="local-code-bench.${backend}"
  launch_path="$HOME/.local/bin:/run/current-system/sw/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
  launchctl remove "$label" >/dev/null 2>&1 || true
  launchctl submit -l "$label" -o "$log_file" -e "$log_file" -- /bin/zsh -c "export HOME='$HOME'; export PATH='$launch_path'; exec $command"
else
  nohup bash -lc "trap '' HUP; exec $command" >"$log_file" 2>&1 < /dev/null &
fi

for _ in {1..60}; do
  if is_ready; then
    sleep 3
    is_ready || break
    echo "$backend ready on port $port"
    exit 0
  fi
  sleep 1
done

echo "$backend did not become ready on port $port; see $log_file" >&2
exit 1
