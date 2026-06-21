# Evaluation Methodology: Fast, Relevant, Comparable

This document records how `local-code-bench` keeps evaluation runs fast without
giving up the comparability that justifies the project. It exists because the
first real benchmark attempt took roughly four hours to complete a single
HumanEval pass against one cloud model, which is not a workable iteration loop.

The root cause was not benchmark size. It was throughput. The two fixes that
matter are now in the harness, and the broader strategy below explains where to
spend effort next.

## The Core Insight

A full HumanEval (164 tasks) or MBPP (about 500 tasks) pass is **generation
bound, not scoring bound**. The expensive step is the model producing a
completion. Running the unit tests afterward is cheap. So the levers that cut
wall time all act on generation, not on the suite.

There are two distinct questions the harness answers, and they have different
fast paths:

1. How fast is a model to serve for agentic coding? This is a speed question.
2. How good are its answers? This is a quality question.

Treating these separately is what makes a fast loop possible.

## Speed: Sweep Mode, Local, Serial, Small

For the speed verdict, do not run a correctness suite at all. The reference
articles this project is modeled on argue that local agentic coding is prefill
bound, so the deciding metric for a local setup is prefill tok/s at agent-length
context, not pass@1.

`--mode sweep` already operationalizes this. It pads a prompt across a context
ladder (2k, 8k, 16k, 24k tokens) and records TTFT, latency, prefill tok/s, and
decode tok/s for each size. That is four streams per model instead of hundreds,
so it finishes in minutes.

```bash
uv run bench --mode sweep --model local-dflash-qwen --run-file results/sweep.jsonl
uv run bench --mode sweep --input results/sweep.jsonl   # summary table
```

Local models stay serial here for a reason explained below.

### Cold Start

An MLX server loads model weights on its first request, not at boot, so a fresh
server bills the entire load (two minutes was observed for the MoE) to whatever
runs first. Two guards keep that out of the measurements. The runner sends a
discarded warmup request per model before timing (default on, `--no-warmup` to
skip), and `scripts/bring-up-local.sh` gates readiness on a real completion that
blocks through the load, so "warm" means the weights are resident. Without these,
the first measured task silently absorbs the cold start and skews its TTFT.

## Quality: Cloud-Concurrent Suites, Capped Generation

For the quality comparison, run the correctness suite, but make the cloud path
fast. Two knobs in `configs/models.yaml` do this, and both are now wired into the
endpoint runner.

### Concurrency

`concurrency` sets how many requests are in flight for a model during a suite
run. Cloud providers scale server-side, so parallel requests cut wall time
roughly in proportion to the worker count while each individual stream still
reports honest per-request timing. A serial, four-hour HumanEval pass drops to
single-digit minutes at `concurrency: 10` with identical scoring.

The runner parallelizes per model with a thread pool. Only the slow work, the
network call and scoring, runs on worker threads. Every result write, counter
update, and progress emit stays on the main thread, so result records and the
run summary need no extra locking and one record is written per task. For
`concurrency: 1` the path stays serial and byte-identical to the original
behavior, which keeps the fault-tolerance and resume guarantees intact.

### Why Local Stays Serial

This is the important nuance. Firing concurrent requests at a single local MLX
server makes them compete for one GPU. That contention distorts the prefill and
decode tok/s numbers, which is the entire measurement this harness exists to
take. So local backends are pinned to `concurrency: 1`. Concurrency is a
per-backend knob, not a global one: parallel for cloud, serial for local. The
default model config encodes exactly this split.

### Generation Cap

`max_tokens` caps generation per task. Coding-suite solutions are short, so an
uncapped verbose model spends decode time and money emitting prose, reasoning,
and markdown that scoring then discards. Cloud entries cap at `max_tokens: 1024`.
When no cap is configured, suite runs apply a default of 1024. Both the cap and
the concurrency can be overridden per run:

```bash
uv run bench --suite humaneval --model openrouter-glm-4.6 --concurrency 16 --max-tokens 768
```

## Choosing The Right Benchmark For Quality

Public leaderboard numbers from the internet are a loose external anchor, not a
primary signal, for two reasons. First, they are not comparable: different
harness, prompt template, sampling, and test set. Second, and more important for
this project, they do not cover the served, quantized configurations under test.
The published score is for the full-precision base model, while this harness
serves a 4-bit DFlash target and a quantized MoE. Quantization and serving change
output quality, and that gap is part of what is being measured. Use net numbers
to sanity-check the ballpark, never to rank.

Building a brand-new benchmark from scratch is also the wrong default. A
contamination-free, well-calibrated suite is a research project on its own and
would carry no external validity. There is one narrow exception, noted below.

The pragmatic path is to reuse a stronger public dataset through this harness, so
quality and speed are measured together and comparably. Recommended progression,
roughly in order of leverage:

- **EvalPlus (HumanEval+ / MBPP+).** Same task IDs and the same generation cost
  as the vanilla suites, but far more unit tests per task. It catches
  wrong-but-passing solutions that plain HumanEval rubber-stamps. Vanilla
  HumanEval and MBPP are saturated and partly leaked, so they barely discriminate
  at the top end. This is the single highest-leverage quality upgrade. It is now
  implemented (see below).
- **A small LiveCodeBench slice for contamination resistance.** Problems are time
  stamped and published after model cutoffs, so a model that wins by memorization
  on HumanEval is exposed. Take a recent window and run a subset.
- **A tiny private tripwire set.** The one home-grown piece worth building: eight
  to ten tasks written in-house and never published. Not for ranking, but as a
  contamination check. If a model aces public suites and face-plants on the
  private set, that is signal the leaderboards cannot give.

Skip LLM-as-judge for code. Execution-based scoring is more reliable and the
sandbox already provides it. Reserve judging for qualities that tests cannot
capture, such as explanation, which is out of scope here.

