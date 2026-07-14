#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

mlx_run_file="${MLX_LM_SWEEP_RUN_FILE:-results/sweep-mlx.jsonl}"
ollama_run_file="${OLLAMA_SWEEP_RUN_FILE:-results/sweep-ollama.jsonl}"
stop_timeout_seconds="${STOP_TIMEOUT_SECONDS:-20}"
keep_servers="${KEEP_LOCAL_SWEEP_SERVERS:-0}"

usage() {
  cat <<'EOF'
Usage: scripts/run-local-sweeps.sh

Runs local sweep benchmarks sequentially so mlx-lm and Ollama never hold
model weights in memory at the same time:

  1. stop both local benchmark servers
  2. start and warm mlx-lm
  3. run: uv run bench --mode sweep --model local-mlx-qwen ...
  4. stop mlx-lm
  5. start and warm Ollama
  6. run: uv run bench --mode sweep --model local-ollama-qwen ...
  7. stop Ollama
  8. summarize both sweep JSONL files

Required when the backend is not already launchable:
  MLX_LM_COMMAND='...'
  OLLAMA_COMMAND='...'

Optional:
  MLX_LM_SWEEP_RUN_FILE=results/sweep-mlx.jsonl
  OLLAMA_SWEEP_RUN_FILE=results/sweep-ollama.jsonl
  WARMUP_TIMEOUT=300
  STOP_TIMEOUT_SECONDS=20
  KEEP_LOCAL_SWEEP_SERVERS=1
  POWER=1                              # needs passwordless sudo for powermetrics
  SWEEP_CONTEXT_SIZES=2000,8000,16000  # cap the ladder to stay out of swap (default 2000,8000,16000,24000)
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

port_for_backend() {
  case "$1" in
    mlx-lm) echo "${MLX_LM_PORT:-8080}" ;;
    ollama) echo "${OLLAMA_PORT:-11434}" ;;
    *)
      echo "unknown backend: $1" >&2
      exit 2
      ;;
  esac
}

model_for_backend() {
  case "$1" in
    mlx-lm) echo "local-mlx-qwen" ;;
    ollama) echo "local-ollama-qwen" ;;
    *)
      echo "unknown backend: $1" >&2
      exit 2
      ;;
  esac
}

run_file_for_backend() {
  case "$1" in
    mlx-lm) echo "$mlx_run_file" ;;
    ollama) echo "$ollama_run_file" ;;
    *)
      echo "unknown backend: $1" >&2
      exit 2
      ;;
  esac
}

server_up() {
  local port="$1"
  curl -fsS --max-time 2 "http://127.0.0.1:${port}/v1/models" >/dev/null 2>&1
}

listener_pids() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
  fi
}

stop_backend() {
  local backend="$1"
  local port
  port="$(port_for_backend "$backend")"
  local label="local-code-bench.${backend}"

  if command -v launchctl >/dev/null 2>&1; then
    launchctl bootout "gui/$(id -u)/${label}" >/dev/null 2>&1 || true
    launchctl remove "$label" >/dev/null 2>&1 || true
  fi

  local pids
  pids="$(listener_pids "$port")"
  if [[ -n "$pids" ]]; then
    # shellcheck disable=SC2086
    kill $pids >/dev/null 2>&1 || true
  fi

  local waited=0
  while server_up "$port"; do
    if (( waited >= stop_timeout_seconds )); then
      pids="$(listener_pids "$port")"
      if [[ -n "$pids" ]]; then
        echo "force stopping $backend on port $port" >&2
        # shellcheck disable=SC2086
        kill -KILL $pids >/dev/null 2>&1 || true
      fi
      break
    fi
    sleep 1
    waited=$((waited + 1))
  done
}

stop_all() {
  stop_backend mlx-lm
  stop_backend ollama
}

cleanup() {
  if [[ "$keep_servers" != "1" ]]; then
    stop_all
  fi
}

start_and_warm() {
  local backend="$1"
  local other="$2"
  local other_port
  other_port="$(port_for_backend "$other")"

  stop_backend "$other"
  stop_backend "$backend"

  echo "starting and warming $backend"
  "scripts/bring-up-local.sh" "$backend"

  if server_up "$other_port"; then
    echo "$other is still listening on port $other_port; refusing mixed-memory sweep" >&2
    exit 1
  fi
}

run_sweep_for_backend() {
  local backend="$1"
  local model
  local run_file
  model="$(model_for_backend "$backend")"
  run_file="$(run_file_for_backend "$backend")"
  mkdir -p "$(dirname "$run_file")"
  # Sweep mode appends, so start from a clean file: a stale run file would stack
  # old (possibly swap-contaminated) records into this run's summary.
  rm -f "$run_file"

  local extra_args=()
  if [[ "${POWER:-0}" == "1" ]]; then
    extra_args+=(--power)
  fi
  if [[ -n "${SWEEP_CONTEXT_SIZES:-}" ]]; then
    extra_args+=(--context-sizes "$SWEEP_CONTEXT_SIZES")
  fi

  echo "running sweep for $backend -> $run_file"
  # ${arr[@]+"${arr[@]}"} expands safely when the array is empty under `set -u`
  # (macOS system bash 3.2 errors on a plain "${arr[@]}" for an empty array).
  uv run bench --mode sweep --model "$model" --run-file "$run_file" ${extra_args[@]+"${extra_args[@]}"}
}

trap cleanup EXIT INT TERM

stop_all

start_and_warm mlx-lm ollama
run_sweep_for_backend mlx-lm
stop_backend mlx-lm

start_and_warm ollama mlx-lm
run_sweep_for_backend ollama
stop_backend ollama

echo "sweep summary"
uv run bench --mode sweep --input "$mlx_run_file" "$ollama_run_file"
