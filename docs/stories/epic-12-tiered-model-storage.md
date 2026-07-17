# Epic 12: Tiered Model Storage (Local + External Repository)

## Epic Overview
**Epic ID**: Epic-12
**Description**: Give the model manager a two-tier storage model so the benchmark machine is no longer limited to what fits on its internal disk. Tier 1 is the **local repository** (the engine model stores Epic-11 already scans, on the internal SSD). Tier 2 is an **external repository** — an attached USB/Thunderbolt SSD that holds the bulk of downloaded models and may be mounted or unplugged at any time. Epic-12 adds: external-tier configuration with mount/availability detection; a unified, tier-aware inventory that merges the Epic-11 local scan with an external scan and tracks where each model lives; safe, integrity-checked **move operations** (promote external→local, demote/evict local→external); a **policy-driven auto-tiering** engine that keeps the local tier under a disk budget by evicting least-recently-used models to external; the ability to **serve a model directly from external** when mounted (with an auto-promote-before-benchmark path for clean speed metrics); and the CLI + dashboard surfaces to see and drive it all.
**Business Value**: On a 48 GB M3 Max with finite internal SSD, model storage is the binding constraint — multi-gigabyte quants quickly crowd out the working set. Today FX must manually juggle which models occupy precious internal disk and which get deleted, and a deleted model means a slow re-download. A second tier on an external SSD turns "delete to make room, re-download later" into "evict to external, promote back in seconds", lets FX keep a far larger library on hand, and reclaims internal disk automatically against a budget — without ever losing track of which models are local, which are external-and-offline, and which engine can run each one.
**Success Metrics**: From the CLI or dashboard FX can see every model with its tier (local / external-online / external-offline), its size, and which inferencers can serve it; promote a model from external to local and demote one back, each verified for integrity and refusing to clobber an in-use model; set a local disk budget and have the harness auto-evict LRU models to external to stay under it, while respecting pins; launch a benchmark against an external model and have it transparently promoted (or served in place) first; and never have the harness crash, mis-report, or lose a model when the external SSD is unplugged mid-session.

## Epic Scope
**Total Stories**: 9 | **Total Points**: 42 | **MVP Stories**: 0 (Should Have / v1.x)

## Decisions Locked With FX
- **External medium**: an attached USB/Thunderbolt **SSD**, identified by a configured mount path plus a stable volume marker so it can be recognised as the *same* external repo across remounts and reported as offline when unplugged. (Network shares / cloud object stores are explicitly out of scope — see Scope Boundaries.)
- **Capabilities**: all four — visibility, promote (external→local), demote/evict (local→external), and serve-directly-from-external when mounted.
- **Placement policy**: **policy-driven auto-tiering** — a local disk budget with LRU eviction to external — in addition to explicit manual promote/demote.
- **Epic shape**: a new Epic-12 that **builds on Epic-11** (local scanner, normalized `LocalModel`, content identity, sharing detection) and reuses Epic-08 (config + `inferencer` CLI) and Epic-09 (dashboard). Epic-11 stays the single-tier local view; Epic-12 adds the external tier and the movement layer on top.

## Scope Boundaries (explicitly NOT building)
- **No network / cloud tier** — NAS (SMB/NFS) and remote object stores (S3, HF private) are deferred. The external-store abstraction should leave room for a pluggable backend later, but only attached-filesystem (`os`/`pathlib`) movement is implemented now.
- **No model downloading** — Epic-12 moves models that already exist between tiers; acquiring new models from the internet remains out of this epic.
- **No multi-machine sync** — a single external SSD attached to the one benchmark machine; no replication or shared-library semantics across hosts.
- **No re-quantisation / format conversion on move** — a move is byte-faithful; converting a GGUF to MLX is not a tiering operation.

## Design Reference
- **Tier model**: `local` (Epic-11 stores on internal disk) and `external` (a configured root on the external SSD, mirroring the per-format store layout so the same scan strategies apply). A model's **content identity** (Epic-11: realpath / Ollama blob sha) is the join key across tiers.
- **External availability**: `mounted` vs `offline`, decided by mount-point existence + a volume marker file written into the external root (so a coincidentally-present empty mount path is not mistaken for the real repo). All read paths degrade gracefully when offline; all move/serve paths require `mounted`.
- **Move safety**: atomic copy-then-verify-then-remove (never delete the source before the destination is verified), an in-use guard (refuse to move a model an active inferencer is serving), and rollback on partial failure.
- **Metrics caveat**: serving from external SSD slows *model load*, not steady-state prefill/decode (weights are resident in unified memory after load). For clean, comparable speed numbers the default benchmark path **promotes to local first**; serve-from-external is an explicit opt-in for ad-hoc/quick runs.

