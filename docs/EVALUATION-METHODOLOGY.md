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
uv run bench --mode sweep --model local-mlx-qwen --run-file results/sweep.jsonl
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
the first measured task silently absorbs the cold start and skews its TTFT. The
discarded request applies only to models with a declared local `inferencer`;
cloud API calls are never used for warmup.

### Power, Energy, And The 48 GB Ceiling

Speed and correctness are not the only axes. Two models can post similar tok/s
while drawing very different power, and on a laptop run for hours, performance per
watt and sustained thermals matter. `--power` samples GPU/CPU power via macOS
`powermetrics` for the duration of a run and records average and peak watts plus
total joules, so tokens-per-joule can be derived against the token counts already
in the task records. It needs root, so it uses `sudo -n` and degrades gracefully
when passwordless sudo is not configured.

A hard operational constraint surfaced in live testing: on a 48 GB machine, two
local inference servers (mlx-lm and Ollama) cannot co-reside with large models
loaded. An idle server can still hold its full model resident, so running both
alongside a large KV cache drives the machine deep into swap, at which point
high-context prefill numbers measure SSD latency rather than the model. Any head-to-head must bring the
servers up one at a time, never concurrently, and the sweep ladder should be capped
below the context size where a single model plus its KV cache starts swapping.

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
serves quantized local builds. Quantization and serving change
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

The release files are not auto-downloaded because their URLs and versions move. Place
`HumanEvalPlus.jsonl(.gz)` and `MbppPlus.jsonl(.gz)` in the cache dir (the names in
`EVALPLUS_FILENAMES`). Download pinned raw releases so benchmark provenance is stable:

```bash
mkdir -p .cache/benchmarks
curl -fL --retry 3 \
  https://github.com/evalplus/humanevalplus_release/releases/download/v0.1.10/HumanEvalPlus.jsonl.gz \
  -o .cache/benchmarks/HumanEvalPlus.jsonl.gz
curl -fL --retry 3 \
  https://github.com/evalplus/mbppplus_release/releases/download/v0.2.0/MbppPlus.jsonl.gz \
  -o .cache/benchmarks/MbppPlus.jsonl.gz
```

Do not load and re-export `get_mbpp_plus()`. EvalPlus restores JSON-encoded tuples,
sets, and complex numbers in memory, after which its generic JSON writer cannot
serialize the dataset. The harness applies the same type restoration when it loads
the official raw MBPP+ release.

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
fails one that is wrong only on a plus input, honors float tolerance, and restores
the tuple, set/dict, and complex input types encoded by the official MBPP+ release.
Full-scale runtime within the chosen timeout still needs live verification on FX's
machine.

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

## The Home-Grown Ladder: Mini-Apps And Debugging Above Function-Level Suites

HumanEval-shaped tasks measure the model one function at a time and barely
exercise anything app-shaped: argument handling, exit codes, exact output
contracts, spec-reading discipline. The home-grown ladder fills the rungs
above them — and, being unpublished before this repository, doubles as a
contamination tripwire: a model that aces the public suites but stumbles here
is pattern-matching, not reading the spec.

The ladder:

- **`logclass-cli` (rung 1, easy mini-app).** A Python port of the OpenCode
  Task A log classifier, so the whole ladder runs through one pipeline with
  comparable pass@1 rows and no Go toolchain. The severity semantics are
  test-enforced to match the Go original's single source of truth
  (`opencode.fixtures.classify_line`), and where `prompts/task-a.md` left
  output formats loose (the Go scorer matches by regex), this spec pins them
  exactly. The Go Task A stays untouched in the `bench opencode` flow — it
  keeps the cross-language axis and its scorecard history. Slices: `counts`,
  `json-filter`, `edge-rules`, `exit-codes`; the discriminating edges are the
  case-sensitive substring rules (lowercase `error` is `unknown`, `WARNING`
  is `warn`, `FATALITY` is `error`) and first-match precedence.
- **`jsondiff-cli` (rung 2, medium mini-app).** A deterministic JSON diff
  tool. The spec pins edges that discriminate between models: JSON type
  strictness (`true` is not `1`, but `1` equals `1.0` — the Python bool-is-int
  trap), no descent into added/removed subtrees, an exact deterministic output
  order, and a three-way exit-code contract. Slices: `core`, `format-order`,
  `type-edges`, `exit-codes`.
- **`calc-cli` (rung 3, hard mini-app).** An arithmetic expression evaluator
  that needs a real tokenizer and recursive-descent parser: right-associative
  `^`, unary minus binding between `^` and `*`/`/` (`-2^2` is -4, `(-2)^2` is
  4), IEEE-double semantics with an exact formatting rule, and an
  all-or-nothing file-mode output contract that forces buffering. The grammar
  is deliberately not Python — `^` is exponentiation and `**` is a syntax
  error — so an `eval()` shortcut fails (validated explicitly with an
  eval-cheat variant). Slices: `arithmetic`, `power-unary`, `format-file`,
  `errors`.
- **`bugfix-py` (rung 4, debugging axis).** Five records, each a small buggy
  module plus a true bug report; the model returns the complete fixed module.
  The skill measured is fault localization and a behaviour-preserving fix, not
  greenfield generation — every test asserts the fixed behaviour *and*
  regression behaviour the fix must not break. The bugs are classic Python
  failure modes: mutable default argument, shallow-copy pollution of module
  defaults, off-by-one dropping the last window, double-applied sort reversal
  flipping the tie-break, and generator exhaustion.

All three are scored black-box in the existing sandbox: acceptance tests drive
the program's entry point in-process (the sandbox forbids subprocesses) and
assert only observable behaviour — stdout, exit codes, return values — never
internals. The mini-app suites split their hidden acceptance tests into
behavioural slices shipped as multiple records sharing one prompt, so a run
yields graded partial credit (which facet broke) instead of a single
all-or-nothing bit, while riding the unchanged pass@1 machinery; `bugfix-py`
gets its granularity from one record per bug.

Offline validation mirrors the EvalPlus approach and runs in CI: each
reference solution must pass every slice in the real sandbox, and known-buggy
variants must fail exactly their targeted slice. `bugfix-py` is additionally
self-proving — the shipped buggy source must fail its own tests (so the bug
report is real) and the reference fix must pass. Each suite is generated
deterministically by its `scripts/build_*_suite.py` script, with a drift test
keeping the checked-in dataset in sync.

The suites are registered in `configs/suites.yaml`, and custom suites
registered there are loadable by name everywhere a built-in suite is — the
endpoint runner, agent mode, and rescore all accept the id:

```bash
uv run bench --suite logclass-cli --model openrouter-glm-4.6 --max-tokens 2048
uv run bench --suite jsondiff-cli --model openrouter-glm-4.6 --max-tokens 2048
uv run bench --suite calc-cli --model openrouter-glm-4.6 --max-tokens 3072
uv run bench --suite bugfix-py --model openrouter-glm-4.6 --max-tokens 2048
uv run bench --mode agent --agent codex --suite bugfix-py
```

Raise `--max-tokens` for the mini-apps (2048 is a sensible floor, 3072 for the
parser-sized `calc-cli`): a full program is longer than a HumanEval completion
and the 1024 suite default truncates mid-source. One agent-mode caveat: the
workspace materializer writes the acceptance tests into `test_solution.py`
alongside the instructions, so *agents can read the tests* while endpoint
models never see them — a TDD-style advantage to keep in mind when comparing
agent scores against endpoint scores on the same suite. Like the canary IDs,
each suite's spec and tests are frozen once benchmarked: change them only by
cutting a new versioned suite id, or historical runs stop being comparable.

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
