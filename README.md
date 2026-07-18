# local-code-bench

Benchmark harness for local, cloud, and agentic coding models on Apple Silicon.

## Why This Exists

`local-code-bench` is a reproducible benchmark harness for comparing coding
models across the execution modes that matter for agentic software work:

- cloud frontier models, such as Claude or the current leading hosted coding
  models
- cloud non-frontier models, such as hosted Qwen, GLM, and similar open-weight
  or lower-cost API models
- local open source models running on Apple Silicon through MLX within a 48 GB
  memory budget
- full agent runs, where the model edits code in a sandbox and is scored by the
  same task tests used for endpoint-only runs

The goal is not just to know whether a model can solve a HumanEval or MBPP task.
The harness records the result and the serving characteristics that decide
whether a model is practical for coding loops: time to first token, total
latency, prompt/prefill throughput, decode throughput, token counts, and cost
where a provider exposes pricing. That makes it possible to compare answer
quality and speed side by side instead of treating local and cloud runs as
separate experiments.

## Local Backend Choice

For local serving, the current focus is two engines: **MLX-LM** and **Ollama**.

MLX-LM (`mlx_lm.server`) is Apple's official MLX toolkit and the baseline native
Apple Silicon server: it exposes an OpenAI-compatible HTTP endpoint while keeping
direct control over MLX loading and quantized model choice. That matters because
this harness needs to compare local models on both output quality and low-level
throughput, including input token/s, output token/s, and prefill behavior.
Ollama is the easiest zero-to-running path — a model registry plus an
OpenAI-compatible API over llama.cpp — and represents what most people actually
run locally.

The local backend pair serves the same Qwen generation through the two runtimes:

- **MLX-LM** serves `mlx-community/Qwen3.6-27B-4bit` on port 8080 from the
  shared Hugging Face cache (`hf-safetensors` format).
- **Ollama** serves `qwen3.6:27b` on port 11434 from its content-addressed blob
  store (`ollama` format).

That makes the local comparison a serving-runtime and on-disk-format shootout
on the same model family, within the 48 GB memory budget of the benchmark
machine.

## Development

Install the project and development tools:

```bash
uv sync
```

Run the test suite:

```bash
uv run pytest
```

Show the current benchmark CLI stub:

```bash
uv run bench --help
```

### Operational Settings

Operational defaults (timeouts, ports, cache/results/state directories, token
caps) live in `configs/settings.yaml` and resolve through one loader with a
fixed precedence: CLI flag > env var > `configs/settings.yaml` > built-in
fallback. The file is additive — delete it (or any key) and behaviour is
unchanged. Measurement-protocol values (benchmark temperature/seed, local-model
concurrency) sit in a read-only `protocol:` section the loader refuses to
override. Full key reference and the audit inventory: `docs/SETTINGS.md`.

## Endpoint Mode

Endpoint models are configured in `configs/models.yaml`. Add a new OpenAI-compatible
backend by adding another `models` entry with a unique `name`, endpoint `base_url`,
`model_id`, pinned revision label, and input/output prices per 1k tokens.

Run one prompt against a configured model:

```bash
uv run bench --model local-example --prompt "Write a Python function that adds two numbers."
```

The command streams `/v1/chat/completions`, measures TTFT, latency, token counts,
prefill tok/s, and decode tok/s, then writes one raw JSONL record under `results/`.
If an endpoint omits usage data, token counts are estimated locally and flagged in
the record.

Run a benchmark suite against selected endpoint models:

```bash
uv run bench --suite humaneval --model local-example --limit 10
uv run bench --suite mbpp --skip openrouter-glm-4.6 --run-file results/mbpp.jsonl --resume
```

For a fast quality check, `--suite canary` runs a fixed 20-task HumanEval anchor
subset instead of the full 164, scored identically, so it is comparable to a full
run but finishes in a fraction of the generations:

```bash
uv run bench --suite canary --model openrouter-glm-4.6
```

For a stronger quality signal, the EvalPlus suites (`humaneval-plus`, `mbpp-plus`)
score each task by differential testing: the candidate is run against the EvalPlus
canonical solution across the union of base and plus inputs, which catches
wrong-but-passing solutions that the vanilla suites accept. The EvalPlus release
files are not auto-downloaded; place the pinned raw releases in the cache dir first:

```bash
mkdir -p .cache/benchmarks
curl -fL --retry 3 \
  https://github.com/evalplus/humanevalplus_release/releases/download/v0.1.10/HumanEvalPlus.jsonl.gz \
  -o .cache/benchmarks/HumanEvalPlus.jsonl.gz
curl -fL --retry 3 \
  https://github.com/evalplus/mbppplus_release/releases/download/v0.2.0/MbppPlus.jsonl.gz \
  -o .cache/benchmarks/MbppPlus.jsonl.gz

uv run bench --suite humaneval-plus --model openrouter-glm-4.6 --timeout 30
```

Use the raw MBPP+ release rather than re-exporting `get_mbpp_plus()`: EvalPlus restores
tuples, sets, and complex numbers in memory, and those values are not JSON-serializable.
The harness performs the same restoration while loading the raw release.

Plus-input sets are large, so raise `--timeout` (per-task sandbox scoring timeout,
default 5s) if tasks start timing out.

### Custom Suites

The built-in suites (`humaneval`, `mbpp`, `canary`, `humaneval-plus`, `mbpp-plus`)
are always known to the harness. You can register **custom suites** — a new suite
that points at a loadable dataset — without changing code, by listing them in an
optional `configs/suites.yaml`:

