# Epic 14: Additional Coding-Agent Harnesses (Claude Code, Qwen Code)

## Epic Overview
**Epic ID**: Epic-14
**Description**: Generalize the harness's agent mode beyond Codex so it can benchmark multiple coding-agent CLIs through one pluggable adapter, then add **Claude Code** and **Qwen Code** as first-class agent harnesses alongside Codex (Epic-06) and OpenCode (Epic-10). Today the agent layer is Codex-only by construction: `AgentType = Literal["codex"]`, `_parse_agent` rejects any other `type`, and the runner hard-codes `build_codex_command`; OpenCode lives in its own separate `opencode/` module. This epic refactors that into a **harness-adapter registry** — each harness is a command builder + output/JSON parser + read-only detection — reusing Epic-06's task-workspace materializer, agent-mode sandbox scoring, and `run_mode=agent` JSONL so every harness is measured comparably. It then onboards the two new CLIs, capturing the crucial asymmetry between them: Qwen Code is OpenAI-compatible and can drive the project's **local** models directly, while Claude Code cannot natively target a local OpenAI endpoint and serves as a **cloud frontier agent baseline** (or drives a local model only via an Anthropic-compatible gateway/shim).
**Business Value**: The project's value is a defensible, apples-to-apples comparison of coding setups on one Apple Silicon box. Codex alone is a single agent baseline; FX actually uses several coding agents. Adding Claude Code (the frontier agent baseline) and Qwen Code (an OpenAI-compatible agent that runs the *same local models* the endpoint suite already measures) turns "how good is one agent" into "how do agents compare on identical tasks, and how much does the agent harness itself add on top of the raw model." Generalizing the runner once means the next harness is a small adapter, not another bespoke module.
**Success Metrics**: From `uv run bench --mode agent --agent <claude-code|qwen-code>` FX can run either CLI unattended against the shared task suites, with each run materialized into an isolated workspace, executed with explicit non-interactive + permission flags, scored by the same sandbox tests as Codex/endpoint completions, and written to agent-mode JSONL that appears in the leaderboard's agent section — with cost recorded when the harness reliably exposes it and marked unavailable otherwise.

## Epic Scope
**Total Stories**: 3 | **Total Points**: 11 | **MVP Stories**: 0 (Should Have / v1.x)

## Decisions Locked With FX
- **One pluggable adapter, not N modules**: refactor the Codex-specific runner into a harness-adapter registry (command builder + output parser + detection) and migrate Codex to be the first adapter. Each new harness is one adapter, reusing the Epic-06 workspace + scoring + JSONL contract — the agent protocol is not forked per harness.
- **Manual install only** (same rule as Epic-08/13): the harness **detects** an installed agent CLI (read-only) and carries a reference `url`; it never installs one. An uninstalled harness is reported with its link.
- **Qwen Code = local-capable agent baseline**: it is OpenAI-compatible and points at a base URL/key, so it can drive the project's local inferencers (dflash/turboquant/mlx-lm/MTPLX, etc.) — making it the first agent harness that benchmarks the *same local model* the endpoint suite measures, isolating "agent overhead on top of the model."
- **Claude Code = frontier/cloud baseline, with a gated local path**: Claude Code does **not** natively target a local OpenAI-compatible endpoint (only Anthropic API / Bedrock / Vertex / Foundry). Its primary role is the cloud frontier agent baseline; driving a *local* model with it is only possible through an Anthropic-compatible gateway/shim (`ANTHROPIC_BASE_URL`/`ANTHROPIC_API_KEY`) and is treated as a separate, explicitly-flagged condition, not the default.
- **Non-interactive + sandboxed by default**: every harness runs one-shot/print mode with locked-down permissions and explicit allowed tools; never the unsafe bypass flags in a benchmark run.
- **Cost honesty per harness**: Claude Code reliably exposes `total_cost_usd` via `--output-format json` against the Anthropic API → record it; Codex and any local/gateway run where usage is absent → `cost_status=unavailable`, consistent with Epic-06.

## Scope Boundaries (explicitly NOT building)
- **No new scoring/task protocol** — reuses Epic-06's materializer, sandbox scoring, and agent-mode JSONL; this epic only adds harness adapters.
- **No agent installation** — detects and drives CLIs FX installed by hand.
- **No bespoke Anthropic shim** — if a local Claude Code run needs an Anthropic-compatible gateway, that gateway is an external dependency FX runs; this epic only wires the env-var path and flags the condition.

## Features in This Epic

### Feature 14.1: Pluggable Agent-Harness Adapter

#### Stories

##### Story 14.1-001: Generalize the Codex-only runner into a harness-adapter registry
**User Story**: As FX, I want the agent runner to support multiple coding-agent CLIs through a small adapter interface so that adding a new harness is one adapter, not a new bespoke module.
**Priority**: Should Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** `configs/agents.yaml` **When** it is loaded **Then** `type` accepts a registered set of harness kinds (not just `codex`), an unknown type is rejected with a `ConfigError` listing the supported kinds, and an optional `url` reference field parses (default none).
- **Given** the existing Codex configuration **When** the registry refactor lands **Then** Codex behaves identically (same `codex exec` command, metadata, and scoring) — it is simply the first registered adapter, with a regression test proving no behavior change.
- **Given** a harness adapter **When** the runner invokes it **Then** the adapter supplies a non-interactive command builder (working dir, prompt, permission/sandbox flags, model, output paths) and an output parser (final result, exit handling, and usage/cost when present), behind one interface the runner calls uniformly.
- **Given** an installed-agent check **When** detection runs **Then** each harness reports installed/not-installed read-only (e.g. `shutil.which` on its `command`), and an uninstalled harness surfaces its `url` rather than any install action.
- **Given** `uv run bench --mode agent --agent <name>` **When** an unknown agent name is given **Then** the CLI errors clearly and lists configured agents (preserving 06.1-004 behavior).

