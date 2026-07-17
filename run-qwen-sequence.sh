#!/usr/bin/env bash
set -euo pipefail

cleanup_local_engines() {
  ollama stop qwen3.6:27b >/dev/null 2>&1 || true
  ollama stop ornith:9b >/dev/null 2>&1 || true
  ollama stop ornith:35b >/dev/null 2>&1 || true
  uv run bench inferencer stop mlx-lm >/dev/null 2>&1 || true
  uv run bench inferencer stop ollama >/dev/null 2>&1 || true
}
trap cleanup_local_engines EXIT

run_local_suites() {
  local model="$1"
  local result_prefix="$2"

  uv run bench \
    --suite humaneval \
    --model "$model" \
    --manage-inferencers \
    --yes \
    --warmup \
    --resume \
    --run-file "results/${result_prefix}-humaneval.jsonl"

  uv run bench \
    --suite humaneval-plus \
    --model "$model" \
    --timeout 30 \
    --manage-inferencers \
    --yes \
    --warmup \
    --resume \
    --run-file "results/${result_prefix}-humaneval-plus.jsonl"

  uv run bench \
    --suite mbpp-plus \
    --model "$model" \
    --timeout 30 \
    --manage-inferencers \
    --yes \
    --warmup \
    --resume \
    --run-file "results/${result_prefix}-mbpp-plus.jsonl"
}

# 1. Ollama: HumanEval+ and MBPP+
uv run bench \
  --suite humaneval-plus \
  --model local-ollama-qwen \
  --timeout 30 \
  --manage-inferencers \
  --yes \
  --warmup \
  --resume \
  --run-file results/ollama-qwen-humaneval-plus.jsonl

uv run bench \
  --suite mbpp-plus \
  --model local-ollama-qwen \
  --timeout 30 \
  --manage-inferencers \
  --yes \
  --warmup \
  --resume \
  --run-file results/ollama-qwen-mbpp-plus.jsonl

# Unload Ollama's model before loading MLX.
ollama stop qwen3.6:27b

# 2. MLX-LM: HumanEval
uv run bench \
  --suite humaneval \
  --model local-mlx-qwen \
  --manage-inferencers \
  --yes \
  --warmup \
  --resume \
  --run-file results/mlx-qwen-humaneval.jsonl

uv run bench inferencer stop mlx-lm

# 3. OpenRouter Qwen: HumanEval, HumanEval+, and MBPP+
uv run bench \
  --suite humaneval \
  --model openrouter-qwen3.6-27b \
  --warmup \
  --resume \
  --run-file results/openrouter-qwen3.6-humaneval.jsonl

uv run bench \
  --suite humaneval-plus \
  --model openrouter-qwen3.6-27b \
  --timeout 30 \
  --warmup \
  --resume \
  --run-file results/openrouter-qwen3.6-humaneval-plus.jsonl

uv run bench \
  --suite mbpp-plus \
  --model openrouter-qwen3.6-27b \
  --timeout 30 \
  --warmup \
  --resume \
  --run-file results/openrouter-qwen3.6-mbpp-plus.jsonl

# 4. MLX-LM Ornith 9B and 35B
run_local_suites local-mlx-ornith-9b mlx-ornith-9b
run_local_suites local-mlx-ornith-35b mlx-ornith-35b

uv run bench inferencer stop mlx-lm

# 5. Ollama Ornith 9B and 35B
run_local_suites local-ollama-ornith-9b ollama-ornith-9b
ollama stop ornith:9b
run_local_suites local-ollama-ornith-35b ollama-ornith-35b
ollama stop ornith:35b
uv run bench inferencer stop ollama

trap - EXIT
echo "All Qwen and Ornith benchmark runs completed."
