# Epic 4: Results & Leaderboard

## Epic Overview
**Epic ID**: Epic-04
**Description**: Turn raw `results/<run>.jsonl` into a publishable `LEADERBOARD.md` with distinct endpoint-model and Codex-agent sections, regenerable offline where enough raw output exists, and document the manual server/Codex bring-up so a visitor can reproduce a run.
**Business Value**: The leaderboard is what makes the public repo self-demonstrating and answers the project's core questions ("best local model per second on 48 GB"; "when does local beat cloud on cost"). Offline re-scoring means a price change or a new ranking formula costs zero model calls.
**Success Metrics**: A committed `LEADERBOARD.md` regenerates from stored endpoint and agent results; endpoint runs are re-scorable offline and Codex agent runs are summarized from recorded task outputs (REQUIREMENTS §7 Phase 3 milestone).

## Epic Scope
**Total Stories**: 3 | **Total Points**: 10 | **MVP Stories**: 3

## Features in This Epic

### Feature 4.1: Leaderboard Generation

#### Stories

##### Story 04.1-001: `LEADERBOARD.md` generator
**User Story**: As a tinkerer, I want a `LEADERBOARD.md` generated from results so that I can see at a glance which endpoint model wins on correctness, speed, and cost, and how Codex performs as an agent baseline.
**Priority**: Must Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** one or more endpoint `results/*.jsonl` **When** I run the generator **Then** `LEADERBOARD.md` is written with per-model pass@1, median latency, prefill tok/s, decode tok/s, and $/task, ranked.
- **Given** one or more Codex agent result records **When** I run the generator **Then** `LEADERBOARD.md` includes a separate agent section with pass@1, wall time, failure counts, sandbox mode, and cost availability.
- **Given** the ranking **When** generated **Then** the ranking key is explicit and documented (e.g. correctness floor, then a speed/cost composite), not a hidden heuristic.
- **Given** model vs. infra failures in the data **When** rendered **Then** they are reflected (e.g. pass@1 over attempted, infra failures noted) rather than silently dropped.

**Technical Notes**: Pure transform of JSONL → Markdown (REQUIREMENTS P0-8). Do not combine endpoint token metrics and agent wall-clock metrics into one hidden composite. Charts are explicitly v2 (P2-4) — table only.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing (fixture JSONL → expected table)
- [x] Documentation updated

**Dependencies**: Epic-03 (full run produces complete JSONL)
**Risk Level**: Low

##### Story 04.1-002: Offline re-score / regenerate
**User Story**: As a reproducer, I want to regenerate scores and the leaderboard from stored JSONL so that I never re-run models just to change the view or update prices.
**Priority**: Must Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** stored endpoint JSONL with raw responses **When** I re-score offline **Then** pass@1 is recomputed in the sandbox without any model calls.
- **Given** stored Codex agent outputs **When** I regenerate **Then** agent leaderboard rows are rebuilt from recorded pass/fail and metadata without re-running Codex.
- **Given** an updated price table **When** I regenerate **Then** $/task and the leaderboard update from existing token counts (REQUIREMENTS P1-2).

**Technical Notes**: Requires raw responses + token counts to be in the JSONL (Epic-01/03 guarantee this). Re-score reuses the Epic-02 sandbox path.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: 04.1-001, 02.2-002, 03.2-001
**Risk Level**: Low

### Feature 4.2: Documentation

#### Stories

##### Story 04.2-001: README with server bring-up guide
**User Story**: As a reproducer, I want a README documenting prerequisites and manual server bring-up so that I can stand up the local servers and run the benchmark myself.
**Priority**: Must Have
**Story Points**: 2

**Acceptance Criteria**:
- **Given** the README **When** I follow it **Then** I can install deps (`uv sync`), set the required API keys, bring up `dflash`/`turboquant` (or use the bring-up script), authenticate Codex CLI, and run endpoint or Codex agent benchmarks.
- **Given** the README **When** read **Then** it states the v1 scope and limitations: endpoint mode is single-turn, Codex is the only MVP agent loop, and Claude Code agent-loop benchmarking is deferred (REQUIREMENTS §4).

**Technical Notes**: Documents the manual bring-up required by REQUIREMENTS §6 DoD #7. Link to `REQUIREMENTS.md` and `STORIES.md`.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing (N/A — docs; verify commands run)
- [x] Documentation updated

**Dependencies**: Epic-03 (03.1-003 bring-up script)
**Risk Level**: Low

## Epic Progress
**Completed**: 3 / 3 stories · 10 / 10 points
