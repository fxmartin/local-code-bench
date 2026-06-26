# Inferencer Installation Guide (MacBook Pro M3 Max, 48 GB)

This guide explains how to **manually install** each inference engine the harness can
detect and manage. Per the Epic-08 decision, `local-code-bench` **never installs,
downloads, or auto-provisions** an engine — it only *detects* what you have already
installed (read-only `shutil.which` / `importlib.util.find_spec` / `.app` presence) and
points you here when one is missing. After following a section, the engine should show up
in:

```bash
uv run bench inferencer status      # installed / running / healthy per engine
uv run bench inferencer list        # installed?, lifecycle, port
```

Every section is aligned **exactly** to `configs/inferencers.yaml` — same detection
target, port, start command, and health URL — so the verify step here is the same check
the harness performs.

> **Hardware**: tuned for an Apple Silicon **M3 Max, 48 GB** unified memory. The 48 GB
> budget must hold the target model + any draft model + the KV cache at once, which is why
> the reference roster is DFlash serving `Qwen3.6-27B-4bit` (dense + speculative) and
> TurboQuant serving the `Qwen3.6-35B-A3B` sparse MoE (~3 B active/token).

---

## 0. Shared prerequisites (do these once)

```bash
# Xcode Command Line Tools (compilers, Metal toolchain)
xcode-select --install

# Homebrew (https://brew.sh)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# uv (this repo's Python manager) — https://docs.astral.sh/uv/
curl -LsSf https://astral.sh/uv/install.sh | sh

# Native arm64 Python 3.12 (NOT Rosetta/x86_64) for the MLX / vLLM engines
brew install python@3.12

# Hugging Face CLI for pulling models (gated/large repos need a login)
uv pip install huggingface_hub
huggingface-cli login
```

### Making the harness *see* your install (important)

Detection runs in the **same environment the harness runs from** (its `uv` venv):

- **Binary** engines (`dflash`, `turboquant-serve`, `llama-server`, `ollama`, `exo`) must
  be on `PATH`. For Python-packaged CLIs, prefer `uv tool install <pkg>` or `pipx install
  <pkg>` so the command is globally on `PATH` (not trapped in a one-off venv).
- **Module** engines (`mlx_lm`, `mlc_llm`, `vllm`) must be importable by the harness's
  Python. Install them into the project environment: `uv pip install <pkg>` from the repo
  root, so `find_spec("mlx_lm")` succeeds when the harness checks.
- **App** engines (LM Studio, GPT4All) just need the `.app` present under `/Applications`
  or `~/Applications`; they are **detect-only** (managed from their own UI).

---

## Quick reference

