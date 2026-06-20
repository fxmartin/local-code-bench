# Non-Functional Requirements

## Overview
**Total Stories**: 5 | **Total Points**: 12

These cross-cutting requirements govern the functional epics. NFR-SEC-001 and NFR-QUAL-001 are **MVP** (they constrain Epics 02–03); the rest are validated throughout.

## Performance Requirements

### Story NFR-PERF-001: Accurate, low-overhead measurement
**User Story**: As a tinkerer, I want measurement overhead to be negligible vs. model latency so that the reported numbers reflect the model, not the harness.
**Priority**: Must Have
**Story Points**: 2

**Acceptance Criteria**:
- **Given** a streamed response **When** TTFT is measured **Then** it is timed at the stream boundary, accurate to ~10 ms, excluding JSON-parsing overhead.
- **Given** any run **When** measured **Then** harness CPU/wall overhead per task is negligible relative to model latency and is not attributed to the model.

**Technical Notes**: Validated against a synthetic timed stream (no live model). Governs Epic-01 (01.2-002).

**Definition of Done**:
- [ ] Implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: Epic-01 (01.2-002)
**Risk Level**: Medium

## Security Requirements

### Story NFR-SEC-001: Sandbox isolation & secret hygiene
**User Story**: As a tinkerer, I want untrusted generated code sandboxed and API keys never exposed so that running the benchmark can't harm my machine or leak credentials.
**Priority**: Must Have (MVP)
**Story Points**: 3

**Acceptance Criteria**:
- **Given** generated code **When** executed **Then** it runs only in an isolated temp dir + subprocess with timeout and **no network**, and cannot write outside that dir.
- **Given** API keys **When** used **Then** they are read from env/`.env` only, never committed, never logged (gitleaks pre-commit enforced).
- **Given** an error from a cloud backend **When** logged **Then** the message contains no secret material.

**Technical Notes**: Realized by Epic-02 (02.2-001) and Epic-03 (03.1-001/002). This is the project's highest-impact risk control (REQUIREMENTS §8).

**Definition of Done**:
- [ ] Implemented and peer reviewed
- [ ] Tests written and passing (timeout, sandbox escape attempt, secret-redaction)
- [ ] Documentation updated

**Dependencies**: Epic-02 (02.2-001)
**Risk Level**: High

## Quality Requirements

### Story NFR-QUAL-001: TDD coverage of harness logic
**User Story**: As a developer, I want the metrics, scoring, and cost logic unit-tested independent of live models so that the numbers are trustworthy and regressions are caught.
**Priority**: Must Have (MVP)
**Story Points**: 3

**Acceptance Criteria**:
- **Given** the harness logic **When** tests run **Then** metrics parsing (TTFT/tok/s), pass@1 scoring, and cost calculation are covered with mocked/synthetic inputs — no live model required.
- **Given** fault-tolerance paths **When** tested **Then** timeout, malformed-output, and backend-error cases are exercised and assert score 0 + continue.
- **Given** the repo **When** committed **Then** tests pass (no `--no-verify`) per global TDD standard.

**Technical Notes**: Cuts across all epics; mock the streamed response and the sandbox boundary. Aligns with CLAUDE.md Testing Strategy.

**Definition of Done**:
- [ ] Implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: Epics 01–03
**Risk Level**: Medium

## Integration Requirements

### Story NFR-INT-001: OpenAI-compatible protocol portability
**User Story**: As a reproducer, I want the harness to depend only on the OpenAI-compatible protocol (plus the one Anthropic special case) so that any conforming server can be benchmarked without code changes.
**Priority**: Should Have
**Story Points**: 2

**Acceptance Criteria**:
- **Given** any OpenAI-compatible `/v1/chat/completions` server **When** added to `models.yaml` **Then** it is measurable with no code change.
- **Given** the Anthropic baseline **When** added **Then** it is the only documented special-case adapter.

**Technical Notes**: Protects the core design constraint (REQUIREMENTS §4). Validated by Epic-01 + Epic-03 provider coverage.

**Definition of Done**:
- [ ] Implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: Epic-01 (01.2-001), Epic-03 (03.1-*)
**Risk Level**: Low

## Infrastructure Requirements

### Story NFR-INF-001: 48 GB hardware-fit verification
**User Story**: As a tinkerer, I want the harness to capture and check resident memory for local models so that I learn early whether the 35B-A3B MoE actually fits 48 GB.
**Priority**: Should Have
**Story Points**: 2

**Acceptance Criteria**:
- **Given** a local MLX backend **When** measured **Then** peak resident memory is captured in run metadata.
- **Given** the 35B-A3B MoE **When** it cannot fit 48 GB **Then** the outcome is recorded as a finding (with fallback guidance) rather than an unhandled crash (REQUIREMENTS §8 top risk).

**Technical Notes**: Single fixed machine (M3 Max 48 GB); no multi-machine support. Pairs with Epic-03 (03.1-003).

**Definition of Done**:
- [ ] Implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: Epic-03 (03.1-003)
**Risk Level**: Medium
