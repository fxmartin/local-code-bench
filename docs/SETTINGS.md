# Settings — `configs/settings.yaml` (story 15.5-001)

Every operational default lives in one checked-in file, `configs/settings.yaml`,
resolved by one loader, `local_code_bench.settings`. Nothing tunable is buried in
a Python constant: the constants still exist, but they are the *fallback layer*
seeded from the loader, not the source of truth.

## Precedence

For every key, highest wins:

1. **CLI flag** — an explicitly passed flag (e.g. `--port`, `--cache-dir`,
   `--timeout`) always wins. Argparse defaults are seeded from the loader, so an
   omitted flag falls through to the layers below.
2. **Env var** — only where one is documented. Today that is
   `BENCH_PROVIDER_TIMEOUT_SECONDS`, the env layer of
   `endpoint.provider_timeout_seconds`, read at request time as before.
3. **`configs/settings.yaml`** — the checked-in file.
4. **Built-in fallback** — the `Settings` dataclass defaults in
   `src/local_code_bench/settings.py`.

The file is additive: if it is absent, or a key is missing, the built-in
fallbacks apply and behaviour is identical to a checkout without the file. The
shipped file intentionally equals the fallbacks; `tests/test_settings.py` locks
that invariant so the two layers cannot drift.

Unknown keys, wrong types, non-positive timeouts/ports, and protocol overrides
are rejected with a `SettingsError` naming the offending `section.key`.

## Keys

| YAML key | Fallback | Consumer | Meaning |
|---|---|---|---|
| `endpoint.max_tokens` | `1024` | `runner.DEFAULT_ENDPOINT_MAX_TOKENS` | Generation cap for suite runs when a model config sets none (CLI `--max-tokens`) |
| `endpoint.provider_timeout_seconds` | `120.0` | `provider._provider_timeout_seconds` | HTTP timeout for provider requests (env `BENCH_PROVIDER_TIMEOUT_SECONDS`) |
| `chat.temperature` | `0.7` | `chat.DEFAULT_TEMPERATURE` | Interactive dashboard-chat sampling (not the benchmark protocol) |
| `chat.max_tokens` | `1024` | `chat.DEFAULT_MAX_TOKENS` | Interactive dashboard-chat generation cap |
| `sandbox.timeout_seconds` | `5.0` | `sandbox` / `scoring` defaults | Per-task scoring timeout for generated code (CLI `--timeout`) |
| `dashboard.host` | `127.0.0.1` | all dashboard servers, CLI `--host` | Bind host (localhost only) |
| `dashboard.port` | `8770` | `dashboard_server`, CLI `--port` | `bench --mode dashboard --serve` results dashboard |
| `dashboard.unified_port` | `8765` | `unified_dashboard`, `inferencers.dashboard`, `launch` | `bench dashboard` / `bench inferencer dashboard` |
| `dashboard.state_file` | `.runtime/dashboard.json` | CLI `--state-file` | Dashboard PID/state file |
| `paths.cache_dir` | `.cache/benchmarks` | `tasks` / `suite_catalog` / `launch` / `unified_dashboard`, CLI `--cache-dir` | Benchmark dataset cache |
| `paths.results_dir` | `results` | CLI `--results-dir`, `unified_dashboard` | Raw JSONL run output |
| `paths.inferencer_state_dir` | `.runtime/inferencers` | CLI `--state-dir` / `--inferencer-state-dir` | Per-engine PID/state files |
| `inferencer.start_timeout_seconds` | `30.0` | `inferencers.manager.start` | Wait for a spawned server to become healthy |
| `inferencer.health_timeout_seconds` | `1.0` | `inferencers.manager.health_check` | Per-probe health-endpoint budget |
| `opencode.build_timeout_seconds` | `60.0` | `opencode.blackbox.score_task_a` | `go build` budget for Task A scoring |
| `opencode.run_timeout_seconds` | `10.0` | `opencode.blackbox.score_task_a` | Per-check run budget for the compiled binary |
| `settings_backup.dir` | `.runtime/settings-backups` | `settings_store.default_settings_store` | Backup dir for validated settings writes |
| `settings_backup.retention` | `10` | `settings_store.default_settings_store` | Backups kept per settings file |
| `pdf.renderer_candidates` | `google-chrome`, `chromium`, plus the Chrome/Chromium `.app` bundle paths | `pdf_export.detect_renderer` via `unified_dashboard` | Chrome/Chromium probed in order for one-click Download PDF (story 17.3-002) — detect-only, never installed. Bare names resolve via `PATH`; entries with a slash are `.app`-relative paths probed under the macOS Application dirs |
| `pdf.render_timeout_seconds` | `60.0` | `pdf_export.render_pdf` via `unified_dashboard` | Subprocess budget for one `--headless --print-to-pdf` render |
| `theme.accent` | `#1e40af` | `theme.tokens_css` via `settings.load_theme_config` | Light-mode accent hue (`#RRGGBB`); the dark-mode tint is derived automatically |
| `theme.danger` | `#991b1b` | `theme.tokens_css` via `settings.load_theme_config` | Light-mode danger hue (`#RRGGBB`); the dark-mode tint is derived automatically |
| `theme.default_mode` | `system` | `theme.theme_head_snippet` via `settings.load_theme_config` | Initial dashboard mode (`light` \| `dark` \| `system`); a stored per-browser toggle choice wins |

