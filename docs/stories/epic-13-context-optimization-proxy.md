# Epic 13: Context-Optimization Proxy Layer

## Epic Overview
**Epic ID**: Epic-13
**Description**: Add a first-class notion of an **optimization proxy** — an OpenAI-compatible middleware that sits *between* the harness/agent and a running inferencer and reduces the context the engine must prefill (e.g. **Headroom**, which compresses tool outputs and file reads via a "context router"; the Rust "token killer" is a sibling). This is **not** an inference engine and explicitly does **not** belong in the Epic-08 inferencer registry: a proxy holds no GPU, generates no tokens, and always runs *in front of* a real inferencer, so the one-active-server invariant does not apply to it. Epic-13 gives proxies their own declarative registry (detect-only, manual-install, link-guided, mirroring Epic-08's rules), a chained lifecycle (`proxy :PORT → upstream-inferencer`), and — the core value — an **A/B measurement treatment** that runs the *same* task with and without the proxy and reports three deltas: **tokens prefilled** (the claimed win), **end-to-end latency**, and **correctness / task-success** (the lossy-compression cost the source demos never measured).
**Business Value**: This project's central thesis is that local agentic coding on Apple Silicon is **prefill-bound** — prompt processing, not decode, is the bottleneck. Context-compression proxies attack exactly that bottleneck from a different angle than a faster engine: instead of prefilling faster, prefill *less*. Public demos claim 20–30% fewer tokens on long coding sessions, but they are single, uncontrolled runs that never check whether compression silently degraded the agent's output. The harness exists to turn such claims into controlled, reproducible, falsifiable results — here, to answer "does an optimization proxy actually speed up local agent runs, and what does it cost in correctness?" before FX adopts one as a daily driver.
**Success Metrics**: From one command FX can register a manually-installed proxy (detect-only, with a docs link), start it in front of a chosen inferencer, and run an agent task twice — bare vs proxied — getting a side-by-side report of tokens prefilled, wall-clock latency, and task-success/correctness, with both raw runs persisted to JSONL and the proxy's identity + configuration captured in run metadata. The harness never reports a proxied run as comparable to a bare run without recording that a proxy mutated the request, and never claims a token saving without also surfacing its correctness delta.

## Epic Scope
**Total Stories**: 4 | **Total Points**: 14 | **MVP Stories**: 0 (Should Have / v2)

## Decisions Locked With FX
- **Category, not an engine**: optimization proxies get their **own registry** (`configs/optimizers.yaml`), separate from `configs/inferencers.yaml`. They are never registered as inferencers — doing so would corrupt the one-active-server invariant (a proxy is engine **+** proxy, never the sole GPU holder).
- **Manual installation only**: same rule as Epic-08 — the harness **never installs/downloads** a proxy. Detection is read-only; every proxy entry carries a reference `url` (website/GitHub/docs) and an uninstalled proxy is reported with that link, not auto-provisioned.
- **Always measured as a treatment, never assumed**: a proxy is only ever evaluated as an **A/B condition** (bare vs proxied) on the *same* task. Because context compression is lossy, **correctness/task-success is a mandatory output**, co-equal with the token-reduction number. A token saving is never reported on its own.
- **Scope is agent mode**: proxies are evaluated against **Epic-06 agent-mode / long-context coding tasks**, where context balloons from tool outputs and file reads (where the 20–30% wins appear). They are **out of scope for the single-turn correctness suite** (HumanEval/MBPP prompts are tiny — compression buys nothing and can only corrupt the task).
- **First proxy**: **Headroom** (`headroom proxy --port <p> <upstream-url>`, dashboard with per-session before/after token counts and a `--learn` tuning flag), registry-designed to admit further proxies (e.g. the Rust "token killer") later.

## Scope Boundaries (explicitly NOT building)
- **No changes to the inferencer registry / mutual-exclusion core** — Epic-13 layers on top of Epic-08; a proxy chains in front of whatever single inferencer Epic-08 has made active.
- **No proxy installation or model downloading** — Epic-13 detects and drives proxies FX installed by hand.
- **No suite-mode (HumanEval/MBPP) proxy path** — single-turn correctness runs do not route through a proxy.
- **No bespoke compression** — the harness measures third-party proxies; it does not implement its own context compressor.

## Features in This Epic

### Feature 13.1: Optimizer Registry & Detection

#### Stories

##### Story 13.1-001: Optimizer config and installed-proxy detection
**User Story**: As FX, I want a declarative registry of context-optimization proxies and read-only detection of which are installed so that the harness knows what proxies it can drive without ever installing one for me.
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** a `configs/optimizers.yaml` describing proxies **When** it is loaded **Then** each entry parses into a frozen `OptimizerConfig` (name, detect kind+target, listen `port`, `health_url`, `start` command template referencing the upstream URL, optional `url` reference link), with duplicate names rejected — reusing the `load_inferencers` loader patterns.
- **Given** an entry whose `detect` mapping has zero or more than one of `binary`/`module`/`app` **When** the config is loaded **Then** a `ConfigError` is raised naming the offending index, mirroring Epic-08.
- **Given** detection runs **When** a proxy binary/module is absent **Then** it reports not-installed (read-only `shutil.which` / `find_spec`, no install attempted) and the entry's `url` is surfaced as the manual-install reference.
- **Given** the `start` template **When** it is resolved **Then** it substitutes both `{port}` (the proxy's listen port) and `{upstream}` (the active inferencer's base URL), so the proxy is always wired to a real engine.
- **Given** `configs/optimizers.yaml` is seeded **Then** it contains a `headroom` entry (`detect: { binary: headroom }`, listen `port: 8787`, `health_url: http://127.0.0.1:{port}/v1/models`, `start: ["headroom", "proxy", "--port", "8787", "{upstream}"]`, `url: https://headroom-docs.vercel.app/docs`).

**Technical Notes**: Add `OptimizerConfig` beside `InferencerConfig` in `config.py` with `load_optimizers(path)`, reusing `_required_str`/`_optional_str`/`_optional_positive_int` and the same one-of detection validation. Detection can reuse `inferencers/detect.py:is_installed` (the detect kinds are identical) or a thin wrapper. Keep the `{upstream}` substitution distinct from `{port}` so the chained-lifecycle story can fill it from the active inferencer. Unit-test the loader (present/absent `url`, duplicate-name rejection, bad `detect` arity) and read-only detection with monkeypatched `shutil.which`, mirroring `tests/test_config.py`.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated (manual-install note + proxy reference link)

**Dependencies**: 08.1-001 (registry + detection patterns to reuse)
**Risk Level**: Low

### Feature 13.2: Chained Proxy Lifecycle

#### Stories

##### Story 13.2-001: Start/stop a proxy chained in front of the active inferencer
**User Story**: As FX, I want to start an optimization proxy in front of a running inferencer and stop it cleanly so that I can route an agent through `proxy → engine` without managing raw processes and ports.
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** an installed proxy and exactly one active inferencer (per Epic-08) **When** I start the proxy **Then** the harness resolves `{upstream}` from the active inferencer's base URL, spawns the proxy, polls its health endpoint until healthy or timeout, and reports it running with its upstream recorded.
- **Given** no inferencer is active **When** a proxy start is attempted **Then** the harness refuses with a clear message (a proxy must front a real engine), starting nothing.
- **Given** a proxy fails to become healthy within the timeout **When** start is attempted **Then** the spawned process is killed, an `OptimizerError` carrying the captured log tail is raised, and no stale state remains.
- **Given** a running proxy **When** I stop it **Then** its process group is terminated gracefully (SIGTERM→SIGKILL), state is removed, and a second stop is a no-op; the upstream inferencer is left untouched.
- **Given** proxy state is persisted **When** status is queried from a new process **Then** liveness is determined via the persisted PID plus a health check, and a dead PID is reported not-running with stale state cleaned up.

**Technical Notes**: Reuse the Epic-08 `manager.py` subprocess/state/health pattern (`Popen(start_new_session=True)`, `os.killpg`, JSON state under `.runtime/optimizers/<name>.json` + `<name>.log`, `urllib` health poll). Add a small `optimizers/manager.py` (or extend the existing manager generically) with `start(cfg, upstream, state_dir)`, `stop(...)`, `status(...)`, and an `OptimizerError(RuntimeError)`. The "active inferencer" lookup reuses Epic-08 `status_all` to find the single running engine and read its `base_url`/port. Test with `Popen`/`health_check`/`os.kill` monkeypatched — no real proxy launched.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 13.1-001, 08.2-001 (lifecycle manager pattern + active-engine status)
**Risk Level**: Medium

### Feature 13.3: A/B Optimization Measurement

#### Stories

##### Story 13.3-001: Bare-vs-proxied A/B run with token, latency, and correctness deltas
**User Story**: As FX, I want to run the same agent task twice — once straight to the engine and once through an optimization proxy — and get a side-by-side report of tokens prefilled, latency, and task-success so that I can see whether the proxy actually helps and what it costs in correctness.
**Priority**: Should Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** an agent-mode task, an active inferencer, and a registered proxy **When** I request an A/B optimization run **Then** the harness executes the task **bare** (agent → engine) and **proxied** (agent → proxy → engine) under identical seed/temp/model, persisting both as separate JSONL records tagged with the condition.
- **Given** both conditions complete **When** the report is generated **Then** it shows, side by side, **total tokens prefilled** (and the proxy's claimed reduction %), **end-to-end latency**, and **task-success / correctness** for each condition, plus the deltas.
- **Given** the proxy mutated the request **When** results are recorded **Then** run metadata captures the proxy name, version/flags, and that a proxy was in-path — so a proxied run is never silently compared as if it were a bare run.
- **Given** a token saving is reported **When** the summary is rendered **Then** the correctness/task-success delta is shown alongside it (never a saving in isolation), making a lossy-but-faster outcome visible.
- **Given** the correctness signal is unavailable for a given task **When** the report is generated **Then** that is stated explicitly rather than implying parity.

**Technical Notes**: Build on the Epic-06 agent-mode runner: parameterize the agent's target base URL so one condition points at the inferencer and the other at the proxy's listen port, holding everything else fixed. Reuse existing token/latency capture; "tokens prefilled" comes from the engine-side request sizes (and, where exposed, the proxy dashboard's before/after counts). Correctness reuses whatever task-scoring the agent task already supports (e.g. build/test pass for Epic-06/Epic-10-style tasks); where a task has no automatic scorer, mark correctness "unverified" rather than assuming success. Emit a small comparison summary (table) and keep raw JSONL re-scorable offline. Test the orchestration with the agent runner and proxy lifecycle mocked, asserting two tagged records and a report that always pairs saving with correctness.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 13.2-001, 06.1-002 (Codex exec runner), 06.1-003 (agent-mode scoring)
**Risk Level**: Medium

### Feature 13.4: Headroom Onboarding & Surface

#### Stories

##### Story 13.4-001: Register Headroom and expose proxies on the CLI (and dashboard)
**User Story**: As FX, I want `bench optimizer list/status/start/stop` plus an A/B run command, with Headroom registered, so that I can drive the proxy layer from the terminal beside the existing inferencer commands.
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** the CLI **When** I run `bench optimizer list` **Then** it prints each proxy with installed state, listen port, and reference `url`, making clear installation is manual (link-guided), and `status` shows installed/running/healthy/upstream.
- **Given** `bench optimizer start <name> --inferencer <engine>` (or against the single active engine) **When** invoked **Then** the proxy starts chained in front of that engine; `stop` stops it idempotently.
- **Given** `bench optimizer ab --task <task> --inferencer <engine> --proxy <name>` **When** invoked **Then** it runs the 13.3-001 bare-vs-proxied comparison and prints the token/latency/correctness summary.
- **Given** any config or lifecycle failure **When** an `optimizer` command runs **Then** the CLI prints `bench: error: ...` to stderr and exits with code 2, consistent with existing commands.
- **Given** the Epic-09 unified dashboard exists **When** the proxy layer is surfaced there **Then** it is shown as a distinct "optimizers" section, never mixed into the inferencer one (deferrable to a dashboard follow-up if Epic-09 is not yet in place).

**Technical Notes**: Add an `optimizer` argparse subparser (`list`/`status`/`start`/`stop`/`ab`) in `cli.py`, branched alongside the Epic-08 `inferencer` subparser, with `--config configs/optimizers.yaml`, `--state-dir .runtime/optimizers`, `--inferencer`, `--proxy`, `--task`, `--yes`. Add `OptimizerError` to the caught-exception tuple mapping to exit 2. The dashboard surface reuses Epic-09's composition; keep it a separate panel. Seed/confirm the `headroom` registry entry (13.1-001) and document its `--learn` flag as an optional manual tuning step the harness neither triggers nor depends on. Test by monkeypatching the optimizer manager + A/B orchestrator and asserting output/exit codes, mirroring `tests/test_cli.py`.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 13.1-001, 13.2-001, 13.3-001 (CLI surfaces the registry, lifecycle, and A/B run); 08.4-001 (CLI subparser pattern); 09.1-001 (optional dashboard panel)
**Risk Level**: Low

## Epic Progress
**Completed**: 0 / 4 stories · 0 / 14 points
- 13.1-001 ⬜ · 13.2-001 ⬜ · 13.3-001 ⬜ · 13.4-001 ⬜
- Source motivation: `articles/Headroom-prompt-processing-proxy-transcript.md` (Headroom token-optimization proxy, ~20–30% fewer tokens prefilled on long coding sessions — uncontrolled demo this epic exists to validate, correctness cost included).