## Features in This Epic

### Feature 12.1: External Tier Configuration & Availability

#### Stories

##### Story 12.1-001: Configure the external repository and detect its availability
**User Story**: As FX, I want to configure an external SSD as a second model repository and have the harness reliably detect whether it is mounted, so that tier-aware features work whether the drive is plugged in or not.
**Priority**: Should Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** config extended with an `external_repo` root path (and per-format subpaths mirroring the Epic-11 local store layout) **When** the config loads **Then** the external-tier metadata is parsed and validated alongside the existing Epic-08/Epic-11 fields, with `~` expansion, and existing single-tier configs remain valid (external tier optional/defaulted).
- **Given** the external SSD is plugged in and contains the volume marker **When** availability is checked **Then** the tier reports `mounted`.
- **Given** the external root path is absent, or present but missing the volume marker **When** availability is checked **Then** the tier reports `offline` rather than raising, and no scan/move is attempted.
- **Given** a first-time setup against an empty external SSD **When** the repo is initialised **Then** the marker and per-format directory skeleton are created so subsequent runs recognise it.

**Technical Notes**: Extend the Epic-11 store config in `config.py`; add `external_repo` describing the second-tier root and a `volume_marker` filename. Availability check is filesystem-only and Darwin-aware (`/Volumes/...` style mounts) like `power.py`, kept pure for testability (monkeypatch a base dir). The external root deliberately mirrors the local per-format layout so Epic-11's scan strategies are reused unchanged against a different root.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: 11.1-001
**Risk Level**: Medium

### Feature 12.2: Two-Tier Unified Inventory

#### Stories

##### Story 12.2-001: Tier-aware inventory merging local and external stores
**User Story**: As FX, I want one inventory that tells me, for every model, whether it is on the local disk, on the external SSD, or both — and whether the external copy is currently reachable — so that I always know where a model lives without guessing.
**Priority**: Should Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** both tiers configured and the external SSD mounted **When** the inventory is built **Then** each `LocalModel` carries a `tier` (`local` | `external`) and the inventory exposes per-model presence across tiers keyed by Epic-11 content identity.
- **Given** the same logical model present on both tiers **When** merged **Then** it is reported once as present-in-both (a redundant-storage candidate for the disk report), not as two unrelated models.
- **Given** the external SSD is offline **When** the inventory is built **Then** local models scan normally and external models are listed as `external (offline)` from the last known catalog (or simply omitted with a clear "external offline" notice if no cache exists), without error.
- **Given** a model only on external while mounted **When** the inventory is built **Then** it appears with `tier=external`, its size, format, quant/provider (reusing Epic-11 provenance), and the inferencers that could serve it.

**Technical Notes**: Reuse Epic-11's scan strategies and `LocalModel`/sharing logic against the external root; add a `tier` field (kept backward-compatible) and a small merge over content identity. Consider persisting a lightweight external catalog (path, identity, size) so the offline view is non-empty; treat it as a cache, never as truth when the drive is mounted.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: 12.1-001, 11.2-001, 11.3-001
**Risk Level**: Medium

**Status**: ✅ Done — `LocalModel` gained a backward-compatible `tier` field and
`inventory.scan_store` (the per-directory scan unit shared by local and external
scans). `inferencers/tiered.py` adds the unified view: `scan_external_tier`
reuses the Epic-11 strategies against the external root, `merge_tiers` collapses
each logical model to one row keyed tier-independently (Ollama blob sha; else
`(format, name)`) — flagging `present_in_both` — and `build_tiered_inventory`
ties it together, persisting a lightweight external catalog when mounted and
falling back to it (flagged `external_cached`) when the SSD is offline, all
without error when the drive is unplugged.

### Feature 12.3: Move Operations (Promote / Demote)

#### Stories

##### Story 12.3-001: Promote a model from external to local (atomic, integrity-checked)
**User Story**: As FX, I want to promote a model from the external SSD to local disk safely, so that I can run it on fast local storage without risking a corrupt or half-copied model.
**Priority**: Should Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** a model present on a mounted external tier and absent locally **When** promote runs **Then** it is copied to the correct per-format local store, verified (size and/or content hash) against the source, and only then made visible as `tier=local`.
- **Given** insufficient local free space for the model **When** promote is requested **Then** it fails fast with a clear error and a suggested amount to free, leaving both tiers untouched.
- **Given** the external SSD is offline, or the model is currently being served by an active inferencer **When** promote is requested **Then** it is refused with an explanatory error and no bytes are moved.
- **Given** a copy interrupted mid-way (failure or integrity mismatch) **When** promote aborts **Then** the partial local copy is cleaned up and the external source is left intact (no data loss).

