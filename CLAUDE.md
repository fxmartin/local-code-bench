# local-claude-code — Benchmarking local & cloud coding models for Claude Code

## Project Context

A CLI benchmark harness for driving and measuring agentic coding models. It runs
**local MLX-served models** on an Apple Silicon Mac against each other through
Claude Code, then against cloud models (GLM, Kimi K2, …) via **OpenRouter** — with
Claude Code itself as the baseline. The goal is to find the fastest, most capable
local coding setup and quantify the gap to the frontier. For FX's own
experimentation and (being public) reproducible by others.

## Tech Stack

- **Language**: Python (CPython, managed with `uv`)
- **Framework**: None — CLI-oriented
- **Runtime**: CPython 3.x via `uv`

## Architecture

A CLI harness that talks to any **OpenAI-compatible `/v1/chat/completions`
endpoint**, so the *same* code measures a local MLX server (e.g. `dflash serve`,
`turboquant-serve`, `mlx_lm.server`) and a remote provider (OpenRouter) by swapping
only the base URL and API key. Following the source articles' method, it measures
per-turn **time-to-first-token (prefill tok/s), decode tok/s, and total latency** —
because local agentic coding is **prefill-bound, not decode-bound**.

## Hardware (fixed benchmark machine)

- **MacBook Pro M3 Max, 48 GB** unified memory. The reference articles used an M4
  64 GB; 48 GB constrains which quantized models fit (target + draft + KV cache).

## Repository Structure

```
local-claude-code/
├── src/                  # harness package (runner, providers, metrics)
├── configs/              # model + provider definitions (local MLX, OpenRouter)
├── prompts/              # task-mode sub-prompts + sweep-mode preambles
├── results/              # raw benchmark output (gitignored)
├── articles/             # reference research (Medium series, PDFs)
├── tests/
├── CLAUDE.md
├── PROJECT-SEED.md
└── .gitignore
```

## Preferred CLI Tools

Use these instead of their traditional counterparts. They're installed and expected.

| Instead of | Use | Why |
|------------|-----|-----|
| `find` | `fd` | Faster, respects `.gitignore` |
| `grep` (via Bash) | `rg` | ripgrep — faster, better defaults |
| `cat` | `bat` | Syntax highlighting, line numbers |
| `cd` | `zoxide` (`z`) | Jump to frecent directories |
| `jq` for JSON | `jq` | Installed for JSON processing |

## Benchmark Protocol (v1)

- **Measurement surface**: direct OpenAI-compatible `/v1/chat/completions` — single-turn,
  controlled prompts. The real Claude Code agentic loop is **deliberately bypassed** in v1
  (it's noisy and unreproducible); it returns in v2.
- **Correctness**: HumanEval + MBPP, **pass@1 at temperature 0**, scored against the
  benchmark's own unit tests run in an isolated sandbox.
- **Speed**: per-turn TTFT / prefill tok/s / decode tok/s / total latency from the stream.
- **Cost**: tokens × dated price table in `configs/models.yaml` ($0 for local).
- **Outputs**: raw `results/<run>.jsonl` (re-scorable offline) → generated `LEADERBOARD.md`.
- **Reproducibility**: fixed seed/temp, pinned model revisions, suite version, and hardware
  tag recorded in every run's metadata.

## Testing Strategy

TDD per global standard. Unit-test the harness logic — **metrics parsing (TTFT/tok/s),
pass@1 scoring, and cost calculation** — independent of any live model (mock the streamed
response). Fault tolerance is a tested requirement: a timed-out / malformed / erroring
backend must be scored 0 and the run must continue. Untrusted generated code runs **only**
in the sandbox (temp dir + subprocess timeout, no network).

## GitHub Operations — Use `gh` CLI (NOT MCP)

Always use `gh` CLI for all GitHub operations (issues, PRs, releases, API calls).

## Story Management Protocol

### Single Source of Truth
The `docs/stories/` directory and its epic files are the **single source of truth** for all story definitions, progress tracking, and acceptance criteria.

### Story File Hierarchy
```
docs/STORIES.md (overview and navigation)
└── docs/stories/
    ├── epic-01-foundation-endpoint-protocol.md
    ├── epic-02-correctness-suite-sandbox.md
    ├── epic-03-model-matrix-resilience.md
    ├── epic-04-results-leaderboard.md
    ├── epic-05-sweep-run-control.md
    └── non-functional-requirements.md
```

### Progress Update Protocol
1. Update story completion checkboxes in epic files
2. Update the Epic Progress line in each epic
3. Mark completed acceptance criteria
4. Update dependency tracking
5. Track completed story points in epic progress sections

### Development Workflow
- **Sprint Planning**: Use epic files for story selection (critical path: Epic-01 → 02 → 03 → 04)
- **Code Reviews**: Link PRs to story IDs (e.g., "Implements Story 01.2-001")
- **Deployment**: Update story status in epic files post-merge
- **Updates**: Maintain within 24 hours of story completion

## Key Docs

- `REQUIREMENTS.md` — v1 Product Requirements (scope, P0/P1/P2, acceptance bar, risks)
- `docs/STORIES.md` — Epic navigation, personas, MVP scope, dependency graph
- `PROJECT-SEED.md` — Project seed data for downstream skills
- `LEADERBOARD.md` — Generated benchmark rankings (created by the harness)
- `articles/` — The two-part Medium series this project is modeled on (local Claude
  Code setup; MoE vs speculative decoding benchmark methodology)
