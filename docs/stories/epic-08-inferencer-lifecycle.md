# Epic 8: Inferencer Lifecycle Management

## Epic Overview
**Epic ID**: Epic-08
**Description**: Add a Python-only capability to detect, view, and control the macOS inference engines on the benchmark machine (DFlash, TurboQuant, MLX-LM, llama.cpp/Metal, Ollama, MLC-LLM, vLLM-mlx, Exo, plus detect-only GUI apps LM Studio and GPT4All). The harness today assumes a server is already running and only knows a `base_url`; this epic gives it a surface-agnostic manager library that detects installed engines, reports running/healthy status, and starts/stops headless servers — exposed first through `bench inferencer` CLI subcommands and then a localhost web control panel. A hard mutual-exclusion rule guarantees only one inference server is active at a time, with a confirmation step before stopping others, and an opt-in path lets a benchmark run auto-start the inferencer its model declares.
**Business Value**: The project's entire premise is trustworthy per-turn speed metrics (TTFT, prefill tok/s, decode tok/s) on a single Apple Silicon box. Those metrics are only valid when exactly one server holds the GPU — two concurrent engines distort prefill/decode timing and silently invalidate a run. FX manages these engines by hand today, which is error-prone and easy to get wrong (a forgotten Ollama daemon or LM Studio server skews every number). One-active lifecycle control turns "did I remember to stop the other server?" from a manual discipline into an enforced invariant.
**Success Metrics**: From one command FX can list which engines are installed, see which is running and healthy, start a chosen engine (being prompted to stop any others first), stop it cleanly, watch a live status table, browse and control the same state from a localhost web page, and run a benchmark that auto-starts exactly the engine its model declares — with the timing-integrity invariant (one active server) enforced in a single place regardless of entry surface.

## Epic Scope
**Total Stories**: 7 | **Total Points**: 25 | **MVP Stories**: 0 (Should Have / v1.x)

