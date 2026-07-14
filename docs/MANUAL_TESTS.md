# Manual Test Plan

This document tracks environment-dependent tests that cannot be fully proven by
the automated `pytest` and `ruff` suite. Record each run with the date, command,
result file, observed result, and any follow-up issue.

## Prerequisites

- Run from the repository root.
- Install dependencies with `uv sync`.
- Keep `.env` local and uncommitted when using API keys.
- Confirm automated verification is green before manual testing:

```bash
uv run pytest
uv run ruff check .
```

## Result Log

| Date | Tester | Test ID | Command or Action | Evidence | Result | Notes |
|------|--------|---------|-------------------|----------|--------|-------|
| 2026-06-21T08:11:35Z | Codex | Prerequisite | `uv run pytest`; `uv run ruff check .` | terminal output | PASS | 66 tests passed, 86.26% coverage, 80% gate reached; Ruff passed. |
| 2026-06-21T08:11:35Z | Codex | MT-001 | OpenRouter GLM/Kimi smoke, `--limit 3` | `results/manual-openrouter-glm.jsonl`; `results/manual-openrouter-kimi.jsonl` | PASS | GLM 3/3 passed; Kimi K2 3/3 passed. |
| 2026-06-21T08:11:35Z | Codex | MT-002 | Anthropic baseline smoke, `--limit 3` | `results/manual-anthropic.jsonl` | FAIL | 3/3 infra failures. Anthropic API returned 404 for configured model `claude-sonnet-4-20250514`. |
| 2026-06-21T08:22:00Z | Codex | MT-002 Retest | Anthropic baseline smoke after model ID update, `--limit 3` | `results/manual-anthropic-fixed.jsonl` | PASS | Updated model ID to `claude-sonnet-4-6`; Anthropic smoke passed 3/3. |
| 2026-06-21T08:11:35Z | Codex | MT-003 | `scripts/bring-up-local.sh dflash`; DFlash smoke | `results/manual-local-dflash.jsonl` | FAIL | Bring-up reported no listener on port 8000; benchmark recorded 3/3 connection-refused infra failures. |
| 2026-06-21T09:00:00Z | Codex | MT-003 Retest | DFlash smoke after server startup and config update | `results/manual-local-dflash-fixed.jsonl` | PASS | Updated `local-dflash-qwen` to served model ID `mlx-community/Qwen3.6-27B-4bit`; smoke passed 3/3. |
| 2026-06-21T08:11:35Z | Codex | MT-004 | `scripts/bring-up-local.sh turboquant`; TurboQuant smoke | `results/manual-local-turboquant.jsonl` | FAIL | Bring-up reported no listener initially; later requests reached port 8001 but `/v1/chat/completions` returned 404 for 3/3 tasks. |
| 2026-06-21T08:35:00Z | Codex | MT-004 Retest | TurboQuant smoke on temporary config pointing to port 8002 | `results/manual-local-turboquant-8002.jsonl` | FAIL | `/v1/models` initially returned `manjunathshiva/Qwen3.6-35B-A3B-tq3-g32`; benchmark produced 1 model failure from remote close, then 2 connection-refused infra failures after the server stopped responding. |
| 2026-06-21T08:49:00Z | Codex | MT-004 Retest | TurboQuant smoke after model download and config update | `results/manual-local-turboquant-fixed.jsonl` | PASS | Updated `local-turboquant-qwen-moe` to port 8002 and model ID `manjunathshiva/Qwen3.6-35B-A3B-tq3-g32`; smoke passed 3/3. |
| 2026-06-21T08:11:35Z | Codex | MT-005 | Full endpoint matrix | `results/manual-full-endpoint-humaneval.jsonl` | DEFERRED | Started and stopped by FX after 4/820 attempts to avoid a long, costly run. Early OpenRouter GLM attempts passed. |
| 2026-06-21T08:11:35Z | Codex | MT-006 | Local server failure recovery | readiness output | BLOCKED | No local DFlash server was running, so there was no successful local task after which to kill the server. |
| 2026-06-21T08:11:35Z | Codex | MT-007 | Endpoint resume, bounded GLM variant | `results/manual-openrouter-glm.jsonl` | PASS | Resume skipped 3/3 completed GLM tasks without duplicates. |
| 2026-06-21T08:11:35Z | Codex | MT-008 | Codex agent smoke, `--limit 3` | `results/manual-codex-agent.jsonl` | PASS | Codex agent completed 3/3 tasks with `sandbox_mode: workspace-write`. |
| 2026-06-21T08:11:35Z | Codex | MT-009 | Codex agent resume | `results/manual-codex-agent.jsonl` | PASS | Resume skipped 3/3 completed Codex tasks without duplicates. |
| 2026-06-21T08:11:35Z | Codex | MT-010 | Leaderboard generation from manual artifacts | `results/manual-LEADERBOARD.md` | PASS | Generated endpoint and agent sections. Output was written under `results/` to avoid overwriting tracked `LEADERBOARD.md`. |
| 2026-06-21T08:11:35Z | Codex | MT-011 | Offline re-score | `results/manual-rescored-humaneval.jsonl` | PASS | Re-scored 3 stored GLM endpoint records with 0 missing tasks and no model calls. |
| 2026-06-21T08:11:35Z | Codex | MT-012 | Sweep prompt generation; local DFlash sweep; sweep summary | terminal output; `results/manual-sweep.jsonl` | PARTIAL | Prompt generation passed. Local DFlash sweep failed with connection refused before writing records. Summary command ran on an empty/nonexistent sweep file and produced no model rows. |
| 2026-06-21T08:11:35Z | Codex | MT-013 | Secret hygiene scan | terminal output | PASS | No OpenRouter/Anthropic key patterns or `.env` paths found in manual result JSONL or generated manual leaderboard. |
| 2026-06-21T08:11:35Z | Codex | MT-014 | Local model memory observation | terminal output | BLOCKED | Escalated `ps -axo pid,comm,rss \| rg "dflash\|turboquant\|mlx\|python"` found no matching local model processes. |