```yaml
suites:
  - id: my-suite           # selector id, must be unique and not a built-in name
    label: My Suite        # optional human label (defaults to the id)
    source: datasets/my-suite.jsonl  # path relative to this file
    format: jsonl          # optional; inferred from the extension (jsonl | json)
```

`source` may be a `.jsonl`/`.jsonl.gz` file (one record per line) or a `.json`
file holding a list (or a `{"data": [...]}` object). The catalog reports each
suite's availability and task count: a suite whose `source` is missing or
unreadable is listed **disabled with a reason** rather than offered and failing
later. EvalPlus suites are likewise shown disabled until their release file is
cached. There is no `suites.yaml` by default; without it only the built-ins are
offered.

### Throughput: Concurrency And Token Caps

Endpoint suite runs are governed by two per-model knobs in `configs/models.yaml`,
because a full HumanEval or MBPP pass is generation-bound, not scoring-bound.

`concurrency` sets how many requests are in flight for a model during a suite
run. Cloud APIs scale server-side, so running requests in parallel cuts wall
time roughly in proportion to the worker count without distorting the per-request
TTFT, prefill, and decode numbers each stream reports. The cloud entries ship at
`concurrency: 10` (Anthropic at 8). Local MLX servers stay at `concurrency: 1`
on purpose: concurrent requests share one GPU and would corrupt the prefill and
decode tok/s measurements this harness exists to take. Keep local backends serial
and lean on `--mode sweep` plus a small task subset for their speed profile.

`max_tokens` caps generation per task. Coding-suite solutions are short, so an
uncapped verbose model wastes decode time and inflates cost on every task. When a
model config sets no cap, the runner applies a default of 1024 for suite runs, and
the Anthropic provider keeps a 4096 fallback when neither request nor config
specifies a value.

One caveat learned the hard way: reasoning models that think in the output stream
(GLM-4.6, Kimi K2, and similar) emit their analysis before the final code block, so
a tight cap truncates the answer mid-reasoning and scoring sees no code, producing
false failures. Give those models headroom; the shipped cloud configs use 2048 to
8192 for exactly this reason. Terse code models can stay near 1024. Note that some
reasoning models (GLM-4.6 on OpenRouter) engage extended reasoning
nondeterministically even at temperature 0, so a task can occasionally exhaust even
a large budget; disabling reasoning at the provider is the alternative if you want
fast, deterministic coding scores rather than reasoned ones.

Both knobs can be overridden per run from the CLI, which is handy for a quick
local sanity check or a one-off heavier cloud sweep:

```bash
uv run bench --suite humaneval --model openrouter-glm-4.6 --concurrency 16 --max-tokens 768
```

A serial, uncapped cloud run of full HumanEval can take hours; the same run at
`concurrency: 10` with a 1024-token cap finishes in minutes with identical
scoring. See [`docs/EVALUATION-METHODOLOGY.md`](docs/EVALUATION-METHODOLOGY.md)
for the full fast-evaluation strategy across speed, quality, and contamination.

Before timing a local model with a declared `inferencer`, the runner sends one
discarded warmup request so the server's cold-start weight load is not billed to
the first measured task. Cloud API models are never warmed up. Local warmup is on
by default; pass `--no-warmup` to skip it.

### Power And Energy (macOS)

`--power` samples GPU/CPU power with macOS `powermetrics` for the duration of an
endpoint suite or sweep run, then writes a `record_type: "power"` record to the run
JSONL with average and peak GPU watts, average combined watts, and total energy in
joules. This is the performance-per-watt axis: two models can post similar tok/s
while drawing very different power, which matters on a laptop you run for hours.

`powermetrics` requires root, so the sampler uses `sudo -n` and fails gracefully
(it records nothing and prints a note) if passwordless sudo is not configured. To
use it, either grant passwordless sudo for `powermetrics` or run the whole command
under sudo:

```bash
sudo -v && uv run bench --suite canary --model local-mlx-qwen --power
```

When a sweep run file carries power records, the `--mode sweep --input` summary adds
a per-model power table (avg/max GPU watts, avg combined watts, energy in joules)
beneath the prefill table, so you see watts next to tok/s. Pair the energy figure
with the token counts in the task records to derive tokens per joule.

### Auto-Starting A Local Inferencer

A local model may declare the inference engine it needs via an `inferencer:` field in
`configs/models.yaml` (e.g. `local-mlx-qwen` declares `mlx-lm`). By default the
harness assumes that server is already running. Pass `--manage-inferencers` to have a
suite or sweep run bring the declared engine up **exclusively** first — every other
running headless server is stopped (after confirmation) so exactly one engine holds the
GPU and the timing numbers stay valid:

```bash
uv run bench --suite canary --model local-mlx-qwen --manage-inferencers --yes
```

`--yes` auto-confirms stopping the other engines (a non-interactive shell defaults to
*no*). MLX-LM runs require `--manage-inferencers` so the harness can bind the result
to the exact `mlx-lm` and `mlx` packages used by the managed process. Ollama may
already be running because its exact version is read from the live `/api/version`
endpoint. A local run fails before writing results if exact provenance is unavailable.

Use `OPENROUTER_API_KEY` for the OpenRouter entries and `ANTHROPIC_API_KEY` for
the Anthropic baseline. API keys are read from the shell environment or a local
`.env` file and are not written to result records. `.env` is gitignored.

```bash
printf 'OPENROUTER_API_KEY=sk-or-...\n' > .env
printf 'ANTHROPIC_API_KEY=sk-ant-...\n' >> .env
```

Local servers are configured as OpenAI-compatible endpoints;
`scripts/bring-up-local.sh mlx-lm` and `scripts/bring-up-local.sh ollama`
print the expected manual server commands. The script's readiness gate issues a
real completion (not just a `/v1/models` ping), so it blocks through the cold-start
weight load and only reports the backend "warm" once the model is actually resident
and able to serve. Tune the load wait with `WARMUP_TIMEOUT` (default 300s).

