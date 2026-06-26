# Epic 9: Unified Dashboard

## Epic Overview
**Epic ID**: Epic-09
**Description**: Bring the three benchmark surfaces — inferencer control (Epic-08), results exploration (Epic-07), and the CLI benchmark runner — together into one localhost web dashboard. From a single page FX can manage local inference engines, browse benchmark results, and launch a new benchmark, where a benchmark is composed as **model + inferencer + a chosen set of test suites** (HumanEval, canary, MBPP, the EvalPlus variants, and config-registered custom suites). Epic-09 does not reinvent the results or inferencer surfaces — it composes Epic-07's live results endpoints and Epic-08's inferencer control panel under one shell and adds the genuinely new pieces: the benchmark launcher and live run monitoring, with the one-active mutual-exclusion invariant enforced for every launched run.
**Business Value**: Today a benchmark run is a multi-step manual ritual — edit `models.yaml`, remember which inferencer the model needs, start exactly that server with nothing else running, invoke the right CLI command, then hand-read JSONL or regenerate tables to see how it went. Each step is a place to get it wrong and silently invalidate a run (a stray server skews timing; the wrong suite scores the wrong thing). A single "pick a model, pick an inferencer, pick suites, go — then watch it and read the results" surface turns that ritual into one guided flow, with timing integrity guaranteed by reusing Epic-08's exclusive start rather than FX's memory.
**Success Metrics**: From one `bench dashboard` command FX can, in one browser tab, see which engines are installed/running/healthy and control them, compose and launch a benchmark by choosing a model, an inferencer, and one or more available suites, watch that run's live progress (passed/failed/remaining, speed, cost), and see the completed run appear in the results views — without editing config by hand, without manually juggling servers, and with exactly one inference server ever active during a run.

## Epic Scope
**Total Stories**: 8 | **Total Points**: 32 | **MVP Stories**: 0 (Should Have / v1.x)

## Decisions Locked With FX
- **Deliverable for this pass**: epic + stories only (no code), consistent with how Epic-08 was authored.
- **Relation to existing epics**: a **new Epic-09** that depends on and composes Epic-07 (results) and Epic-08 (inferencers); both remain intact as building blocks. Epic-09 owns only the unifying shell, the benchmark launcher, run monitoring, the suite catalog, and cross-section cohesion/safety.
- **Carried-over timing rules** (from Epic-08): full lifecycle for headless servers, detect-only for GUI apps, and a hard one-active mutual-exclusion rule enforced through `start_exclusive` — every benchmark launched from the dashboard goes through it.
- **Chat: build native, do not embed** (Feature 9.5): a thin chat panel over the existing OpenAI-compatible streaming (`provider.py`) is preferred to embedding a heavyweight external app (Open WebUI). Embedding would add a Node/Svelte + DB/auth service on its own port, duplicate the dashboard's model/inferencer selection, and break the stdlib-first, self-contained, no-CDN convention. Chat is fundamentally a thin client over streaming the harness already does.

## Features in This Epic

### Feature 9.1: Unified Dashboard Shell

#### Stories

##### Story 09.1-001: Single-page unified dashboard with Inferencers / Results / Run sections
**User Story**: As FX, I want one localhost page with Inferencers, Results, and Run sections so that I can manage engines, read results, and launch benchmarks without juggling separate tools.
**Priority**: Should Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** the `bench dashboard` command **When** I run it **Then** a single stdlib HTTP server starts bound to `127.0.0.1`, prints its URL, and serves one page with navigable Inferencers, Results, and Run sections.
- **Given** the dashboard page **When** I switch between sections **Then** navigation works without a frontend build step and without reloading the whole app.
- **Given** the Inferencers section **When** it loads **Then** it reuses the Epic-08 inferencer control panel behavior, and the Results section reuses Epic-07's live results data — no duplicated business logic.
- **Given** any response from the unified server **When** rendered **Then** it contains no API keys, `.env` contents, or host-sensitive paths, and the server binds to localhost only.

**Technical Notes**: New `src/local_code_bench/dashboard/app.py` (or extend the Epic-08 `inferencers/dashboard.py` server) using the stdlib `http.server`, reusing Epic-08's `manager`/status JSON and Epic-07's results aggregates (07.1-001/07.3-001) rather than re-querying. Serve one self-contained page (inlined CSS/JS, no CDN). Expose via a single `bench dashboard [--port 8765]` entry that supersedes the per-epic `bench inferencer dashboard` once this lands. Keep section panels as thin clients over the existing JSON endpoints.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: 07.3-001, 08.6-001
**Risk Level**: Medium