**Technical Notes**: Replace `AgentType = Literal["codex"]` with a registry-backed validation in `config.py` (`_parse_agent` consults the registered harness kinds), and add optional `url` via `_optional_str`. Introduce an adapter protocol in `agents.py` (e.g. `build_command(agent, workspace) -> list[str]` + `parse_result(stdout, exit_code) -> dict`), refactor `build_codex_command` into a `CodexAdapter`, and route `run` through the selected adapter. Keep OpenCode's existing `opencode/invoke.py` path working (either register it as an adapter or leave it routed as-is, documented). Cover with the existing fake-executable test pattern (`tests/test_agents.py`): assert Codex output is byte-for-byte unchanged and that an unknown type/agent errors.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing (Codex regression + unknown-type/agent errors)
- [ ] Documentation updated

**Dependencies**: 06.1-002 (Codex runner to refactor), 06.1-004 (agent CLI)
**Risk Level**: Medium

### Feature 14.2: Claude Code Harness

#### Stories

##### Story 14.2-001: Claude Code agent harness (frontier baseline + gated local path)
**User Story**: As FX, I want to benchmark Claude Code as a coding agent so that I have a frontier agent-harness baseline scored on the same tasks as Codex and the local models.
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** a Claude Code agent entry **When** the harness runs a task **Then** it invokes `claude` non-interactively in the materialized workspace with `-p/--print`, `--output-format json`, an explicit non-interactive permission mode plus allowed tools, and a selected `--model`, never an unsafe bypass flag.
- **Given** the JSON result **When** the run completes **Then** the harness records the final result, `session_id`, and `total_cost_usd`/usage when present; if usage is absent (e.g. a gateway/local run), `cost_status=unavailable` is recorded.
- **Given** Claude Code cannot natively target a local OpenAI endpoint **When** a local-model run is requested **Then** it is supported only via the Anthropic-compatible gateway path (`ANTHROPIC_BASE_URL`/`ANTHROPIC_API_KEY`) and recorded as a distinct, explicitly-flagged condition; the default Claude Code run is the cloud frontier baseline.
- **Given** Claude Code is not installed or `claude` is missing **When** detection runs **Then** it reports not-installed and surfaces the docs `url`, attempting no install.
- **Given** a timeout or nonzero exit **When** the run completes **Then** the task is recorded as an infra failure and the benchmark continues (Epic-06 fault-tolerance contract).

**Technical Notes**: Add a `ClaudeCodeAdapter` (command kind `claude-code`, `command: claude`, `url: https://code.claude.com/docs`). Prefer a locked-down invocation: `--print --output-format json --permission-mode dontAsk --allowedTools "Read,Edit,Bash"` and `--model <id-or-alias>`; consider `--bare` for reproducibility (skips hook/skill/MCP/CLAUDE.md discovery) and pass `ANTHROPIC_API_KEY` via env rather than keychain. Parse `total_cost_usd`/`usage` from the JSON; treat their absence as unavailable. Never store API keys in JSONL/metadata. Test with a fake `claude` executable emitting representative JSON, mirroring the Codex fake-exec tests.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing (fake `claude`, cost present/absent, infra-failure path)
- [ ] Documentation updated (cloud-baseline role + gateway caveat for local models)

**Dependencies**: 14.1-001
**Risk Level**: Medium

### Feature 14.3: Qwen Code Harness

#### Stories

##### Story 14.3-001: Qwen Code agent harness (drives local and cloud OpenAI-compatible models)
**User Story**: As FX, I want to benchmark Qwen Code against my local models so that I can measure an agent harness on the exact same local model the endpoint suite uses, isolating agent overhead.
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** a Qwen Code agent entry **When** the harness runs a task **Then** it invokes `qwen` non-interactively in the materialized workspace with `-p/--prompt` and `--output-format json`, with optional `--system-prompt`/`--append-system-prompt` for run-specific instructions.
- **Given** Qwen Code is OpenAI-compatible **When** a local model is targeted **Then** it is pointed at a local inferencer's base URL/key (the same engine the endpoint suite measures), so the run benchmarks the agent on the identical local model.
- **Given** the JSON result **When** the run completes **Then** the harness records the final result and usage when present; absent usage → `cost_status=unavailable` ($0 for local).
- **Given** Qwen Code is not installed **When** detection runs **Then** it reports not-installed and surfaces the repo/docs `url`, attempting no install.
- **Given** a timeout or nonzero exit **When** the run completes **Then** the task is recorded as an infra failure and the benchmark continues.

**Technical Notes**: Add a `QwenCodeAdapter` (command kind `qwen-code`, `command: qwen`, `url: https://github.com/QwenLM/qwen-code`). Use `--prompt/-p` + `--output-format json` (JSON is buffered and emitted at session end). Configure the OpenAI-compatible endpoint via Qwen Code's settings/env (base URL + key) so it can target a local inferencer; this is the first agent harness that reuses the local model path directly. Test with a fake `qwen` executable emitting representative JSON.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing (fake `qwen`, local-endpoint config, infra-failure path)
- [ ] Documentation updated

**Dependencies**: 14.1-001
**Risk Level**: Medium

## Epic Progress
**Completed**: 0 / 3 stories · 0 / 11 points
- 14.1-001 ⬜ · 14.2-001 ⬜ · 14.3-001 ⬜
- Extends the agent-harness lineage: Codex (Epic-06) and OpenCode (Epic-10) are the existing harnesses; this epic generalizes the runner and adds Claude Code (frontier/cloud baseline) and Qwen Code (OpenAI-compatible, drives local models).
