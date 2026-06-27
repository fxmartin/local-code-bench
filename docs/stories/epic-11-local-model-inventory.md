# Epic 11: Local Model Inventory & Sharing

## Epic Overview
**Epic ID**: Epic-11
**Description**: Give the harness and dashboard a format-aware view of the models actually downloaded on the benchmark machine, organised per inferencer, and detect when several inferencers could serve the same on-disk model. The inferencer epics (Epic-08 lifecycle, Epic-09 unified dashboard) can detect and control engines but are blind to local model storage, which differs by engine and format — GGUF files, the Ollama content-addressed blob store, MLX / HuggingFace safetensors caches, and the LM Studio / GPT4All model directories. Epic-11 adds a scanner that reads each store with the right strategy, a normalized model-inventory record carrying format/size/quant/provider, shared-repository detection so the same repository is recognised across compatible engines, and the CLI + dashboard surfaces to view it all.
**Business Value**: On a 48GB box, disk and clarity both matter. FX cannot currently see what is downloaded, in which format, or whether two engines are quietly holding (or could share) the same multi-gigabyte repository. That leads to redundant downloads, wasted disk, and guesswork when choosing what to benchmark. Surfacing the inventory — and the fact that, say, three MLX-based engines can all serve one HuggingFace cache entry, or that a model exists twice as GGUF and MLX — turns "what can I actually run, and what's eating my disk?" into a glance, and lets the Epic-09 launcher map a chosen downloaded model onto whichever installed engine can run it.
**Success Metrics**: From the CLI or dashboard FX can list every downloaded model per inferencer with its format, quant, and size; see which models are shareable across multiple inferencers (one logical model, several capable engines); pick a downloaded model in the launcher and have it mapped to a compatible inferencer; and read a disk-footprint report that flags duplicate downloads and the bytes a shared repository would reclaim.

## Epic Scope
**Total Stories**: 6 | **Total Points**: 24 | **MVP Stories**: 0 (Should Have / v1.x)

## Decisions Locked With FX
- **Placement**: a new Epic-11 depending on Epic-08 (inferencer registry/detection) and Epic-09 (dashboard surface); both remain intact.
- **Provenance reuse**: parse quant string and source provider (the Unsloth-vs-Bartowski lesson from Epic-10) from model paths/filenames where possible.

## Format / Store Landscape (scanner design reference)
- **GGUF** — `*.gguf` files in configured model dirs (llama.cpp and other GGUF consumers).
- **Ollama** — content-addressed blob store (`~/.ollama/models`: `manifests/` + sha256 `blobs/`), not plain files.
- **MLX / HuggingFace safetensors** — HF hub cache (`~/.cache/huggingface/hub`, `models--org--repo` dirs); used by MLX-LM, DFlash, TurboQuant, vLLM-mlx, MLC-LLM, Exo.
- **LM Studio** — its models dir (`~/.cache/lm-studio/models` or `~/.lmstudio/models`), GGUF + MLX.
- **GPT4All** — its app-support models dir, GGUF.

## Features in This Epic

### Feature 11.1: Model Store Discovery

#### Stories

##### Story 11.1-001: Per-inferencer model-store config and format-aware scanner
**User Story**: As FX, I want each inferencer's local model store scanned with a strategy that understands its format so that I can see what is downloaded for every engine.
**Priority**: Should Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** `configs/inferencers.yaml` extended with each engine's `model_store` path(s) and `format` (gguf | ollama | hf-safetensors | mlx) **When** the config loads **Then** the store metadata is parsed and validated alongside the existing Epic-08 fields.
- **Given** an installed engine with a populated store **When** the scanner runs **Then** the store is read by the format-appropriate strategy (GGUF file glob, Ollama manifest+blob parse, HF `models--*` cache walk, LM Studio / GPT4All dirs) and the present models are listed.
- **Given** a missing or empty store, or a non-Darwin platform path **When** scanning **Then** it yields no rows rather than raising, with `~` expansion handled.

**Technical Notes**: New `src/local_code_bench/inferencers/inventory.py` with a per-format scan strategy dispatched on the config's `format`. Reuse the Epic-08 config-loading style in `config.py` for the new `model_store`/`format` fields (optional, defaulted, so existing entries stay valid). Darwin-aware path handling like `power.py`. Keep each strategy pure and filesystem-only for testability (monkeypatch a base dir / `pathlib` walks, as `tests/test_config.py` and the planned Epic-08 detection tests do).

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 08.1-001
**Risk Level**: Medium

### Feature 11.2: Inventory Data Model & Provenance

#### Stories

##### Story 11.2-001: Normalized LocalModel records with provenance
**User Story**: As FX, I want each discovered model normalized into one record with format, size, quant, and provider so that inventory views and sharing detection use a consistent shape.
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** a discovered model **When** normalized **Then** a frozen `LocalModel` record carries repo/name, format, on-disk path, size in bytes, quant string, provider, owning inferencer, and a content identity.
- **Given** a model path/filename **When** parsed **Then** the quant (e.g. `IQ3_XXS`, `Q4_K_M`) and source provider (Unsloth/Bartowski) are extracted where present, reusing the Epic-10 provenance-parsing approach.
- **Given** a model with no recognisable quant or provider **When** normalized **Then** those fields degrade to null without failing the scan.
- **Given** the same file scanned twice **When** records are produced **Then** the content identity is stable across scans.