### Feature 9.2: Benchmark Launcher

#### Stories

##### Story 09.2-001: Compose a benchmark from model + inferencer + suites
**User Story**: As FX, I want to pick a model, an inferencer, and one or more test suites in the Run section so that I can launch a benchmark without editing config files by hand.
**Priority**: Should Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** the Run section **When** it loads **Then** the model selector is populated from `models.yaml`, the inferencer selector from `inferencers.yaml`, and the suite selector from the available-suites catalog (09.5-001).
- **Given** I select a model, an inferencer, and one or more suites **When** I review the composition **Then** the form validates the combo and warns when the chosen inferencer differs from the model's declared `inferencer`, before anything is launched.
- **Given** a valid composition **When** I submit it **Then** a launch request carrying the model, inferencer, and suite list is sent to the launch endpoint and I see the run accepted.
- **Given** an invalid or empty composition **When** I submit **Then** the form rejects it with an actionable message and launches nothing.

**Technical Notes**: Pure dashboard UI + a small read endpoint that returns the current model/inferencer/suite catalogs as JSON (reusing `load_models`, `load_inferencers`, and the suite catalog from 09.5-001). Validation mirrors the harness's existing config-validation tone. Multi-suite selection maps to running the chosen suites in sequence for the selected model. Keep the form a thin client; all authority lives in the launch endpoint (09.3-001).

**Implementation**: `src/local_code_bench/unified_dashboard.py` — `catalog_action`
behind `GET /api/catalog` returns `{models, inferencers, suites}` (models carry their
declared `inferencer` for the differ-warning; suites come from `suite_catalog`'s
availability-aware payload), and the Run-section form is a thin client that populates
the three selectors, validates the combo client-side (rejecting empty/invalid with an
actionable message, warning on inferencer mismatch), and posts a valid composition to
`POST /api/run` — wired here to delegate to the 09.3-001 orchestrator (`launch_action`).
`serve_dashboard` loads the model registry and builds the `RunOrchestrator`; the
`bench dashboard` CLI gains `--models` / `--suites`. Tests in `tests/test_unified_dashboard.py`.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: 09.1-001, 09.3-001, 09.5-001
**Risk Level**: Medium

##### Story 09.3-001: Launch orchestration endpoint
**User Story**: As FX, I want submitting a composition to exclusively start the right inferencer and run the chosen suites in the background so that launching a benchmark is one click instead of a manual sequence.
**Priority**: Should Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** a launch request **When** it is received **Then** the endpoint validates the model+inferencer+suite combo and rejects unknown or incompatible selections with a clear error.
- **Given** a valid request **When** the run starts **Then** the inferencer is started exclusively via Epic-08's `start_exclusive` (same confirmation contract) so exactly one inference server is active.
- **Given** the inferencer is up **When** the benchmark runs **Then** the chosen suites are executed in the background through the existing `run_endpoint_suite`, writing JSONL to `results/`, and the endpoint returns a run id.
- **Given** generated code from a run **When** it is scored **Then** it executes only in the existing sandbox.
- **Given** a run is already in flight **When** another launch is submitted **Then** it is serialized or rejected so the one-active-server invariant is never violated.

**Technical Notes**: New `POST /api/run` handler that composes existing pieces: `start_exclusive` (08.3-001) for the inferencer, then `run_endpoint_suite` (Epic-01 runner) per selected suite, writing to a fresh `results/<run>.jsonl` via `new_run_path`. Run the suite work on a background thread; track run state (id, status, counts) in memory plus the JSONL file as source of truth. Reject concurrent launches while one is active (single-run lock). No new scoring path — reuse the sandbox. Test by patching `start_exclusive` and `run_endpoint_suite` and asserting orchestration order, the single-run lock, and run-id return.

**Implementation**: `src/local_code_bench/launch.py` — `RunOrchestrator` (single-run
lock, in-memory `RunState`, background suite thread) plus the `POST /api/run`
handler (`launch_action` / `handle_request` / `make_server`). The exclusive start
mirrors the inferencer dashboard's `409 {needs_confirmation, others}` contract and
calls `manager.start_exclusive`; suites run in order through `run_endpoint_suite`,
writing JSONL via `new_run_path`. Tests in `tests/test_launch.py`.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: 08.3-001, 01.2-003 (runner + JSONL), 02.1-001 (suite loaders)
**Risk Level**: High

