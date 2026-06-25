# Epic 7: Results Dashboard

## Epic Overview
**Epic ID**: Epic-07
**Description**: Add a Python-only dashboard surface for exploring benchmark JSONL results. The dashboard has two delivery modes: a committed static HTML artifact for sharing through the repo, and a CLI-served local view with live endpoints that read result files dynamically while FX investigates runs.
**Business Value**: The Markdown leaderboard answers the headline ranking question, but it is too flat for analysis. FX needs to inspect run history, per-task failures, cost/quality/speed tradeoffs, and sweep behavior without hand-reading JSONL or regenerating ad hoc tables. A dashboard makes benchmark results easier to trust, explain, and iterate on.
**Success Metrics**: A basic dashboard can be generated from stored JSONL, opened locally without a frontend build step, served from the CLI for live browsing, linked from the README, and used to answer "which model is good enough, fast enough, and cheap enough?" with sortable views and basic charts.

## Epic Scope
**Total Stories**: 6 | **Total Points**: 20 | **MVP Stories**: 0 (Post-v1 / v2)

## Features in This Epic

### Feature 7.1: Dashboard Data Model

#### Stories

##### Story 07.1-001: Dashboard result aggregation model
**User Story**: As FX, I want dashboard-ready aggregates built from endpoint, agent, and sweep JSONL files so that every dashboard view uses one consistent interpretation of the benchmark data.
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** one or more endpoint result JSONL files **When** dashboard data is loaded **Then** records are grouped by model, suite, run mode, and task id with pass rate, failure counts, median latency, TTFT, prefill/decode throughput, token counts, and cost available to consumers.
- **Given** agent-mode JSONL records **When** dashboard data is loaded **Then** agent rows expose pass rate, wall time, sandbox mode, exit status, and failure reasons without mixing agent wall-clock metrics into endpoint token-throughput metrics.
- **Given** sweep JSONL records **When** dashboard data is loaded **Then** context-size, TTFT, and prefill throughput are exposed for charting.
- **Given** malformed or partial JSONL input **When** loading dashboard data **Then** invalid records are reported as data-quality warnings rather than crashing the whole dashboard.

**Technical Notes**: Reuse the existing leaderboard and sweep parsing patterns where possible. Keep this as a pure Python transform with unit tests over fixture JSONL.

**Implementation**: `src/local_code_bench/dashboard_model.py` exposes `build_dashboard_data(records)` (pure transform) and `load_dashboard_data(paths)` (tolerant JSONL reader). Endpoint results aggregate by `(model, suite)` with per-task drilldown; agent results aggregate by `(agent, suite)` in a separate type so wall-clock metrics never mix into endpoint throughput; sweep records become charting-ready `SweepPoint`s. Unreadable lines and unrecognized records surface as `DataQualityWarning`s instead of crashing. Latest record per task/context wins, matching leaderboard dedupe semantics.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: 01.2-003, 04.1-001, 05.1-002, 06.1-003
**Risk Level**: Medium

### Feature 7.2: Static Dashboard Artifact

#### Stories

##### Story 07.2-001: Static HTML dashboard generator
**User Story**: As FX, I want to generate a static HTML dashboard from stored results so that I can commit it to the repo and link it from the README.
**Priority**: Should Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** stored result JSONL files **When** I run the dashboard generator **Then** it writes a self-contained HTML file with embedded CSS, embedded dashboard data, and no Node/Vite build step.
- **Given** the generated HTML file **When** I open it directly in a browser **Then** I can browse the core dashboard without running a local server.
- **Given** the dashboard output **When** committed to the repo **Then** it contains no API keys, `.env` contents, raw secrets, or host-sensitive paths.
- **Given** the README **When** updated **Then** it links to the generated dashboard artifact and documents the command that regenerates it.

**Technical Notes**: Python-only. Prefer a small HTML template rendered by Python over adding a frontend build chain. Use plain JavaScript only where it materially improves interactivity.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: 07.1-001
**Risk Level**: Medium

##### Story 07.2-002: CLI dashboard mode
**User Story**: As FX, I want `uv run bench --mode dashboard` to generate or serve the dashboard so that the dashboard workflow lives beside leaderboard, rescore, and sweep commands.
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** one or more `--input` result files **When** I run `uv run bench --mode dashboard --output results/dashboard.html` **Then** the static dashboard is generated.
- **Given** `--serve` or the project-equivalent serve option **When** I run dashboard mode **Then** a local HTTP server starts and prints the URL.
- **Given** invalid arguments **When** dashboard mode is invoked **Then** the CLI prints actionable errors consistent with existing CLI behavior.

