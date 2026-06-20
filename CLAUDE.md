# local-code-bench — Benchmarking local, cloud, and Codex coding runs

## Project Context

A CLI benchmark harness for driving and measuring coding models and coding-agent
runs. It compares **local MLX-served models**, cloud endpoints (GLM, Kimi K2, ...),
and Codex CLI agent runs on the same Apple Silicon machine. The goal is to find
the fastest usable local coding setup, quantify the gap to frontier cloud options,
and measure Codex as a first-class coding-agent baseline.

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
local-code-bench/
├── src/                  # harness package (runner, providers, metrics)
├── configs/              # model + provider definitions (local MLX, OpenRouter)
├── prompts/              # task-mode sub-prompts + sweep-mode preambles
├── results/              # raw benchmark output (gitignored)
├── articles/             # reference research (Medium series, PDFs)
├── tests/
├── CLAUDE.md
├── AGENTS.md
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

- **Endpoint mode**: direct OpenAI-compatible `/v1/chat/completions` — single-turn,
  controlled prompts.
- **Agent mode**: Codex CLI via `codex exec`, run non-interactively in an isolated
  task workspace and scored with the same benchmark tests where practical.
- **Correctness**: HumanEval + MBPP, **pass@1 at temperature 0**, scored against the
  benchmark's own unit tests run in an isolated sandbox.
- **Speed**: per-turn TTFT / prefill tok/s / decode tok/s / total latency from the stream.
- **Cost**: endpoint tokens × dated price table in `configs/models.yaml` ($0 for local);
  Codex agent cost is marked unavailable unless reliable usage data is exposed.
- **Outputs**: raw `results/<run>.jsonl` (re-scorable offline) → generated `LEADERBOARD.md`.
- **Reproducibility**: fixed seed/temp, pinned model revisions, suite version, and hardware
  tag recorded in every run's metadata.

## Testing Strategy

TDD per global standard. Unit-test the harness logic — **metrics parsing (TTFT/tok/s),
pass@1 scoring, and cost calculation** — independent of any live model (mock the streamed
response). Fault tolerance is a tested requirement: a timed-out / malformed / erroring
backend must be scored 0 and the run must continue. Untrusted generated code runs **only**
in the sandbox (temp dir + subprocess timeout, no network).

## Releases — Automated (python-semantic-release)

Releases are **fully automated on push to `main`** via `.github/workflows/release.yml`.
There is no manual tagging. The version lives in `pyproject.toml:project.version`.

- **Commits drive the bump** (Conventional Commits): `feat:` → MINOR, `fix:`/`perf:`
  → PATCH, `BREAKING CHANGE:`/`!` → MAJOR. `docs/chore/ci/refactor/test/build` → no
  release. A push with no releasable commits is a no-op.
- **Pre-1.0 semantics**: `major_on_zero = false` — while on `0.x`, breaking changes
  bump the MINOR (`0.1.0` → `0.2.0`), not `1.0.0`. Promote to `1.0.0` deliberately
  (cut it manually or flip the flag) when the v1 acceptance bar is met.
- **What a release does**: bumps `pyproject.toml`, updates `CHANGELOG.md`, creates the
  `vX.Y.Z` tag, and publishes a GitHub Release. No package build / no PyPI upload.
- **Commit-format gate**: `.github/workflows/commit-format.yml` (commitlint) rejects
  non-conventional commits on PRs, protecting the release signal. Config:
  `.commitlintrc.json`. PSR's own release commits push directly to `main` and are exempt.
- **If branch protection is added** requiring PRs on `main`, the built-in `GITHUB_TOKEN`
  may be blocked from pushing the bump commit — switch the release workflow to a PAT.

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
- `AGENTS.md` — Codex project instructions
- `PROJECT-SEED.md` — Project seed data for downstream skills
- `LEADERBOARD.md` — Generated benchmark rankings (created by the harness)
- `articles/` — The two-part Medium series this project is modeled on (local Claude
  Code setup; MoE vs speculative decoding benchmark methodology)
