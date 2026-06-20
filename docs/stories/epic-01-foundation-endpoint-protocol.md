# Epic 1: Foundation & Endpoint Protocol

## Epic Overview
**Epic ID**: Epic-01
**Description**: Establish the `uv` Python project, the config-driven model roster, and the core measurement loop: send a controlled single-turn prompt to any OpenAI-compatible `/v1/chat/completions` endpoint, stream the response, and capture speed metrics to raw JSONL. This is the spine every other epic builds on.
**Business Value**: Without a trustworthy, reproducible way to measure a single endpoint, no comparison is credible. This epic makes "measure a model" a one-command, data-driven operation.
**Success Metrics**: One model, one prompt, real metrics (TTFT, prefill tok/s, decode tok/s, latency, tokens) written to `results/<run>.jsonl` (REQUIREMENTS §7 Phase 0 milestone).

## Epic Scope
**Total Stories**: 6 | **Total Points**: 20 | **MVP Stories**: 6

## Features in This Epic

### Feature 1.1: Project Scaffold & Configuration

#### Stories

##### Story 01.1-001: `uv` project scaffold
**User Story**: As a developer, I want a standard `uv`-managed Python project skeleton so that the harness has a clean, reproducible dependency and entrypoint setup.
**Priority**: Must Have
**Story Points**: 2

**Acceptance Criteria**:
- **Given** a clean repo **When** I run `uv sync` **Then** the project and its dev dependencies (pytest, ruff) install without error.
- **Given** the project **When** I inspect it **Then** there is a `src/` package, a `pyproject.toml` with a `bench` CLI entrypoint stub, and `tests/`.
- **Given** the entrypoint stub **When** I run `uv run bench --help` **Then** it prints usage and exits 0.

**Technical Notes**: Python 3.x via `uv`. Single package under `src/`. No framework. Wire `ruff` to the global CLI-tools standard.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: None
**Risk Level**: Low

##### Story 01.1-002: `models.yaml` schema & loader
**User Story**: As a reproducer, I want every backend defined in `configs/models.yaml` so that adding a model is data, not code.
**Priority**: Must Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** a `models.yaml` entry with name, type (`openai`/`anthropic`), base URL, model id, pinned revision, and price-per-1k-tokens **When** the loader parses it **Then** it returns a validated, typed config object.
- **Given** a malformed/missing-field entry **When** the loader parses it **Then** it raises a clear, actionable validation error naming the offending field.
- **Given** a valid config with 5 backends **When** loaded **Then** a 6th can be added by editing YAML only, with no code change (REQUIREMENTS P0-1).

**Technical Notes**: Validate with a typed model (e.g. pydantic or dataclass + manual validation). Price table is dated data per backend for the cost calc in Epic-03.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 01.1-001
**Risk Level**: Low

### Feature 1.2: Endpoint Protocol & Metrics

#### Stories

##### Story 01.2-001: OpenAI-compatible streaming provider adapter
**User Story**: As a developer, I want a provider adapter that streams `/v1/chat/completions` so that the same code talks to local MLX servers and OpenRouter by swapping base URL + key.
**Priority**: Must Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** a config pointing at a running OpenAI-compatible endpoint **When** I send a prompt **Then** the adapter streams tokens and returns the full response plus raw token-usage data.
- **Given** the same adapter and a different base URL/key **When** I target OpenRouter **Then** no code changes are required (REQUIREMENTS §4 core constraint).
- **Given** temperature 0 in the request **When** supported by the backend **Then** it is passed through for determinism.

**Technical Notes**: Use the streaming SSE response to time first-token arrival. Keep the Anthropic baseline as a separate adapter (Epic-03) behind the same interface.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing (mocked stream)
- [ ] Documentation updated

**Dependencies**: 01.1-002
**Risk Level**: Medium

##### Story 01.2-002: Streaming metrics capture
**User Story**: As a tinkerer, I want per-turn TTFT, prefill tok/s, decode tok/s, latency, and token counts so that I can compare models on the metrics that govern how the agent feels.
**Priority**: Must Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** a streamed response **When** the first token arrives **Then** TTFT is recorded; prefill tok/s = prompt_tokens / TTFT.
- **Given** the remaining stream **When** it completes **Then** decode tok/s = completion_tokens / (total_time − TTFT), plus total latency and tokens in/out are recorded.
- **Given** a backend that omits usage data **When** measured **Then** token counts fall back to a local tokenizer estimate, flagged as estimated in metadata.

**Technical Notes**: TTFT accuracy target ~10 ms (NFR-PERF-001). Time at the stream boundary, not after JSON parsing.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing (synthetic timed stream)
- [ ] Documentation updated

**Dependencies**: 01.2-001
**Risk Level**: Medium

##### Story 01.2-003: JSONL results writer
**User Story**: As a reproducer, I want every run written as raw JSONL so that I can re-score and rebuild leaderboards without re-running models.
**Priority**: Must Have
**Story Points**: 2

**Acceptance Criteria**:
- **Given** a completed task measurement **When** it is recorded **Then** a JSON line with prompt, raw response, all metrics, tokens, and (later) pass/fail + cost is appended to `results/<run>.jsonl`.
- **Given** `results/` **When** a run starts **Then** the run file is uniquely named and never overwrites a prior run.

**Technical Notes**: One line per task. `results/` is gitignored. Schema stable enough that Epic-02 (pass/fail) and Epic-03 (cost) extend it additively.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 01.2-002
**Risk Level**: Low

##### Story 01.2-004: `bench` CLI single-model run
**User Story**: As a tinkerer, I want `uv run bench` to run a model against a prompt end to end so that I can verify the whole measurement chain works.
**Priority**: Must Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** a configured backend **When** I run `uv run bench --model <name> --prompt <text>` **Then** it streams, measures, and writes a JSONL line, printing a one-line summary.
- **Given** an unknown model name **When** I run it **Then** it errors clearly and lists available backends.

**Technical Notes**: This is the Phase 0 milestone vehicle. Full-suite orchestration arrives in Epic-02 (02.2-003).

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 01.2-003
**Risk Level**: Low

## Epic Progress
**Completed**: 0 / 6 stories · 0 / 20 points