## Theme (story 16.4-001)

The `theme:` block styles every dashboard surface through the shared token
layer (`src/local_code_bench/theme.py`). Only the light-mode hues are
configured; each dark-mode stop is *derived* (lightness lifted, hue preserved,
until WCAG AA 4.5:1 against the dark canvas), so one hue per role holds under
customization. The render path re-reads the file per page render, so a saved
edit shows on the next refresh without a restart. Malformed values (bad hex,
unknown mode) are rejected by the loader — and therefore by the Settings tab's
validated write path — never rendered as a broken theme. A hue with poor AA
contrast against either mode's background produces a *warning* on save, not a
rejection.

## Read-only protocol section

The `protocol:` section exists for visibility only. The loader refuses any value
that differs from the locked constants, so the settings file cannot become a
side door around the measurement protocol:

| Key | Locked value | Where the protocol lives |
|---|---|---|
| `protocol.benchmark_temperature` | `0.0` | `runner.py` builds every suite `ChatRequest` at temperature 0; `bench opencode --temperature` defaults to 0 |
| `protocol.benchmark_seed` | `0` | `bench opencode --seed` default; recorded in run metadata |
| `protocol.local_concurrency` | `1` | per-model `concurrency` in `configs/models.yaml`; local MLX servers stay at 1 so shared-GPU contention cannot distort prefill/decode measurements |

Changing the protocol means changing the source *and*
`docs/EVALUATION-METHODOLOGY.md`, deliberately.

## Settings change log & manual restore (15.4-001)