**Technical Notes**: Match the existing argparse style in `cli.py`. Avoid a long-running server by default; static generation should remain the default path.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 07.2-001, 07.3-001
**Risk Level**: Low

### Feature 7.3: Live Local Dashboard

#### Stories

##### Story 07.3-001: Live results HTTP endpoints
**User Story**: As FX, I want the CLI-served dashboard to read result files through local HTTP endpoints so that I can refresh the browser while benchmark runs are still producing JSONL.
**Priority**: Should Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** a dashboard server pointed at one or more result files **When** I request the dashboard data endpoint **Then** it returns current dashboard aggregates as JSON.
- **Given** result files that grow while the server is running **When** I refresh the dashboard **Then** the server reflects newly appended records without restarting.
- **Given** missing, malformed, or partially written JSONL lines **When** the endpoint is requested **Then** the response includes data-quality warnings and still returns valid aggregates from readable records.
- **Given** the server is running **When** accessed from a browser on the same machine **Then** it serves only local dashboard assets and result-derived JSON.

**Technical Notes**: Python standard library HTTP server is acceptable for the first version. No authentication is required if the server binds to localhost only. Implemented in `src/local_code_bench/dashboard_server.py` as `serve_dashboard(paths, ...)`: a localhost-bound `http.server` whose handler holds only the result-file paths and rebuilds aggregates on every `GET /api/data` request, so appended records appear on refresh without a restart. Aggregation and tolerant JSONL reading (malformed/partial lines become data-quality warnings) are delegated to `dashboard_model.load_dashboard_data` (07.1-001) so the live view shares one interpretation with the static artifact. The two read-only routes (`GET /` page, `GET /api/data` JSON) are all the server exposes; CLI wiring lands with story 07.2-002.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: 07.1-001
**Risk Level**: Medium

### Feature 7.4: Analysis Views

#### Stories

##### Story 07.4-001: Leaderboard, run history, and per-task drilldown
**User Story**: As FX, I want sortable benchmark tables and per-task drilldown so that I can move from a headline ranking to the specific failures or slow tasks behind it.
**Priority**: Should Have
**Story Points**: 2

**Acceptance Criteria**:
- **Given** endpoint and agent result data **When** I open the dashboard **Then** I can sort and filter leaderboard rows by model, suite, run mode, pass rate, latency, throughput, cost, and failure count.
- **Given** multiple result files **When** I open run history **Then** I can compare runs by timestamp, model, suite, task count, pass rate, and median speed.
- **Given** a model row **When** I drill into task details **Then** I can inspect task id, pass/fail, failure reason, latency, cost, token counts, and a bounded raw-response preview.

**Technical Notes**: Start basic: tables, filters, and bounded previews. Avoid building a full observability product.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 07.1-001, 07.2-001
**Risk Level**: Low

##### Story 07.4-002: Basic tradeoff and sweep charts
**User Story**: As FX, I want basic charts for cost, quality, speed, and sweep behavior so that I can see tradeoffs faster than by scanning tables.
**Priority**: Should Have
**Story Points**: 2

**Acceptance Criteria**:
- **Given** endpoint model aggregates **When** I open the charts view **Then** I can see cost vs. quality and quality vs. speed scatter charts.
- **Given** sweep records **When** I open the charts view **Then** I can see TTFT or prefill throughput by context size and model.
- **Given** incomplete metrics for a model **When** charts are rendered **Then** the dashboard omits that point with a visible data-quality note rather than plotting misleading zeros.
- **Given** the generated static HTML **When** opened offline **Then** charts render without fetching external JavaScript from a CDN.

**Technical Notes**: Basic charts are enough for this epic. Use lightweight inlined JavaScript or simple SVG/canvas generated from the embedded data. Do not add matplotlib PNG generation unless the implementation clearly benefits from it.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 07.1-001, 07.2-001
**Risk Level**: Medium

## Epic Progress
**Completed**: 3 / 6 stories · 13 / 20 points

- [x] 07.1-001 Dashboard result aggregation model (3 pts)
- [x] 07.2-001 Static HTML dashboard generator (5 pts)
- [x] 07.3-001 Live results HTTP endpoints (5 pts)
