# Epic 15: Settings Management (Dashboard Settings Tab)

## Epic Overview
**Epic ID**: Epic-15
**Description**: Give the unified dashboard (Epic-09) a **Settings tab** that surfaces every piece of harness configuration in one place — the model matrix (`configs/models.yaml`), the inferencer registry with its storage paths and tiering policy (`configs/inferencers.yaml`, including `external_repo` and `auto_tier`), the suite catalog (`configs/suites.yaml`), the agent harnesses (`configs/agents.yaml`), and a new **harness-defaults file (`configs/settings.yaml`)** that absorbs every operational value currently hardcoded in the source — first as a grouped, provenance-labelled read view, then as validated editors. Edits flow through one safe write pipeline: validated with the same loaders the harness itself uses, written atomically with a timestamped backup, preserving file comments, refusing to clobber concurrent external edits, and never touching any file outside the registered config set. Secrets are handled by reference only — the tab shows *which* environment variable a provider reads and whether it is set, never its value.
**Guiding Principle — nothing hardcoded**: every operational value an operator might reasonably tune (timeouts, ports, default token caps, directories, anchor sets, retention) lives in a YAML file under `configs/` and is visible in the Settings tab. Source-code constants are permitted only as last-resort fallbacks for a missing file/key, and any such fallback must mirror the shipped YAML value. Resolution precedence is fixed and documented: explicit CLI flag > environment variable > YAML setting > built-in fallback.
**Business Value**: Today changing any setting means knowing which of four YAML files owns it, editing it by hand, and finding out at the next CLI run whether the edit was valid. That is fine for FX-as-developer but hostile to FX-as-operator mid-benchmark-campaign: adding a model, pointing a store at a new path, or bumping a suite timeout is a context switch out of the dashboard and into an editor, with no guardrails against typos that silently invalidate a run (a duplicate model name, a concurrency that corrupts local speed metrics, a price table entry that skews cost math). A Settings tab makes the whole configuration visible and safely editable from the same surface that runs the benchmarks, with validation *before* the file is written instead of a stack trace after.
**Success Metrics**: From the dashboard FX can see every setting grouped by domain (models / inferencers / storage / suites / agents) with the file it comes from; add, edit, or remove a model entry and have the result validated and written with comments intact; change a store path, external-repo root, or auto-tier budget and see the tier views pick it up without restarting the dashboard; never see a secret value rendered anywhere; and never lose a hand-made edit — a conflicting external change is detected and surfaced, every write leaves a restorable backup, and an invalid edit is rejected with the loader's real error message before anything touches disk.

## Epic Scope
**Total Stories**: 8 | **Total Points**: 34 | **MVP Stories**: 0 (Should Have / v1.x)

## Decisions Locked With FX (confirmed 2026-07-17)
- **Comment preservation strategy**: **`ruamel.yaml` round-trip editing** (a new dependency) — edits keep the load-bearing comments in the config files (the whole benchmark protocol lives in `models.yaml` comments). Line-level patching was the rejected fallback.
- **Protocol-locked fields (display-only, with rationale)**: local-model `concurrency` (must stay 1 per the measurement protocol) and benchmark temperature/seed (fixed by the reproducibility protocol). Model pins (`model_id` / `pinned_revision`) remain **editable**.
- **Prompts out of scope for v1**: `prompts/*.md` are content, not settings — excluded from the Settings tab for v1; revisit if dashboard editing proves useful.

## Scope Boundaries (explicitly NOT building)
- **No secret management** — API keys stay in the shell environment. The tab shows the `api_key_env` *name* and a set/unset indicator; it never displays, stores, or edits a secret value, and no secret ever appears in a settings API response.
- **No arbitrary file editing** — the write path accepts exactly the registered config files (`models.yaml`, `inferencers.yaml`, `suites.yaml`, `agents.yaml`); it is not a general YAML editor and never follows a client-supplied path.
- **No schema invention in editors** — the section editors expose what the config loaders accept; they never write keys no loader reads. The one deliberate schema addition in this epic is `configs/settings.yaml` (Feature 15.5), whose keys exist precisely because the harness already consumes them as hardcoded constants.
- **No multi-user story** — single operator on localhost; conflict handling is last-write detection against external edits (editor, git pull), not collaborative editing.
- **No remote sync** — settings live in the repo's `configs/` files; no cloud profiles or per-machine overlays.