## Decisions Locked With FX
- **Form factor**: CLI manager core + localhost web view on top — one surface-agnostic library reused by both, not two implementations.
- **Engine scope**: full lifecycle for the headless servers (DFlash, TurboQuant, MLX-LM, llama.cpp, Ollama, MLC-LLM, vLLM-mlx, Exo); **detect-only** status for the GUI apps (LM Studio, GPT4All), which cannot be cleanly started/stopped headlessly. DFlash and TurboQuant are the project's existing reference local backends, already configured in `configs/models.yaml` at ports 8000 and 8002.
- **Benchmark link**: wired in (opt-in) — a model may declare the inferencer it needs and a run can auto-start it exclusively.
- **GUI apps / Ollama daemon**: warn-and-refuse by default (never force-quit a user's GUI), with an explicit `--force` escape hatch.
- **Manual installation only**: the harness **never installs, downloads, or auto-provisions** any inference engine. Detection stays strictly read-only (`shutil.which` / `find_spec` / `.app` presence); lifecycle covers only start/stop of engines FX has already installed by hand. Every inferencer therefore carries a reference `url` (official website or GitHub), and when an engine is not installed the harness points FX to that link rather than running any install command.

## Features in This Epic

### Feature 8.1: Inferencer Registry & Detection

#### Stories

##### Story 08.1-001: Inferencer config and installed-engine detection
**User Story**: As FX, I want a declarative registry of my macOS inference engines and a way to detect which are installed so that the harness knows what it can manage without me hardcoding anything.
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** a `configs/inferencers.yaml` describing the engines **When** it is loaded **Then** each entry parses into a frozen `InferencerConfig` (name, lifecycle, detect kind+target, port, health URL, optional start/stop) with duplicate names rejected, mirroring the existing `load_agents` loader.
- **Given** an entry whose `detect` mapping has zero or more than one of `binary`/`module`/`app` **When** the config is loaded **Then** a `ConfigError` is raised naming the offending index.
- **Given** a `server`-lifecycle entry without a `start` command, or an `app`-lifecycle entry that defines `start`/`stop` **When** the config is loaded **Then** a `ConfigError` is raised.
- **Given** a loaded registry **When** detection runs on macOS **Then** binary engines are detected via `shutil.which`, module engines via `importlib.util.find_spec`, and app engines via `/Applications` and `~/Applications` bundle presence.
- **Given** a non-Darwin platform **When** detection runs for an `app` engine **Then** it reports not-installed rather than raising, matching the `power.py` Darwin guard.

**Technical Notes**: Add `InferencerConfig` (and a `Lifecycle = Literal["server", "app"]`) beside `ModelConfig`/`AgentConfig` in `src/local_code_bench/config.py`, with `load_inferencers(path) -> dict[str, InferencerConfig]` reusing `_required_str`/`_optional_str`/`_optional_positive_int`. `health_url` may contain `{port}`; resolve via a small helper. Detection lives in a new `src/local_code_bench/inferencers/detect.py` exposing `is_installed(cfg)` and `detect_all(configs)`; guard `find_spec` against `ImportError`/`ModuleNotFoundError` (broken namespace packages → not installed) and use a module-level `_app_dirs()` so tests can monkeypatch it. Seed `configs/inferencers.yaml` with 10 engines — headless `server`: dflash (`binary: dflash`, 8000), turboquant (`binary: turboquant-serve`, 8002), mlx-lm (`module: mlx_lm`, 8080), llama-cpp (`binary: llama-server`, 8081), ollama (`binary: ollama`, 11434, custom `stop: ["ollama","stop"]`), mlc-llm (`module: mlc_llm`, 8082), vllm-mlx (`module: vllm`, 8001 — off 8000 to avoid colliding with dflash), exo (`binary: exo`, 52415); detect-only `app`: lm-studio (`app: "LM Studio.app"`, 1234), gpt4all (`app: "GPT4All.app"`, 4891). Most health URLs are `http://127.0.0.1:{port}/v1/models`; ollama uses `/api/tags`. Unit-test with monkeypatched `shutil.which`/`find_spec`/`_app_dirs`, mirroring `tests/test_config.py`.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: 01.1-002 (models.yaml + config loader), 01.2-003 (config validation patterns)
**Risk Level**: Low

### Feature 8.2: Lifecycle Manager

#### Stories

##### Story 08.2-001: Start, stop, and status with persisted process state
**User Story**: As FX, I want to start, stop, and check the status of a headless inference server through the harness so that I do not have to manage raw processes and ports by hand.
**Priority**: Should Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** an installed `server`-lifecycle engine that is not running **When** I start it **Then** the harness spawns the process, polls its health endpoint until it responds or a timeout elapses, and reports it running and healthy.
- **Given** a server fails to become healthy within the timeout **When** start is attempted **Then** the spawned process is killed and an `InferencerError` is raised carrying the tail of the captured log, and no stale state file remains.
- **Given** a running server I started earlier (state persisted across CLI invocations) **When** I query status from a new process **Then** liveness is determined via the persisted PID plus a health check, and a dead PID is reported as not-running with the stale state cleaned up.
- **Given** a running server **When** I stop it **Then** the process group is terminated gracefully (SIGTERM, then SIGKILL after a grace period), the state file is removed, and a second stop is a no-op.
- **Given** an `app`-lifecycle (GUI) engine **When** I try to start or stop it **Then** the harness refuses with a friendly "manage it from its own UI" message rather than spawning or killing anything, while still reporting detect/health status.

**Technical Notes**: New `src/local_code_bench/inferencers/manager.py` modeled on the `PowerSampler` subprocess pattern in `power.py` (Popen → terminate → kill → communicate-with-timeout, errors swallowed). Persist one JSON state file per started server under `.runtime/inferencers/<name>.json` (`{name,pid,port,started_at,command,health_url}`) plus a `<name>.log` for captured stdout/stderr; `.runtime/` is a sibling of `results/`/`.cache/` and must be added to `.gitignore`. Use `subprocess.Popen(..., start_new_session=True)` so SIGTERM reaches the whole group via `os.killpg`; use `os.kill(pid, 0)` for liveness. Health polling via `urllib`, swallowing `URLError`. Provide `status(cfg, state_dir)`, `status_all(configs, state_dir)`, `health_check(url, timeout)`, `start(...)`, `stop(...)`, an `InferencerStatus` dataclass (name, installed, lifecycle, running, pid, port, healthy, detail), and an `InferencerError(RuntimeError)`. Test with `subprocess.Popen`/`health_check`/`os.kill` monkeypatched to fakes, mirroring `tests/test_agents.py` — no real server launched.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: 08.1-001
**Risk Level**: Medium

### Feature 8.3: Mutual Exclusion

#### Stories

##### Story 08.3-001: Exclusive start with confirmation
**User Story**: As FX, I want starting one inference server to require stopping any others — after I confirm — so that exactly one engine ever holds the GPU and my timing measurements stay valid.
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** other inference servers are running **When** I request an exclusive start of a target **Then** the list of engines that would be stopped is presented and I am asked to confirm before anything stops.
- **Given** I decline the confirmation **When** exclusive start runs **Then** nothing is stopped, the target is not started, and the operation aborts with a clear message.
- **Given** I accept the confirmation **When** exclusive start runs **Then** each running headless engine is stopped, then the target is started, leaving exactly one server active.
- **Given** a running engine is a detect-only GUI app **When** exclusive start runs **Then** the GUI app blocks the start with a warning to quit it manually, unless `--force` is supplied.

**Technical Notes**: Add `running_others(target, configs, state_dir)` and `start_exclusive(target_cfg, configs, state_dir, *, confirm, force=False, progress=None)` to `manager.py`. `confirm` is an injected `Callable[[list[InferencerStatus]], bool]` so the identical mutual-exclusion rule serves every surface (CLI, web) without duplication — this is the one place the timing-integrity invariant is enforced. Test by injecting a confirm spy and patching `start`/`stop` to record calls; assert decline aborts without starting, accept stops-then-starts, and GUI-app-without-`force` blocks. No subprocess needed.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: 08.2-001
**Risk Level**: Medium

### Feature 8.4: CLI Surface

#### Stories

##### Story 08.4-001: `bench inferencer` subcommands and live status watch
**User Story**: As FX, I want `bench inferencer list/status/start/stop` commands and a live status view so that I can manage engines from the terminal beside the existing benchmark commands.
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** the CLI **When** I run `bench inferencer list` **Then** it prints each engine with installed state, lifecycle, and port; and `bench inferencer status` prints installed/running/healthy/pid in a table.
- **Given** `bench inferencer start <name>` **When** other engines are running **Then** I am prompted to confirm stopping them; `--yes` auto-confirms, a non-tty defaults to no, and `--force` permits starting past a running GUI app.
- **Given** `bench inferencer stop <name>` **When** invoked **Then** the engine is stopped (idempotently).
- **Given** `bench inferencer status --watch` **When** invoked **Then** the status table re-renders on an interval using ANSI clearing (no curses dependency).
- **Given** any config or lifecycle failure **When** an `inferencer` command runs **Then** the CLI prints `bench: error: ...` to stderr and exits with code 2, consistent with existing commands.

**Technical Notes**: Add an argparse subparser for `inferencer` (`list`/`status`/`start`/`stop`) in `src/local_code_bench/cli.py`, branched at the top of `main` before the existing flat `--mode` dispatch so every current flow (endpoint/agent/sweep/leaderboard/rescore) stays backward compatible. Keep a `run_inferencer_command(args) -> int` helper to keep `main` readable; the CLI's `confirm` reads stdin (`input()` y/N), honoring `--yes`/non-tty/`--force`. Add `InferencerError` to the caught-exception tuple already mapping `ConfigError` to exit 2. Flags include `--config configs/inferencers.yaml`, `--state-dir .runtime/inferencers`, `--watch`, `--yes`, `--force`. Test by monkeypatching the manager functions (as `tests/test_cli.py` patches `run_endpoint_suite`) and asserting output and exit codes.

**Implementation note**: this story also added the mutual-exclusion core its `start` command depends on (`running_others` / `start_exclusive` in `manager.py`, with the injected-`confirm` contract) because 08.3-001 had not yet merged when this landed; 08.3-001's acceptance criteria are satisfied by these functions and their tests.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: 08.3-001
**Risk Level**: Low

### Feature 8.5: Benchmark Integration

#### Stories

##### Story 08.5-001: Auto-start the inferencer a model declares
**User Story**: As FX, I want a model to declare which inferencer it needs and a run to optionally bring that engine up exclusively so that a benchmark of a local model starts the right server without a separate manual step.
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** a `ModelConfig` with an optional `inferencer` field **When** models.yaml is loaded **Then** the field parses (default none) without breaking any existing model entry.
- **Given** `--manage-inferencers` and a selected model that declares an inferencer **When** a run starts **Then** that inferencer is started exclusively (same confirmation/`--yes` flow as the CLI) before the suite runs.
- **Given** `--manage-inferencers` is not passed **When** a run starts **Then** behavior is unchanged — the harness assumes the server is already up.
- **Given** the declared inferencer ports **When** matched against the existing `local-dflash-qwen` (8000) and `local-turboquant-qwen-moe` (8002) entries **Then** they line up without further config changes.

**Technical Notes**: Add an optional `inferencer: <name>` to `ModelConfig`/`_parse_model` in `config.py`. Add a `--manage-inferencers` flag to the endpoint/sweep flow; when set, call `manager.start_exclusive(...)` for the model's declared inferencer before the run (and optionally stop it after). This is strictly opt-in so the default "assume server is up" path is untouched. Cover with a test asserting no behavior change when the flag is absent and an exclusive start when present (manager patched).

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: 08.3-001
**Risk Level**: Medium

### Feature 8.6: Web Control Panel

#### Stories

##### Story 08.6-001: Localhost web dashboard for inferencer control
**User Story**: As FX, I want a localhost web page that shows engine status and lets me start/stop engines so that I can control inferencers visually, reusing the same mutual-exclusion rule as the CLI.
**Priority**: Should Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** the dashboard server is running **When** I open it in a browser on the same machine **Then** a self-contained page (inlined CSS/JS, no CDN) shows a live status table fed by a `GET /api/status` JSON endpoint.
- **Given** I request to start an engine while others are running **When** the start is posted **Then** the server responds `409` with the list of engines that would be stopped, the page shows a confirm modal, and a confirmed re-post performs the exclusive start.
- **Given** the dashboard **When** it binds **Then** it listens on `127.0.0.1` only and never force-quits a GUI app.
- **Given** any dashboard response **When** rendered **Then** it contains no API keys, `.env` contents, or host-sensitive secrets.

**Technical Notes**: New `src/local_code_bench/inferencers/dashboard.py` using the Python stdlib `http.server` bound to localhost, reusing `manager.py` so no business logic is duplicated. The two-step `409 {needs_confirmation, others}` → confirm modal → `POST ...&confirm=1` is the web realization of the injected `confirm` contract from 08.3-001 (web supplies `confirm=lambda _: True`). Reuse the stdlib-server pattern planned in Epic-07's live-results story (07.3-001) rather than coupling to it. Expose it via `bench inferencer dashboard [--port 8765]`. Test by driving the handler with a fake request and asserting `/api/status` JSON plus the 409→confirm→start sequence.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: 08.3-001
**Risk Level**: Medium

### Feature 8.7: New Engine Onboarding

#### Stories

##### Story 08.7-001: Register MTPLX (native MTP) and add reference URLs with manual-install guidance
**User Story**: As FX, I want MTPLX — the native Multi-Token-Prediction MLX runtime — registered as a manageable inferencer, and every engine to carry a website/GitHub link, so that I can benchmark MTP speculative decoding under the harness's controlled protocol while the harness only ever *detects* engines I have installed by hand and points me to each project's own install page.
**Priority**: Should Have
**Story Points**: 3

**Context**: MTPLX (`github.com/youssofal/mtplx`) is an MLX-native MTP speculative-decoding runtime for Apple Silicon: a model drafts several tokens ahead with its own built-in MTP heads, verifies them in one batched forward pass, and keeps only what passes exact rejection sampling — no external drafter. It exposes an OpenAI- and Anthropic-compatible server (`mtplx serve --port <p>` → `/v1/models`, `/health`, `/v1/chat/completions`, `/v1/messages`), so it drops into the existing endpoint protocol exactly like dflash/turboquant. Public reports claim ~23% higher decode tok/s vs oMLX and up to 2.24× decode on Qwen 3.6 27B at coding temperatures; the harness exists precisely to validate such claims under fixed seed/temp, pinned revisions, and a one-active-server invariant rather than the uncontrolled side-by-side in the source video (`articles/MTPLX-vs-oMLX-MTP-transcript.md`). This story sits in the speculative-decoding-vs-MoE thesis as a third acceleration family alongside DFlash (spec decoding) and TurboQuant (MoE).

**Acceptance Criteria**:
- **Given** `configs/inferencers.yaml` **When** it is loaded **Then** it includes an `mtplx` entry: `lifecycle: server`, `detect: { binary: mtplx }`, `health_url: http://127.0.0.1:{port}/v1/models`, `start: ["mtplx", "serve", "--port", "<port>"]`, on a port that does **not** collide with an existing engine (MTPLX defaults to 8000, which dflash already owns — remap to **8003**).
- **Given** the `InferencerConfig` schema **When** an entry is parsed **Then** it accepts an **optional `url`** field (official website or GitHub); a missing `url` is allowed (default none) and does not break loading, and every existing engine entry is backfilled with its reference link.
- **Given** MTPLX is **not** installed on this machine **When** detection runs **Then** it reports not-installed (read-only `shutil.which`, no install attempted) and any surface that surfaces the result presents the engine's `url` as the place to install it manually.
- **Given** `bench inferencer list` (and `status`) **When** run **Then** the per-engine `url` is shown, and the output makes clear the harness does not install engines — installation is manual, link-guided.
- **Given** MTPLX is installed and started exclusively **When** a benchmark targets an MTPLX model **Then** the existing endpoint protocol measures it unchanged (TTFT / prefill / decode tok/s), at local `concurrency: 1`, scored by the same suite as any other local engine.
- **Given** the model-artifact constraint **When** an MTPLX run is configured **Then** the harness documents that MTPLX requires its **own pre-built MTP models** (the `Youssofal` Hugging Face catalog: Qwen 3.5/3.6, Gemma 4) verified by `mtplx inspect`, so an MTPLX row in `configs/models.yaml` points at an MTPLX-specific repo and a strict same-artifact A/B against other engines is **not** claimed — the run metadata records the differing model build.
- **Given** MTPLX's optional per-machine auto-tuning step **When** documented **Then** it is described as a **manual** pre-step FX runs once outside the harness (the harness neither triggers tuning nor depends on it).

**Technical Notes**: Schema change is additive — add an `optional` `url: str | None` to `InferencerConfig`/`load_inferencers` in `config.py` via the existing `_optional_str` helper; no validation beyond "string if present". Backfill `url` on all current entries: dflash and turboquant → the reference Medium series in `articles/` (confirm canonical project links with FX before pinning), mlx-lm → `https://github.com/ml-explore/mlx-lm`, llama-cpp → `https://github.com/ggml-org/llama.cpp`, ollama → `https://ollama.com`, mlc-llm → `https://github.com/mlc-ai/mlc-llm`, vllm-mlx → `https://github.com/vllm-project/vllm`, exo → `https://github.com/exo-explore/exo`, lm-studio → `https://lmstudio.ai`, gpt4all → `https://github.com/nomic-ai/gpt4all`, mtplx → `https://github.com/youssofal/mtplx`. MTPLX is a `server`-lifecycle binary (`mtplx`), so it reuses the existing `manager.py` start/stop/health path with no new lifecycle code — only the registry row plus the `url` field and a list/status output tweak. Add an MTPLX model entry to `configs/models.yaml` (`inferencer: mtplx`, `concurrency: 1`, `cost: $0` local) pointing at a `Youssofal/...-MTPLX` repo. Extend the loader tests in `tests/test_config.py` for the new `url` field (present / absent) and the duplicate-port guard if one is added; assert detection stays read-only. No engine is installed in CI — detection is monkeypatched as in 08.1-001.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated (manual-install note + per-engine links; MTPLX model-artifact + tuning caveat)

**Dependencies**: 08.1-001 (registry + detection), 08.4-001 (`bench inferencer` CLI surface), 08.5-001 (model `inferencer` field for auto-start)
**Risk Level**: Low

## Epic Progress
**Completed**: 6 / 7 stories · 22 / 25 points
- 08.1-001 ✅ · 08.2-001 ✅ · 08.3-001 ✅ · 08.4-001 ✅ · 08.5-001 ✅ · 08.6-001 ✅ · 08.7-001 ⬜
- 08.3-001 (exclusive start) was delivered as part of 08.4-001 (`start_exclusive`/`running_others` in `manager.py`, `tests/test_inferencers_exclusive.py`); its separately-built branch was redundant and dropped.
- 08.7-001 (MTPLX + reference URLs) reopens the epic to onboard a new engine and codify the manual-install / link-only rule across the registry.
