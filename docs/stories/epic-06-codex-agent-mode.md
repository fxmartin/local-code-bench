# Epic 6: Codex Agent Mode

## Epic Overview
**Epic ID**: Epic-06
**Description**: Add MVP support for benchmarking Codex CLI as an agent, separate from endpoint-mode model benchmarks. The harness materializes each benchmark task into an isolated workspace, invokes `codex exec` non-interactively with explicit sandbox settings, captures run metadata/output, and scores the resulting code with the same sandboxed correctness path used for endpoint completions.
**Business Value**: Makes Codex a first-class baseline in the repo rather than treating the project as Claude-only or endpoint-only. FX can compare raw model endpoint performance against a real coding-agent workflow while keeping the protocols distinct enough to be defensible.
**Success Metrics**: A bounded Codex agent run completes unattended against the shared task abstraction, writes agent-mode JSONL records, and appears in the generated leaderboard's agent section.

## Epic Scope
**Total Stories**: 4 | **Total Points**: 13 | **MVP Stories**: 4

## Features in This Epic

### Feature 6.1: Codex Agent Benchmarking

#### Stories

##### Story 06.1-001: Codex task workspace materializer
**User Story**: As a tinkerer, I want each benchmark task converted into an isolated workspace so that Codex can edit files like a coding agent instead of returning only a chat completion.
**Priority**: Must Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** a HumanEval or MBPP task **When** materialized for agent mode **Then** a temp workspace contains a prompt/instructions file, an editable solution file, and the task tests in a deterministic layout.
- **Given** the same task and seed **When** materialized twice **Then** file names, instructions, and test content are stable except for the temp directory path.
- **Given** a completed or failed agent run **When** cleanup runs **Then** temp workspaces are removed unless debug retention is explicitly requested.

**Technical Notes**: Reuses Epic-02's task abstraction and sandbox cleanup expectations. Do not mix agent workspaces with endpoint JSONL output files.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: 02.1-001, 02.1-002
**Risk Level**: Medium

##### Story 06.1-002: Codex `exec` runner
**User Story**: As a tinkerer, I want the harness to run Codex through `codex exec` so that Codex is measured through its scriptable agent interface.
**Priority**: Must Have
**Story Points**: 4

**Acceptance Criteria**:
- **Given** `configs/agents.yaml` with a Codex target **When** the runner starts **Then** it builds a `codex exec` command with explicit working directory, sandbox mode, timeout, optional model/profile, and output capture paths.
- **Given** Codex exits 0 **When** the run completes **Then** stdout/stderr, final message, wall time, exit code, CLI version, sandbox mode, and command metadata are recorded without secrets.
- **Given** Codex times out or exits nonzero **When** the run completes **Then** the task is recorded as an infra failure and the overall benchmark continues.

**Technical Notes**: Use `codex exec` non-interactive mode. Prefer `--sandbox workspace-write`; never use dangerous bypass flags in benchmark runs.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing with a fake `codex` executable
- [x] Documentation updated

**Dependencies**: 06.1-001, 01.2-003
**Risk Level**: Medium

##### Story 06.1-003: Agent-mode scoring
**User Story**: As a tinkerer, I want Codex's edited workspace scored by the same tests as endpoint completions so that agent-mode pass/fail is comparable at the task level.
**Priority**: Must Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** a Codex-edited solution file **When** scored **Then** the sandbox runner executes the benchmark tests and records pass/fail plus failure reason.
- **Given** no usable solution output **When** scoring starts **Then** the task is recorded as failed with a clear extraction/output reason.
- **Given** agent-mode results **When** written to JSONL **Then** they identify `run_mode=agent`, `agent=codex`, task id, pass/fail, wall time, sandbox mode, and `cost_status=unavailable` unless reliable usage data exists.

**Technical Notes**: Endpoint token metrics and agent wall-clock metrics are not interchangeable; keep them as separate result fields.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: 06.1-002, 02.2-001, 02.2-002
**Risk Level**: Medium

##### Story 06.1-004: Agent-mode CLI
**User Story**: As a tinkerer, I want `uv run bench --mode agent --agent codex` so that I can run Codex against a suite without changing code.
**Priority**: Must Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** `uv run bench --mode agent --agent codex --suite humaneval --limit N` **When** run **Then** only the requested tasks run through Codex and write agent-mode JSONL records.
- **Given** endpoint mode **When** run without `--mode agent` **Then** existing endpoint behavior remains the default.
- **Given** an unknown agent name **When** run **Then** the CLI errors clearly and lists configured agents.

**Technical Notes**: Agent selection belongs in runner/config code, not in endpoint provider adapters.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: 06.1-003, 03.3-002
**Risk Level**: Low

## Epic Progress
**Completed**: 4 / 4 stories · 13 / 13 points
