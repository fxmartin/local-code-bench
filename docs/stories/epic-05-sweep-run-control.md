# Epic 5: Sweep Mode & Run Control

## Epic Overview
**Epic ID**: Epic-05
**Description**: The v1.x stretch epic. Add "sweep mode" — pad a realistic agentic system preamble to 2k/8k/16k/24k tokens and measure prefill-vs-context — to independently reproduce or refute the source articles' central thesis on this hardware (M3 Max 48 GB). Plus run-control quality-of-life: resume a partial run.
**Business Value**: Directly tests the project's intellectual core — *is agentic coding prefill-bound, and does the MoE win?* — on FX's actual machine, not the articles' 64 GB. Resume saves wasted model time on long runs.
**Success Metrics**: The prefill-vs-context curve is reproduced across the configured backends (REQUIREMENTS §7 Phase 4 milestone).

## Epic Scope
**Total Stories**: 3 | **Total Points**: 9 | **MVP Stories**: 0 (Should Have / v1.x)

## Features in This Epic

### Feature 5.1: Sweep Mode

#### Stories

##### Story 05.1-001: Agentic-preamble padding sweep
**User Story**: As a tinkerer, I want to send a tiny question behind a system preamble padded to 2k/8k/16k/24k tokens so that I can isolate the prefill-vs-context curve that governs agentic coding.
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** `--mode sweep` **When** I run a backend **Then** it sends the same small question behind preambles padded to each target size (2k/8k/16k/24k) and records TTFT/prefill tok/s per size.
- **Given** the padded prompts **When** generated **Then** padding is deterministic and token-count-accurate per backend tokenizer.

**Technical Notes**: Mirrors the Part 2 "sweep mode." Reuses Epic-01 metrics; only the prompt construction is new (REQUIREMENTS P1-1).

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: Epic-01 (01.2-002), Epic-03 (03.1-*)
**Risk Level**: Low

##### Story 05.1-002: Prefill-vs-context curve output
**User Story**: As a tinkerer, I want sweep results summarized as a prefill-vs-context table so that I can compare TTFT across context sizes and models.
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** completed sweep runs **When** summarized **Then** a table reports TTFT and prefill tok/s by model × context size, with the dense-vs-MoE comparison highlighted.
- **Given** the table **When** generated **Then** it states whether this hardware reproduces the articles' MoE-prefill-advantage finding.

**Technical Notes**: Table only; charts remain v2 (P2-4). Feeds the project's headline conclusion.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: 05.1-001, Epic-04 (04.1-001 generator patterns)
**Risk Level**: Low

### Feature 5.2: Run Control

#### Stories

##### Story 05.2-001: Resume a partial run
**User Story**: As a tinkerer, I want to resume an interrupted run so that I don't re-pay for tasks already completed.
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** a `results/<run>.jsonl` with some tasks done **When** I resume with the same run id **Then** completed (model×task) pairs are skipped and only remaining ones run.
- **Given** a resumed run **When** it finishes **Then** the JSONL is a complete, non-duplicated record.

**Technical Notes**: Completes REQUIREMENTS P1-3 (subset was 03.3-002). Keyed on (run id, model, task id) from existing JSONL.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: 01.2-003, 03.3-002
**Risk Level**: Low

## Epic Progress
**Completed**: 3 / 3 stories · 9 / 9 points
