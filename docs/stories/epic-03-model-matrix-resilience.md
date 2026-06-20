# Epic 3: Full Model Matrix & Resilience

## Epic Overview
**Epic ID**: Epic-03
**Description**: Wire all five v1 backends behind the provider interface — two local MLX servers, two OpenRouter cloud models, and the Claude Anthropic-API baseline — then make a full multi-backend run trustworthy: cost per task, reproducibility metadata, and fault tolerance so one flaky model can't sink the run.
**Business Value**: Turns the harness from "measure one model" into "rank the field," and adds the cost axis that answers half the project thesis — *is local actually worth it vs. paying for GLM/Kimi?*
**Success Metrics**: An unattended run across all 5 backends survives a deliberately killed local server (REQUIREMENTS §7 Phase 2 milestone).

## Epic Scope
**Total Stories**: 7 | **Total Points**: 22 | **MVP Stories**: 7

## Features in This Epic

### Feature 3.1: Provider Coverage

#### Stories

##### Story 03.1-001: OpenRouter backends (GLM 4.6, Kimi K2)
**User Story**: As a tinkerer, I want GLM 4.6 and Kimi K2 reachable via OpenRouter so that I can compare local models against frontier cloud options on the same suite.
**Priority**: Must Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** `OPENROUTER_API_KEY` in env/`.env` **When** I target an OpenRouter backend **Then** the existing OpenAI-compatible adapter works with only base-URL/key/model config (no new code).
- **Given** a missing/invalid key **When** I run **Then** it fails with a clear, secret-safe error (never logs the key).

**Technical Notes**: Reuses Epic-01's adapter. Secrets via env/`.env` only, never committed (gitleaks already enforced).

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: Epic-01 (01.2-001)
**Risk Level**: Low

##### Story 03.1-002: Anthropic baseline adapter
**User Story**: As a tinkerer, I want Claude reachable via the Anthropic API so that I have a frontier baseline to measure the gap against.
**Priority**: Must Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** `ANTHROPIC_API_KEY` **When** I target the Claude baseline **Then** a separate adapter (Anthropic message format) returns the same normalized response + token usage as the OpenAI adapter, behind the shared interface.
- **Given** the baseline **When** measured **Then** the same metrics (TTFT/latency/tokens/cost) are captured as for every other backend.

**Technical Notes**: The only non-OpenAI endpoint — explicit special case noted in REQUIREMENTS §5 P0-2. Streaming for TTFT parity.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing (mocked)
- [ ] Documentation updated

**Dependencies**: Epic-01 (01.2-002)
**Risk Level**: Medium

##### Story 03.1-003: Local MLX backends + bring-up script
**User Story**: As a tinkerer, I want the two local MLX servers configured and a bring-up script so that I can start `dflash`/`turboquant` and point the harness at them.
**Priority**: Must Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** a running `dflash serve` (Qwen3.6-27B 4-bit + DFlash) on its port **When** I target it **Then** the harness measures it like any OpenAI-compatible backend.
- **Given** a running `turboquant-serve` (Qwen3.6-35B-A3B MoE) **When** I target it **Then** it works identically; the resident memory footprint is captured and checked against the 48 GB ceiling (REQUIREMENTS §8 top risk).
- **Given** the bring-up script **When** I run it **Then** it starts a server idempotently and prints readiness — but the harness never auto-manages the server lifecycle (orchestration is out of scope).

**Technical Notes**: Bring-up *script* only (allowed); no orchestration. Verify the MoE actually fits 48 GB early — record an OOM as a finding, not a crash.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing (config/contract level; live server manual)
- [ ] Documentation updated (bring-up steps)

**Dependencies**: Epic-01 (01.2-001)
**Risk Level**: Medium

### Feature 3.2: Cost & Reproducibility

#### Stories

##### Story 03.2-001: Per-task cost calculation
**User Story**: As a tinkerer, I want $ cost per task for cloud backends so that local-vs-cloud lands on one comparable axis.
**Priority**: Must Have
**Story Points**: 2

**Acceptance Criteria**:
- **Given** token counts and the dated price table in `models.yaml` **When** a cloud task completes **Then** cost = (in_tokens·in_price + out_tokens·out_price) is recorded in the JSONL line.
- **Given** a local backend **When** measured **Then** cost is recorded as $0.
- **Given** stored JSONL **When** prices later change **Then** cost is recomputable offline from token counts (REQUIREMENTS §8 price-drift mitigation).

**Technical Notes**: Pure function of tokens × price data — unit-testable without any live call (NFR-QUAL-001).

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 01.2-003
**Risk Level**: Low

##### Story 03.2-002: Reproducibility metadata
**User Story**: As a reproducer, I want each run to record seed/temp, pinned model revisions, suite version, hardware tag, and timestamp so that the numbers are defensible.
**Priority**: Must Have
**Story Points**: 2

**Acceptance Criteria**:
- **Given** a run **When** it starts **Then** a metadata header/record captures fixed seed, temperature, each backend's pinned revision, suite + version, hardware tag (M3 Max 48 GB), and timestamp.
- **Given** two runs of the same config **When** compared **Then** the metadata is identical except timestamp.

**Technical Notes**: Implements REQUIREMENTS P0-9. Metadata travels with the JSONL so re-scoring (Epic-04) is self-describing.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 01.2-003
**Risk Level**: Low

### Feature 3.3: Resilience

#### Stories

##### Story 03.3-001: Fault tolerance across backends
**User Story**: As a tinkerer, I want a flaky backend logged and scored 0 without aborting the run so that one bad model doesn't waste the whole benchmark.
**Priority**: Must Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** a backend that times out, errors, or returns malformed output **When** encountered **Then** the task is logged with the failure reason, scored 0, and the run continues to the next task/backend (REQUIREMENTS P0-7).
- **Given** a local server killed mid-run **When** the next request fails **Then** remaining tasks for that backend are marked failed-infra and other backends complete normally.
- **Given** the run end **When** summarized **Then** model failures and infra failures are reported distinctly.

**Technical Notes**: This is the Phase 2 milestone behavior. Weak local models emitting malformed code is a *signal*, not a bug (REQUIREMENTS §8).

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing (injected timeout/error/malformed)
- [ ] Documentation updated

**Dependencies**: 02.2-003, 03.1-001..003
**Risk Level**: Medium

##### Story 03.3-002: Run a subset of backends
**User Story**: As a tinkerer, I want to run/skip specific backends so that I can iterate quickly without hitting every model.
**Priority**: Must Have
**Story Points**: 2

**Acceptance Criteria**:
- **Given** `--model a,b` or `--skip c` **When** I run **Then** only the selected backends are measured.
- **Given** no selection **When** I run **Then** all configured backends run (full matrix).

**Technical Notes**: Partial of REQUIREMENTS P1-3 (resume is Epic-05). Keep selection logic in the runner, not the adapters.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 02.2-003
**Risk Level**: Low

## Epic Progress
**Completed**: 0 / 7 stories · 0 / 22 points