Every write through the dashboard's settings pipeline (`POST
/api/settings/write`) appends one line to an append-only JSONL change log,
`settings-changes.jsonl`, under the dashboard's state dir
(`paths.inferencer_state_dir`, default `.runtime/inferencers`). Each line
records the timestamp, file, config domain, a summary of *what kind of* change
landed (key paths for a structured edit, `full-document edit` for a whole-file
write — never a value, so no secret can enter the log), and the name of the
15.2-001 backup snapshot taken just before the write. The log is bounded by
simple rotation: once it holds 500 lines it is renamed to
`settings-changes.jsonl.1` (replacing the previous generation) and a fresh
file starts. The Settings tab shows the recent entries at the bottom of the
page.

A landed write is applied to the running dashboard immediately: the in-memory
model/inferencer registries (and the external/auto-tier blocks parsed from
`inferencers.yaml`) reload in place, so the launcher catalog, chat, tier view,
inventory, and the next benchmark launch all use the post-edit values without
a restart. A run already in flight keeps the model config it resolved at
launch. The write response names the changed domains and the panels a client
should re-poll.

The Settings tab also polls each config file's content hash; if a file changes
outside the dashboard while the tab is open, the affected group is flagged
with a "changed on disk — reload" banner and any submission carrying the stale
hash is refused (HTTP 409) until the group is reloaded.

**Restoring a backup is a manual file operation** (deliberately not a
one-click action in v1):

1. Find the change to undo in the Settings tab's change log (or in
   `.runtime/inferencers/settings-changes.jsonl`) and note its backup
   snapshot name, e.g. `models.yaml.20260718T091530`.
2. Copy the snapshot back over the live file:
   `cp .runtime/settings-backups/models.yaml.20260718T091530 configs/models.yaml`
   (the backup dir is `settings_backup.dir`).
3. Reload the affected group in the Settings tab (the banner appears because
   the file changed on disk — that is the detection working as intended).

## Settings tab: the Harness group (story 15.5-002)

The unified dashboard's Settings tab renders a **Harness** group over this file,
like every other config surface:

- Every key appears with its **resolved effective value** and the **source
  layer** that produced it — `env`, `yaml`, or `fallback` — resolved by the
  loader (`settings_provenance`), not recomputed by the tab. CLI flags are
  per-invocation, so they can never be the resolved source at render time;
  where a documented flag exists the tab notes it as the "overrides per run"
  layer.
- A key currently overridden by an environment variable (today:
  `BENCH_PROVIDER_TIMEOUT_SECONDS`) still shows its YAML field as editable, but
  the tab states that the env override wins until unset — an edit is never
  silently shadowed by an invisible layer.
- Edits save through `POST /api/settings/harness` into the story 15.2-001
  store: the submitted values are coerced by the loader's own key map, then the
  write runs the full pipeline — conflict check against the hash the form was
  loaded with, loader validation, timestamped backup, atomic replace. The tab
  can never produce a `settings.yaml` the harness would refuse to load.
- The read-only `protocol:` entries render locked with their rationale and no
  edit affordance.

`configs/settings.yaml` is a registered config in the settings store
(`config id "settings"`), so programmatic edits get the same validated, atomic,
comment-preserving write path as the other registered YAML files.

## Audit inventory (15.5-001)

Every hardcoded operational value found in the audit, with its disposition:

| Value | Owner module | Disposition |
|---|---|---|
| Endpoint suite default `max_tokens` = 1024 | `runner.py` | **Externalized** → `endpoint.max_tokens` |
| Provider timeout = 120 s (env `BENCH_PROVIDER_TIMEOUT_SECONDS`) | `provider.py` | **Externalized** → `endpoint.provider_timeout_seconds`; env var kept as the env layer of the same key |
| Chat defaults: temperature 0.7, max_tokens 1024 | `chat.py` | **Externalized** → `chat.temperature`, `chat.max_tokens` |
| Sandbox scoring timeout = 5 s | `sandbox.py`, `scoring.py` | **Externalized** → `sandbox.timeout_seconds` |
| Dashboard ports 8770 (results) / 8765 (unified + inferencer) and host 127.0.0.1 | `cli.py`, `dashboard_server.py`, `unified_dashboard.py`, `inferencers/dashboard.py`, `launch.py` | **Externalized** → `dashboard.port`, `dashboard.unified_port`, `dashboard.host` |
| Suite cache dir `.cache/benchmarks` | `tasks.py`, `suite_catalog.py`, `launch.py`, `unified_dashboard.py`, `cli.py` | **Externalized** → `paths.cache_dir` |
| Results dir `results` | `cli.py`, `unified_dashboard.py` | **Externalized** → `paths.results_dir` |
| Inferencer state dir `.runtime/inferencers` | `cli.py` | **Externalized** → `paths.inferencer_state_dir` |
| Dashboard state file `.runtime/dashboard.json` | `cli.py` | **Externalized** → `dashboard.state_file` |
| Inferencer start/health timeouts 30 s / 1 s | `inferencers/manager.py` | **Externalized** → `inferencer.start_timeout_seconds`, `inferencer.health_timeout_seconds` |
| OpenCode build/run timeouts 60 s / 10 s | `opencode/blackbox.py` | **Externalized** → `opencode.build_timeout_seconds`, `opencode.run_timeout_seconds` |
| 15.2-001 backup dir/retention | `settings_store.py` | **Externalized** → `settings_backup.dir`, `settings_backup.retention`, wired by `default_settings_store()` |
| Theme accent/danger hues + initial mode | `theme.py` | **Externalized** (16.4-001) → `theme.accent`, `theme.danger`, `theme.default_mode`; module constants remain as the shipped defaults and the single home of color literals |
| Canary anchor set `CANARY_HUMANEVAL_IDS` | `tasks.py` | **Non-setting (protocol-locked)** — the fixed anchor set is what makes historical canary runs comparable; no settings key exists on purpose |
| Benchmark temperature 0 / seed 0 | `runner.py`, `cli.py` (opencode), `opencode/invoke.py` | **Non-setting (protocol-locked)** — read-only `protocol:` section documents them; loader refuses overrides |
| Local-model concurrency = 1 | `configs/models.yaml` per-model `concurrency` | **Non-setting (protocol-locked)** — a per-model measurement-protocol knob in the model registry, not an operational default |
| Config registry paths (`configs/models.yaml`, `configs/inferencers.yaml`, `configs/agents.yaml`, `configs/suites.yaml`) | `cli.py` | **Non-setting** — these select *which config files to load*; putting them in a config file would be circular. CLI flags cover overrides |
| Inferencer start `poll_interval` 0.5 s / stop `grace_period` 5 s | `inferencers/manager.py` | **Non-setting** — internal pacing of the subprocess pattern, not a behaviour anyone tunes; revisit if a real need appears |
| `--watch` refresh interval 2 s | `cli.py` | **Non-setting** — cosmetic display cadence with its own CLI flag (`--interval`) |
| Sweep context sizes 2000/8000/16000/24000 | `sweep.py` | **Non-setting** — part of the sweep methodology inherited from the source articles; overridable per run via `--context-sizes` |
| Provider class ctor defaults (`timeout_seconds=120.0`) | `provider.py` | **Non-setting** — dead defaults; `provider_for_model` always passes the resolved timeout. Kept for direct-construction tests |