## Design Reference
- **One read model**: a settings aggregator walks the registered config files with the harness's own loaders (`config.py`: model configs, `InferencerConfig`, external repo, `load_autotier`, suite catalog, agent configs) and produces one grouped, serialisable settings document — each group tagged with its source file and each value round-trippable back to an edit. The dashboard renders that document; it never parses YAML client-side.
- **One write pipeline** (15.2-001) shared by every editor: proposed edit → validate by running the *actual* loader against the edited document (never a parallel schema) → atomic write (temp file + rename) with a timestamped backup alongside → post-write re-read to confirm round-trip. A content hash captured at read time is required on write; a mismatch (file changed externally since the form loaded) rejects the write and surfaces a reload prompt instead of silently clobbering.
- **Secrets by reference**: settings responses carry `api_key_env` names plus a boolean `is_set` resolved server-side; values never leave the server process. Same localhost-only binding as every other dashboard endpoint.
- **Live reload**: dashboard panels already re-read configs per request where cheap; after a successful write the settings API says which domains changed so open panels (models list, tier view, launcher) can refresh themselves without a dashboard restart.

## Features in This Epic

### Feature 15.1: Unified Settings View

#### Stories

##### Story 15.1-001: Read-only Settings tab aggregating every config surface
**User Story**: As FX, I want a Settings tab that shows all harness configuration — models, inferencers, storage paths and tiering, suites, and agents — grouped and labelled with the file each setting comes from, so that I can see the whole configuration without opening four YAML files.
**Priority**: Should Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** the dashboard is running **When** I open the Settings tab **Then** I see the configuration grouped by domain — Models, Inferencers, Storage (local stores + `external_repo` + `auto_tier`), Suites, Agents — each group labelled with its source file, rendered from a single `GET /api/settings` document.
- **Given** a model entry with an `api_key_env` **When** the settings render **Then** the tab shows the environment-variable *name* and a set/unset indicator, and the settings API response contains no secret value anywhere.
- **Given** a config file that is missing or fails to parse **When** the tab loads **Then** that group degrades to an inline error naming the file and the loader's message, while the other groups render normally.
- **Given** any settings group **When** rendered **Then** protocol-locked values (local `concurrency`, benchmark temperature/seed) are visibly marked read-only with a one-line rationale.

**Technical Notes**: Server-side aggregator over the existing `config.py` loaders producing one JSON document; no YAML parsing in the browser. Reuse the dashboard's section/tab pattern (Epic-09). `is_set` for env vars resolved via `os.environ` membership only. This story is view-only — no write path yet.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 09.1-001
**Risk Level**: Low

### Feature 15.2: Safe Write Pipeline

#### Stories

##### Story 15.2-001: Validated, atomic, comment-preserving settings writes
**User Story**: As FX, I want every settings edit validated with the harness's own loaders and written atomically with a backup and conflict detection, so that the dashboard can never produce a config the CLI would reject, silently clobber my hand edits, or lose the comments that document the benchmark protocol.
**Priority**: Should Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** a proposed edit to a registered config file **When** it is submitted **Then** the edited document is validated by running the same loader the harness uses, and a failing edit is rejected with the loader's real error message and no bytes written.
- **Given** a valid edit **When** it is applied **Then** the file is written atomically (temp + rename) with a timestamped backup of the previous version created alongside, and existing YAML comments and key order are preserved.
- **Given** the file changed on disk after the form was loaded (external editor, git pull) **When** the edit is submitted with the stale content hash **Then** the write is refused with a conflict response prompting a reload — never a silent overwrite.
- **Given** any write request naming a file outside the registered config set **When** received **Then** it is rejected outright; the write path resolves its own file paths and never trusts a client-supplied one.

**Acceptance Criteria (rollback)**:
- **Given** a write that fails mid-way (disk error, failed post-write re-read) **When** it aborts **Then** the original file is intact (atomic rename semantics) and the failure is reported with the backup path.

**Technical Notes**: One shared module (e.g. `src/local_code_bench/settings_store.py`) used by all Feature-15.3 editors: read (content + hash) / validate / write (atomic + backup) / conflict check. Comment preservation via `ruamel.yaml` round-trip (pending the dependency decision above); keep the store pure and filesystem-injectable for tests, mirroring the tiering modules. Backups under a bounded, gitignored directory (e.g. `configs/.backups/`) with simple retention.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 15.1-001
**Risk Level**: High

### Feature 15.3: Section Editors

#### Stories

