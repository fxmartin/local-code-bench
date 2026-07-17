# Epic 15: Settings Management (Dashboard Settings Tab)

## Epic Overview
**Epic ID**: Epic-15
**Description**: Give the unified dashboard (Epic-09) a **Settings tab** that surfaces every piece of harness configuration in one place — the model matrix (`configs/models.yaml`), the inferencer registry with its storage paths and tiering policy (`configs/inferencers.yaml`, including `external_repo` and `auto_tier`), the suite catalog (`configs/suites.yaml`), and the agent harnesses (`configs/agents.yaml`) — first as a grouped, provenance-labelled read view, then as validated editors. Edits flow through one safe write pipeline: validated with the same loaders the harness itself uses, written atomically with a timestamped backup, preserving file comments, refusing to clobber concurrent external edits, and never touching any file outside the registered config set. Secrets are handled by reference only — the tab shows *which* environment variable a provider reads and whether it is set, never its value.
**Business Value**: Today changing any setting means knowing which of four YAML files owns it, editing it by hand, and finding out at the next CLI run whether the edit was valid. That is fine for FX-as-developer but hostile to FX-as-operator mid-benchmark-campaign: adding a model, pointing a store at a new path, or bumping a suite timeout is a context switch out of the dashboard and into an editor, with no guardrails against typos that silently invalidate a run (a duplicate model name, a concurrency that corrupts local speed metrics, a price table entry that skews cost math). A Settings tab makes the whole configuration visible and safely editable from the same surface that runs the benchmarks, with validation *before* the file is written instead of a stack trace after.
**Success Metrics**: From the dashboard FX can see every setting grouped by domain (models / inferencers / storage / suites / agents) with the file it comes from; add, edit, or remove a model entry and have the result validated and written with comments intact; change a store path, external-repo root, or auto-tier budget and see the tier views pick it up without restarting the dashboard; never see a secret value rendered anywhere; and never lose a hand-made edit — a conflicting external change is detected and surfaced, every write leaves a restorable backup, and an invalid edit is rejected with the loader's real error message before anything touches disk.

## Epic Scope
**Total Stories**: 6 | **Total Points**: 26 | **MVP Stories**: 0 (Should Have / v1.x)

## Decisions To Confirm With FX
- **Comment preservation strategy**: config files carry load-bearing comments (the whole benchmark protocol lives in `models.yaml` comments). The design assumes round-trip YAML editing (`ruamel.yaml` — a new dependency) so edits keep comments; the fallback is targeted line-level patching. Confirm the dependency is acceptable before 15.2-001 starts.
- **Protocol-locked fields**: which settings are display-only with a rationale instead of editable — proposed: local-model `concurrency` (must stay 1 per the measurement protocol), benchmark temperature/seed (fixed by the reproducibility protocol). Confirm the list.
- **Prompts in scope?** `prompts/*.md` are configuration-adjacent. Proposed: out of scope for v1 of this epic (they are content, not settings); revisit if editing them from the dashboard proves useful.

## Scope Boundaries (explicitly NOT building)
- **No secret management** — API keys stay in the shell environment. The tab shows the `api_key_env` *name* and a set/unset indicator; it never displays, stores, or edits a secret value, and no secret ever appears in a settings API response.
- **No arbitrary file editing** — the write path accepts exactly the registered config files (`models.yaml`, `inferencers.yaml`, `suites.yaml`, `agents.yaml`); it is not a general YAML editor and never follows a client-supplied path.
- **No schema invention** — the editors expose what the existing `config.py` loaders accept; a config feature that does not exist in the harness is not added here.
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

## Epic Progress
**Completed**: 0 / 6 stories · 0 / 26 points
