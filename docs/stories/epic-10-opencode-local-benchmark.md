# Epic 10: OpenCode Local Coding Benchmark

> **Epic ID alias**: LLMBENCH-1 · **Owner**: FX
> *Reference: Glukhov, R. "Best LLMs for OpenCode, From Gemma 4 to Qwen 3.6, Tested Locally." glukhov.org / Towards AI, Apr 2026.*

## Epic Overview
**Epic ID**: Epic-10 (LLMBENCH-1)
**Description**: A one-command, objective benchmark that scores any OpenCode-compatible local model on two independent axes — **open-ended coding ability** (Task A) and **strict rule-following** (Task B) — using a compiler and a diff as the only judges. It logs quant provenance (the Unsloth-vs-Bartowski lesson) and run mode (the default-vs-thinking lesson), and produces a comparable scorecard across whatever engines are installed (oMLX, MLX-LM, Ollama, LM Studio, …). It re-implements the *idea* of Rost Glukhov's locally-hosted OpenCode test as a fully automated, deterministic harness sized for a 48GB M3 Max, so results are reproducible and comparable rather than eye-balled on 16GB at aggressive quants.
**Business Value**: Glukhov's central, non-obvious finding is that open-ended coding and strict rule-following are *different skills* — Qwen 3.5 27B (IQ3_XXS) passed all 8 CLI unit tests yet several strong coders collapsed on a structured map (slug mismatches, dropped fields, 8 pages collapsing to 1 URL). Two confounders compound this: quant **source** matters as much as bit-width (Unsloth's IQ3_XXS scored 5.0% error where Bartowski's quant of the *same model at the same bit level* scored 100%), and config can mask capability (GPT-OSS 20B failed in default mode but became capable in high-thinking mode). FX needs an automated, deterministic harness that measures both skills separately and surfaces these variables, so "which local model is actually good" is answered by evidence, not vibes.
**Success Metrics**: Run end-to-end with `./run-bench.sh --model <name>` and get a scorecard with zero manual grading; re-running the same model + quant + seed yields identical scores (determinism); and the scorecard captures the three article variables — the coding/rule-following skill split, the quant source, and the run mode.

## Epic Scope
**Total Stories**: 5 | **Total Points**: 21 | **MVP Stories**: 0 (Should Have / v1.x; 1.5 optional stretch)

## Out of Scope
- Cloud API models (the point is local).
- Subjective code-quality grading — the compiler and a diff are the only judges.
- Fine-tuning or quantizing models ourselves.

## Decisions Locked With FX
- **Implementation approach** (when built): a Python module inside the existing `local_code_bench` package, reusing `provider.py` (OpenAI-compatible streaming), `metrics.py` (TTFT / tokens-per-second), `results.py` (JSONL), and the `scoring.py` fence-extraction approach; a thin `run-bench.sh` wrapper exposes the epic's literal `run-bench.sh --model …` interface.
- **Fixture domain**: the neutral **log-line severity classifier** below (deterministic, trivial ground truth, one fixture feeds both tasks).
- **Task B format**: **JSON** (line-number → level); malformed/unparseable output scores 100% error and is flagged `PARSE_FAIL`.
- **Engine endpoint defaults**: pin defaults for all Epic-08 inferencers (dflash 8000, turboquant 8002, mlx-lm 8080, llama-cpp 8081, ollama 11434, mlc-llm 8082, vllm-mlx 8001, exo 52415, lm-studio 1234, gpt4all 4891), selectable via `--engine`.

## System Under Test
A small, self-contained Go CLI — a **log-line classifier**. No network, fully deterministic, so ground truth is trivial to define and one fixture feeds both tasks.

Behaviour the model must produce:
- `classify <file>` reads a log file, tags each line by severity rules, prints counts per level.
- `--json` emits structured output (this path bridges into Task B).
- `--filter <level>` prints only matching lines.
- Exit codes: `0` success, `1` file not found, `2` bad arguments.

Severity rules (given verbatim to the model, and the ground truth for Task B):
- Line contains `ERROR` or `FATAL` (case-sensitive) → `error`
- Else contains `WARN` → `warn`
- Else contains `INFO` → `info`
- Else → `unknown`
- First matching rule wins, evaluated in the order above.

