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

For local MLX serving, the current focus is **TurboQuant** and **DFlash** rather
than Ollama or LM Studio.

TurboQuant and DFlash are closer to the workload this repo is trying to measure:
they expose OpenAI-compatible HTTP endpoints while keeping control over MLX
loading, quantized model choices, draft/verify behavior, prompt processing, and
serving parameters. That matters because this harness needs to compare local
models on both output quality and low-level throughput, including input token/s,
output token/s, and prefill behavior.

The local backend pair is intentionally not the same model served two ways. It
tracks the two Apple Silicon strategies described in the reference articles:

- **DFlash** serves `mlx-community/Qwen3.6-27B-4bit`, a dense 27B target with a
  purpose-built `z-lab/Qwen3.6-27B-DFlash` draft model. DFlash is lossless
  speculative decoding: the draft proposes tokens and the target verifies them,
  so decode throughput improves without changing the target model's output.
- **TurboQuant** serves `manjunathshiva/Qwen3.6-35B-A3B-tq3-g32`, a sparse MoE
  with about 35B total parameters but far fewer active parameters per token.
  The reference benchmark argues that this matters most for long agent prompts,
  where prefill dominates the wait time and speculative decoding alone cannot
  help as much.

That makes the local comparison "dense 27B + speculative decoding" versus
"sparse MoE + quantized serving," not a pure serving-runtime shootout. If a
future experiment needs to isolate only the server implementation, add a separate
same-model pair rather than replacing these article-aligned baselines.

Ollama and LM Studio are useful general-purpose local model tools, but they are
less suitable as the primary benchmark path here. They add product-level
abstractions around model management and serving, can hide implementation
details that affect timing, and are not optimized for the specific MLX
experiments this repo is running on a 48 GB Apple Silicon machine. They remain
reasonable compatibility targets later, but TurboQuant and DFlash give the
harness a cleaner path for reproducible local-vs-cloud measurements right now.

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
file is not auto-downloaded; place it in the cache dir first:

```bash
pip install evalplus
python -c "from evalplus.data import get_human_eval_plus, write_jsonl; \
write_jsonl('.cache/benchmarks/HumanEvalPlus.jsonl', list(get_human_eval_plus().values()))"

uv run bench --suite humaneval-plus --model openrouter-glm-4.6 --timeout 30
```

Plus-input sets are large, so raise `--timeout` (per-task sandbox scoring timeout,
default 5s) if tasks start timing out.

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
uncapped verbose model wastes decode time and inflates cost on every task. The
cloud entries cap at `max_tokens: 1024`. When a model config sets no cap, the
runner applies a default of 1024 for suite runs. The Anthropic provider keeps its
4096 fallback when neither the request nor config specifies a value.

Both knobs can be overridden per run from the CLI, which is handy for a quick
local sanity check or a one-off heavier cloud sweep:

```bash
uv run bench --suite humaneval --model openrouter-glm-4.6 --concurrency 16 --max-tokens 768
```

A serial, uncapped cloud run of full HumanEval can take hours; the same run at
`concurrency: 10` with a 1024-token cap finishes in minutes with identical
scoring. See [`docs/EVALUATION-METHODOLOGY.md`](docs/EVALUATION-METHODOLOGY.md)
for the full fast-evaluation strategy across speed, quality, and contamination.

Use `OPENROUTER_API_KEY` for the OpenRouter entries and `ANTHROPIC_API_KEY` for
the Anthropic baseline. API keys are read from the shell environment or a local
`.env` file and are not written to result records. `.env` is gitignored.

```bash
printf 'OPENROUTER_API_KEY=sk-or-...\n' > .env
printf 'ANTHROPIC_API_KEY=sk-ant-...\n' >> .env
```

Local MLX servers are configured as OpenAI-compatible endpoints;
`scripts/bring-up-local.sh dflash` and `scripts/bring-up-local.sh turboquant`
print the expected manual server commands.

## Agent Mode

Codex agent targets live in `configs/agents.yaml`. A bounded run materializes each
task into an isolated workspace, invokes `codex exec` with an explicit sandbox,
then scores the resulting `solution.py` with the same tests as endpoint runs:

```bash
uv run bench --mode agent --agent codex --suite humaneval --limit 3
```

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

## Verification Status

Last automated verification: 2026-06-21.

```bash
uv run pytest        # 86 passed, 87.02% coverage, 80% coverage gate reached
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
