# Inferencer Installation Guide (MacBook Pro M3 Max, 48 GB)

This guide explains how to **manually install** each inference engine the harness can
detect and manage. Per the Epic-08 decision, `local-code-bench` **never installs,
downloads, or auto-provisions** an engine — it only *detects* what you have already
installed (read-only `shutil.which` / `importlib.util.find_spec`) and points you here
when one is missing. After following a section, the engine should show up in:

```bash
uv run bench inferencer status      # installed / running / healthy per engine
uv run bench inferencer list        # installed?, lifecycle, port
```

Every section is aligned **exactly** to `configs/inferencers.yaml` — same detection
target, port, start command, and health URL — so the verify step here is the same check
the harness performs.

> **Hardware**: tuned for an Apple Silicon **M3 Max, 48 GB** unified memory. The 48 GB
> budget must hold the target model + the KV cache at once, which is why the reference
> roster serves `Qwen3.6-27B-4bit` on MLX-LM and the matching `qwen3.6:27b` tag on
> Ollama.

---

## 0. Shared prerequisites (do these once)

```bash
# Xcode Command Line Tools (compilers, Metal toolchain)
xcode-select --install

# Homebrew (https://brew.sh)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# uv (this repo's Python manager) — https://docs.astral.sh/uv/
curl -LsSf https://astral.sh/uv/install.sh | sh

# Native arm64 Python 3.12 (NOT Rosetta/x86_64) for the MLX engine
brew install python@3.12

# Hugging Face CLI for pulling models (gated/large repos need a login)
uv pip install huggingface_hub
huggingface-cli login
```

### Making the harness *see* your install (important)

Detection runs in the **same environment the harness runs from** (its `uv` venv):

- **Binary** engines (`ollama`) must be on `PATH`.
- **Module** engines (`mlx_lm`) must be importable by the harness's Python. Install
  them into the project environment: `uv pip install <pkg>` from the repo root, so
  `find_spec("mlx_lm")` succeeds when the harness checks.

---

## Quick reference

| Harness name | Detect | Port | Install (short) | Lifecycle | Source |
|---|---|---|---|---|---|
| `mlx-lm` | module `mlx_lm` | 8080 | `uv pip install mlx-lm` | server | [ml-explore/mlx-lm](https://github.com/ml-explore/mlx-lm) |
| `ollama` | binary `ollama` | 11434 | `brew install --cask ollama` | server | [ollama.com](https://ollama.com) |

---

## 1. MLX-LM (`mlx_lm`) — port 8080

**What it is**: Apple's official MLX language-model toolkit, including an OpenAI-compatible
`mlx_lm.server`. The baseline native Apple Silicon server; models come from the shared
Hugging Face hub cache (`~/.cache/huggingface/hub`, `hf-safetensors` format).

```bash
# Install into the harness environment so `find_spec("mlx_lm")` succeeds
uv pip install mlx-lm

# Start (matches configs/inferencers.yaml: ["mlx_lm.server", "--port", "8080"])
mlx_lm.server --model mlx-community/Qwen3.6-27B-4bit --port 8080
```
**Verify**: `curl -s http://127.0.0.1:8080/v1/models` then `uv run bench inferencer start
mlx-lm`. **M3 Max**: pick `mlx-community/*` quants sized to leave headroom for KV.
**Uninstall**: `uv pip uninstall mlx-lm`. Source: <https://github.com/ml-explore/mlx-lm>

---

## 2. Ollama (`ollama`) — port 11434

**What it is**: model registry + OpenAI-compatible API over llama.cpp (MLX path maturing).
Easiest zero-to-running. Models live in a content-addressed blob store under
`~/.ollama/models` (`ollama` format).

```bash
brew install --cask ollama           # or download the app from https://ollama.com

ollama serve                         # foreground server (matches the config start argv)
ollama pull qwen3.6:27b              # pull a model (separate shell)
```
The Ollama app may already run the server on :11434; the harness uses `ollama serve` to
start and `ollama stop` to stop. **Verify**: `curl -s http://127.0.0.1:11434/api/tags`
then `uv run bench inferencer start ollama`. **Uninstall**: `brew uninstall --cask
ollama`. Source: <https://ollama.com>

---

## After installing: one active engine at a time

The benchmark's speed metrics (TTFT, prefill/decode tok/s) are only valid when **exactly
one** inference server holds the GPU. The harness enforces this — starting one engine
prompts to stop the other:

```bash
uv run bench inferencer start mlx-lm        # prompts to stop any other running engine
uv run bench inferencer start mlx-lm --yes  # auto-confirm stopping others
uv run bench inferencer stop mlx-lm         # idempotent stop
uv run bench inferencer status              # see installed / running / healthy
```

If an engine isn't installed, `bench inferencer status` reports it as not installed and
points back to the reference URL in this guide.