**Technical Notes**: `LocalModel` frozen dataclass in `inventory.py`. Content identity = `os.path.realpath` of the model file/dir (or the Ollama blob sha) so it is symlink-stable and reused by sharing detection (11.3). Quant/provider parsing shared with Epic-10's scorecard provenance logic to avoid two implementations.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 11.1-001
**Risk Level**: Low

### Feature 11.3: Shared-Repository Detection

#### Stories

##### Story 11.3-001: Detect models usable by multiple inferencers
**User Story**: As FX, I want to see when several inferencers can serve the same on-disk model so that I am not downloading the same repository more than once and the launcher can map a model to a capable engine.
**Priority**: Should Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** the normalized inventory **When** sharing is computed **Then** models are grouped by (format, content identity), where identity is realpath or the Ollama blob sha.
- **Given** two engines pointing at the same HF cache or the same `.gguf` file **When** grouped **Then** they are reported as sharing one logical model with both engines listed.
- **Given** models in incompatible formats **When** grouped **Then** they are not falsely merged.
- **Given** a model owned by a single engine **When** grouped **Then** it shows a single owner and is not flagged as shared.

**Technical Notes**: A pure grouping function over `LocalModel`s in `inventory.py` returning sharing sets. Format compatibility is the first key so an MLX and a GGUF copy of the same base model are never merged as one stored artifact (they are surfaced separately, and the duplicate-download case is handled by 11.6). Symlinked/shared directories collapse via realpath.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: 11.2-001
**Risk Level**: Medium

### Feature 11.4: CLI Surface

#### Stories

##### Story 11.4-001: `bench inferencer models` listing
**User Story**: As FX, I want a CLI command to list downloaded models per inferencer and the shared ones so that I can inspect inventory from the terminal.
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** `bench inferencer models` **When** run **Then** it prints a table of downloaded models per inferencer with format, quant, and size.
- **Given** `--shared` **When** run **Then** it shows the sharing sets — each logical model and the inferencers that can serve it.
- **Given** `--json` **When** run **Then** it emits the inventory as JSON.
- **Given** a config or scan failure **When** the command runs **Then** it prints `bench: error: ...` and exits 2, consistent with existing commands.

**Technical Notes**: Extend Epic-08's `inferencer` subcommand (08.4-001) with a `models` verb rather than a new top-level command. Reuse the table-rendering and error-mapping conventions from the Epic-08 CLI work.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 11.3-001, 08.4-001
**Risk Level**: Low

### Feature 11.5: Dashboard Inventory View

#### Stories

##### Story 11.5-001: Model inventory panel in the unified dashboard
**User Story**: As FX, I want a dashboard panel showing downloaded models per inferencer and the shared ones, linked to the launcher, so that I can pick a local model visually and run it on a capable engine.
**Priority**: Should Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** the unified dashboard **When** I open the inventory section **Then** it shows per-inferencer models grouped by format, with quant and size.
- **Given** the shared-models view **When** rendered **Then** each logical model lists the inferencers that can serve it.
- **Given** a downloaded model **When** I select it **Then** the launcher (Epic-09) is pre-filled with that model and a compatible inferencer.
- **Given** any inventory response **When** served **Then** it binds localhost only and exposes no secrets or host-sensitive paths beyond what identifies a model.

**Technical Notes**: A new section in the Epic-09 unified dashboard (09.1-001) fed by the inventory scanner's JSON, reusing the dashboard's localhost server and the launcher form (09.2-001) for the cross-link. No business logic duplicated — the section is a thin client over the scanner output.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 11.3-001, 09.1-001, 09.2-001
**Risk Level**: Medium

### Feature 11.6: Disk Usage & Duplicate Storage

#### Stories

##### Story 11.6-001: Disk footprint and duplicate-download report
**User Story**: As FX, I want a disk-usage report that flags duplicate downloads so that I can reclaim space by consolidating onto a shared repository.
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** the inventory **When** the report is generated **Then** it shows total bytes per format and per engine.
- **Given** the same base model downloaded more than once (e.g. as GGUF and as MLX, or duplicated across stores) **When** the report runs **Then** it is flagged with the reclaimable bytes consolidation would save.
- **Given** a model present in a single copy **When** the report runs **Then** it is not flagged as duplicate.

**Technical Notes**: A summarisation function over the inventory + sharing sets. Distinguish "shared" (one stored artifact, several capable engines — good) from "duplicated" (the same base model materialised more than once on disk — reclaimable). Surface the report in both the CLI (`models --disk`) and the dashboard panel.

**Definition of Done**:
- [x] Code implemented and peer reviewed
- [x] Tests written and passing
- [x] Documentation updated

**Dependencies**: 11.3-001
**Risk Level**: Low

## Epic Progress
**Completed**: 4 / 6 stories · 16 / 24 points
