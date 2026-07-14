#!/usr/bin/env bash
set -euo pipefail

backend="${1:-mlx-lm}"

port=""
command_var=""
example=""
case "$backend" in
  mlx-lm)
    port="${MLX_LM_PORT:-8080}"
    command_var="MLX_LM_COMMAND"
    example="mlx_lm.server --model mlx-community/Qwen3.6-27B-4bit --port ${port}"
    ;;
  ollama)
    port="${OLLAMA_PORT:-11434}"
    command_var="OLLAMA_COMMAND"
    example="ollama serve"
    ;;
  *)
    echo "unknown backend: $backend" >&2
    exit 2
    ;;
esac

# Process is listening and answering the models endpoint.
server_up() {
  curl -fsS --max-time 2 "http://127.0.0.1:${port}/v1/models" >/dev/null 2>&1
}

# Truly ready: a real completion returns. The first call after boot forces the
# weights to load, so this blocks through the cold start (up to WARMUP_TIMEOUT)
# rather than letting the first benchmark task absorb it. That is the whole point:
# when this returns, the model is resident and warm.
warm() {
  server_up || return 1
  local model=""
  if command -v jq >/dev/null 2>&1; then
    model="$(curl -fsS --max-time 5 "http://127.0.0.1:${port}/v1/models" | jq -r '.data[0].id // empty' 2>/dev/null || true)"
  fi
  : "${model:=warmup}"
  curl -fsS --max-time "${WARMUP_TIMEOUT:-300}" \
    -H 'Content-Type: application/json' \
    -d "{\"model\":\"${model}\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"max_tokens\":1}" \
    "http://127.0.0.1:${port}/v1/chat/completions" >/dev/null 2>&1
}

if warm; then
  echo "$backend warm on port $port"
  exit 0
fi

command="${!command_var:-}"
if [[ -z "$command" ]]; then
  echo "$backend is not listening on port $port." >&2
  echo "Set $command_var to start it idempotently, for example:" >&2
  echo "  export $command_var='$example'" >&2
  exit 1
fi
command="$(printf '%s' "$command" | tr '\n\t' ' ')"

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

# First wait for the process to start listening, then force a warmup completion
# that blocks through weight loading so "warm" means genuinely ready to serve.
for _ in {1..60}; do
  server_up && break
  sleep 1
done

if ! server_up; then
  echo "$backend did not start listening on port $port; see $log_file" >&2
  exit 1
fi

if warm; then
  echo "$backend warm on port $port"
  exit 0
fi

echo "$backend is listening but failed a warmup completion on port $port; see $log_file" >&2
exit 1