## EvalPlus: Differential Testing In The Existing Sandbox

The EvalPlus suites are wired as `--suite humaneval-plus` and `--suite mbpp-plus`.
Rather than bolt on the upstream EvalPlus evaluator, the harness keeps scoring
inside its own sandbox by generating a self-contained `test_code` per task. That
generated test rebuilds the EvalPlus canonical solution in an isolated namespace
(so it cannot shadow the candidate), then asserts the candidate matches the
reference on every base and plus input, with float tolerance from each task's
`atol` and deep-copied arguments so in-place mutation cannot leak between the two
calls. A candidate that is right on the base inputs but wrong on a plus input
fails, which is the entire point of the plus sets.

The release file is not auto-downloaded, because the URL and version move and the
file is large. Place `HumanEvalPlus.jsonl(.gz)` or `MbppPlus.jsonl(.gz)` in the
cache dir (the names in `EVALPLUS_FILENAMES`), for example by exporting from the
`evalplus` package:

```bash
pip install evalplus
python -c "from evalplus.data import get_human_eval_plus, write_jsonl; \
write_jsonl('.cache/benchmarks/HumanEvalPlus.jsonl', list(get_human_eval_plus().values()))"
```

Plus-input sets are large, so the per-task sandbox timeout is tunable with
`--timeout` (default 5s). Raise it if tasks start timing out.

## Reasoning Models: Disable Reasoning Or Budget For It

Live canary runs surfaced a calibration trap. Reasoning models such as GLM-4.6 on
OpenRouter stream their chain-of-thought in a separate `reasoning` field and only
emit the answer in `content` after reasoning finishes. The scorer reads `content`,
which is correct, but if a token cap is exhausted during reasoning the answer never
arrives and the task fails with empty content. Worse, these models engage extended
reasoning nondeterministically even at temperature 0, so the same task can pass on
one run and fail on the next as it flips between answering directly and reasoning
past the budget. Chasing this with ever larger `max_tokens` is a losing game.

The cleaner fix for a coding benchmark is to disable reasoning at the provider, so
the model returns the answer directly: fast, cheap, and deterministic. Each model
config takes an `extra_body` mapping that is merged verbatim into the request body,
which keeps provider-specific knobs out of the code. The shipped GLM-4.6 and Kimi K2
entries use it to turn reasoning off:

```yaml
extra_body:
  reasoning:
    enabled: false
```

The exact field is provider and model specific (OpenRouter also exposes
`reasoning: {exclude: true}` and `reasoning_effort`), so confirm it takes effect on
a live run. If you would rather measure reasoned scores, drop `extra_body` and give
the model a generous `max_tokens` (8192 or more) instead, accepting the slower,
costlier, occasionally flaky runs that come with it.

What was validated offline: the differential engine itself, against synthetic
records run through the real sandbox, confirming it passes a correct solution,
fails one that is wrong only on a plus input, and honors float tolerance. What
still needs live verification on FX's machine: that the fields in the downloaded
EvalPlus release match the loader's expected names (`task_id`, `entry_point`,
`prompt`, `canonical_solution`, `base_input`, `plus_input`, `atol`) for the
installed version, and that full-scale runs complete within the chosen timeout.

## Keeping Quality Runs Small: Anchor Subsets

Even on the fast cloud path, a full suite per model is more than is needed for a
ranking. Replace a random `--limit N` (which takes the first N tasks and is both
biased and noisy) with an anchor subset: a fixed set of roughly 20 to 50 items,
chosen so their aggregate pass rate predicts the full suite score within a few
points and with known error bars. This is the tinyBenchmarks / IRT anchor-point
technique. The goal is a "still usable?" quality signal in tens of generations,
not hundreds.

The harness ships this today as the `canary` suite:

```bash
uv run bench --suite canary --model openrouter-glm-4.6
```

`canary` is a fixed, deterministic 20-task spread of HumanEval, defined by
`CANARY_HUMANEVAL_IDS` in `src/local_code_bench/tasks.py`. It reuses the real
HumanEval prompts and unit tests, so a canary record is scored exactly like a full
HumanEval record and the two are directly comparable. The current ID list is a
hand-curated stand-in for a formal IRT-selected set. When the item-response
machinery lands, regenerate the list from per-task discrimination data but keep
the IDs stable so historical canary runs stay comparable. The set is HumanEval
only for now; extending it to a cross-suite anchor is a matter of adding curated
MBPP IDs alongside.

## Tiering: Spend Generation Where It Is Cheap

Putting it together, the harness runs each evaluation where its cost is lowest:

- **Local models:** sweep for speed (serial, tiny) plus a small anchor subset for
  a correctness sanity check. Never the full suite, because local generation is
  slow and cannot be parallelized on one box.
- **Cloud models:** the full or anchored suite, fast, because requests run
  concurrently and generation is capped. This is where the quality ranking lives.
- **Codex agent:** the heavy agentic suites (for example Aider polyglot or
  SWE-bench style multi-file edits) belong only here, run once, since they are
  slow by nature and measure a different capability.

## Status

Implemented in the harness today: per-model `concurrency`, per-model and default
`max_tokens` caps, parallel cloud suite execution with one record per task, CLI
overrides (`--concurrency`, `--max-tokens`, `--timeout`), the `canary` anchor
subset (`--suite canary`), and the EvalPlus differential suites (`--suite
humaneval-plus` / `mbpp-plus`, pending live dataset verification). Sweep mode
predates this work and already covers the speed path.

Documented here as the roadmap, not yet implemented: a LiveCodeBench slice, the
private tripwire set, and formal IRT-based regeneration of the canary anchor IDs.
These are dataset and scoring additions that build on the work above.