## MT-001: OpenRouter Endpoint Smoke Test

**Purpose**: Prove OpenAI-compatible cloud endpoints work with real OpenRouter
credentials and write usable JSONL records.

**Setup**:

```bash
export OPENROUTER_API_KEY=...
```

**Commands**:

```bash
uv run bench --suite humaneval --model openrouter-glm-4.6 --limit 3 --run-file results/manual-openrouter-glm.jsonl
uv run bench --suite humaneval --model openrouter-kimi-k2 --limit 3 --run-file results/manual-openrouter-kimi.jsonl
```

**Pass criteria**:

- Commands complete without leaking secrets to stdout, stderr, or JSONL.
- JSONL records include endpoint metrics, token counts, raw responses, score
  status, and reproducibility metadata.
- Provider failures, if any, are recorded as infrastructure failures without
  crashing unrelated runs.

## MT-002: Anthropic Baseline Smoke Test

**Purpose**: Prove the Anthropic adapter works against the real API baseline.

**Setup**:

```bash
export ANTHROPIC_API_KEY=...
```

**Command**:

```bash
uv run bench --suite humaneval --model anthropic-claude-baseline --limit 3 --run-file results/manual-anthropic.jsonl
```

**Pass criteria**:

- Command completes or records recoverable infrastructure failures.
- JSONL records distinguish `provider_type: anthropic` from OpenAI-compatible
  endpoint runs.
- Token and cost fields are sufficient for leaderboard generation.

## MT-003: Local MLX-LM Server Bring-Up

**Purpose**: Prove the documented local MLX-LM bring-up flow matches the
configured `local-mlx-qwen` endpoint.

**Setup**:

```bash
export MLX_LM_COMMAND='mlx_lm.server --model mlx-community/Qwen3.6-27B-4bit --port 8080'
scripts/bring-up-local.sh mlx-lm
```

**Command**:

```bash
uv run bench --suite humaneval --model local-mlx-qwen --limit 3 --run-file results/manual-local-mlx.jsonl
```

**Pass criteria**:

- Bring-up script reports readiness on port 8080.
- Benchmark command writes scored endpoint records.
- Local records show zero configured token cost and include timing metrics.

## MT-004: Local Ollama Server Bring-Up

**Purpose**: Prove the documented local Ollama bring-up flow matches the
configured `local-ollama-qwen` endpoint.

**Setup**:

```bash
ollama pull qwen3.6:27b
export OLLAMA_COMMAND='ollama serve'
scripts/bring-up-local.sh ollama
```

**Command**:

```bash
uv run bench --suite humaneval --model local-ollama-qwen --limit 3 --run-file results/manual-local-ollama.jsonl
```

**Pass criteria**:

- Bring-up script reports readiness on port 11434.
- Benchmark command writes scored endpoint records.
- Local records show zero configured token cost and include timing metrics.

## MT-005: Full Endpoint Matrix Run

**Purpose**: Prove all configured endpoint backends can run unattended in one
suite invocation.

**Setup**:

- OpenRouter and Anthropic keys are available.
- MLX-LM and Ollama servers are running and ready.

**Command**:

```bash
uv run bench --suite humaneval --skip local-example --run-file results/manual-full-endpoint-humaneval.jsonl
```

**Pass criteria**:

- The run attempts the five real endpoint backends, excluding the
  `local-example` placeholder.
- A failure in one backend is recorded and does not abort the remaining backends.
- The output JSONL has no duplicate `(model, task_id)` pairs.

## MT-006: Local Server Failure Recovery

**Purpose**: Prove the runner survives a local server being killed mid-run.

**Setup**:

- Start at least one local backend.
- Use a run limit high enough to allow killing the server during execution.

**Command**:

```bash
uv run bench --suite humaneval --model local-mlx-qwen --limit 10 --run-file results/manual-killed-server.jsonl
```

**Action**:

- Kill the MLX-LM server after at least one task completes.

**Pass criteria**:

