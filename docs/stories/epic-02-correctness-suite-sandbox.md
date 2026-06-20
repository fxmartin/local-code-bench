# Epic 2: Correctness Suite & Sandbox

## Epic Overview
**Epic ID**: Epic-02
**Description**: Add the automated-correctness half of the v1 thesis. Load the HumanEval and MBPP benchmarks, run each endpoint model's generated code or Codex agent workspace output against the benchmark's own unit tests inside an isolated sandbox, and score pass@1 at temperature 0 where endpoint generation is used. Speed without correctness is a vanity metric for a coding tool — this epic makes "did it actually work?" a number.
**Business Value**: Filters out fast-but-wrong models and produces externally comparable, credible correctness numbers on a public repo.
**Success Metrics**: pass@1 scored for one backend on the full HumanEval suite (REQUIREMENTS §7 Phase 1 milestone).

## Epic Scope
**Total Stories**: 5 | **Total Points**: 16 | **MVP Stories**: 5

## Features in This Epic

### Feature 2.1: Benchmark Loaders

#### Stories

##### Story 02.1-001: HumanEval loader
**User Story**: As a tinkerer, I want HumanEval's 164 problems loaded with their prompts and test code so that I can score correctness against a standard suite.
**Priority**: Must Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** the HumanEval dataset **When** the loader runs **Then** each problem yields task_id, prompt, canonical entry point, and the provided test code.
- **Given** no network/offline **When** the dataset is already cached **Then** loading succeeds from cache.
- **Given** the suite version **When** a run starts **Then** the version is recorded in run metadata (REQUIREMENTS P0-9).

**Technical Notes**: Fetch once, cache locally (`.cache/`, gitignored). Keep the loader interface shared with MBPP so endpoint and Codex agent runners are suite-agnostic.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: Epic-01 (01.1-001)
**Risk Level**: Low

##### Story 02.1-002: MBPP loader
**User Story**: As a tinkerer, I want MBPP loaded behind the same interface so that I get correctness spread beyond saturated HumanEval.
**Priority**: Must Have
**Story Points**: 2

**Acceptance Criteria**:
- **Given** the MBPP (sanitized) dataset **When** the loader runs **Then** each problem yields prompt, test list, and expected entry point via the shared task interface.
- **Given** both suites configured **When** a run is requested **Then** the runner can target either or both.

**Technical Notes**: MBPP addresses the HumanEval-saturation risk (REQUIREMENTS §8). Reuse the 02.1-001 task abstraction.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: 02.1-001
**Risk Level**: Low

### Feature 2.2: Sandboxed Scoring

#### Stories

##### Story 02.2-001: Sandboxed code execution runner
**User Story**: As a tinkerer, I want generated code executed in an isolated sandbox so that an untrusted model can't wedge, pollute, or compromise my Mac.
**Priority**: Must Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** generated code or an agent-edited solution + its test **When** executed **Then** it runs in a temp dir in a subprocess with a wall-clock timeout, no network access, and no write access outside the temp dir.
- **Given** code that infinite-loops **When** executed **Then** the timeout fires, the process is killed, and the task is recorded as failed (not hung).
- **Given** a completed sandbox run **When** it finishes **Then** the temp dir is cleaned up regardless of outcome.

**Technical Notes**: Subprocess with `timeout`; pin the sandbox Python. This story implements the security-critical NFR-SEC-001. Network blocking is for the scored generated code, not necessarily for the Codex process that produces edits.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing (incl. timeout + malicious-write attempt)
- [x] Documentation updated

**Dependencies**: Epic-01 (01.1-001)
**Risk Level**: High

##### Story 02.2-002: pass@1 scoring at temperature 0
**User Story**: As a tinkerer, I want pass@1 computed by running the benchmark's tests so that correctness is deterministic and standard.
**Priority**: Must Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** a model's single completion at temperature 0 **When** scored **Then** the task passes only if the benchmark's provided tests all pass in the sandbox.
- **Given** a completion needing extraction (code fences, prose) **When** scored **Then** the code is extracted deterministically before execution, and extraction failure is recorded as a fail with reason.
- **Given** the same model + suite + seed **When** re-run **Then** pass@1 reproduces exactly (REQUIREMENTS P0-5, P0-9).

**Technical Notes**: pass@1 only for v1 (pass@k deferred). Distinguish *model failure* (wrong/malformed) from *infra failure* (sandbox error) in the record.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: 02.2-001, 02.1-001
**Risk Level**: Medium

##### Story 02.2-003: Full-suite run against one backend
**User Story**: As a tinkerer, I want `uv run bench` to run a full suite against one backend unattended so that I reach the Phase 1 milestone.
**Priority**: Must Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** a backend and `--suite humaneval` **When** I run `uv run bench` **Then** all problems run end to end, each writing a JSONL line with metrics + pass/fail, and a summary prints pass@1.
- **Given** a long run **When** it executes **Then** progress is visible (per-task or counter) and the process can be interrupted without corrupting the JSONL.

**Technical Notes**: Extends 01.2-004 from single-prompt to suite iteration. Fault tolerance across backends is Epic-03 (03.3-001); here, a single backend on a full suite.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: 02.2-002, 01.2-003
**Risk Level**: Medium

## Epic Progress
**Completed**: 5 / 5 stories · 16 / 16 points