**Technical Notes**: New move module (e.g. `src/local_code_bench/inferencers/tiering.py`). Copy-then-verify-then-(optionally)-remove; promote does not delete the external source by default (it becomes a present-in-both redundancy the disk report can flag). Reuse the Epic-08 active-inferencer state for the in-use guard. Integrity = size match plus a content hash where cheap; for Ollama use the blob sha already in the identity.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: 12.2-001
**Risk Level**: High

##### Story 12.3-002: Demote / evict a model from local to external
**User Story**: As FX, I want to demote a model from local disk out to the external SSD, so that I can reclaim internal disk while keeping the model one promote away.
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** a local model and a mounted external tier **When** demote runs **Then** it is copied to the external store, verified, and only then removed from local, freeing the local bytes.
- **Given** the model already exists and verifies on external **When** demote runs **Then** the redundant copy is reused (no re-copy) and the local copy is removed, reclaiming space immediately.
- **Given** the model is currently being served, or the external SSD is offline, or external lacks free space **When** demote is requested **Then** it is refused with a clear error and the local copy is preserved.
- **Given** an interrupted demote **When** it aborts **Then** the local copy is never removed unless a verified external copy exists (no path to data loss).

**Technical Notes**: Reuse the 12.3-001 copy/verify/guard primitives; demote is the mirror operation with the destination/source swapped and the delete-after-verify guaranteed to run only against a verified external copy. Share free-space and in-use checks.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: 12.3-001
**Risk Level**: Medium

**Status**: ✅ Done — `tiering.demote_model` (with `plan_demotion` / `DemotePlan` /
`DemoteResult` / `DemoteError`) is the mirror of promote: copy local → external
staging → verify (size + content hash) → atomic publish, and only once a verified
external copy exists is the local copy removed. A verified existing external copy is
reused (no re-copy). Refuses up front (offline / in-use / missing source / divergent
external copy / no space) and aborts cleanly on any failure, always preserving the
local copy until a verified external copy exists.

### Feature 12.4: Auto-Tiering Policy

#### Stories

##### Story 12.4-001: Disk-budget + LRU auto-tiering with pinning and dry-run
**User Story**: As FX, I want the harness to keep my local tier under a disk budget by automatically evicting the least-recently-used models to external, so that internal disk stays healthy without me babysitting it.
**Priority**: Should Have
**Story Points**: 8

**Acceptance Criteria**:
- **Given** a configured local disk budget (max GB or min-free GB) and recorded last-used timestamps **When** the policy evaluates **Then** it selects LRU local models to demote until the budget is satisfied, and reports the plan.
- **Given** `--dry-run` (default for safety) **When** the policy runs **Then** it prints exactly which models it would evict and the bytes reclaimed, moving nothing until explicitly applied.
- **Given** a model marked **pinned** **When** the policy selects eviction candidates **Then** pinned models are never evicted, even if it means the budget cannot be fully met (which is surfaced as a warning).
- **Given** the external SSD is offline when the policy runs **When** evaluated **Then** it makes no changes and reports that auto-tiering is paused until the external repo is available.
- **Given** the policy applies evictions **When** it runs **Then** each eviction reuses the verified 12.3-002 demote path (no unsafe deletes) and last-used data is updated.

**Technical Notes**: Policy engine over the unified inventory + a last-used signal (benchmark run history / serve events; fall back to file mtime). Pure planner returning an eviction plan, with apply delegating to demote. Pin list in config or a small state file. Keep the planner deterministic and side-effect-free for testing; the apply step is the only one that touches disk.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: 12.3-002
**Risk Level**: High