##### Story 15.3-001: Models editor
**User Story**: As FX, I want to add, edit, duplicate, and remove entries in `configs/models.yaml` from the Settings tab — name, endpoint, model id, pinned revision, token caps, extra_body knobs, and the price table — so that growing the benchmark matrix no longer requires hand-editing YAML.
**Priority**: Should Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** the Models group **When** I add or edit an entry **Then** a form exposes the fields the model schema accepts (name, type, base_url, model_id, pinned_revision, api_key_env, concurrency, max_tokens, extra_body, price_per_1k_tokens, inferencer) and the saved result appears in the models list and the benchmark launcher without a dashboard restart.
- **Given** an edit producing a duplicate model name **When** submitted **Then** it is rejected naming the clash before the write pipeline is invoked.
- **Given** a local model entry (an `inferencer`-declaring or localhost-endpoint model) **When** editing **Then** `concurrency` is locked at 1 with the measurement-protocol rationale shown; cloud entries keep it editable.
- **Given** a remove action **When** confirmed (with an explicit confirmation step) **Then** the entry is removed via the same validated write path; there is no bulk-delete.
- **Given** price-table fields **When** edited **Then** non-numeric or negative values are rejected inline before submission.

**Technical Notes**: Thin form over the 15.2-001 pipeline; duplicate/price/lock checks are pre-validation UX on top of (not instead of) the loader validation. `extra_body` is edited as a validated YAML/JSON fragment rather than field-by-field — it is intentionally open-ended.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 15.2-001
**Risk Level**: Medium

##### Story 15.3-002: Inferencers & storage editor
**User Story**: As FX, I want to edit inferencer store paths, the external repository, and the auto-tiering policy from the Settings tab, so that storage configuration — the settings I touch most as the model library grows — is managed where I can see its effects.
**Priority**: Should Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** the Inferencers group **When** rendered **Then** each engine shows its lifecycle, detection, port, `model_store` paths, and format, with store paths and format editable and lifecycle/detection/start argv display-only (they are install facts, not preferences).
- **Given** the Storage group **When** I edit `external_repo` (root, volume_marker, subpaths) or `auto_tier` (max_local_gb, min_free_gb, pins) **Then** the change is validated and written, and the tier view reflects it on next refresh without restarting the dashboard.
- **Given** a store or external-root path edit **When** the path does not currently exist or (for external) lacks its volume marker **Then** the editor warns but does not block — an unplugged SSD is a normal state, not an error.
- **Given** an engine that is currently running **When** its settings are edited **Then** the editor flags that the change applies from the next start, reusing the Epic-08 state to detect the running engine.

**Technical Notes**: Same 15.2-001 pipeline against `inferencers.yaml`. Path hints reuse `expand_store_path` and the Epic-12 availability check; running-engine detection reuses the Epic-08 status API. Pins editor should offer current inventory names (Epic-11) as suggestions rather than free text only.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 15.2-001, 12.1-001
**Risk Level**: Medium

##### Story 15.3-003: Suites & agents editor
**User Story**: As FX, I want to view and edit the suite catalog and agent harness entries from the Settings tab, so that suite defaults (timeouts, task subsets) and agent configurations are adjustable without leaving the dashboard.
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** the Suites group **When** rendered **Then** built-in suites are listed read-only (they are code, not config) and `configs/suites.yaml` entries are editable — names, task selections, per-suite timeout/max_tokens defaults — through the validated write path.
- **Given** the Agents group **When** rendered **Then** `configs/agents.yaml` entries (harness command, workspace policy) are visible and editable with the same pipeline, and fields the agent runner treats as fixed are marked read-only.
- **Given** an edit that would orphan a reference (a suite name used by a saved dashboard launcher selection) **When** submitted **Then** the editor warns about the dangling reference but allows the change.

**Technical Notes**: Reuses the suite catalog loader (`suite_catalog.py`) and agent config loader as validators. Smallest of the editors — mostly wiring existing loaders into the shared pipeline and forms.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 15.2-001
**Risk Level**: Low

### Feature 15.4: Change Safety & Live Reload

#### Stories

