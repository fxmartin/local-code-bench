#!/usr/bin/env bash
set -euo pipefail

cleanup_local_engines() {
  uv run bench inferencer stop mlx-lm >/dev/null 2>&1 || true
  ollama stop qwen3.6:27b >/dev/null 2>&1 || true
}
trap cleanup_local_engines EXIT

# 1. Ollama: HumanEval+ and MBPP+
uv run bench \
  --suite humaneval-plus \
  --model local-ollama-qwen \
  --timeout 30 \
  --warmup \
  --resume \
  --run-file results/ollama-qwen-humaneval-plus.jsonl

uv run bench \
  --suite mbpp-plus \
  --model local-ollama-qwen \
  --timeout 30 \
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

trap - EXIT
echo "All Qwen benchmark runs completed."