**Status**: ✅ Done — `inferencers/autotier.py` adds the policy engine. `plan_autotier`
is a pure, deterministic planner: it collapses local models to logical artifacts
(counting a shared download once), computes the shortfall as the stricter of a
`max_local_gb` footprint cap and a `min_free_gb` floor, ranks non-pinned models
least-recently-used first (ties broken by name) and selects evictions until the budget
is met — returning an `AutoTierPlan` (evictions, bytes reclaimed, `satisfied`,
warnings) that moves nothing, so the CLI/dashboard `--dry-run` is just "plan and
print". Pinned models are never selected (shortfall surfaced as a warning); when the
external tier is offline the plan is `paused` with no evictions. `apply_plan` is the
only disk-touching step: it replays each eviction through the verified
`tiering.demote_model` path (12.3-002) and records the move in a `LastUsedStore`
(recorded benchmark/serve timestamps keyed by content identity, mtime fallback).
Budget + pins are configured via an optional `auto_tier` block (`config.load_autotier`).

### Feature 12.5: Serve-From-External & Benchmark Integration

#### Stories

##### Story 12.5-001: Serve directly from external, with auto-promote-before-benchmark
**User Story**: As FX, I want to launch a benchmark against a model that lives on the external SSD and have the harness do the right thing — promote it local first for clean metrics, or serve it in place for a quick run — so that the external tier is usable end-to-end, not just storage.
**Priority**: Should Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** a benchmark targets an external-only model and the SSD is mounted **When** the run starts **Then** the default behaviour promotes it to local first (12.3-001), and the run records that a promotion occurred in its metadata.
- **Given** `--serve-from-external` (opt-in) **When** the run starts **Then** the inferencer is pointed at the external path without copying, and the run metadata flags that speed numbers include external load (so they are not silently compared against local-loaded runs).
- **Given** the targeted external model and the SSD is offline **When** the run starts **Then** it fails fast with a clear "external repo offline — plug in the SSD or choose a local model" error before any model is loaded.
- **Given** a model already present locally **When** a run starts **Then** no promotion or external serving occurs (local is always preferred).

**Technical Notes**: Hook the tiering resolver into the benchmark launch path (Epic-09 launcher / Epic-08 auto-start). Promotion reuses 12.3-001; serve-from-external just resolves the inferencer's model path to the external location. Record tier provenance in the run metadata so the leaderboard/dashboard can caveat external-served speed.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: 12.3-001, 09.2-001
**Risk Level**: Medium

### Feature 12.6: CLI & Dashboard Surfaces

#### Stories

##### Story 12.6-001: CLI tier inventory and move commands
**User Story**: As FX, I want CLI commands to see which tier each model is on and to promote, demote, and run auto-tiering, so that I can manage storage from the terminal.
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** the Epic-11 `bench inferencer models` listing **When** run **Then** it gains a `tier` column (`local` / `external` / `external-offline`) and a `--tier` filter.
- **Given** `bench inferencer promote <model>` / `demote <model>` **When** run **Then** the corresponding verified move executes, with progress and a clear final summary (bytes moved, new tier).
- **Given** `bench inferencer tier --apply` (and default `--dry-run`) **When** run **Then** it shows or applies the auto-tiering plan from 12.4-001.
- **Given** any tier/move failure (offline SSD, no space, in-use, bad model) **When** the command runs **Then** it prints `bench: error: ...` and exits 2, consistent with existing commands.

**Technical Notes**: Extend the Epic-08 `inferencer` subcommand with `promote`/`demote`/`tier` verbs and tier columns/filters on `models`, reusing the existing table-rendering and error-mapping conventions. Thin CLI over the 12.2–12.4 logic; no business logic in the command layer.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: 12.4-001, 11.4-001, 08.4-001
**Risk Level**: Low

**Status**: ✅ Done — `bench inferencer` gained a `tier` column plus `--tier`
filter on `models` (live external scan when the SSD is mounted, the persisted
offline catalog otherwise), and `promote` / `demote` / `tier` verbs as a thin
shell over the 12.2–12.4 logic: `promote`/`demote` run the verified
`tiering.promote_model` / `demote_model` moves with a bytes-moved + new-tier
summary, and `tier` shows the `autotier.plan_autotier` dry-run plan by default
and applies it via the verified demote path with `--apply`. Every refusal
(offline SSD, missing model, no `external_repo`/`auto_tier`, or a move guard)
maps to `bench: error: ...` with exit 2, consistent with the other verbs.

##### Story 12.6-002: Dashboard tier view and move controls
**User Story**: As FX, I want the dashboard inventory panel to show each model's tier and let me promote/demote and trigger auto-tiering with a click, so that I can manage storage visually alongside everything else.
**Priority**: Should Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** the Epic-11 inventory panel **When** rendered **Then** each model shows its tier and external availability, with present-in-both and reclaimable-space hints from the disk report.
- **Given** a model row **When** I trigger promote or demote **Then** the verified move runs server-side with live progress, and the panel refreshes the model's tier on completion.
- **Given** the auto-tiering plan **When** I open the tiering view **Then** it shows the dry-run plan (evictions + bytes reclaimed) with an explicit apply action that respects pins.
- **Given** the external SSD is offline **When** the panel loads **Then** external models are clearly marked offline and move/tier actions are disabled with an explanation; the endpoint binds localhost only and leaks no host-sensitive paths beyond what identifies a model.