## Features in This Epic

### Feature 10.1: Harness Scaffold & Model Invocation

#### Stories

##### Story 10.1-001: Fixed-prompt invocation and capture
**User Story**: As the benchmark operator, I want a single script that sends a fixed prompt to a chosen local model and captures the raw output plus timing so that every model is tested under identical conditions.
**Priority**: Should Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** a chosen model **When** I run `run-bench.sh --model <name> [--mode default|thinking] [--endpoint <url>]` **Then** the run is driven end-to-end against that model.
- **Given** any OpenAI-compatible endpoint (oMLX, Ollama, LM Studio) **When** a model is invoked **Then** it is reached via `/v1/chat/completions`, with oMLX's Anthropic endpoint supported as an option.
- **Given** the prompt files `prompts/task-a.md` and `prompts/task-b.md` **When** a run starts **Then** prompt text is read from those files, never inlined, so prompts are version-controlled and identical across models.
- **Given** a completed invocation **When** captured **Then** the harness records raw response, wall-clock seconds, prompt+completion tokens, and tokens/sec.
- **Given** a run **When** metadata is logged **Then** it includes model name, quant string, provider (the Unsloth-vs-Bartowski lesson), endpoint, mode, and seed/temperature.
- **Given** determinism is required **When** a model is invoked **Then** temperature is pinned (default 0) and the seed is logged.

**Technical Notes**: Reuse `provider_for_model` (`src/local_code_bench/provider.py:120`), `capture_stream_metrics` (`src/local_code_bench/metrics.py:35`), and `ChatRequest`. Add optional, defaulted fields to `ModelConfig` in `config.py` (`quant`, `provider`, `engine`, `thinking_extra_body`) so existing entries and tests are unaffected; allow CLI overrides (`--quant/--provider/--engine`) and an `--endpoint` override via `dataclasses.replace`. Branch `bench opencode` at the top of `main` (`cli.py`) with its own flag namespace, keeping the existing flat `--mode` flow intact; `run-bench.sh` = `exec uv run bench opencode "$@"`. A new `opencode/engines.py` maps the 10 Epic-08 engines to default `/v1` endpoints for `--engine`.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 01.1-003 (OpenAI-compatible provider), 01.2-001 (streaming metrics)
**Risk Level**: Low

### Feature 10.2: Task A — Open-Ended Coding (auto-scored)

#### Stories