- Completed tasks remain in the JSONL.
- Later tasks for the killed backend are recorded as infrastructure failures.
- The process exits cleanly rather than hanging indefinitely.

## MT-007: Resume Partial Endpoint Run

**Purpose**: Prove `--resume` skips already completed model/task pairs and
continues the same JSONL run without duplication.

**Setup**:

- Use a partial run file from MT-005 or MT-006.

**Command**:

```bash
uv run bench --suite humaneval --run-file results/manual-full-endpoint-humaneval.jsonl --resume
```

**Pass criteria**:

- Completed pairs are skipped.
- Missing pairs are run.
- The final JSONL contains one record per completed `(model, task_id)` pair.

## MT-008: Codex Agent Smoke Test

**Purpose**: Prove `codex exec` agent mode works with the configured sandbox and
writes agent-mode records.

**Setup**:

```bash
codex --version
```

**Command**:

```bash
uv run bench --mode agent --agent codex --suite humaneval --limit 3 --run-file results/manual-codex-agent.jsonl
```

**Pass criteria**:

- Codex runs unattended through `codex exec`.
- Records include `run_mode: agent`, CLI metadata, wall time, exit code,
  `sandbox_mode: workspace-write`, scoring result, and failure reason when
  applicable.
- Agent workspaces remain isolated from endpoint output.

## MT-009: Codex Agent Resume

**Purpose**: Prove agent-mode resume skips already completed tasks.

**Setup**:

- Use a partial or completed `results/manual-codex-agent.jsonl`.

**Command**:

```bash
uv run bench --mode agent --agent codex --suite humaneval --limit 3 --run-file results/manual-codex-agent.jsonl --resume
```

**Pass criteria**:

- Completed `(agent, task_id)` pairs are skipped.
- No duplicate agent records are appended for skipped tasks.

## MT-010: Leaderboard Regeneration

**Purpose**: Prove endpoint and agent JSONL outputs can be turned into a
publishable leaderboard.

**Command**:

```bash
uv run bench --mode leaderboard \
  --input results/manual-full-endpoint-humaneval.jsonl results/manual-codex-agent.jsonl \
  --output LEADERBOARD.md
```

**Pass criteria**:

- `LEADERBOARD.md` is regenerated without exceptions.
- Endpoint and agent sections remain distinct.
- Endpoint rows include pass rate, latency, throughput, and cost fields.
- Agent rows include pass rate, wall time, sandbox mode, and failure count.

## MT-011: Offline Re-Score

**Purpose**: Prove stored endpoint JSONL can be re-scored without model calls.

**Command**:

```bash
uv run bench --mode rescore --suite humaneval \
  --input results/manual-full-endpoint-humaneval.jsonl \
  --output results/manual-rescored-humaneval.jsonl
```

**Pass criteria**:

- Command performs no network model calls.
- Output contains recomputed scoring results.
- Re-scored records can be used as leaderboard input.

## MT-012: Sweep Prompt And Run

**Purpose**: Prove sweep mode can generate padding prompts and run a configured
model.

**Commands**:

```bash
uv run bench --mode sweep --prompt "Return 1"
uv run bench --mode sweep --model local-mlx-qwen --prompt "Return 1" --run-file results/manual-sweep.jsonl
uv run bench --mode sweep --input results/manual-sweep.jsonl
```

**Pass criteria**:

- Prompt generation prints multiple context sizes.
- Sweep run writes JSONL records with the selected model and prompt size data.
- Sweep summary reads stored records without rerunning the model.

## MT-013: Sandbox And Secret Hygiene Spot Check

**Purpose**: Manually confirm generated output and result files do not expose
host-sensitive data.

**Commands**:

```bash
rg -n "OPENROUTER_API_KEY|ANTHROPIC_API_KEY|sk-or-|sk-ant-" results LEADERBOARD.md
rg -n "/Users/fxmartin/.*\\.env|\\.env" results LEADERBOARD.md
```

**Pass criteria**:

- Both `rg` commands return no matches; exit code 1 from `rg` is acceptable for
  this check.
- No API key values are present in result files or leaderboards.
- `.env` contents are not copied into artifacts.
- Generated code execution remains constrained to benchmark workspaces and
  sandbox runner behavior.

## MT-014: Local Model Memory Observation

**Purpose**: Record whether each local model fits comfortably on the M3 Max
48 GB machine during representative runs.

**Setup**:

- Start the local backend under test.
- Start a system monitor or command-line measurement process.

**Suggested commands**:

```bash
ps -axo pid,comm,rss | rg "ollama|mlx|python"
uv run bench --suite humaneval --model local-mlx-qwen --limit 10 --run-file results/manual-memory-mlx.jsonl
uv run bench --suite humaneval --model local-ollama-qwen --limit 10 --run-file results/manual-memory-ollama.jsonl
```

**Pass criteria**:

- Peak observed resident memory is recorded in the Result Log.
- Memory pressure does not make the machine unusable.
- Any out-of-memory or swap-heavy behavior is documented with the model name,
  command, and approximate peak memory.