##### Story 09.4-001: Live run progress and auto-refreshed results
**User Story**: As FX, I want to watch a launched run's progress and have results refresh when it finishes so that I can follow a benchmark from launch to verdict in one place.
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** a run launched from the dashboard **When** it is in flight **Then** I can see live progress: passed/failed/remaining counts, the current task, and speed/cost accumulated so far.
- **Given** a run reaches a terminal state **When** it completes **Then** the dashboard shows the terminal status and the Results section reflects the new JSONL through Epic-07's live aggregates without restarting the server.
- **Given** a run fails or is aborted **When** I view it **Then** a clear reason is surfaced rather than a silent stop.

**Technical Notes**: A `GET /api/run/<id>` (or `/api/runs`) status endpoint the page polls, fed by the in-memory run state from 09.3-001 and/or by tailing the run's JSONL. On completion, the Results section re-fetches Epic-07's live aggregates (07.3-001) pointed at the new file. Keep polling simple (interval refresh); no websockets needed. Test with a fake run-state source asserting progress, terminal, and failure rendering.

**Implementation**: `RunOrchestrator.run_payload`/`runs_payload` and the
`accumulated_metrics` JSONL tailer in `src/local_code_bench/launch.py` expose live
progress (passed/failed/remaining counts, current task, and accumulated cost / decode
tok/s). The unified dashboard wires the orchestrator into `DashboardContext` and adds
`POST /api/run` (delegating to `launch.launch_action`), `GET /api/runs`, and
`GET /api/run/<id>`; `/api/data` now also globs `results_dir` so a launched run's JSONL
appears without a restart. The Run section's **Live Runs** monitor polls `/api/runs`
every 2s, surfaces terminal status + failure reason, and triggers the Results refresh
on completion. Tests in `tests/test_launch.py` and `tests/test_unified_dashboard.py`.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: 09.3-001, 07.3-001
**Risk Level**: Medium

### Feature 9.3: Test-Suite Catalog

#### Stories

##### Story 09.5-001: Available-suites catalog and custom-suite registration
**User Story**: As FX, I want the launcher to list every available test suite — built-in and custom — so that I can benchmark against new suites without editing code.
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** the built-in suites (humaneval, mbpp, canary, humaneval-plus, mbpp-plus) **When** the catalog is requested **Then** each is listed with its identity and task count where known.
- **Given** a config-registered custom suite **When** the catalog is requested **Then** the new suite appears in the launcher without any code change.
- **Given** a suite that is currently unavailable (e.g. a missing EvalPlus cache file) **When** the catalog is rendered **Then** it is shown disabled with the reason, rather than offered and failing at launch.

**Technical Notes**: A `suite_catalog()` helper that enumerates the existing `SuiteName` suites plus any entries from an optional `configs/suites.yaml` (id, loader hint, source path), reusing the Epic-02 `load_suite`/dataset-cache logic to compute availability and counts. Surface it as a JSON endpoint consumed by 09.2-001. Keep "custom suite" definition minimal — point at a loadable dataset; do not build a full plugin system. Test the catalog with built-ins present, a registered custom suite, and an unavailable (missing-cache) suite.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 02.1-001 (suite loaders), 09.2-001
**Risk Level**: Medium

### Feature 9.4: Cohesion & Safety

#### Stories

##### Story 09.6-001: Cross-section flow and localhost-only safety
**User Story**: As FX, I want the sections to flow into one another and the whole surface to stay safe-by-default so that launching, watching, and reviewing a run feels like one tool I can trust on my machine.
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** I launch a benchmark **When** it starts **Then** the dashboard moves me from launch to live progress to the completed results, and the Inferencers section reflects which engine the run brought up.
- **Given** the unified server **When** it runs **Then** it binds to localhost only with no auth beyond that (documented as a single-user benchmark-box tool), and no endpoint leaks API keys, `.env` contents, or host paths.
- **Given** a GUI app inferencer **When** a launch or control action touches it **Then** it is never force-quit (Epic-08 warn-and-refuse rule holds), and all generated code runs only in the sandbox.