## Agent Mode

Coding-agent targets live in `configs/agents.yaml`. Each entry names a registered
harness `type`, the CLI `command`, an explicit `sandbox`, and optional reference
metadata such as `model`, `profile`, and `url`. The harness validates `type`
against the adapter registry, rejects unknown kinds with the supported list, and
only detects installed CLIs read-only from `command`; it never installs an agent.

Codex, Claude Code, and Qwen Code are registered adapters. A bounded run
materializes each task into an isolated workspace, invokes the selected CLI with
the configured sandbox/permission flags, then scores the resulting `solution.py`
with the same tests as endpoint runs:

```bash
uv run bench --mode agent --agent codex --suite humaneval --limit 3
uv run bench --mode agent --agent claude-code --suite humaneval --limit 3
```

The default `claude-code` entry is a cloud frontier baseline using Anthropic's
Claude Code CLI (`claude`) with `--output-format json`; JSONL records include
the final result, `session_id`, and `total_cost_usd`/`usage` when Claude Code
reports them. `claude-code-local-gateway` is intentionally separate: Claude Code
does not target local OpenAI-compatible endpoints directly, so local-model runs
must go through an Anthropic-compatible gateway exposed with
`ANTHROPIC_BASE_URL` and an API key read from `anthropic_api_key_env`. Gateway
runs are flagged in the record and report `cost_status=unavailable` when usage
or cost is absent.

Qwen Code uses the OpenAI-compatible path and can point at the same local
inferencer endpoint as endpoint-mode models. The bundled `qwen-code` agent
targets `http://localhost:8080/v1` (the mlx-lm server); override or add entries in
`configs/agents.yaml` with `base_url`, `model`, and optional `api_key_env` when
the endpoint requires a key:

```bash
export QWEN_LOCAL_API_KEY=local-secret   # only if your local server requires one
uv run bench --mode agent --agent qwen-code --suite humaneval --limit 3
```

For Qwen runs the harness sets `OPENAI_BASE_URL`, `OPENAI_MODEL`, and, when
`api_key_env` is configured and present, `OPENAI_API_KEY` only in the subprocess
environment. Secrets are not written into JSONL results.

### Bare-vs-proxied A/B runs (Epic-13)

`--ab-proxy <name>` runs each agent task twice under identical config — **bare**
(agent → engine) and **proxied** (agent → optimizer proxy → engine) — by swapping
only the agent's configured base URL to the proxy's listen port. The proxy must be
registered in `configs/optimizers.yaml` and already running and healthy (see
`--optimizer-state-dir`, default `.runtime/optimizers`); the upstream engine it
fronts is captured from its 13.2 lifecycle state:

```bash
uv run bench --mode agent --agent qwen-code --suite humaneval --limit 3 \
  --ab-proxy headroom
```