##### Story 10.2-001: Extract, compile, and behaviourally test the generated Go
**User Story**: As the operator, I want the model's generated Go extracted, compiled, and behaviourally tested so that coding ability is judged by a compiler and a test binary, not by eye.
**Priority**: Should Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** a model response **When** scoring Task A **Then** the Go source is extracted from a fenced block, tolerating minor preamble.
- **Given** extracted source **When** scored **Then** `go build` runs in a temp dir and the **compiles** pass/fail is captured first.
- **Given** a built binary **When** the fixed black-box suite runs **Then** it checks correct counts on a known fixture, valid schema-correct `--json`, `--filter error` returning only error lines, and exit code `1` on missing file / `2` on bad args.
- **Given** the suite result **When** scored **Then** score = `tests_passed / tests_total`, and a non-compiling submission scores 0 and is flagged `BUILD_FAIL` (mirrors the article's hard-fail rows).
- **Given** scoring **When** evaluated **Then** only observable behaviour is asserted — never internal structure.

**Technical Notes**: A Go-aware variant of `extract_code` (`src/local_code_bench/scoring.py:19`). `opencode/blackbox.py` runs the compiled binary via `subprocess.run` (the model's Go is compiled, not exec'd as Python — distinct from the existing Python sandbox path). Ship `opencode/reference/classifier.go` as the canonical correct implementation so the black-box suite — and the DoD "reference runs clean end-to-end" — is verifiable with no live model. Tests skip-guard when `shutil.which("go")` is None (Go 1.24.7 confirmed in CI here).

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 10.1-001
**Risk Level**: Medium

### Feature 10.3: Task B — Strict Rule-Following (auto-scored)

#### Stories

##### Story 10.3-001: Structured classification map diffed against ground truth
**User Story**: As the operator, I want the model to emit a structured classification map for a fixed log fixture, diffed against ground truth, so that rule-following is measured independently of coding skill.
**Priority**: Should Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** the Task B prompt **When** rendered **Then** it gives the same severity rules plus a fixed input fixture and asks for JSON mapping each line number to its level — no code, just the artifact.
- **Given** the model output **When** scored **Then** the harness parses it and diffs against generated ground truth.
- **Given** the diff **When** metrics are computed **Then** it reports **error rate** = mismatches / expected lines (lower better), **coverage** = lines present / lines expected (catches dropped rows), and a **collision check** that flags when distinct inputs map to a colliding/identical key (the "8 pages → 1 URL" failure).
- **Given** malformed or unparseable output **When** scored **Then** it scores 100% error and is flagged `PARSE_FAIL`.

**Technical Notes**: `opencode/fixtures.py` `classify_line` implements the severity rules verbatim (first-match-wins, case-sensitive) and is the single source of truth for both Task A's behavioural expectations and Task B's ground truth. The Task B prompt carries a `{{FIXTURE}}` placeholder substituted at render time from `fixtures/opencode-sample.log`, keeping prompt text version-controlled and the fixture authoritative.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: 10.1-001
**Risk Level**: Medium

### Feature 10.4: Scorecard & Provenance Report

#### Stories

##### Story 10.4-001: Comparable scorecard with provenance note
**User Story**: As FX reviewing results, I want a single comparable scorecard across all models run so that I can see the coding/rule-following split and the effect of quant source at a glance.
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** a completed run **When** recorded **Then** it is appended to `results/scorecard.csv` and a Markdown table is rendered sorted passing rows first, then by Task B error rate ascending (the article's ordering).
- **Given** the scorecard **When** rendered **Then** columns include model, quant, provider, mode, Task A (build + tests n/total), Task B (error %, coverage %, collisions), tokens/sec, and wall-clock.
- **Given** two rows that are the same base model at the same bit-width from different providers **When** the report is generated **Then** a short "provenance note" section surfaces them and reports the delta — the Unsloth-vs-Bartowski detector, made first-class.

**Technical Notes**: `opencode/scorecard.py` with `append_run`, `render_markdown`, and `provenance_note` (group rows by base model + bit-width parsed from the quant string, surface provider-only pairs and their score delta). Reuse `results.py` (`new_run_path`/`append_jsonl`) to also keep a JSONL provenance record alongside the CSV.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 10.2-001, 10.3-001
**Risk Level**: Low

### Feature 10.5: Reproducibility & Engine Matrix (stretch)

#### Stories

##### Story 10.5-001: Sweep, repeat/variance, and engine version
**User Story**: As the operator, I want to sweep the same test across multiple installed engines and modes so that I can isolate engine and mode effects (the GPT-OSS default-vs-thinking lesson).
**Priority**: Should Have (stretch)
**Story Points**: 3

**Acceptance Criteria**:
- **Given** a model list **When** I run `run-bench.sh --sweep models.txt` **Then** it iterates the list and produces one consolidated scorecard.
- **Given** `--repeat N` **When** a model is run **Then** it runs N times and reports variance (the article saw run-to-run swings on the 35B; variance should be visible, not averaged away).
- **Given** each scorecard row **When** recorded **Then** it captures the engine version (e.g. oMLX build, Ollama version).

**Technical Notes**: `--sweep`/`--repeat` flags on the `opencode` subcommand. Capture engine version via the engine's version endpoint or CLI where available; record it per row alongside the existing metadata.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 10.4-001
**Risk Level**: Medium

## Definition of Done (Epic)
- All of 10.1-001–10.4-001 implemented; 10.5-001 optional.
- A reference model (whatever is installed) runs clean end-to-end and produces a scorecard.
- README documents how to add a model and how to read the provenance note.
- Determinism verified: same inputs, same scores on rerun.

## Epic Progress
**Completed**: 1 / 5 stories · 5 / 21 points (10.3-001)