| Harness name | Detect | Port | Install (short) | Lifecycle | Source |
|---|---|---|---|---|---|
| `dflash` | binary `dflash` | 8000 | `uv tool install dflash-mlx` | server | [bstnxbt/dflash-mlx](https://github.com/bstnxbt/dflash-mlx) |
| `turboquant` | binary `turboquant-serve` | 8002 | `uv tool install turboquant-mlx` | server | [helgklaizar/turboquant-mlx](https://github.com/helgklaizar/turboquant-mlx) |
| `mlx-lm` | module `mlx_lm` | 8080 | `uv pip install mlx-lm` | server | [ml-explore/mlx-lm](https://github.com/ml-explore/mlx-lm) |
| `llama-cpp` | binary `llama-server` | 8081 | `brew install llama.cpp` | server | [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp) |
| `ollama` | binary `ollama` | 11434 | `brew install --cask ollama` | server | [ollama.com](https://ollama.com) |
| `mlc-llm` | module `mlc_llm` | 8082 | `pip install --pre -f https://mlc.ai/wheels mlc-llm mlc-ai` | server | [llm.mlc.ai](https://llm.mlc.ai) |
| `vllm-mlx` | binary `vllm-mlx` | 8001 | `uv tool install vllm-mlx` | server | [waybarrios/vllm-mlx](https://github.com/waybarrios/vllm-mlx) |
| `exo` | binary `exo` | 52415 | clone + `uv run exo` (see §8) | server | [exo-explore/exo](https://github.com/exo-explore/exo) |
| `mtplx` | binary `mtplx` | 8003 | `uv tool install mtplx` (see §9) | server | [youssofal/mtplx](https://github.com/youssofal/mtplx) |
| `lm-studio` | app `LM Studio.app` | 1234 | download app, enable server | app (detect-only) | [lmstudio.ai](https://lmstudio.ai) |
| `gpt4all` | app `GPT4All.app` | 4891 | download app, enable API | app (detect-only) | [nomic.ai/gpt4all](https://www.nomic.ai/gpt4all) |

---

## 1. DFlash (`dflash`) — port 8000

**What it is**: lossless DFlash speculative decoding for MLX. `dflash serve` wraps
`mlx_lm.server` and auto-discovers the matching draft model, exposing an OpenAI-compatible
endpoint. This repo's reference dense backend (serves `mlx-community/Qwen3.6-27B-4bit` with
the `z-lab/Qwen3.6-27B-DFlash` draft).

```bash
# Install so the `dflash` CLI is on PATH
uv tool install dflash-mlx          # or: pipx install dflash-mlx

# Start (matches configs/inferencers.yaml: ["dflash", "serve"], port 8000)
dflash serve
```

**Verify**:
```bash
curl -s http://127.0.0.1:8000/v1/models
uv run bench inferencer start dflash      # harness-managed start (stops other engines first)
```
**M3 Max / 48 GB**: the 27B-4bit target + draft + KV fits comfortably in 48 GB. **Uninstall**:
`uv tool uninstall dflash-mlx`. Source: <https://github.com/bstnxbt/dflash-mlx> · <https://pypi.org/project/dflash-mlx/>

---

## 2. TurboQuant (`turboquant-serve`) — port 8002

**What it is**: near-optimal KV-cache compression for MLX (up to ~5× memory reduction),
with a serving entry point used here to run the `Qwen3.6-35B-A3B` sparse MoE
(`manjunathshiva/Qwen3.6-35B-A3B-tq3-g32`, ~35 B total / ~3 B active per token).

```bash
uv tool install turboquant-mlx       # provides the turboquant-serve console script

# Start (matches configs/inferencers.yaml: ["turboquant-serve"], port 8002)
turboquant-serve
```

> **Note**: `turboquant-mlx` is primarily a KV-cache-compression library that plugs into
> `mlx_lm`. Confirm your install exposes a `turboquant-serve` command (`which
> turboquant-serve`); if your chosen TurboQuant distribution doesn't ship that server
> wrapper, install the serving variant the roster uses and ensure the binary lands on
> `PATH`. The harness only needs `turboquant-serve` resolvable.

**Verify**: `curl -s http://127.0.0.1:8002/v1/models` then `uv run bench inferencer start
turboquant`. **M3 Max / 48 GB**: the MoE must fit 48 GB — this is the tightest of the
roster; tq3 quant + KV compression is what makes it fit. Source:
<https://github.com/helgklaizar/turboquant-mlx> · <https://pypi.org/project/turboquant-mlx/>

---

## 3. MLX-LM (`mlx_lm`) — port 8080

**What it is**: Apple's official MLX language-model toolkit, including an OpenAI-compatible
`mlx_lm.server`. The baseline native Apple Silicon server.

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

## 4. llama.cpp (`llama-server`) — port 8081

**What it is**: the C++/Metal inference engine; GGUF is universal. `llama-server` is its
OpenAI-compatible HTTP server. Metal is enabled by default on macOS.

```bash
brew install llama.cpp               # ships llama-server with Metal

# Start (matches configs/inferencers.yaml: ["llama-server", "--port", "8081"])
llama-server -m ~/models/your-model.gguf --port 8081 -ngl 99
```
`-ngl 99` offloads all layers to the GPU. **Verify**: `curl -s
http://127.0.0.1:8081/v1/models` then `uv run bench inferencer start llama-cpp`.
**Uninstall**: `brew uninstall llama.cpp`. Source: <https://github.com/ggml-org/llama.cpp>

---

## 5. Ollama (`ollama`) — port 11434

**What it is**: model registry + OpenAI-compatible API over llama.cpp (MLX path maturing).
Easiest zero-to-running.

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

## 6. MLC-LLM (`mlc_llm`) — port 8082

**What it is**: TVM-compiled kernels with a paged KV cache; strong for long contexts. Ships
an OpenAI-compatible `mlc_llm serve`.

```bash
# Metal (Apple Silicon) prebuilt wheels — install into the harness environment
python3.12 -m pip install --pre -U -f https://mlc.ai/wheels mlc-llm mlc-ai

# Start (matches configs/inferencers.yaml: ["mlc_llm", "serve", "--port", "8082"])
mlc_llm serve HF://mlc-ai/Qwen3.6-27B-q4f16_1-MLC --port 8082
```
**Verify**: `curl -s http://127.0.0.1:8082/v1/models` then `uv run bench inferencer start
mlc-llm`. **M3 Max**: MLC's paged KV scales most predictably as context grows — useful for
long-context comparisons. Source: <https://llm.mlc.ai/docs/install/mlc_llm>

---

## 7. vLLM on Apple Silicon (`vllm-mlx`) — port 8001

**What it is**: continuous batching / server-grade serving brought to Apple Silicon
(OpenAI- and Anthropic-compatible, native MLX backend).

**Default — `vllm-mlx` (matches `configs/inferencers.yaml`):** the config detects the
`vllm-mlx` console script and starts `vllm-mlx serve --port 8001`.
```bash
uv tool install vllm-mlx             # or: pipx install vllm-mlx (puts `vllm-mlx` on PATH)
vllm-mlx serve <model> --port 8001
```

> **Alternative — vllm-metal plugin.** A separate project installs the standard `vllm`
> package + a Metal backend and serves with `vllm serve`. If you prefer it, install via
> `curl -fsSL https://raw.githubusercontent.com/vllm-project/vllm-metal/main/install.sh |
> bash` and update this inferencer's `detect` to `module: vllm` and `start` to
> `["vllm", "serve", "--port", "8001"]`.

**Verify**: `curl -s http://127.0.0.1:8001/v1/models` then `uv run bench inferencer start
vllm-mlx`. Sources: <https://github.com/waybarrios/vllm-mlx> ·
<https://github.com/vllm-project/vllm-metal>

---

## 8. Exo (`exo`) — port 52415

**What it is**: peer-to-peer model sharding across devices (honorable mention for a
multi-machine setup). OpenAI-compatible API on :52415.

```bash
# Prereqs
brew install uv node
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh && rustup toolchain install nightly

# Install
git clone https://github.com/exo-explore/exo
cd exo/dashboard && npm install && npm run build && cd ..
uv run exo                           # serves the API/dashboard at http://localhost:52415
```

> **Detection note**: the harness detects a bare `exo` binary on `PATH` and starts it with
> `["exo"]`, but upstream runs it as `uv run exo` from the clone. Expose an `exo` on
> `PATH` (a small wrapper script that runs `uv run --directory ~/exo exo`, or a packaged
> install) so `shutil.which("exo")` finds it.

**Verify**: `curl -s http://127.0.0.1:52415/v1/models`. Source:
<https://github.com/exo-explore/exo>

---

## 9. MTPLX (`mtplx`) — port 8003

**What it is**: native **Multi-Token-Prediction** MLX runtime for Apple Silicon. A model
drafts several tokens ahead with its own built-in MTP heads, verifies them in one batched
forward pass, and keeps only what passes exact rejection sampling — **no external drafter**
(unlike DFlash's separate draft model). It exposes an OpenAI- and Anthropic-compatible
server, so it drops into the endpoint protocol exactly like dflash/turboquant. This is the
roster's third acceleration family (alongside DFlash spec-decoding and TurboQuant MoE).

```bash
# Install so the `mtplx` CLI is on PATH
uv tool install mtplx               # or: pipx install mtplx

# Start (matches configs/inferencers.yaml: ["mtplx", "serve", "--port", "8003"])
# Port remapped to 8003: MTPLX defaults to 8000, which dflash already owns.
mtplx serve --port 8003
```

> **Own pre-built models required.** MTPLX runs only **MTP-specific model builds** — the
> [`Youssofal` Hugging Face catalog](https://huggingface.co/Youssofal) (Qwen 3.5/3.6,
> Gemma 4). Verify a downloaded model with `mtplx inspect <path-or-repo>` before serving.
> Because the artifact differs from the other engines' models, the `local-mtplx-qwen` row
> in `configs/models.yaml` points at an MTPLX-specific repo and **a strict same-artifact
> A/B against other engines is not claimed** — each run's metadata records the model build.

> **Optional auto-tuning is a manual pre-step.** MTPLX can per-machine auto-tune its
> acceptance/draft settings. Run that **once yourself, outside the harness** — the harness
> neither triggers tuning nor depends on it. The benchmark measures whatever MTPLX serves.

**Verify**:
```bash
curl -s http://127.0.0.1:8003/v1/models
uv run bench inferencer start mtplx       # harness-managed start (stops other engines first)
```
**M3 Max / 48 GB**: pick an MTPLX build sized to leave KV headroom. Source:
<https://github.com/youssofal/mtplx>

---

## 10. LM Studio (`LM Studio.app`) — port 1234 — detect-only

**What it is**: polished GUI with llama.cpp **and** MLX backends. The harness only detects
it and reports status/health — **start and stop it from its own UI** (the harness refuses
to manage GUI apps headlessly).

1. Download **LM Studio** from <https://lmstudio.ai> and move it to `/Applications`.
2. In the app, open the **Developer / Local Server** tab and **Start Server** (default
   port **1234**, OpenAI-compatible).
3. Load a model in the app.

**Verify**: `curl -s http://127.0.0.1:1234/v1/models` then `uv run bench inferencer status`
(shows `lm-studio` installed, and healthy when its server is up). Source:
<https://lmstudio.ai>

---

## 11. GPT4All (`GPT4All.app`) — port 4891 — detect-only

**What it is**: consumer chat app (llama.cpp backend). Detect-only, like LM Studio.

1. Download **GPT4All** from <https://www.nomic.ai/gpt4all> and move it to `/Applications`.
2. In **Settings → Application**, enable the **API Server** (default port **4891**).
3. Load a model.

**Verify**: `curl -s http://127.0.0.1:4891/v1/models` then `uv run bench inferencer status`.
Source: <https://www.nomic.ai/gpt4all>

---

## After installing: one active engine at a time

The benchmark's speed metrics (TTFT, prefill/decode tok/s) are only valid when **exactly
one** inference server holds the GPU. The harness enforces this — starting one engine
prompts to stop the others:

```bash
uv run bench inferencer start dflash        # prompts to stop any other running engine
uv run bench inferencer start dflash --yes  # auto-confirm stopping others
uv run bench inferencer stop dflash         # idempotent stop
uv run bench inferencer status              # see installed / running / healthy
```

If an engine isn't installed, `bench inferencer status` reports it as not installed and
points back to the reference URL in this guide.