Both conditions land in the same JSONL run file, each record tagged with an
`optimization` block (`condition`, `proxy_in_path`, and — for proxied records —
the proxy's name, port, upstream, and start command), so a proxied run is never
silently compared as if it were bare and the raw file stays re-scorable offline.
The printed report shows, side by side, tokens prefilled (with the measured
reduction %), end-to-end latency, and task success with their deltas — a token
saving is never shown without its correctness delta, and a condition whose
correctness signal is missing is reported `unverified` rather than implying
parity. Agents without a configurable base URL (e.g. a `codex` entry routed via
its own profile) are refused, and `--resume` is not supported with `--ab-proxy`.
The same comparison is also available as `bench optimizer ab --task <suite>
--agent <agent> --proxy <name>` (see the optimizer lifecycle commands below).

## OpenCode Benchmark

The `opencode` subcommand drives the Epic-10 local-model benchmark: it sends two
fixed, version-controlled prompts to a chosen model under identical, deterministic
conditions and captures the raw output, timing, tokens, and full provenance. The
prompts live in `prompts/task-a.md` (open-ended Go coding) and `prompts/task-b.md`
(strict JSON rule-following) and are read from disk — never inlined — so they are
identical across every model. A thin `run-bench.sh` wrapper forwards every flag:

```bash
# Against a model's configured endpoint:
./run-bench.sh --model local-mlx-qwen

# Pick a known engine's default /v1 endpoint instead of editing config:
./run-bench.sh --model local --engine ollama

# Override the endpoint directly, flip on high-reasoning mode, and tag provenance:
./run-bench.sh --model local --endpoint http://127.0.0.1:8080/v1 \
  --mode thinking --quant IQ3_XXS --provider unsloth
```

`--engine` resolves to the locked default `/v1` endpoint for either of the configured
engines (`mlx-lm`, `ollama`); `--endpoint` wins when both are given.
Each run records the quant string, quant provider (the Unsloth-vs-Bartowski lesson),
engine, endpoint, mode, and the pinned seed/temperature (temperature defaults to 0
for determinism). Results are written to `results/opencode-<model>-*.jsonl`.

Equivalent without the wrapper:

```bash
uv run bench opencode --model local-mlx-qwen
```

### Scorecard and provenance note

After each run the harness scores both tasks and appends one comparable row to
`results/scorecard.csv` (with a `results/scorecard.jsonl` provenance record), then
renders `results/scorecard.md`. Task A's generated Go is compiled and
behaviourally tested; Task B's JSON is diffed against ground truth. The Markdown
table lists every model run, sorted **passing rows first, then by Task B error
rate ascending** (the article's ordering), with columns for model, quant,
provider, mode, Task A (build + tests n/total), Task B (error %, coverage %,
collisions), tokens/sec, and wall-clock.

The **provenance note** at the bottom is the Unsloth-vs-Bartowski detector made
first-class: it groups rows by base model and the bit-width parsed from the quant
string, and surfaces any pair that is the *same model at the same bit level from
different quant providers*, reporting the Task B error-rate delta between them
(the article saw 5.0% vs 100% on the identical model and bit-width). To compare
two providers, run the same model twice, tagging each with `--provider` (and
`--quant` if the strings differ); both rows land in the same scorecard and the
note pairs them automatically.

### Sweep, repeat, and engine version

Benchmark several models in one go with `--sweep`, which reads a model-list file
(one configured model name per line; blank lines and `#` comments are ignored) and
folds every result into the same consolidated scorecard:

```bash
# models.txt: one model name per line
./run-bench.sh --sweep models.txt --engine ollama
```

`--repeat N` runs each model N times. Run-to-run swings are real (the article saw
them on a 35B), so the rows are kept individually and a **Variance** section is
added below the scorecard — reporting the mean, min, max, and σ (in percentage
points) of the Task B error rate, the tok/s range, and how many of the N runs
passed Task A — rather than averaging the spread away:

```bash
./run-bench.sh --model local-mlx-qwen --repeat 3
```

Every local scorecard row records the exact **engine version**. Ollama is queried
through its live `/api/version` endpoint; MLX-LM records both the `mlx-lm` and `mlx`
package versions backing the harness-managed process. Capture is strict: a local run
does not start if the version cannot be established.

### Engine provenance schema

Local endpoint, sweep, agent, and OpenCode result records carry the same normalized
object:

```json
{
  "engine": {
    "name": "mlx-lm",
    "versions": {"mlx-lm": "0.31.3", "mlx": "0.32.0"},
    "capture_method": "managed-process"
  }
}
```

Ollama uses `capture_method: live-api`. Historical records patched from verified
installation history use `manual-backfill`; the reusable migration command is
`scripts/backfill-engine-provenance.py` and requires a backup directory. Resume
refuses to append if the current engine fingerprint differs from the existing run.
Leaderboards, sweeps, dashboards, and run history group identical model names by
engine fingerprint so results from different runtime versions are never merged.

Cloud endpoint records use a separate stable provider identity instead of
fabricating a versioned local engine object:

```json
{"endpoint_provider": "openrouter.ai"}
```

Direct Anthropic records use `anthropic`; other OpenAI-compatible endpoints use
their normalized hostname. Comparison surfaces prefer exact local `engine`
provenance, then `endpoint_provider`, and show `unknown (legacy)` only when neither
identity is available.

## Leaderboard And Sweep

Generate a Markdown leaderboard from stored JSONL:

```bash
uv run bench --mode leaderboard --input results/run.jsonl --output LEADERBOARD.md
```

Generate deterministic sweep prompts or summarize stored sweep records:

```bash
uv run bench --mode sweep --prompt "Return 1"
uv run bench --mode sweep --input results/sweep.jsonl
```

The sweep pads the prompt across a context ladder (default `2000,8000,16000,24000`)
and records TTFT and prefill tok/s at each size. Override the ladder with
`--context-sizes` to keep a model below the context length where it starts swapping
on constrained memory:

```bash
uv run bench --mode sweep --model local-ollama-qwen --context-sizes 2000,8000,16000
```

`scripts/run-local-sweeps.sh` automates the mlx-lm-vs-Ollama comparison safely: it
brings up one local server at a time (refusing to proceed if the other is still
resident), sweeps it, then swaps, writing `results/sweep-mlx.jsonl` and
`results/sweep-ollama.jsonl` (override with `MLX_LM_SWEEP_RUN_FILE` /
`OLLAMA_SWEEP_RUN_FILE`; start commands come from `MLX_LM_COMMAND` /
`OLLAMA_COMMAND`). It honors `SWEEP_CONTEXT_SIZES` and `POWER=1`.

## Results Dashboard

Generate a self-contained static HTML dashboard from stored result JSONL. The
output embeds its own CSS and dashboard data — no Node/Vite build step and no CDN
fetches — so you can commit it to the repo and open it directly in a browser:

```bash
# Integrated CLI (lives beside leaderboard, rescore, and sweep):
uv run bench --mode dashboard --input results/run.jsonl --output results/dashboard.html

# Or the generator-only module entry point:
uv run python -m local_code_bench.dashboard --input results/run.jsonl --output docs/dashboard.html
```

Static generation is the default path; `--output` defaults to
`results/dashboard.html`. Add `--serve` to start a localhost HTTP server instead
of writing a file — it re-reads the result files on every request, so refreshing
the browser shows records a benchmark run is still appending without a restart.
`--host`/`--port` (default `127.0.0.1:8770`) tune the bind address; the server
stays localhost-only and read-only:

```bash
uv run bench --mode dashboard --input results/run.jsonl --serve --port 8770
```

Pass `--input` more than once to merge several result files into one view. The
generator embeds only a curated set of aggregate fields (model/agent/suite names,
engine versions, pass rates, latency, TTFT, throughput, cost), and reduces data-quality warning
sources to file basenames, so API keys, `.env` contents, raw secrets, and host
paths never reach the committed artifact.

Alongside the tables the dashboard renders three basic tradeoff charts —
**Cost vs Quality**, **Quality vs Speed**, and **Sweep — Prefill Throughput by
Context Size** — as inline SVG generated in Python from the embedded data, so they
display offline with no CDN or JavaScript. Models or sweep points missing a charted
metric are omitted with a visible data-quality note rather than plotted as
misleading zeros. The committed copy lives at
[`docs/dashboard.html`](docs/dashboard.html) — open it directly in any browser (no
server required) and regenerate it with the command above when results change. The
default empty-state copy renders the dashboard shell until you regenerate it from
your own runs (results JSONL is gitignored).

Every dashboard surface (the static artifact, the live results server, the
unified dashboard, and the inferencer panel) ships designed light and dark
modes. Pages follow the OS `prefers-color-scheme` by default; the round toggle
in the top-right corner switches instantly and remembers the choice in the
browser's `localStorage`, applied before first paint so a reload never flashes
the wrong theme. The charts follow the same monochrome-plus-accent language:
axes, gridlines, labels, and series resolve from the shared theme tokens and
re-color live when the mode toggles, with the accent reserved for the
highlighted series and the remaining series told apart by grey ramp stops plus
per-series dash patterns and marker shapes.

## Inferencer Lifecycle

`bench inferencer` detects, inspects, and controls the local inference engines
declared in `configs/inferencers.yaml` (MLX-LM and Ollama). Exactly one headless
server is allowed to hold the GPU at a time, so timing measurements stay valid:

```bash
uv run bench inferencer list              # installed state, lifecycle, port, and reference URL
uv run bench inferencer status            # installed/running/healthy/pid/url table
uv run bench inferencer status --watch    # live table, refreshes on --interval (ANSI clear)
uv run bench inferencer start mlx-lm      # prompts to stop any other running engines first
uv run bench inferencer start mlx-lm --yes    # auto-confirm stopping others (non-tty defaults to no)
uv run bench inferencer stop mlx-lm       # idempotent stop
uv run bench inferencer models            # downloaded models per engine: format, quant, size, tier
uv run bench inferencer models --shared   # only models several engines can serve (sharing sets)
uv run bench inferencer models --json     # emit the inventory as JSON
uv run bench inferencer models --tier external   # filter to one tier (local|external|external-offline)
uv run bench inferencer promote qwen      # copy an external model to local disk (verified move)
uv run bench inferencer demote qwen       # evict a local model out to the external SSD (verified move)
uv run bench inferencer tier              # auto-tiering plan (dry-run: shows evictions, moves nothing)
uv run bench inferencer tier --apply      # apply the auto-tiering plan via the verified demote path
```

`bench inferencer models` scans each engine's configured `model_store` with a
format-aware strategy and lists what is actually downloaded — name, format, quant,
and on-disk size. `--shared` collapses copies of the same on-disk artifact (one HF
cache entry or one Ollama blob) into a single logical model and names every engine
that can serve it, so engines sharing one download show up once. `--json` emits the same inventory machine-readably. Each row also
carries a `TIER` column (`local`, `external`, or `external-offline`) and `--tier`
filters the listing to one tier. A config or scan failure prints `bench: error: ...`
and exits 2, like the other verbs.

**Move commands (Epic-12).** `bench inferencer promote <model>` copies a model from
the external SSD to local disk, and `demote <model>` evicts a local model out to the
external SSD — both run the verified copy → integrity-check → publish move from the
tiering layer (promote never deletes the external source; demote removes the local
copy only after a verified external copy exists, and reuses a redundant external copy
when one is already present). Each prints the bytes moved and the model's new tier.
`bench inferencer tier` shows the disk-budget auto-tiering plan (a safe dry-run by
default — which least-recently-used models it would evict and the bytes reclaimed,
moving nothing); `tier --apply` applies it through the same verified demote path,
respecting pins and pausing when the SSD is offline. Any move/tier failure (offline
SSD, no space, in-use model, unknown model, or no `external_repo`/`auto_tier`
configured) prints `bench: error: ...` and exits 2.

**External tier (Epic-12).** An optional `external_repo` block in
`configs/inferencers.yaml` registers a second-tier model repository on an attached
USB/Thunderbolt SSD (`root` + a `volume_marker` sentinel file). The harness detects
the drive purely from the filesystem: the tier reads `mounted` only when the root and
its marker are both present, and `offline` otherwise — never an error — so tier-aware
features degrade gracefully whether the SSD is plugged in or not. First-time setup
writes the marker and a per-format directory skeleton (mirroring the local store
layout) so subsequent runs recognise the same repo. Omit the block for a single-tier,
local-only setup.

**Auto-tiering (Epic-12).** An optional `auto_tier` block in `configs/inferencers.yaml`
keeps the local tier under a disk budget by evicting least-recently-used models out to
the external SSD. Set `max_local_gb` (the most GiB the local-tier models may occupy),
`min_free_gb` (the least free GiB to keep on the local volume), or both — when both are
set the stricter requirement wins. The policy ranks models by last use (recorded
benchmark/serve history, falling back to file mtime) and plans evictions oldest-first;
each applied eviction reuses the verified demote path (copy → verify → only-then remove
local, never an unsafe delete). `pins` lists model names that are **never** auto-evicted,
even when that leaves the budget unmet — the shortfall is reported as a warning rather
than evicting a pinned model. Planning is a safe dry-run by default: it reports exactly
which models it would evict and the bytes reclaimed, moving nothing until an eviction is
explicitly applied. When the external SSD is offline auto-tiering is paused and makes no
changes. Omit the block to leave auto-tiering disabled.

Starting an engine enforces the one-active invariant: any other running headless
servers are listed and stopped only after you confirm, then the target starts.
State lives under `.runtime/inferencers/` (gitignored). Override paths with
`--config` and `--state-dir`.

The harness never installs an engine — it only detects what you have, and `list`/`status`
print each engine's reference `url` so an uninstalled engine points you to its own install
page. Installation is always manual and link-guided. For step-by-step, per-engine setup on
the M3 Max (install, start, verify), see
[`docs/INFERENCER-INSTALLATION.md`](docs/INFERENCER-INSTALLATION.md).

**Context-optimization proxies (Epic-13).** `configs/optimizers.yaml` declares the
context-optimization proxies the harness can drive (seeded with
[Headroom](https://headroom-docs.vercel.app/docs) on port 8787). A proxy sits between
the harness and an engine: its `start` template substitutes both `{port}` (the proxy's
own listen port) and `{upstream}` (the active inferencer's base URL), so it is always
wired to a real engine. As with engines, the harness never installs a proxy —
detection is read-only, and a missing proxy is reported as not installed with its
reference `url` as the manual-install link (see the proxies section of
[`docs/INFERENCER-INSTALLATION.md`](docs/INFERENCER-INSTALLATION.md)).

`bench optimizer` drives the proxy layer beside the `bench inferencer` commands
(Story 13.4-001):

```bash
uv run bench optimizer list               # installed state, listen port, and reference URL
uv run bench optimizer status             # installed/running/healthy/upstream table
uv run bench optimizer start headroom     # chain in front of the single active engine
uv run bench optimizer start headroom --inferencer mlx-lm   # chain in front of a named running engine
uv run bench optimizer stop headroom      # idempotent stop (never touches the upstream engine)
uv run bench optimizer ab --task humaneval --agent qwen-code --proxy headroom --limit 3
```

`start` refuses when the target engine is not running (a proxy must front a real
engine), and `stop` only signals the proxy's own process group. `ab` runs the same
bare-vs-proxied comparison as `--mode agent --ab-proxy` — `--task` names the suite,
`--agent` the configured agent, and `--proxy` the registered proxy — and prints the
side-by-side token/latency/correctness report. Proxy state lives under
`.runtime/optimizers/` (override with `--state-dir`); any config or lifecycle
failure prints `bench: error: ...` and exits 2, consistent with the other verbs.
Headroom's own `--learn` flag is an optional manual tuning step you can run
yourself — the harness neither triggers nor depends on it.

## Unified Dashboard

`bench dashboard` serves a single localhost page that brings the inferencer control
panel, a read-only **Optimizers** section, the live results view, a benchmark
**Run** section, a **Chat** section, and a **Settings** section together —
switch between them client-side with no reload and no build step:

```bash
uv run bench dashboard                       # serve on http://127.0.0.1:8765
uv run bench dashboard &                     # background process; lifecycle state is recorded
uv run bench dashboard --status              # report its PID and URL
uv run bench dashboard --stop                # gracefully stop that exact process
uv run bench dashboard --port 8888           # pick a different localhost port
uv run bench dashboard --input results/run.jsonl   # Results section reads these files
uv run bench dashboard --models configs/models.yaml --suites configs/suites.yaml  # Run launcher catalogs
uv run bench dashboard --models configs/models.yaml   # model registry for chat
```

It composes the existing surfaces rather than duplicating them: the **Inferencers**
section drives the same exclusive start/stop as `bench inferencer` (exactly one
headless server ever holds the GPU), and the **Results** section reuses the live
aggregates, so a still-running run shows up on refresh without a restart. The
**Optimizers** section (Epic-13) is a distinct panel — never mixed into the
Inferencers one — showing each registered proxy's installed/running/healthy state
and upstream; it is read-only, with lifecycle driven from `bench optimizer
start/stop` (registry and state paths: `--optimizers`, `--optimizer-state-dir`). The
**Run** section composes a benchmark from a model, an inferencer, and one or more
test suites: the selectors are populated from `--models` (`configs/models.yaml`),
`--config` (`configs/inferencers.yaml`), and the available-suites catalog (built-in
suites plus any custom suites registered in `--suites`, `configs/suites.yaml`).
Unavailable suites (e.g. a missing EvalPlus cache file) are shown disabled with the
reason. The form warns when the chosen inferencer differs from the model's declared
`inferencer` before anything is launched, and submitting a valid composition posts
to the launch endpoint, which exclusively starts the inferencer and runs the suites.
It then monitors launched benchmarks live: a **Live Runs** table polls `/api/runs`
to show each run's status, passed/failed/remaining counts, current task, and the cost
and decode tok/s accumulated so far; when a run reaches a terminal state its status is
shown (a failed or aborted run surfaces a reason rather than stopping silently) and
the Results section refreshes to reflect the new JSONL — no restart. By default the
Results section reads every `*.jsonl` under `--results-dir` (default `results/`),
which is also where launched runs write, so newly launched runs appear automatically;
pass `--input` one or more times to view specific files instead. `--config` and
`--state-dir` point at the inferencer registry and its state dir. The server binds
localhost only and exposes no API keys, `.env` contents, or host paths. Dashboard
process state lives at `.runtime/dashboard.json`; override it with `--state-file`.
Stop validates the saved process identity before sending SIGTERM, so stale state or
PID reuse cannot kill an unrelated process. `--exit-with-parent` (used by the macOS
app, which runs the dashboard as a supervised child) makes the process terminate
itself as soon as its parent dies, so a force-quit of the app cannot leave an
orphaned dashboard. This supersedes `bench inferencer dashboard`, which remains
available.

A `POST /api/chat` endpoint streams a model reply token-by-token over Server-Sent
Events, so you can smoke-test a model without writing a benchmark. Post a JSON body
of `{model, messages, system?, temperature?, max_tokens?}` — multi-turn state lives
in the client and is sent each turn — and read back `data: {"delta": ...}` chunks
ending in a `data: {"done": true, ...}` event with token usage. Chat talks to the
inferencer already serving the model's `base_url`; it never starts a second server,
so the one-active invariant holds. The model registry comes from `--models` (default
`configs/models.yaml`); a missing registry disables chat without taking the dashboard
down. The **Chat** section is a thin browser client over this endpoint: pick a model
and inferencer from the same catalog the launcher uses, set an optional system prompt,
temperature, and max-tokens, then send a message and watch the reply stream into a
multi-turn pane (with a Stop control that cancels the stream cleanly). It is part of
the same self-contained page — inlined CSS/JS, no CDN or build step — and never starts
a server, so bring one up in the Inferencers or Run section first.

The **Inventory** section is a thin browser client over a `GET /api/inventory`
endpoint that surfaces the Epic-11 local model-store scanner: it lists the models
downloaded on this box per inferencer, grouped by on-disk format with their quant,
provider, and size, and a second table flags the *shared* ones — a single stored
artifact (the same HuggingFace cache entry or Ollama blob) several
engines can serve, so you are not storing it more than once. Clicking a downloaded
model jumps to the **Run** section with a compatible inferencer pre-filled, so you can
benchmark a local download visually. The endpoint projects only what identifies a
model (name, format, quant, provider, size, and serving inferencers); on-disk paths
are never sent, and like every section it binds localhost only.

When an `external_repo` (and optionally `auto_tier`) block is configured, the
**Inventory** section also renders a **Storage tiers** view backed by `GET /api/tiers`:
one row per logical model with its tier badge (`local`, `external`, or
`local + external (redundant)`), the external SSD's availability, and an across-tier
**reclaimable** hint summing the redundant copies. Each row offers a one-click
**Promote** (external → local) or **Demote** (local → external) that runs the verified
move server-side via `POST /api/promote` / `POST /api/demote` (copy → verify → publish,
never deleting a source before its destination is verified) and refreshes the model's
tier on completion. An **Auto-tiering** sub-panel shows the dry-run eviction plan from
`GET /api/tier-plan` (the LRU models it would evict to stay under the disk budget, the
bytes reclaimed, and the pinned models it will never touch) with an explicit **Apply**
action (`POST /api/tier-apply`) that runs each eviction through the same verified demote
path. When the SSD is unplugged its models are marked offline and every move/apply action
is disabled with an explanation; the tier endpoints project only model-identity fields —
never an on-disk path — and bind localhost only.

A `GET /api/compare?axis=<id>` endpoint aggregates the raw `results/*.jsonl` into
paired, comparable per-configuration statistics for the benchmark-comparison views:
for each model/engine/quant configuration it reports median and p95 TTFT, prefill
tok/s, decode tok/s, and total latency, pass@1 per suite, cost per task, and the
memory footprint from the local inventory where known — with the contributing run
IDs, suite version, and hardware tag attached to every number. Configurations of
the same nominal model pair up via the Epic-11 `base_model_key` normalization
(axes: `engine`, `quant`, and the controlled `gpt-oss` identical-weights pair),
and runs from different suites, suite versions, or hardware tags are excluded from
a comparison with an explicit reason instead of being silently averaged. An unknown
axis returns 404 with the list of available axes.

The comparison axes the Benchmarks tab renders are declared as data in
`configs/comparisons.yaml` — the seven proposed comparisons (engine, architecture,
size ladder, quantization, context scaling, specialized vs general, local vs cloud)
ship in the catalog, and an eighth is a config edit, not code. Each axis declares an
id, a title, two or more **cohort filters** (matching on model-name globs, explicit
name lists, the model's declared `inferencer`, and/or a quant token such as `q4` or
`4bit`), a **pairing key** (`base_model`, `base_model_engine`, or `suite_context`),
the **highlighted controlled pairs** (e.g. the gpt-oss identical-weights engine A/B),
and deterministic **verdict rules** with their thresholds. A malformed axis is
rejected with a loader error naming the offending field while the valid axes still
load, so one bad edit never blanks the catalog. Verdict thresholds are shipped
defaults; a rule carrying a `settings_key` (e.g. the quality bar at
`benchmark_dashboard.quality_bar`) will resolve through the settings layer once it
lands. An axis whose cohorts have no runs yet is still declared — the loader exposes
which configured models would populate each cohort, so the tab can say "no comparable
runs yet" and list what to run.

The **Benchmarks** section renders the selected axis as a designed report rather than
a table to interpret. The axis picker (`GET /api/compare/axes`) lists the whole
catalog with data-ready axes first and empty ones marked "no data yet"; selecting an
axis fetches `GET /api/compare/report?axis=<id>` and renders a two-sided hero (side
names in the comparison side colors), a subtitle stating the controlled variables,
and methodology chips built from the contributing runs' metadata — engine versions,
suite + version, seed/temperature, hardware tag, and run dates. Each cohort member
gets a stat panel (prefill, decode, TTFT, pass@1, cost/task) with side-colored bars,
and controlled pairs (identical weights, same generation) are badged with the
catalog's stated reason. Two cross-cutting sections follow: the Pareto frontier of
pass@1 vs median decode tok/s over *every* configuration with data (points sized by
memory footprint, the frontier accent-marked) and, where Epic-05 sweep records exist,
the context-scaling curve of prefill throughput by context size. The comparison side
colors are theme tokens (`--cmp-side-1..4`, resolving through the chart palette), so
the report renders in both light and dark modes with no raw color literals; the
catalog is re-read per request, so a `configs/comparisons.yaml` edit shows up on
refresh, and a broken catalog degrades to an inline picker error.

The **Settings** section (`GET /api/settings`) aggregates every harness config surface
into one view, so you can see the whole configuration without opening four
YAML files: **Models** (`--models`, default `configs/models.yaml`), **Inferencers** and
**Storage** — local model stores plus the optional `external_repo` / `auto_tier` tier
blocks — (`--config`, default `configs/inferencers.yaml`), **Suites** (`--suites`,
default `configs/suites.yaml`), and **Agents** (`--agents`, default
`configs/agents.yaml`). Each group is labelled with the file it comes from and is
aggregated server-side; a missing or unparsable
file degrades that one group to an inline error naming the file while the others
render. Model and agent API-key entries show only the environment-variable *name*
with a set/unset indicator — never a value. Protocol-locked values are marked
read-only with a one-line rationale: local endpoint `concurrency` (one request at a
time so shared-GPU contention cannot distort prefill/decode measurements) and the
benchmark temperature/seed (pass@1 is measured at temperature 0 with a fixed seed).

The **Suites** and **Agents** groups are editable in place: each carries an
"Edit `<file>`" form that loads the file's YAML and saves it through the validated
settings store (`GET`/`POST /api/settings/config`). Every edit is validated by the
harness's own loaders before a byte lands — the dashboard can never save a config
the CLI would reject — and valid edits are written atomically with a timestamped
backup of the previous version (default `.runtime/settings-backups/`, retention
configurable via `settings_backup.*` in `configs/settings.yaml`). A form that went
stale because the file changed on disk is refused with a conflict instead of
silently overwriting. Built-in suites are code, not config — `configs/suites.yaml`
registers custom suites only — and the agent harness `type` is marked read-only
(harness adapters are code). Removing or renaming a custom suite id that saved runs
in the history still reference produces a dangling-reference warning, but the change
is allowed.

Below the aggregate sits the **Inferencers & storage editor**
(`GET`/`POST /api/settings/inferencers`), the tab's editable surface for
`inferencers.yaml`: per-engine `model_store` paths and on-disk format, the
`external_repo` block, and the `auto_tier` policy — the storage settings that change
most as the model library grows. Lifecycle, detection, port, and start/stop commands
are install facts, shown for reference only; the server rejects any edit outside the
editable set. Writes ride the validated settings pipeline (conflict-checked against
the loaded content hash, validated by the harness's own loaders — including the
`external_repo`/`auto_tier` blocks — then written atomically with a backup), so the
dashboard can never produce a config the CLI would reject. A store path or external
root that does not exist yet only *warns* — an unplugged SSD is a normal state, not
an error — and a running engine (Epic-08 state) is flagged so you know its edit
applies from the next start. The tier and inventory views pick up a saved edit on
their next refresh without restarting the dashboard, and the pins editor suggests
current inventory model names. The Models group remains read-only — edit
`configs/models.yaml` directly to change it.

## macOS App

`app/macos/` holds a native SwiftUI shell that hosts the unified dashboard in a
full-bleed `WKWebView` — Dock icon, real window (size/position restored across
launches), no browser tab to lose. It launches `bench dashboard` itself, shows a
native loading state until the service answers on `/api/status` (and the tail of
the service log if startup fails — never a blank error page), and keeps the
service running from the menu bar when the window is closed, so in-flight runs
and tier moves survive; reopening the window reattaches to the same session. On
first run it asks where benchmark data lives: a private
`~/Library/Application Support/LocalCodeBench` directory, or an existing
`local-code-bench` checkout so configs and results are shared with the CLI.

The app supervises the service it launches: a crash is restarted with
exponential backoff, repeated crash-looping surfaces the service log instead of
restarting forever, and quitting kills the whole process group. App-launched
services run with `bench dashboard --exit-with-parent`, so even a force-quit of
the app leaves no orphaned harness process behind. A dashboard already running
from the CLI is attached to instead of spawning a second one — the menu bar
labels the mode (`app-managed` vs `CLI-owned`) and quitting the app leaves a
CLI-owned service untouched.

It is a Swift Package (open `app/macos/Package.swift` in Xcode, or build from
the CLI — Command Line Tools are enough, no full Xcode required):

```bash
cd app/macos
swift build                        # compile the app + kit
swift run LocalCodeBench           # run the shell (unbundled, for development)
swift run LocalCodeBenchChecks     # run the kit's test suite
```

`scripts/build-macos-app.sh` assembles a self-contained `dist/LocalCodeBench.app`
that embeds a relocatable CPython (python-build-standalone) with the harness
wheel installed, so the app runs on a Mac with no Python or uv installed.

The testable logic (startup state machine, log tailing, data-location store,
link/download policy, service launch plan) lives in the `LocalCodeBenchKit`
library; `LocalCodeBenchChecks` is an assertion-based runner used instead of
`swift test` because the XCTest/Testing runtime ships only with full Xcode.

## Verification Status

Last automated verification: 2026-06-27.

```bash
uv run pytest        # 781 passed, 97.79% coverage, 80% coverage gate reached
uv run ruff check .  # All checks passed
```

Manual, environment-dependent validation is tracked in
[`docs/MANUAL_TESTS.md`](docs/MANUAL_TESTS.md).

Automated verification covers config parsing (including per-model concurrency and
max_tokens), real HumanEval/MBPP cache loading, stream metric math, OpenAI/Anthropic
stream parsing including the generation cap, macOS `sandbox-exec` scoring guards,
offline re-score, endpoint resume/fault handling, concurrent suite execution that
writes one record per task, fake Codex execution, leaderboard generation, sweep
execution with mocked providers, and pytest-cov coverage reporting with an 80%
minimum gate.

Live validation still requires FX's local/cloud runtime environment: running the
full configured model matrix, killing a local MLX server mid-run, and measuring
actual local model resident memory on the M3 Max 48 GB machine.