**Technical Notes**: Cross-link the sections (launch → `GET /api/run/<id>` → results pointed at the run's JSONL; Inferencers panel reads the same `status_all`). Centralize a response-sanitization pass so no secrets reach the browser. Re-assert the Epic-08 safety rules at the unified layer rather than re-implementing them. This story is the integration/security seam — its tests assert the launch→watch→results path end-to-end (with backend pieces faked) and that security ACs hold across endpoints.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 09.1-001, 09.4-001
**Risk Level**: Medium

### Feature 9.5: Model Chat / Interactive Testing

#### Stories

##### Story 09.7-001: Streaming chat endpoint
**User Story**: As FX, I want to send chat messages to the selected model through the dashboard and stream the reply so that I can quickly smoke-test a model's behaviour without writing a benchmark.
**Priority**: Should Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** a selected model (and the active inferencer) **When** I post a multi-turn message list to the chat endpoint **Then** the reply is streamed back token-by-token via the existing OpenAI-compatible provider.
- **Given** an optional system prompt, temperature, and max-tokens **When** I send a chat turn **Then** they are applied to the request; defaults are sensible and the conversation preserves prior turns.
- **Given** a chat targets a local model **When** another inferencer is active **Then** the one-active invariant is respected — chat talks to the running engine and does not silently start a second server.
- **Given** a streaming reply in progress **When** I stop it **Then** the stream is cancelled cleanly.
- **Given** any chat response **When** served **Then** it binds localhost only and leaks no API keys, `.env` contents, or host paths.

**Technical Notes**: A `POST /api/chat` handler on the Epic-09 dashboard server that builds a multi-message `ChatRequest` and streams through `provider_for_model` / `OpenAIStreamingProvider` (`src/local_code_bench/provider.py`) — reuse, not reimplementation. Stream Server-Sent Events to the browser so the existing token-by-token parsing path is mirrored client-side. Multi-turn state lives client-side and is posted each turn (no server DB), keeping with the stdlib-first, single-user model. Reuse the dashboard's response-sanitization (09.6-001). Test with a monkeypatched provider (as `tests/test_provider.py` does) asserting streamed chunks, applied params, and multi-turn message assembly — no live model.

**Implementation**: `src/local_code_bench/chat.py` — `chat_action` /
`build_chat_request` / `sse_chat_events` parse a multi-turn body and stream the reply
token-by-token as SSE through `provider_for_model`. The provider
(`src/local_code_bench/provider.py`) gained a multi-turn `ChatRequest`
(`messages` + `system`) used by both the OpenAI and Anthropic adapters. The
`POST /api/chat` route is wired into the unified dashboard server
(`src/local_code_bench/unified_dashboard.py`), which now loads the model registry via
`--models` (best-effort, chat-disabling on failure). Tests in `tests/test_chat.py`
plus chat-routing tests in `tests/test_unified_dashboard.py` and provider multi-turn
tests in `tests/test_provider.py`.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: 09.1-001, 01.1-003 (OpenAI-compatible provider)
**Risk Level**: Medium

##### Story 09.7-002: Chat panel UI
**User Story**: As FX, I want a Chat section in the dashboard with a message pane and model/inferencer picker so that testing a model is a few clicks, in the same surface as launching benchmarks.
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** the dashboard **When** I open the Chat section **Then** I can pick a model and inferencer (reusing the launcher's selectors) and see a multi-turn message pane.
- **Given** I type a message and send **When** the reply streams **Then** it renders incrementally with a visible stop control, and the conversation scrolls as a normal chat.
- **Given** controls for system prompt, temperature, and max-tokens **When** I adjust them **Then** subsequent turns use the new values.
- **Given** the page **When** served **Then** it is self-contained (inlined CSS/JS, no CDN/Node build) and localhost-only, consistent with the rest of the dashboard.

**Technical Notes**: A new Chat section in the unified dashboard page (09.1-001) that is a thin client over `POST /api/chat` (09.7-001), reusing the launcher's model/inferencer selectors (09.2-001). Keep markdown/rendering minimal — the goal is a fast smoke test, not a full chat product. No new front-end dependencies.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 09.7-001, 09.2-001
**Risk Level**: Low

## Epic Progress
**Completed**: 5 / 8 stories · 23 / 32 points

- [x] 09.1-001 — Single-page unified dashboard with Inferencers / Results / Run sections (5 pts)
- [x] 09.2-001 — Compose a benchmark from model + inferencer + suites (5 pts) (`src/local_code_bench/unified_dashboard.py`)
- 09.3-001 Launch orchestration endpoint — done (`src/local_code_bench/launch.py`)
- [x] 09.4-001 — Live run progress and auto-refreshed results (3 pts) — `launch.py` run payloads + `unified_dashboard.py` `/api/runs` monitor
- [x] 09.7-001 — Streaming chat endpoint (5 pts) — done (`src/local_code_bench/chat.py`)