**Technical Notes**: Extend the Epic-09/Epic-11 inventory section (11.5-001) with tier badges and move/tier controls backed by the dashboard's localhost server calling the 12.2–12.4 logic. Reuse the dashboard's live-progress mechanism for moves; keep the panel a thin client over the tiering API.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: 12.4-001, 11.5-001, 09.1-001
**Risk Level**: Medium

**Status**: ✅ Done — the unified dashboard's Inventory section gained a tier-aware
**Storage tiers** view (`GET /api/tiers`): one row per logical model with its tier
badge, the external SSD's availability, present-in-both flagging, and an across-tier
reclaimable hint. Per-row **Promote** / **Demote** controls call `POST /api/promote` /
`POST /api/demote` (the verified 12.3 move path) and refresh the model's tier on
completion; an **Auto-tiering** sub-panel renders the 12.4 dry-run eviction plan
(`GET /api/tier-plan`) with an explicit **Apply** action (`POST /api/tier-apply`) that
respects pins. When the SSD is offline, external rows are marked offline and all
move/apply actions are disabled; every tier endpoint projects only model-identity
fields (never an on-disk path) and binds localhost only.

##### Story 12.6-003: Non-blocking dashboard moves with live progress
**User Story**: As FX, I want promote/demote to run in the background with live byte progress in the dashboard, so that a multi-gigabyte move never freezes the UI while it copies.
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** a promote or demote is triggered from the tier view **When** the request is valid **Then** it returns immediately (`202` + a job snapshot) and the copy runs on a background worker — every dashboard panel stays responsive for the duration of the move.
- **Given** a move is running **When** the client polls `GET /api/move-status` **Then** it reports verb, model, live bytes copied / total (measured from the move's staging path), percent, and elapsed seconds — identity fields only, never an on-disk path — and the tier view's move buttons are disabled.
- **Given** a move is running **When** a second move or a tier-apply is requested **Then** it is refused with `409` — exactly one operation mutates the stores at a time.
- **Given** the move completes or fails **When** the status is polled **Then** the result payload (or the tiering error, verbatim) is reported, the panel refreshes the model's tier, and a page reloaded mid-move resumes the live progress display.

**Technical Notes**: `MoveWorker` in `unified_dashboard.py`: one background daemon thread, refusals stay synchronous on the request thread (offline / unknown model / busy), progress measured via the new public `tiering.staging_path` helper. Move safety is unchanged — the worker runs the same copy → verify → atomically-publish tiering path, so a dashboard killed mid-move leaves both tiers intact and the next move cleans the stale staging.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: 12.6-002
**Risk Level**: Medium

**Status**: ✅ Done — `POST /api/promote` / `POST /api/demote` now validate up front and
return `202` with a job snapshot while the verified move runs on the single-slot
`MoveWorker`; `GET /api/move-status` reports live staging-path byte progress, the
result payload, or the move error verbatim. A second move or `tier-apply` during a
running move is refused with `409`. The tier view polls the status once per second,
shows "Promoting/Demoting <model>… X of Y (Z%), Ns elapsed", disables move buttons
while busy, resumes the display after a page reload mid-move, and refreshes the
model's tier on completion.

## Epic Progress
**Completed**: 9 / 9 stories · 42 / 42 points
- 12.1-001 — External repo config + mount/availability detection (Should, 5 pts)
- 12.2-001 — Tier-aware inventory merging local + external stores (Should, 5 pts)
- 12.3-001 — Promote a model from external to local (Should, 5 pts)
- 12.3-002 — Demote / evict a model from local to external (Should, 3 pts)
- 12.4-001 — Disk-budget + LRU auto-tiering with pinning and dry-run (Should, 8 pts)
- 12.5-001 — Serve directly from external, auto-promote-before-benchmark (Should, 5 pts)
- 12.6-001 — CLI tier inventory and move commands (Should, 3 pts)
- 12.6-002 — Dashboard tier view and move controls (Should, 5 pts)
- 12.6-003 — Non-blocking dashboard moves with live progress (Should, 3 pts)