##### Story 15.4-001: External-change detection, live reload, and change log
**User Story**: As FX, I want the Settings tab to notice when a config file changes outside the dashboard, apply my saved changes to the running dashboard without a restart, and keep a small log of what changed when, so that the dashboard and my editor never fight over the files and I can always answer "what did I change before the numbers moved?".
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** an open Settings tab **When** a registered config file changes on disk (hash mismatch on poll) **Then** the affected group shows a "changed on disk — reload" banner and blocks stale submissions (15.2-001's conflict check) until reloaded.
- **Given** a successful settings write **When** it completes **Then** the response names the changed domains and open dashboard panels that consume them (models list, launcher, tier view, inventory) refresh without a restart.
- **Given** any write through the pipeline **When** it lands **Then** one line is appended to a settings change log (timestamp, file, domain, summary), viewable from the tab; the log records *that* and *what kind of* change happened, never secret values.
- **Given** a backup created by 15.2-001 **When** I view the change log **Then** each entry links the backup snapshot that restore would use, and restoring is a manual file operation (documented), not a one-click action in v1.

**Technical Notes**: Polling hash check piggybacks on the tab's existing refresh cycle — no filesystem watcher dependency. Change log is an append-only JSONL under the state dir (same conventions as tiering's `LastUsedStore`), bounded by simple rotation. Panel refresh reuses the per-request config re-read the dashboard already does; this story only adds the "which domains changed" signal.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 15.2-001
**Risk Level**: Medium

### Feature 15.5: Nothing Hardcoded — Externalized Harness Defaults

#### Stories

##### Story 15.5-001: Audit hardcoded defaults and externalize them into `configs/settings.yaml`
**User Story**: As FX, I want every operational default currently hardcoded in the source moved into a checked-in `configs/settings.yaml` with a single loader and a fixed precedence order, so that no tunable behaviour is buried in a Python constant.
**Priority**: Should Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** the codebase **When** the audit story completes **Then** a written inventory (in the epic file or a design note) lists every hardcoded operational value with its owner module, and each is either externalized or explicitly ruled a non-setting (with rationale). Known targets at writing time: endpoint suite default `max_tokens` (`runner.DEFAULT_ENDPOINT_MAX_TOKENS` = 1024), chat defaults (`chat.DEFAULT_TEMPERATURE` = 0.7, `DEFAULT_MAX_TOKENS` = 1024), sandbox timeout (5 s), provider timeout (120 s, today env-only via `BENCH_PROVIDER_TIMEOUT_SECONDS`), dashboard hosts/ports (8765 / 8770), suite cache dir (`.cache/benchmarks`), canary anchor set (`CANARY_HUMANEVAL_IDS`), inferencer start/health timeouts (30 s / 1 s), OpenCode build/run timeouts (60 s / 10 s), results and state directories, and the 15.2-001 backup directory/retention.
- **Given** `configs/settings.yaml` exists **When** any consumer needs one of these values **Then** it resolves through one shared loader with the documented precedence (CLI flag > env var > `settings.yaml` > built-in fallback), and the built-in fallback equals the shipped YAML value.
- **Given** `configs/settings.yaml` is absent or a key is missing **When** the harness runs **Then** behaviour is identical to today (fallbacks apply) — the file is additive, never a breaking requirement.
- **Given** a protocol-locked value (benchmark temperature/seed, local-model concurrency) **When** the file is authored **Then** it is either excluded or present under a clearly marked read-only section the loader refuses to override — the settings file must not become a side door around the measurement protocol.

**Technical Notes**: One loader module (e.g. `config.load_settings` or `settings.py`) returning a typed defaults object injected at the existing call sites — the constants become the fallback layer rather than the source of truth. Migrate call sites incrementally but land the loader + file + inventory in one story so the Settings tab (15.5-002) has a complete surface. `BENCH_PROVIDER_TIMEOUT_SECONDS` keeps working as the env layer of the same key.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: None (config layer; precedes the tab surfacing)
**Risk Level**: Medium

##### Story 15.5-002: Harness defaults group in the Settings tab
**User Story**: As FX, I want the externalized harness defaults visible and editable in the Settings tab like every other group, so that "everything in YAML, everything in the tab" holds end to end.
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** the Settings tab **When** it renders **Then** a Harness group shows every `configs/settings.yaml` key with its resolved effective value *and* its source layer (flag / env / yaml / fallback), so an env override is never mistaken for the YAML value.
- **Given** a key currently overridden by an environment variable **When** displayed **Then** the YAML field is editable but the tab states the env override wins until unset — editing never silently loses to an invisible layer.
- **Given** an edit to the Harness group **When** submitted **Then** it flows through the 15.2-001 pipeline (loader validation, atomic write, backup, conflict check) like every other file.
- **Given** read-only protocol-locked entries **When** rendered **Then** they appear with their rationale and no edit affordance, consistent with 15.1-001.

**Technical Notes**: Thin extension of the 15.1-001 aggregator and 15.3 editor pattern; the interesting part is surfacing per-key provenance (which precedence layer produced the effective value), which the 15.5-001 loader should expose rather than the tab recomputing it.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 15.5-001, 15.2-001
**Risk Level**: Low

## Epic Progress
**Completed**: 0 / 8 stories · 0 / 34 points
