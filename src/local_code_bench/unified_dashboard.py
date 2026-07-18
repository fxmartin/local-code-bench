"""Single-page unified dashboard: Inferencers, Results, and Run on one localhost page.

This is the Epic-09 shell (story 09.1-001). It does not reinvent the inferencer
control panel (Epic-08) or the live results view (Epic-07); it *composes* them under
one stdlib ``http.server`` bound to ``127.0.0.1`` and serves a single self-contained
page (inlined CSS/JS, no CDN, no build step) whose three sections are switched
client-side without reloading the app.

All business logic stays where it already lives — every endpoint here delegates:

- ``GET /``           -> the unified page (inlined assets)
- ``GET /api/status`` -> :func:`inferencers.dashboard.status_action` (Epic-08)
- ``POST /api/start`` -> :func:`inferencers.dashboard.start_action`  (Epic-08, exclusive)
- ``POST /api/stop``  -> :func:`inferencers.dashboard.stop_action`   (Epic-08)
- ``GET /api/data``   -> :func:`dashboard_server.data_action`        (Epic-07 aggregates)

Both delegated surfaces already project onto JSON-safe fields only (no API keys,
``.env`` contents, or host-sensitive paths), and the server binds localhost only,
so no authentication is required — a single-user benchmark-box tool. The Run
section is the navigable seam the benchmark launcher (story 09.2-001) plugs into.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import chat
from . import compare
from . import compare_report
from . import dashboard_lifecycle
from . import dashboard_server as results_panel
from . import launch
from . import settings_editor
from . import settings_panel
from .config import (
    AutoTierConfig,
    ConfigError,
    ExternalRepoConfig,
    InferencerConfig,
    ModelConfig,
    OptimizerConfig,
    TokenPrices,
    load_autotier,
    load_external_repo,
    load_inferencers,
    load_models,
    load_optimizers,
)
from .dashboard_model import load_dashboard_data
from .inferencers import autotier
from .inferencers import dashboard as inferencer_panel
from .inferencers import inventory
from .inferencers import tiered, tiering
from .inferencers.external import check_availability
from .optimizers import manager as optimizer_manager
from .settings import get_settings
from .settings_store import SettingsStore, default_settings_store
from .suite_catalog import catalog_payload
from .theme import THEME_CSS, THEME_HEAD_SNIPPET, THEME_TOGGLE_SNIPPET

DEFAULT_HOST = get_settings().dashboard_host
DEFAULT_PORT = get_settings().unified_dashboard_port
DEFAULT_CACHE_DIR = get_settings().cache_dir
DEFAULT_RESULTS_DIR = get_settings().results_dir

_TRUTHY = {"1", "true", "yes", "on"}

# Keys whose values are secret-bearing and must never reach the browser, matched
# case-insensitively against the exact key name. The delegated actions already
# project onto safe fields; dropping these here is the defense-in-depth backstop so
# a future field carrying one of them cannot leak by oversight.
_SECRET_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "api_key_env",
        "authorization",
        "access_token",
        "secret",
        "password",
        "base_url",
    }
)

# Absolute home/system paths that would expose the benchmark box's filesystem layout.
# Error strings (e.g. a run's failure reason) can capture a real path; we keep the
# basename so the message stays useful but strip the host-revealing directories.
_HOST_PATH = re.compile(r"/(?:Users|home|root)/[^\s'\":,)]+")


def _redact_paths(text: str) -> str:
    return _HOST_PATH.sub(lambda m: "<redacted>/" + m.group(0).rstrip("/").rsplit("/", 1)[-1], text)


def sanitize_payload(value: object) -> object:
    """Recursively scrub a JSON-able value of secrets before it reaches the browser.

    This is the single response-sanitization seam for the unified dashboard
    (story 09.6-001): every JSON endpoint ships through :func:`_json`, so secret
    leaks are caught in one place rather than trusted to each delegated action. It
    drops secret-bearing keys (:data:`_SECRET_KEYS`) and redacts absolute host paths
    from string values, so an error message that captured a real filesystem path
    cannot expose where the box keeps its configs or results.
    """

    if isinstance(value, dict):
        return {
            key: sanitize_payload(item)
            for key, item in value.items()
            if not (isinstance(key, str) and key.lower() in _SECRET_KEYS)
        }
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, str):
        return _redact_paths(value)
    return value


class MoveWorker:
    """Runs one tier move at a time in a background thread (story 12.6-003).

    The dashboard's HTTP loop is single-threaded, so a synchronous multi-GB
    promote/demote used to freeze every panel until the copy finished. The worker
    moves the copy off the request thread: ``start`` launches the verified tiering
    move in a daemon thread and returns immediately, ``status`` reports the one
    current/last job — with live byte progress measured from the move's staging
    path — and a second ``start`` while a move is running is refused, so at most
    one move ever mutates the stores at a time.

    Safety is unchanged: the thread runs the same copy → verify → atomically
    publish tiering path, which cleans its own staging on failure and never
    deletes a source before a verified destination exists — so a dashboard killed
    mid-move leaves both tiers intact.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._job: dict | None = None
        self._probe: Callable[[], int] | None = None

    @property
    def busy(self) -> bool:
        """True while a move is running (new moves and tier-apply must wait)."""

        with self._lock:
            return self._job is not None and self._job["state"] == "running"

    def start(
        self,
        *,
        verb: str,
        name: str,
        store_format: str,
        bytes_total: int,
        probe: Callable[[], int],
        run: Callable[[], object],
        payload: Callable[[object], dict],
    ) -> bool:
        """Launch ``run`` in the background; False when a move is already running.

        ``probe`` measures bytes copied so far (from the staging path) for live
        progress; ``payload`` projects the tiering result into the identity-only
        response shape once the move completes.
        """

        with self._lock:
            if self._job is not None and self._job["state"] == "running":
                return False
            self._job = {
                "verb": verb,
                "name": name,
                "format": store_format,
                "state": "running",
                "bytes_total": bytes_total,
                "error": None,
                "result": None,
                "started": time.monotonic(),
                "finished": None,
            }
            self._probe = probe
            self._thread = threading.Thread(
                target=self._run, args=(run, payload), daemon=True
            )
            self._thread.start()
        return True

    def _run(self, run: Callable[[], object], payload: Callable[[object], dict]) -> None:
        try:
            result = run()
        except (tiering.PromoteError, tiering.DemoteError) as exc:
            self._finish(state="error", error=str(exc))
            return
        except Exception as exc:  # never leave a job stuck "running"
            self._finish(state="error", error=f"move failed unexpectedly: {exc}")
            return
        self._finish(state="done", result=payload(result))

    def _finish(
        self, *, state: str, error: str | None = None, result: dict | None = None
    ) -> None:
        with self._lock:
            if self._job is None:  # pragma: no cover - start() always sets it
                return
            self._job["state"] = state
            self._job["error"] = error
            self._job["result"] = result
            self._job["finished"] = time.monotonic()

    def status(self) -> dict | None:
        """The current/last job as a client payload, or None before any move.

        Progress for a running job is measured live from the staging path, capped
        at ``bytes_total`` (the copy briefly holds staging + published bytes around
        the atomic rename). Identity fields only — never an on-disk path.
        """

        with self._lock:
            if self._job is None:
                return None
            job = dict(self._job)
            probe = self._probe
        if job["state"] == "done":
            bytes_done = job["bytes_total"]
        elif job["state"] == "running" and probe is not None:
            try:
                bytes_done = min(probe(), job["bytes_total"]) if job["bytes_total"] else probe()
            except OSError:
                bytes_done = 0
        else:
            bytes_done = 0
        end = job["finished"] if job["finished"] is not None else time.monotonic()
        return {
            "verb": job["verb"],
            "name": job["name"],
            "format": job["format"],
            "state": job["state"],
            "bytes_total": job["bytes_total"],
            "bytes_done": bytes_done,
            "elapsed_seconds": round(end - job["started"], 1),
            "error": job["error"],
            "result": job["result"],
        }

    def wait(self, timeout: float | None = 30.0) -> None:
        """Block until the current move thread exits (tests and shutdown hooks)."""

        thread = self._thread
        if thread is not None:
            thread.join(timeout)


@dataclass(frozen=True)
class DashboardContext:
    """Everything the unified server needs to answer a request, held by reference.

    The inferencer registry and state dir drive the Inferencers section; the result
    paths drive the Results section. Result files are re-read on every ``/api/data``
    request (never preloaded), so a still-running benchmark's records appear on
    refresh without a restart.

    The Run section (story 09.2-001) adds the model registry plus the suite-catalog
    lookups (``cache_dir`` / ``suites_path``) behind ``/api/catalog``, and a launch
    ``orchestrator`` (story 09.3-001) behind ``/api/run`` so the launcher form is a
    thin client whose authority lives in the orchestrator. The same orchestrator's
    in-memory run state feeds the status endpoints the page polls (``GET /api/runs`` /
    ``/api/run/<id>``, story 09.4-001), and ``results_dir`` is scanned per
    ``/api/data`` request so a freshly launched run's JSONL appears in the Results
    section without a restart.
    """

    configs: dict[str, InferencerConfig]
    state_dir: str | Path
    result_paths: list[str | Path] = field(default_factory=list)
    models: dict[str, ModelConfig] = field(default_factory=dict)
    orchestrator: launch.RunOrchestrator | None = None
    cache_dir: str | Path = DEFAULT_CACHE_DIR
    suites_path: str | Path = "configs/suites.yaml"
    results_dir: str | Path | None = None
    # Epic-12 tiered storage (story 12.6-002): the optional external SSD tier and
    # the auto-tiering policy drive the Inventory section's tier badges, the
    # promote/demote controls, and the auto-tiering plan. Both are optional so a
    # single-tier config (no ``external_repo`` / ``auto_tier`` block) still serves
    # the dashboard — the tier view then shows only local models with no controls.
    external_cfg: ExternalRepoConfig | None = None
    autotier_cfg: AutoTierConfig | None = None
    # Story 12.6-003: single background worker for promote/demote so a multi-GB
    # copy never blocks the (single-threaded) request loop or freezes the UI.
    move_worker: MoveWorker = field(default_factory=MoveWorker)
    # Story 15.1-001: source files behind the read-only Settings tab. The tab
    # re-reads them per ``/api/settings`` request, so YAML edits show up on refresh
    # and a broken file degrades to that one group's inline error.
    models_path: str | Path = "configs/models.yaml"
    agents_path: str | Path = "configs/agents.yaml"
    inferencers_path: str | Path = "configs/inferencers.yaml"
    # Story 17.2-001: the comparison-axis catalog behind the Benchmarks tab. It is
    # re-read per request like the settings sources, so a catalog edit (an eighth
    # comparison) shows up on refresh and a broken file degrades to a picker error.
    comparisons_path: str | Path = "configs/comparisons.yaml"
    # Epic-13 (story 13.4-001): the context-optimization proxy registry drives the
    # read-only Optimizers section — a distinct panel, never mixed into the
    # Inferencers one. Lifecycle stays on the CLI (`bench optimizer start/stop`).
    optimizer_configs: dict[str, OptimizerConfig] = field(default_factory=dict)
    optimizer_state_dir: str | Path = ".runtime/optimizers"
    # Story 15.3-003: the validated write path behind the Settings tab's suites &
    # agents editors. The store resolves file paths from its own registry, so a
    # request can never name a file outside the registered config set.
    settings_store: SettingsStore = field(default_factory=default_settings_store)


@dataclass(frozen=True)
class Response:
    """A fully-formed HTTP response: status, content type, and encoded body."""

    status: int
    content_type: str
    body: bytes


def _json(status: int, payload: dict) -> Response:
    body = json.dumps(sanitize_payload(payload)).encode("utf-8")
    return Response(status, "application/json; charset=utf-8", body)


def _is_truthy(values: list[str]) -> bool:
    return bool(values) and values[0].lower() in _TRUTHY


def catalog_action(ctx: DashboardContext) -> tuple[int, dict]:
    """Return the model/inferencer/suite catalogs the launcher form populates from.

    Each surface is projected onto JSON-safe identity fields only — no base URLs,
    API-key env names, or host paths — so the localhost page can render selectors
    without any secret reaching the browser. Models carry their declared
    ``inferencer`` so the form can warn when the chosen inferencer differs.
    """

    models = [
        {"name": cfg.name, "type": cfg.type, "inferencer": cfg.inferencer}
        for cfg in ctx.models.values()
    ]
    inferencers = [{"name": cfg.name, "lifecycle": cfg.lifecycle} for cfg in ctx.configs.values()]
    suites = catalog_payload(cache_dir=ctx.cache_dir, suites_path=ctx.suites_path)["suites"]
    return 200, {"models": models, "inferencers": inferencers, "suites": suites}


def settings_action(ctx: DashboardContext) -> tuple[int, dict]:
    """Return the aggregated read-only settings document (story 15.1-001).

    Delegates to :func:`settings.settings_payload`, which re-reads every config
    surface per request and degrades a missing/broken file to that group's inline
    error. Env-var indicators carry the variable *name* plus set/unset only, so
    the payload survives the 09.6-001 sanitize seam with no secret to strip.
    """

    return 200, settings_panel.settings_payload(
        models_path=ctx.models_path,
        inferencers_path=ctx.inferencers_path,
        agents_path=ctx.agents_path,
        suites_path=ctx.suites_path,
        cache_dir=ctx.cache_dir,
    )


def optimizers_action(ctx: DashboardContext) -> tuple[int, dict]:
    """Status rows for the Optimizers section (Epic-13, story 13.4-001).

    Read-only: the panel shows installed/running/healthy/upstream per registered
    proxy; starting and stopping stays on the CLI (`bench optimizer start/stop`)
    so the dashboard never races the 13.2 lifecycle state files.
    """

    rows = []
    for name, cfg in ctx.optimizer_configs.items():
        st = optimizer_manager.status(cfg, ctx.optimizer_state_dir)
        rows.append(
            {
                "name": name,
                "installed": st.installed,
                "running": st.running,
                "healthy": st.healthy,
                "port": st.port,
                "pid": st.pid,
                "upstream": st.upstream,
                "url": cfg.url,
                "detail": st.detail,
            }
        )
    return 200, {"optimizers": rows}



def _inventory_chat_models(ctx: DashboardContext) -> list[inventory.LocalModel]:
    return inventory.normalize_all(inventory.scan_inferencers(ctx.configs.values()))


def chat_catalog_action(ctx: DashboardContext) -> tuple[int, dict]:
    """Return live inventory models plus inferencer state for the Chat selectors.

    The benchmark launcher needs cloud and manual endpoint entries, so
    :func:`catalog_action` intentionally stays broad. Chat is different: it should only
    offer models actually present in the local inventory, then let the user filter by
    compatible inferencer and see whether that inferencer is already running.
    """

    inventory_models = _inventory_chat_models(ctx)
    models_by_name: dict[str, list[inventory.LocalModel]] = {}
    for model in inventory_models:
        if model.inferencer in ctx.configs:
            models_by_name.setdefault(model.name, []).append(model)

    status_code, status_payload = inferencer_panel.status_action(ctx.configs, ctx.state_dir)
    statuses = {
        row["name"]: row for row in status_payload.get("inferencers", []) if isinstance(row, dict)
    }
    models = [
        {
            "name": name,
            "inferencers": sorted({model.inferencer for model in rows}),
            "formats": sorted({model.store_format for model in rows}),
            "quant": next((model.quant for model in rows if model.quant), None),
            "provider": next((model.provider for model in rows if model.provider), None),
            "size_bytes": max(model.size_bytes for model in rows),
        }
        for name, rows in models_by_name.items()
    ]
    inferencers = []
    for cfg in ctx.configs.values():
        row = statuses.get(cfg.name, {})
        model_count = sum(1 for model in inventory_models if model.inferencer == cfg.name)
        inferencers.append(
            {
                "name": cfg.name,
                "lifecycle": cfg.lifecycle,
                "installed": bool(row.get("installed", False)),
                "running": bool(row.get("running", False)),
                "healthy": bool(row.get("healthy", False)),
                "pid": row.get("pid"),
                "port": row.get("port", cfg.port),
                "detail": row.get("detail", ""),
                "model_count": model_count,
                "available": model_count > 0,
            }
        )
    return status_code, {"models": models, "inferencers": inferencers}


def _chat_model_from_inventory(
    local_model: inventory.LocalModel, inferencer_cfg: InferencerConfig
) -> ModelConfig:
    model_id = (
        "default_model"
        if local_model.store_format == "hf-safetensors" and local_model.path
        else local_model.name
    )
    return ModelConfig(
        name=local_model.name,
        type="openai",
        base_url=f"http://127.0.0.1:{inferencer_cfg.port}/v1",
        model_id=model_id,
        pinned_revision="inventory",
        concurrency=1,
        max_tokens=chat.DEFAULT_MAX_TOKENS,
        price_per_1k_tokens=TokenPrices(input=0.0, output=0.0),
        inferencer=local_model.inferencer,
        quant=local_model.quant,
        provider=local_model.provider,
    )


def _chat_models_for_request(
    ctx: DashboardContext, parsed: dict[str, object]
) -> tuple[int, dict] | dict[str, ModelConfig]:
    name = parsed.get("model")
    if not isinstance(name, str):
        return ctx.models

    compatible = [
        model
        for model in _inventory_chat_models(ctx)
        if model.name == name and model.inferencer in ctx.configs
    ]
    if not compatible:
        return ctx.models

    selected = parsed.get("inferencer")
    if isinstance(selected, str) and selected:
        matches = [model for model in compatible if model.inferencer == selected]
        if not matches:
            return 400, {"error": f"{name!r} is not available for inferencer {selected!r}"}
        chosen = matches[0]
    elif len({model.inferencer for model in compatible}) == 1:
        chosen = compatible[0]
        parsed["inferencer"] = chosen.inferencer
    else:
        names = ", ".join(sorted({model.inferencer for model in compatible}))
        return 400, {"error": f"select an inferencer for {name!r}: {names}"}

    return {name: _chat_model_from_inventory(chosen, ctx.configs[chosen.inferencer])}


def _configs_for_chat_model_start(
    ctx: DashboardContext, inferencer_name: str, model_name: str
) -> dict[str, InferencerConfig]:
    cfg = ctx.configs.get(inferencer_name)
    if cfg is None or cfg.start is None:
        return ctx.configs

    local_model = next(
        (
            model
            for model in _inventory_chat_models(ctx)
            if model.name == model_name
            and model.inferencer == inferencer_name
            and model.store_format == "hf-safetensors"
            and model.path
        ),
        None,
    )
    if local_model is None or not cfg.start or cfg.start[0] != "mlx_lm.server":
        return ctx.configs

    patched = dict(ctx.configs)
    patched[inferencer_name] = replace(
        cfg,
        start=_command_with_option(cfg.start, "--model", local_model.path),
    )
    return patched


def _command_with_option(command: tuple[str, ...], option: str, value: str) -> tuple[str, ...]:
    updated: list[str] = []
    skip_next = False
    replaced = False
    for item in command:
        if skip_next:
            skip_next = False
            continue
        if item == option:
            updated.extend((option, value))
            skip_next = True
            replaced = True
        else:
            updated.append(item)
    if not replaced:
        updated.extend((option, value))
    return tuple(updated)



def inventory_action(ctx: DashboardContext) -> tuple[int, dict]:
    """Return the local model inventory: per-inferencer downloads + shared sets.

    A thin projection over the Epic-11 scanner (story 11.5-001): it scans each
    configured inferencer's model store, normalizes the hits, and groups them into
    logical models so the dashboard can render downloads per inferencer and flag the
    ones several engines can serve. Only identity fields reach the browser — name,
    format, quant, provider, size, and owning inferencer(s); the on-disk path and
    content identity are deliberately omitted so no host-sensitive path leaks (AC4).
    """

    models = inventory.normalize_all(inventory.scan_inferencers(ctx.configs.values()))
    return 200, {
        "models": [
            {
                "name": model.name,
                "format": model.store_format,
                "quant": model.quant,
                "provider": model.provider,
                "size_bytes": model.size_bytes,
                "inferencer": model.inferencer,
            }
            for model in models
        ],
        "shared": [_shared_payload(group) for group in inventory.shared_models(models)],
    }


def _shared_payload(group: inventory.SharedModel) -> dict:
    """Project one shared logical model: identity fields from its first member.

    A :class:`inventory.SharedModel` carries the grouping key and its member
    records; name/quant/provider/size live on the members, so the head model
    supplies them (every member shares the same on-disk artifact). The on-disk
    path and content identity are deliberately omitted (AC4).
    """

    head = group.models[0]
    return {
        "name": head.name,
        "format": group.store_format,
        "quant": head.quant,
        "provider": head.provider,
        "size_bytes": head.size_bytes,
        "inferencers": list(group.inferencers),
    }


# --- Tiered storage (story 12.6-002): tier view + promote/demote + auto-tier ---
#
# Every action below is a thin server-side seam over the Epic-12 tiering API
# (12.2 inventory / 12.3 moves / 12.4 auto-tiering): no tiering business logic
# lives here. Each projects onto identity + tier fields only — never an on-disk
# path or content identity — so the localhost page can render and drive moves
# without a host-sensitive path reaching the browser (AC4).


def _scan_local(ctx: DashboardContext) -> list[inventory.LocalModel]:
    """The live Epic-11 local scan, normalized as local-tier records."""

    return inventory.normalize_all(inventory.scan_inferencers(ctx.configs.values()))


def tier_inventory_action(ctx: DashboardContext) -> tuple[int, dict]:
    """Return the unified two-tier inventory: each model's tier + availability.

    A thin projection over the Epic-12 unified inventory (story 12.2-001): it merges
    the live local scan with the external tier (live when mounted, from the cached
    catalog when offline) into one row per logical model carrying its ``tiers`` and
    serving engines. ``present_in_both`` flags a model held redundantly on both tiers
    and ``reclaimable_bytes`` sums those redundant copies (the across-tier
    reclaimable hint, AC1). Only identity fields reach the browser — name, format,
    quant, provider, size, engines, tiers — never the on-disk path or content
    identity (AC4).
    """

    tiered_inventory = tiered.build_tiered_inventory(
        _scan_local(ctx),
        ctx.external_cfg,
        list(ctx.configs.values()),
        state_dir=ctx.state_dir,
    )
    models = tiered_inventory.models
    return 200, {
        "external_availability": tiered_inventory.external_availability.value,
        "external_cached": tiered_inventory.external_cached,
        "reclaimable_bytes": sum(m.size_bytes for m in models if m.present_in_both),
        "total_bytes": sum(m.size_bytes for m in models),
        "models": [
            {
                "name": model.name,
                "format": model.store_format,
                "quant": model.quant,
                "provider": model.provider,
                "size_bytes": model.size_bytes,
                "inferencers": list(model.inferencers),
                "tiers": list(model.tiers),
                "present_in_both": model.present_in_both,
            }
            for model in models
        ],
    }


def _compatible_inferencer(ctx: DashboardContext, store_format: str) -> InferencerConfig | None:
    """The first engine with a local store that can serve ``store_format``."""

    for cfg in ctx.configs.values():
        if cfg.store_format == store_format and cfg.model_store:
            return cfg
    return None


_BUSY_ERROR = "another move is already in progress — wait for it to finish"


def _path_bytes(path: Path) -> int:
    """Best-effort on-disk size of a file or directory; 0 when absent."""

    try:
        if path.is_file():
            return path.stat().st_size
        if not path.is_dir():
            return 0
        total = 0
        for child in path.rglob("*"):
            if child.is_file() and not child.is_symlink():
                try:
                    total += child.stat().st_size
                except OSError:
                    continue
        return total
    except OSError:
        return 0


def _move_progress_probe(destination: Path) -> Callable[[], int]:
    """Bytes a running move has copied so far, measured from its staging path.

    The verified move copies into ``staging_path(destination)`` and publishes with
    one atomic rename, so staging size *is* the live progress; after the rename
    (just before the job flips to done) the destination carries the bytes instead.
    """

    staging = tiering.staging_path(destination)

    def probe() -> int:
        done = _path_bytes(staging)
        return done if done else _path_bytes(destination)

    return probe


def promote_action(ctx: DashboardContext, name: str, store_format: str) -> tuple[int, dict]:
    """Start a background promote of an external-tier model into a local store.

    A thin seam over :func:`tiering.promote_model` (story 12.3-001), run on the
    :class:`MoveWorker` (story 12.6-003) so a multi-GB copy never blocks the
    request loop: validation refuses up front — no external tier, offline SSD,
    unknown model, no compatible store, or a move already running — and a valid
    request returns ``202`` immediately with the job snapshot; completion, live
    byte progress, and any :class:`tiering.PromoteError` are reported by
    ``GET /api/move-status``. Responses carry only identity fields — never an
    on-disk path (AC4).
    """

    if ctx.external_cfg is None:
        return 409, {"error": "no external tier is configured — nothing to promote from"}
    if not check_availability(ctx.external_cfg).is_mounted:
        return 409, {"error": f"external repo offline — plug in the SSD before promoting {name}"}

    source = _find_external(ctx, name, store_format)
    if source is None:
        return 404, {"error": f"{name}: not found on the external tier"}
    target = _compatible_inferencer(ctx, store_format)
    if target is None:
        return 404, {
            "error": f"no inferencer with a local store can serve {store_format} model {name}"
        }

    try:
        plan = tiering.plan_promotion(source, target)
    except tiering.PromoteError as exc:
        return 409, {"error": str(exc)}

    external_cfg, configs, state_dir = ctx.external_cfg, ctx.configs, ctx.state_dir
    started = ctx.move_worker.start(
        verb="promote",
        name=name,
        store_format=store_format,
        bytes_total=plan.size_bytes,
        probe=_move_progress_probe(plan.destination),
        run=lambda: tiering.promote_model(source, target, external_cfg, configs, state_dir),
        payload=lambda result: {
            "promoted": {
                "name": name,
                "tier": "local",
                "bytes_copied": result.bytes_copied,
                "verified": result.verified,
            }
        },
    )
    if not started:
        return 409, {"error": _BUSY_ERROR}
    return 202, {"job": ctx.move_worker.status()}


def demote_action(ctx: DashboardContext, name: str, store_format: str) -> tuple[int, dict]:
    """Start a background demote of a local-tier model out to the external tier.

    A thin seam over :func:`tiering.demote_model` (story 12.3-002), run on the
    :class:`MoveWorker` (story 12.6-003) exactly like :func:`promote_action`:
    up-front refusals stay synchronous (409/404), a valid request returns ``202``
    with the job snapshot, and progress/completion/errors are reported by
    ``GET /api/move-status``. Responses carry only identity fields — never an
    on-disk path (AC4).
    """

    if ctx.external_cfg is None:
        return 409, {"error": "no external tier is configured — nowhere to demote to"}
    if not check_availability(ctx.external_cfg).is_mounted:
        return 409, {"error": f"external repo offline — plug in the SSD before demoting {name}"}

    source = _find_local(ctx, name, store_format)
    if source is None:
        return 404, {"error": f"{name}: not found on the local tier"}

    try:
        plan = tiering.plan_demotion(source, ctx.external_cfg)
    except tiering.DemoteError as exc:
        return 409, {"error": str(exc)}

    external_cfg, configs, state_dir = ctx.external_cfg, ctx.configs, ctx.state_dir
    started = ctx.move_worker.start(
        verb="demote",
        name=name,
        store_format=store_format,
        bytes_total=plan.size_bytes,
        probe=_move_progress_probe(plan.destination),
        run=lambda: tiering.demote_model(source, external_cfg, configs, state_dir),
        payload=lambda result: {
            "demoted": {
                "name": name,
                "tier": "external",
                "bytes_reclaimed": result.bytes_reclaimed,
                "verified": result.verified,
                "reused_existing": result.reused_existing,
            }
        },
    )
    if not started:
        return 409, {"error": _BUSY_ERROR}
    return 202, {"job": ctx.move_worker.status()}


def move_status_action(ctx: DashboardContext) -> tuple[int, dict]:
    """The current/last background move as ``{"job": ...}`` (story 12.6-003).

    ``job`` is ``null`` before any move; while one runs it carries live byte
    progress measured from the staging path, and once finished it carries the
    same result payload the synchronous endpoints used to return (or the move
    error verbatim). Identity fields only — never an on-disk path.
    """

    return 200, {"job": ctx.move_worker.status()}


def _find_external(
    ctx: DashboardContext, name: str, store_format: str
) -> inventory.LocalModel | None:
    """The live external-tier record matching ``name`` + ``store_format``."""

    if ctx.external_cfg is None:
        return None
    for model in tiered.scan_external_tier(ctx.external_cfg, list(ctx.configs.values())):
        if model.name == name and model.store_format == store_format:
            return model
    return None


def _find_local(ctx: DashboardContext, name: str, store_format: str) -> inventory.LocalModel | None:
    """The live local-tier record matching ``name`` + ``store_format``."""

    for model in _scan_local(ctx):
        if model.name == name and model.store_format == store_format:
            return model
    return None


def _local_free_bytes(ctx: DashboardContext) -> int | None:
    """Free space on the first configured local store volume (for min-free budgets)."""

    for cfg in ctx.configs.values():
        if not cfg.model_store:
            continue
        probe = inventory.expand_store_path(cfg.model_store[0])
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        try:
            return shutil.disk_usage(probe).free
        except OSError:
            continue
    return None


def _build_autotier_plan(
    ctx: DashboardContext, cfg: AutoTierConfig
) -> tuple[autotier.AutoTierPlan, autotier.LastUsedStore]:
    """Build the dry-run eviction plan and the last-used store backing its LRU signal.

    Pure planning over the live local scan: side-effect-free (the apply step is the
    only one that touches disk). The plan is :attr:`AutoTierPlan.paused` when the
    external tier is unconfigured or offline. ``cfg`` is passed explicitly (the
    callers gate on a configured policy) so the budget/pins are always present.
    """

    available = ctx.external_cfg is not None and check_availability(ctx.external_cfg).is_mounted
    store = autotier.LastUsedStore(ctx.state_dir)
    plan = autotier.plan_autotier(
        _scan_local(ctx),
        autotier.budget_from_config(cfg),
        pins=cfg.pins,
        external_available=available,
        free_bytes=_local_free_bytes(ctx),
        last_used=store.last_used,
    )
    return plan, store


def _plan_payload(plan: autotier.AutoTierPlan) -> dict:
    """Project an eviction plan onto identity fields only — never a path (AC4)."""

    return {
        "paused": plan.paused,
        "satisfied": plan.satisfied,
        "bytes_to_reclaim": plan.bytes_to_reclaim,
        "bytes_reclaimed": plan.bytes_reclaimed,
        "local_total_bytes": plan.local_total_bytes,
        "pinned": list(plan.pinned),
        "warnings": list(plan.warnings),
        "evictions": [
            {"name": e.name, "format": e.store_format, "size_bytes": e.size_bytes}
            for e in plan.evictions
        ],
    }


def tier_plan_action(ctx: DashboardContext) -> tuple[int, dict]:
    """Return the auto-tiering dry-run plan: what it would evict and the bytes freed.

    A thin seam over :func:`autotier.plan_autotier` (story 12.4-001). When no
    ``auto_tier`` policy is configured the plan is inert (``configured=False``,
    nothing to evict). Pinned models are never selected; the plan is ``paused``
    when the external tier is offline. Nothing is moved (AC3 — dry-run is the
    default).
    """

    if ctx.autotier_cfg is None:
        return 200, {
            "configured": False,
            "paused": False,
            "satisfied": True,
            "bytes_to_reclaim": 0,
            "bytes_reclaimed": 0,
            "local_total_bytes": 0,
            "pinned": [],
            "warnings": [],
            "evictions": [],
        }
    plan, _ = _build_autotier_plan(ctx, ctx.autotier_cfg)
    return 200, {"configured": True, **_plan_payload(plan)}


def tier_apply_action(ctx: DashboardContext) -> tuple[int, dict]:
    """Apply the auto-tiering plan, evicting each model through the verified demote path.

    A thin seam over :func:`autotier.apply_plan` (story 12.4-001) — the only tier
    action that touches disk. Refuses when no policy is configured or the plan is
    paused (external tier offline); pins are respected because the planner never
    selects them. The response carries only per-model names + bytes reclaimed,
    never an on-disk path (AC4).
    """

    if ctx.autotier_cfg is None:
        return 409, {"error": "auto-tiering is not configured — nothing to apply"}
    if ctx.move_worker.busy:
        return 409, {"error": "a tier move is in progress — wait for it before applying evictions"}

    plan, store = _build_autotier_plan(ctx, ctx.autotier_cfg)
    if plan.paused or ctx.external_cfg is None:
        return 409, {
            "error": "auto-tiering is paused: the external repo is offline — no changes made"
        }
    try:
        results = autotier.apply_plan(
            plan,
            ctx.external_cfg,
            ctx.configs,
            ctx.state_dir,
            now=time.time(),
            last_used_store=store,
        )
    except (autotier.AutoTierError, tiering.DemoteError) as exc:
        return 409, {"error": str(exc)}
    return 200, {
        "count": len(results),
        "applied": [
            {
                "name": result.plan.name,
                "bytes_reclaimed": result.bytes_reclaimed,
                "reused_existing": result.reused_existing,
            }
            for result in results
        ],
    }


def compare_axes_action(ctx: DashboardContext) -> tuple[int, dict]:
    """Return the Benchmarks tab's axis picker: the catalog with data-readiness.

    A thin seam over :func:`compare_report.axes_action` — catalog and results are
    re-read per request, so a new run (or a catalog edit) reorders the picker on
    refresh without a restart.
    """

    catalog = compare_report.load_catalog_safe(ctx.comparisons_path)
    stats = compare.build_configuration_stats(
        [Path(p) for p in _resolve_result_paths(ctx)]
    )
    return compare_report.axes_action(catalog, stats, ctx.models)


def compare_report_action(ctx: DashboardContext, axis_id: str) -> tuple[int, dict]:
    """Return one axis rendered as report data (story 17.2-001).

    Bundles everything the report view shows for the selected axis: the paired
    member stats (memory footprints from the live local scan for the size-scaled
    frontier points), each contributing run's metadata header for the methodology
    chips, and the sweep observations for the context-scaling section.
    """

    paths = [Path(p) for p in _resolve_result_paths(ctx)]
    catalog = compare_report.load_catalog_safe(ctx.comparisons_path)
    stats = compare.build_configuration_stats(
        paths, memory=compare.memory_index(_scan_local(ctx))
    )
    return compare_report.report_action(
        catalog,
        stats,
        axis_id,
        models=ctx.models,
        sweep_points=load_dashboard_data(paths).sweep_points,
        metadata_by_run=compare_report.read_run_metadata(paths),
    )


def _resolve_result_paths(ctx: DashboardContext) -> list[str | Path]:
    """Explicit ``--input`` files plus any ``*.jsonl`` under ``results_dir``.

    A run launched from the dashboard writes a fresh file under ``results_dir``; by
    globbing it per request (and de-duplicating against the explicit list) the
    Results section reflects the new JSONL without a restart (story 09.4-001 AC2).
    """

    paths: list[str | Path] = list(ctx.result_paths)
    if ctx.results_dir is not None:
        directory = Path(ctx.results_dir)
        if directory.is_dir():
            seen = {str(Path(p)) for p in paths}
            for found in sorted(directory.glob("*.jsonl")):
                if str(found) not in seen:
                    paths.append(found)
    return paths


def handle_request(
    method: str, path: str, ctx: DashboardContext, body: bytes = b""
) -> Response | chat.ChatStreamResponse:
    """Route one request to the unified page or a delegated section action.

    ``body`` carries the raw POST payload; only ``/api/chat`` and ``/api/run`` consume
    it (the other POST actions are driven by query params). A chat launch returns a
    streaming :class:`chat.ChatStreamResponse`; everything else returns a buffered
    ``Response``.
    """

    parts = urlsplit(path)
    route = parts.path
    query = parse_qs(parts.query)
    name = query.get("name", [""])[0]

    if method == "GET" and route == "/":
        return Response(200, "text/html; charset=utf-8", render_page().encode("utf-8"))
    if method == "GET" and route == "/api/status":
        return _json(*inferencer_panel.status_action(ctx.configs, ctx.state_dir))
    if method == "POST" and route == "/api/start":
        confirm = _is_truthy(query.get("confirm", []))
        force = _is_truthy(query.get("force", []))
        model_name = query.get("model", [""])[0]
        configs = (
            _configs_for_chat_model_start(ctx, name, model_name)
            if model_name
            else ctx.configs
        )
        return _json(
            *inferencer_panel.start_action(
                name, configs, ctx.state_dir, confirm=confirm, force=force
            )
        )
    if method == "POST" and route == "/api/stop":
        return _json(*inferencer_panel.stop_action(name, ctx.configs, ctx.state_dir))
    if method == "GET" and route == "/api/optimizers":
        return _json(*optimizers_action(ctx))
    if method == "GET" and route == "/api/data":
        return _json(*results_panel.data_action(_resolve_result_paths(ctx)))
    if method == "GET" and route == "/api/compare":
        return _json(
            *compare.compare_action(
                [Path(p) for p in _resolve_result_paths(ctx)],
                query.get("axis", [""])[0],
                memory=compare.memory_index(_scan_local(ctx)),
            )
        )
    if method == "GET" and route == "/api/compare/axes":
        return _json(*compare_axes_action(ctx))
    if method == "GET" and route == "/api/compare/report":
        return _json(*compare_report_action(ctx, query.get("axis", [""])[0]))
    if method == "GET" and route == "/api/catalog":
        return _json(*catalog_action(ctx))
    if method == "GET" and route == "/api/settings":
        return _json(*settings_action(ctx))
    if method == "GET" and route == "/api/settings/config":
        return _json(*settings_editor.read_action(ctx.settings_store, query.get("id", [""])[0]))
    if method == "POST" and route == "/api/settings/config":
        try:
            parsed = json.loads(body or b"{}")
        except json.JSONDecodeError:
            return _json(400, {"error": "invalid JSON body"})
        return _json(
            *settings_editor.write_action(
                ctx.settings_store,
                query.get("id", [""])[0],
                parsed,
                referenced_suites=lambda: settings_editor.referenced_suite_ids(
                    _resolve_result_paths(ctx)
                ),
            )
        )
    if method == "GET" and route == "/api/chat/catalog":
        return _json(*chat_catalog_action(ctx))
    if method == "GET" and route == "/api/inventory":
        return _json(*inventory_action(ctx))
    if method == "GET" and route == "/api/tiers":
        return _json(*tier_inventory_action(ctx))
    if method == "GET" and route == "/api/move-status":
        return _json(*move_status_action(ctx))
    if method == "POST" and route == "/api/promote":
        return _json(*promote_action(ctx, name, query.get("format", [""])[0]))
    if method == "POST" and route == "/api/demote":
        return _json(*demote_action(ctx, name, query.get("format", [""])[0]))
    if method == "GET" and route == "/api/tier-plan":
        return _json(*tier_plan_action(ctx))
    if method == "POST" and route == "/api/tier-apply":
        return _json(*tier_apply_action(ctx))
    if method == "POST" and route == "/api/run":
        if ctx.orchestrator is None:
            return _json(503, {"error": "launching is unavailable: no model registry loaded"})
        try:
            parsed = json.loads(body or b"{}")
        except json.JSONDecodeError:
            return _json(400, {"error": "invalid JSON body"})
        return _json(*launch.launch_action(ctx.orchestrator, parsed))
    if method == "POST" and route == "/api/chat":
        try:
            parsed = json.loads(body or b"{}")
        except json.JSONDecodeError:
            return _json(400, {"error": "invalid JSON body"})
        if not isinstance(parsed, dict):
            return _json(400, {"error": "request body must be a JSON object"})
        models = _chat_models_for_request(ctx, parsed)
        if isinstance(models, tuple):
            return _json(*models)
        result = chat.chat_action(parsed, models)
        if isinstance(result, chat.ChatStreamResponse):
            return result
        return _json(*result)
    if method == "GET" and route == "/api/runs":
        runs = ctx.orchestrator.runs_payload() if ctx.orchestrator is not None else []
        return _json(200, {"runs": runs})
    if method == "GET" and route.startswith("/api/run/"):
        if ctx.orchestrator is not None:
            payload = ctx.orchestrator.run_payload(route[len("/api/run/") :])
            if payload is not None:
                return _json(200, payload)
        return _json(404, {"error": "unknown run"})
    return _json(404, {"error": "not found"})


def make_handler(ctx: DashboardContext) -> type[BaseHTTPRequestHandler]:
    """Build a request-handler class closed over the dashboard context."""

    class _DashboardHandler(BaseHTTPRequestHandler):
        def _dispatch(self, method: str, body: bytes = b"") -> None:
            response = handle_request(method, self.path, ctx, body)
            if isinstance(response, chat.ChatStreamResponse):
                self._stream(response)
                return
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            self.end_headers()
            self.wfile.write(response.body)

        def _stream(self, response: chat.ChatStreamResponse) -> None:
            self.send_response(response.status)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                for piece in response.events:
                    self.wfile.write(piece.encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                # Client hit "stop" / closed the tab: cancel the stream cleanly so the
                # upstream provider connection is released.
                close = getattr(response.events, "close", None)
                if callable(close):
                    close()

        def do_GET(self) -> None:  # noqa: N802 - http.server callback name
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802 - http.server callback name
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            self._dispatch("POST", body)

        def log_message(self, format: str, *args: object) -> None:  # silence default logging
            return

    return _DashboardHandler


def make_server(
    ctx: DashboardContext,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> HTTPServer:
    """Create an ``HTTPServer`` bound to localhost only."""

    return HTTPServer((host, port), make_handler(ctx))


def _build_orchestrator(
    models_path: str | Path | None,
    configs: dict[str, InferencerConfig],
    state_dir: str | Path,
    results_dir: str | Path | None,
) -> launch.RunOrchestrator | None:
    """Build the Run-section orchestrator, or ``None`` when launching is unavailable.

    Needs both a writable ``results_dir`` and a loadable model registry; if the
    models file is missing or invalid the dashboard still serves the Inferencers and
    Results sections (the Run monitor just shows no launches).
    """

    if results_dir is None or models_path is None:
        return None
    try:
        models = load_models(models_path)
    except (ConfigError, OSError):
        return None
    return launch.RunOrchestrator(
        models=models,
        inferencers=configs,
        state_dir=state_dir,
        results_dir=results_dir,
    )


def serve_dashboard(
    config_path: str | Path,
    state_dir: str | Path,
    result_paths: list[str | Path],
    *,
    models_path: str | Path = "configs/models.yaml",
    agents_path: str | Path = "configs/agents.yaml",
    results_dir: str | Path = DEFAULT_RESULTS_DIR,
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
    suites_path: str | Path = "configs/suites.yaml",
    optimizers_path: str | Path = "configs/optimizers.yaml",
    optimizer_state_dir: str | Path = ".runtime/optimizers",
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    progress: Callable[[str], None] | None = None,
    dashboard_state_file: str | Path | None = None,
) -> None:
    """Load the inferencer + model registries and serve the unified dashboard.

    Serves until interrupted. The launch orchestrator is wired here so the Run
    section's form (story 09.2-001) posts to the same single-run authority Epic-08's
    exclusive start lives behind (story 09.3-001). The model registry also powers the
    Chat section (story 09.7-001); it is loaded best-effort so a missing/invalid
    ``models.yaml`` disables chat (and leaves the launcher with no models) without
    taking the rest of the dashboard down.
    """

    lifecycle = (
        dashboard_lifecycle.dashboard_process(
            dashboard_state_file,
            host=host,
            port=port,
        )
        if dashboard_state_file is not None
        else _null_dashboard_process()
    )
    with lifecycle:
        configs = load_inferencers(config_path)
        models = _load_models_safe(models_path, progress)
        external_cfg, autotier_cfg = _load_tier_configs_safe(config_path, progress)
        optimizer_configs = _load_optimizers_safe(optimizers_path, progress)
        orchestrator = launch.RunOrchestrator(
            models=models,
            inferencers=configs,
            state_dir=state_dir,
            results_dir=results_dir,
            cache_dir=cache_dir,
        )
        ctx = DashboardContext(
            configs=configs,
            state_dir=state_dir,
            result_paths=list(result_paths),
            models=models,
            orchestrator=orchestrator,
            cache_dir=cache_dir,
            suites_path=suites_path,
            results_dir=results_dir,
            external_cfg=external_cfg,
            autotier_cfg=autotier_cfg,
            models_path=models_path,
            agents_path=agents_path,
            inferencers_path=config_path,
            optimizer_configs=optimizer_configs,
            optimizer_state_dir=optimizer_state_dir,
        )
        server = make_server(ctx, host=host, port=port)
        if progress is not None:
            progress(f"unified dashboard on http://{host}:{port} (Ctrl-C to stop)")
        try:
            server.serve_forever()
        except (KeyboardInterrupt, dashboard_lifecycle.DashboardTermination):
            pass
        finally:
            server.server_close()


@contextmanager
def _null_dashboard_process() -> Iterator[None]:
    yield


def _load_models_safe(
    models_path: str | Path, progress: Callable[[str], None] | None
) -> dict[str, ModelConfig]:
    """Load the model registry, degrading to an empty catalog (no chat) on failure."""

    try:
        return load_models(models_path)
    except ConfigError as exc:
        if progress is not None:
            progress(f"chat disabled: {exc}")
        return {}


def _load_optimizers_safe(
    optimizers_path: str | Path, progress: Callable[[str], None] | None
) -> dict[str, OptimizerConfig]:
    """Load the proxy registry, degrading to an empty Optimizers panel on failure."""

    try:
        return load_optimizers(optimizers_path)
    except ConfigError as exc:
        if progress is not None:
            progress(f"optimizers panel empty: {exc}")
        return {}


def _load_tier_configs_safe(
    config_path: str | Path, progress: Callable[[str], None] | None
) -> tuple[ExternalRepoConfig | None, AutoTierConfig | None]:
    """Load the optional external-tier + auto-tier configs from the inferencers YAML.

    Both blocks are optional (a single-tier config declares neither). A malformed
    block degrades that tier feature to disabled — the tier view then shows only
    local models with no move/auto-tier controls — rather than taking the whole
    dashboard down.
    """

    try:
        external_cfg = load_external_repo(config_path)
    except ConfigError as exc:
        if progress is not None:
            progress(f"external tier disabled: {exc}")
        external_cfg = None
    try:
        autotier_cfg = load_autotier(config_path)
    except ConfigError as exc:
        if progress is not None:
            progress(f"auto-tiering disabled: {exc}")
        autotier_cfg = None
    return external_cfg, autotier_cfg


def render_page() -> str:
    """Return the self-contained unified page (inlined CSS/JS, no external assets)."""

    return _PAGE


_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>local-code-bench Dashboard</title>
<!--__THEME_HEAD__-->
<style>
/*__THEME_CSS__*/
  header { padding: var(--space-4) var(--space-6) 0; border-bottom: 1px solid var(--border); }
  h1 { margin: 0 0 var(--space-3); }
  nav { display: flex; gap: var(--space-1); }
  nav button { border: 1px solid var(--border); border-bottom: none;
    border-radius: var(--radius-sm) var(--radius-sm) 0 0; background: transparent;
    padding: var(--space-1) var(--space-3); }
  nav button.active { font-weight: 600; background: var(--surface-hover); color: var(--accent); }
  main { margin: var(--space-5) var(--space-6) var(--space-7); }
  table { max-width: 80rem; margin-top: var(--space-2); }
  th[data-sort-key] { cursor: pointer; user-select: none; }
  th[data-sort-key]:hover { text-decoration: underline; }
  tr.row-clickable { cursor: pointer; }
  tr.row-clickable:hover { background: var(--surface-hover); }
  #leaderboard-filter { margin-top: var(--space-2); width: 22rem; max-width: 100%; }
  #drilldown { margin-top: var(--space-3); }
  #drilldown table { max-width: 100%; }
  #drilldown .preview { font-family: var(--font-mono); font-size: var(--text-xs);
    white-space: pre-wrap; max-width: 28rem; }
  #warnings li { font-family: var(--font-mono); font-size: var(--text-sm); }
  .run-grid { display: flex; gap: var(--space-6); flex-wrap: wrap; }
  .run-grid select { min-width: 18rem; }
  #run-suites { border: 1px solid var(--border); border-radius: var(--radius-sm);
    padding: var(--space-2) var(--space-3); max-width: 40rem;
    display: flex; flex-direction: column; gap: var(--space-1); }
  #run-suites label { display: flex; gap: var(--space-2); align-items: baseline; }
  #run-suites label.disabled { color: var(--text-muted); }
  #run-suites .reason { color: var(--text-muted); font-size: var(--text-sm); }
  .run-actions { margin-top: var(--space-4); }
  .chat-grid { display: flex; gap: var(--space-6); flex-wrap: wrap; align-items: flex-end; }
  .chat-grid select { min-width: 16rem; }
  .chat-grid input[type="number"] { width: 6rem; }
  #chat-system { width: 100%; max-width: 46rem; margin-top: var(--space-2); }
  #chat-messages { border: 1px solid var(--border); border-radius: var(--radius-sm);
    padding: var(--space-3); max-width: 46rem; height: 24rem; overflow-y: auto;
    margin-top: var(--space-3); display: flex; flex-direction: column; gap: var(--space-2); }
  .chat-msg { padding: var(--space-2) var(--space-3); border-radius: var(--radius-md);
    max-width: 90%; white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.4; }
  .chat-msg.user { align-self: flex-end; background: var(--accent-soft); }
  .chat-msg.assistant { align-self: flex-start; background: var(--surface-hover); }
  .chat-msg .role { font-size: var(--text-xs); color: var(--text-muted); display: block;
    margin-bottom: 0.15rem; }
  .chat-metrics { margin-top: var(--space-2); padding-top: var(--space-2);
    border-top: 1px solid var(--border); font-family: var(--font-mono);
    font-size: var(--text-xs); white-space: normal; display: grid;
    grid-template-columns: auto auto; column-gap: var(--space-4); row-gap: 0.15rem; }
  .chat-metrics span:nth-child(odd) { color: var(--text-muted); }
  .chat-compose { display: flex; gap: var(--space-2); margin-top: var(--space-3);
    max-width: 46rem; }
  #chat-input { flex: 1; resize: vertical; min-height: 2.4rem; }
  /* Report idiom (story 17.2-001): reusable hero / kicker / chip / stat-tile
     primitives for the Benchmarks report view — the shapes future report
     surfaces (and the PDF export) reuse. Side colors resolve only through the
     --cmp-side-* tokens; cohorts past the fourth cycle the classes. */
  #bench-axis { min-width: 22rem; max-width: 100%; margin-left: var(--space-2); }
  .side-1 { --side-color: var(--cmp-side-1); }
  .side-2 { --side-color: var(--cmp-side-2); }
  .side-3 { --side-color: var(--cmp-side-3); }
  .side-4 { --side-color: var(--cmp-side-4); }
  .report-kicker { font-size: var(--text-xs); font-weight: 600; letter-spacing: 0.08em;
    text-transform: uppercase; color: var(--text-muted); margin: var(--space-5) 0 0; }
  .report-hero { display: flex; align-items: baseline; gap: var(--space-3);
    flex-wrap: wrap; margin: var(--space-1) 0 var(--space-2); }
  .report-hero .hero-side { font-size: var(--text-xl); font-weight: 650;
    letter-spacing: -0.015em; color: var(--side-color, var(--text)); }
  .report-hero .hero-vs { color: var(--text-muted); font-size: var(--text-lg); }
  .report-subtitle { color: var(--text-muted); max-width: 46rem; margin: 0 0 var(--space-3); }
  .chip-row { display: flex; gap: var(--space-2); flex-wrap: wrap; margin: 0 0 var(--space-4); }
  .chip b { color: var(--text); font-weight: 600; }
  .panel-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(17rem, 1fr));
    gap: var(--space-3); max-width: 80rem; }
  .stat-panel { border: 1px solid var(--border); border-radius: var(--radius-md);
    padding: var(--space-3); }
  .stat-panel h4 { margin: 0; font-size: var(--text-sm); font-weight: 600;
    color: var(--side-color, var(--text)); overflow-wrap: anywhere; }
  .stat-panel .panel-meta { font-size: var(--text-xs); color: var(--text-muted);
    margin: 0 0 var(--space-2); }
  .stat-row { display: grid; grid-template-columns: 6.5rem 1fr auto; gap: var(--space-2);
    align-items: center; font-size: var(--text-xs); margin-top: var(--space-1); }
  .stat-row .num { font-variant-numeric: tabular-nums; }
  .stat-bar { height: 0.45rem; border-radius: var(--radius-sm); background: var(--surface-hover); }
  .stat-bar i { display: block; height: 100%; border-radius: inherit;
    background: var(--side-color, var(--chart-grey-1)); }
  .cmp-badge { border-color: var(--accent); color: var(--accent); }
  .chart-svg { width: 100%; max-width: 520px; height: auto; display: block; }
  .chart-svg .axis { stroke: var(--chart-axis); stroke-width: 1; }
  .chart-svg .grid { stroke: var(--chart-grid); stroke-width: 1; }
  .chart-svg .tick { fill: var(--chart-label); font-size: 10px; }
  .chart-svg .axis-title { fill: var(--chart-label); font-size: 11px; }
  .legend { list-style: none; padding: 0; margin: var(--space-2) 0 0; display: flex;
    flex-wrap: wrap; gap: var(--space-3); font-size: var(--text-xs); color: var(--text-muted); }
  .legend .swatch { margin-right: var(--space-1); }
  .settings-source { color: var(--text-muted); font-size: var(--text-xs); font-weight: 400;
    margin-left: var(--space-2); font-family: var(--font-mono); }
  .lock-note { color: var(--warn-fg); }
  .flag-set { color: var(--ok-fg); } .flag-unset { color: var(--warn-fg); }
  .settings-editor { margin: var(--space-2) 0 var(--space-4); }
  .settings-editor summary { cursor: pointer; color: var(--text-muted); }
  .settings-editor textarea { width: 100%; min-height: 14rem; resize: vertical;
    font-family: var(--font-mono); font-size: var(--text-sm); }
  .settings-editor .editor-note { color: var(--text-muted); font-size: var(--text-sm); }
  .settings-editor .editor-warnings { color: var(--warn-fg); }
</style>
</head>
<body>
<header>
  <!--__THEME_TOGGLE__-->
  <h1>local-code-bench</h1>
  <nav id="nav">
    <button data-section="inferencers" class="active">Inferencers</button>
    <button data-section="optimizers">Optimizers</button>
    <button data-section="results">Results</button>
    <button data-section="benchmarks">Benchmarks</button>
    <button data-section="inventory">Inventory</button>
    <button data-section="run">Run</button>
    <button data-section="chat">Chat</button>
    <button data-section="settings">Settings</button>
  </nav>
</header>
<main>

<section id="section-inferencers" class="section">
  <h2>Inferencer Control</h2>
  <p id="inf-err" class="err"></p>
  <table>
    <thead>
      <tr><th></th><th>Engine</th><th>Version</th><th>Lifecycle</th><th>Port</th><th>PID</th><th>State</th><th></th></tr>
    </thead>
    <tbody id="rows"></tbody>
  </table>
</section>

<section id="section-optimizers" class="section" hidden>
  <h2>Context-Optimization Proxies</h2>
  <p class="note">Proxies chained in front of an engine (Epic-13). This panel is
    read-only: drive the lifecycle from the CLI with <code>bench optimizer start/stop</code>;
    installation is manual via each proxy's reference URL.</p>
  <p id="opt-err"></p>
  <table>
    <thead>
      <tr><th></th><th>Proxy</th><th>Installed</th><th>Port</th><th>Upstream</th><th>State</th><th>URL</th></tr>
    </thead>
    <tbody id="opt-rows"></tbody>
  </table>
</section>

<section id="section-results" class="section" hidden>
  <h2>Live Benchmark Results</h2>
  <p class="empty" id="updated"></p>
  <h3>Leaderboard</h3>
  <p class="empty">Click a column header to sort; click a row to drill into its tasks.</p>
  <input id="leaderboard-filter" type="search" placeholder="Filter by model, agent, suite, or run mode">
  <table>
    <thead>
      <tr>
        <th data-sort-key="name">Model / Agent</th>
        <th data-sort-key="engine_label">Engine</th>
        <th data-sort-key="run_mode">Run Mode</th>
        <th data-sort-key="suite">Suite</th>
        <th class="num" data-sort-key="pass_rate">pass@1</th>
        <th class="num" data-sort-key="median_speed_seconds">Median Latency / Wall</th>
        <th class="num" data-sort-key="median_prefill_tokens_per_second">Prefill tok/s</th>
        <th class="num" data-sort-key="median_decode_tokens_per_second">Decode tok/s</th>
        <th class="num" data-sort-key="mean_cost_usd">$/task</th>
        <th class="num" data-sort-key="failure_count">Failures</th>
      </tr>
    </thead>
    <tbody id="leaderboard"></tbody>
  </table>
  <div id="drilldown"></div>

  <h3>Run History</h3>
  <table>
    <thead>
      <tr>
        <th>Run</th><th>Timestamp</th><th>Models / Agents</th><th>Engines</th><th>Suites</th>
        <th class="num">Tasks</th><th class="num">pass@1</th><th class="num">Median Speed</th>
      </tr>
    </thead>
    <tbody id="run-history"></tbody>
  </table>

  <h3>Sweep</h3>
  <table>
    <thead>
      <tr>
        <th>Model</th><th>Engine</th><th class="num">Context Tokens</th>
        <th class="num">TTFT</th><th class="num">Prefill tok/s</th>
      </tr>
    </thead>
    <tbody id="sweep"></tbody>
  </table>

  <h3 id="warnings-title" hidden>Data-quality warnings</h3>
  <ul id="warnings"></ul>
</section>

<section id="section-benchmarks" class="section" hidden>
  <h2>Benchmarks</h2>
  <p class="note">A finished run matrix read as evidence: pick a comparison axis from the
    catalog (configs/comparisons.yaml) to render it as a report — paired stat panels per
    cohort member, the cross-cutting Pareto frontier, and context scaling where sweep data
    exists. Axes without comparable runs yet list which models to run.</p>
  <p id="bench-err" class="err"></p>
  <p>
    <label for="bench-axis">Comparison axis</label>
    <select id="bench-axis"></select>
  </p>
  <div id="bench-report"></div>
</section>

<section id="section-inventory" class="section" hidden>
  <h2>Local Model Inventory</h2>
  <p class="note">Models downloaded on this box, grouped by inferencer and on-disk
    format. Click a row to jump to the Run section with a compatible inferencer
    pre-filled. The shared table flags one logical model several engines can serve —
    a single download, reusable — so you are not storing it more than once.</p>
  <p id="inv-err" class="err"></p>
  <h3>Downloads by inferencer</h3>
  <table>
    <thead>
      <tr>
        <th>Inferencer</th><th>Format</th><th>Model</th><th>Quant</th>
        <th>Provider</th><th class="num">Size</th>
      </tr>
    </thead>
    <tbody id="inv-models"></tbody>
  </table>
  <h3>Shared across inferencers</h3>
  <p class="empty">One stored artifact several engines can serve.</p>
  <table>
    <thead>
      <tr>
        <th>Format</th><th>Model</th><th>Quant</th>
        <th class="num">Size</th><th>Inferencers</th>
      </tr>
    </thead>
    <tbody id="inv-shared"></tbody>
  </table>

  <h2>Storage tiers</h2>
  <p class="note">Where each model lives across the local disk and the external SSD.
    <strong>Promote</strong> copies an external model onto fast local storage;
    <strong>Demote</strong> evicts a local model out to the SSD to reclaim internal
    disk. Every move is verified server-side (copy → check → publish) and never
    deletes a source before its destination is verified. When the SSD is unplugged
    its models show as offline and move actions are disabled.</p>
  <p id="tier-status" class="note"></p>
  <p id="tier-err" class="err"></p>
  <table>
    <thead>
      <tr>
        <th>Format</th><th>Model</th><th>Quant</th><th>Provider</th>
        <th class="num">Size</th><th>Tier</th><th>Inferencers</th><th></th>
      </tr>
    </thead>
    <tbody id="tier-models"></tbody>
  </table>

  <h3>Auto-tiering</h3>
  <p class="note">Keep the local tier under its disk budget by evicting the
    least-recently-used models to the SSD. This is a dry-run plan — pinned models
    are never evicted; click Apply to run the eviction through the verified demote
    path. Disabled while the SSD is offline or no budget is configured.</p>
  <p id="tier-plan-status" class="note"></p>
  <table>
    <thead>
      <tr><th>Model</th><th>Format</th><th class="num">Size</th></tr>
    </thead>
    <tbody id="tier-plan"></tbody>
  </table>
  <p class="run-actions">
    <button class="act danger" id="tier-apply" disabled>Apply eviction plan</button>
  </p>
  <p id="tier-plan-msg"></p>
</section>

<section id="section-run" class="section" hidden>
  <h2>Run a Benchmark</h2>
  <p class="note">Compose a benchmark from a model, an inferencer, and one or more test
    suites, then launch it. The launch is exclusive: starting a run brings up exactly
    one inference server. Launched runs appear in the live monitor below, and the
    Results section refreshes automatically when a run finishes.</p>
  <p id="run-load-err" class="err"></p>
  <div class="run-grid">
    <div>
      <h3><label for="run-model">Model</label></h3>
      <select id="run-model"></select>
    </div>
    <div>
      <h3><label for="run-inferencer">Inferencer</label></h3>
      <select id="run-inferencer"></select>
    </div>
  </div>
  <p id="run-warn" class="warn"></p>
  <h3>Test suites</h3>
  <fieldset id="run-suites"></fieldset>
  <p class="run-actions">
    <button class="act" id="run-launch">Launch benchmark</button>
  </p>
  <p id="run-msg"></p>
  <h3>Live Runs</h3>
  <p id="run-err" class="err"></p>
  <table>
    <thead>
      <tr>
        <th>Run</th><th>Model</th><th>Suites</th><th>Status</th>
        <th>Progress</th><th>Current Task</th>
        <th class="num">Decode tok/s</th><th class="num">Cost</th><th>Reason</th>
      </tr>
    </thead>
    <tbody id="runs"></tbody>
  </table>
</section>

<section id="section-chat" class="section" hidden>
  <h2>Chat with a Model</h2>
  <p class="note">Smoke-test a downloaded model conversationally without writing a benchmark.
    The model and inferencer selectors use the Inventory scan, so each choice narrows to
    compatible local options. If a compatible server is stopped, start it here before sending.</p>
  <p id="chat-load-err" class="err"></p>
  <div class="chat-grid">
    <div>
      <h3><label for="chat-model">Model</label></h3>
      <select id="chat-model"></select>
    </div>
    <div>
      <h3><label for="chat-inferencer">Inferencer</label></h3>
      <select id="chat-inferencer"></select>
    </div>
    <div>
      <h3><label for="chat-temperature">Temperature</label></h3>
      <input id="chat-temperature" type="number" min="0" max="2" step="0.1" value="0.7">
    </div>
    <div>
      <h3><label for="chat-max-tokens">Max tokens</label></h3>
      <input id="chat-max-tokens" type="number" min="1" step="1" value="1024">
    </div>
  </div>
  <p class="run-actions">
    <button class="act" id="chat-start" disabled>Start selected inferencer</button>
  </p>
  <h3><label for="chat-system">System prompt (optional)</label></h3>
  <textarea id="chat-system" rows="2" placeholder="e.g. You are a terse coding assistant."></textarea>
  <div id="chat-messages"></div>
  <div class="chat-compose">
    <textarea id="chat-input" rows="2" placeholder="Type a message…"></textarea>
    <button class="act" id="chat-send">Send</button>
    <button class="act" id="chat-stop" disabled>Stop</button>
  </div>
  <p id="chat-err" class="err"></p>
</section>

<section id="section-settings" class="section" hidden>
  <h2>Settings</h2>
  <p class="note">Every harness config surface in one read-only view — models, inferencers,
    storage tiering, suites, and agents — each group labelled with the file it comes from.
    Env-var entries show only the variable name and whether it is currently set. Values
    marked read-only are fixed by the benchmark protocol; edit the YAML files to change
    everything else (there is no editor here yet).</p>
  <p id="settings-err" class="err"></p>
  <div id="settings-groups"></div>
</section>

</main>

<div id="modal">
  <div class="card">
    <p id="modal-msg"></p>
    <ul id="modal-list"></ul>
    <button class="act danger" id="modal-confirm">Stop them &amp; start</button>
    <button class="act" id="modal-cancel">Cancel</button>
  </div>
</div>

<script>
// Client-side section navigation: show one section, no reload, no build step.
(function () {
  const buttons = document.querySelectorAll("#nav button");
  const sections = {
    inferencers: document.getElementById("section-inferencers"),
    optimizers: document.getElementById("section-optimizers"),
    results: document.getElementById("section-results"),
    benchmarks: document.getElementById("section-benchmarks"),
    inventory: document.getElementById("section-inventory"),
    run: document.getElementById("section-run"),
    chat: document.getElementById("section-chat"),
    settings: document.getElementById("section-settings"),
  };
  function show(name) {
    for (const key in sections) sections[key].hidden = key !== name;
    buttons.forEach((b) => b.classList.toggle("active", b.dataset.section === name));
  }
  buttons.forEach((b) => b.addEventListener("click", () => show(b.dataset.section)));
  // Exposed so the Inventory section can jump to the Run launcher on row-click.
  window.showSection = show;
  show("inferencers");
})();

// Inferencers section: thin client over Epic-08's /api/status, /api/start, /api/stop.
(function () {
  const rows = document.getElementById("rows");
  const err = document.getElementById("inf-err");
  const modal = document.getElementById("modal");
  let pending = null;
  const STARTING = new Set();

  function setError(msg) { err.textContent = msg || ""; }

  async function refresh() {
    try {
      const res = await fetch("/api/status");
      const data = await res.json();
      render(data.inferencers || []);
    } catch (e) {
      setError("status unavailable: " + e);
    }
  }

  function render(items) {
    rows.innerHTML = "";
    for (const it of items) {
      if (it.running) STARTING.delete(it.name);
      const starting = STARTING.has(it.name);
      const tr = document.createElement("tr");
      const dot = it.running ? "up" : (starting ? "warn" : "down");
      const detail = starting ? "starting…" : it.detail;
      const action = it.lifecycle === "app"
        ? "<span>manage in app</span>"
        : (it.running
            ? `<button class="act danger" data-stop="${it.name}">Stop</button>`
            : (starting
                ? `<button class="act" data-starting="${it.name}" disabled>Starting…</button>`
                : `<button class="act" data-start="${it.name}">Start</button>`));
      tr.innerHTML =
        `<td><span class="dot ${dot}"></span></td>` +
        `<td>${it.name}</td><td>${it.engine_version || "-"}</td>` +
        `<td>${it.lifecycle}</td><td>${it.port}</td>` +
        `<td>${it.pid ?? ""}</td><td>${detail}</td><td>${action}</td>`;
      rows.appendChild(tr);
    }
  }

  async function post(url) {
    const res = await fetch(url, { method: "POST" });
    let body = {};
    try { body = await res.json(); } catch (e) { body = {}; }
    return { status: res.status, body };
  }

  async function startEngine(name, confirm, afterStart, model) {
    setError("");
    STARTING.add(name);
    refresh();
    let url = "/api/start?name=" + encodeURIComponent(name) + (confirm ? "&confirm=1" : "");
    if (model) url += "&model=" + encodeURIComponent(model);
    const { status, body } = await post(url);
    if (status === 409 && body.needs_confirmation) {
      STARTING.delete(name);
      refresh();
      openModal(name, body, afterStart, model);
      return;
    }
    if (status >= 400) {
      STARTING.delete(name);
      setError(body.message || body.error || ("start failed (" + status + ")"));
    }
    refresh();
    if (status < 400 && afterStart) afterStart();
  }

  function openModal(name, body, afterStart, model) {
    pending = { name, afterStart, model };
    document.getElementById("modal-msg").textContent = body.message || "Confirm exclusive start.";
    const list = document.getElementById("modal-list");
    list.innerHTML = "";
    for (const o of body.others || []) {
      const li = document.createElement("li");
      li.textContent = o.name + " (port " + o.port + ")";
      list.appendChild(li);
    }
    modal.classList.add("show");
  }

  function closeModal() { modal.classList.remove("show"); pending = null; }

  document.getElementById("modal-confirm").onclick = () => {
    const item = pending; closeModal();
    if (item) startEngine(item.name, true, item.afterStart, item.model);
  };
  document.getElementById("modal-cancel").onclick = closeModal;

  window.startInferencer = function (name, afterStart, model) {
    return startEngine(name, false, afterStart, model);
  };

  rows.addEventListener("click", (ev) => {
    const start = ev.target.getAttribute("data-start");
    const stop = ev.target.getAttribute("data-stop");
    if (start) startEngine(start, false);
    if (stop) post("/api/stop?name=" + encodeURIComponent(stop)).then(refresh);
  });

  refresh();
  setInterval(refresh, 2000);
})();

// Optimizers section: read-only status over Epic-13's /api/optimizers — a
// distinct panel from Inferencers; lifecycle stays on the CLI (bench optimizer).
(function () {
  const rows = document.getElementById("opt-rows");
  const err = document.getElementById("opt-err");

  async function refresh() {
    try {
      const res = await fetch("/api/optimizers");
      const data = await res.json();
      render(data.optimizers || []);
      err.textContent = "";
    } catch (e) {
      err.textContent = "optimizer status unavailable: " + e;
    }
  }

  function render(items) {
    rows.innerHTML = "";
    for (const it of items) {
      const tr = document.createElement("tr");
      const dot = it.running ? (it.healthy ? "up" : "warn") : "down";
      tr.innerHTML =
        `<td><span class="dot ${dot}"></span></td>` +
        `<td>${it.name}</td><td>${it.installed ? "yes" : "no"}</td>` +
        `<td>${it.port}</td><td>${it.upstream || "-"}</td>` +
        `<td>${it.detail}</td><td>${it.url || "-"}</td>`;
      rows.appendChild(tr);
    }
  }

  refresh();
  setInterval(refresh, 2000);
})();

// Results section: thin client over Epic-07's /api/data live aggregates.
(function () {
  let DATA = { endpoint_models: [], agent_runs: [], sweep_points: [], runs: [], warnings: [] };
  let SORT = { key: "pass_rate", dir: -1 };
  let OPEN = null;

  function pct(value) { return (Number(value || 0) * 100).toFixed(1) + "%"; }
  function num(value, digits) {
    if (value === null || value === undefined) return "-";
    return Number(value).toFixed(digits === undefined ? 3 : digits);
  }
  function cell(text, numeric) {
    const td = document.createElement("td");
    if (numeric) td.className = "num";
    td.textContent = text;
    return td;
  }
  function fillEmpty(tbody, cols, label) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = cols;
    td.className = "empty";
    td.textContent = label;
    tr.appendChild(td);
    tbody.appendChild(tr);
  }

  function leaderboardRows() {
    const rows = [];
    for (const m of DATA.endpoint_models || []) {
      rows.push({
        kind: "endpoint", name: m.model, engine_label: m.engine_label,
        run_mode: "endpoint", suite: m.suite,
        pass_rate: m.pass_rate, median_speed_seconds: m.median_latency_seconds,
        median_prefill_tokens_per_second: m.median_prefill_tokens_per_second,
        median_decode_tokens_per_second: m.median_decode_tokens_per_second,
        mean_cost_usd: m.mean_cost_usd, failure_count: m.failure_count, tasks: m.tasks || [],
      });
    }
    for (const a of DATA.agent_runs || []) {
      rows.push({
        kind: "agent", name: a.agent, engine_label: a.engine_label,
        run_mode: "agent", suite: a.suite,
        pass_rate: a.pass_rate, median_speed_seconds: a.median_wall_time_seconds,
        median_prefill_tokens_per_second: null, median_decode_tokens_per_second: null,
        mean_cost_usd: null, failure_count: a.failure_count, tasks: a.tasks || [],
      });
    }
    return rows;
  }

  function applyFilterAndSort(rows) {
    const q = (document.getElementById("leaderboard-filter").value || "").toLowerCase().trim();
    let out = rows;
    if (q) {
      out = rows.filter((r) =>
        [r.name, r.engine_label, r.run_mode, r.suite]
          .some((v) => (v || "").toLowerCase().includes(q)));
    }
    const key = SORT.key, dir = SORT.dir;
    return out.slice().sort((a, b) => {
      let x = a[key], y = b[key];
      if (typeof x === "string" || typeof y === "string") {
        return String(x || "").localeCompare(String(y || "")) * dir;
      }
      if (x === null || x === undefined) return 1;
      if (y === null || y === undefined) return -1;
      return (x - y) * dir;
    });
  }

  function renderLeaderboard() {
    const tbody = document.getElementById("leaderboard");
    tbody.innerHTML = "";
    const rows = applyFilterAndSort(leaderboardRows());
    if (!rows.length) { fillEmpty(tbody, 10, "No leaderboard rows yet."); return; }
    for (const r of rows) {
      const tr = document.createElement("tr");
      tr.className = "row-clickable";
      tr.append(
        cell(r.name), cell(r.engine_label || "unknown (legacy)"),
        cell(r.run_mode), cell(r.suite || "-"),
        cell(pct(r.pass_rate), true), cell(num(r.median_speed_seconds), true),
        cell(num(r.median_prefill_tokens_per_second), true),
        cell(num(r.median_decode_tokens_per_second), true),
        cell(r.mean_cost_usd === null ? "-" : num(r.mean_cost_usd, 6), true),
        cell(r.failure_count, true),
      );
      tr.addEventListener("click", () => {
        OPEN = { kind: r.kind, name: r.name, engine_label: r.engine_label, suite: r.suite };
        renderDrilldown();
      });
      tbody.appendChild(tr);
    }
  }

  function findRow(open) {
    return leaderboardRows().find(
      (r) => r.kind === open.kind && r.name === open.name &&
        r.engine_label === open.engine_label && r.suite === open.suite);
  }

  function renderDrilldown() {
    const host = document.getElementById("drilldown");
    host.innerHTML = "";
    if (!OPEN) return;
    const row = findRow(OPEN);
    if (!row) { OPEN = null; return; }

    const title = document.createElement("h3");
    title.textContent = "Tasks - " + row.name + " / " + row.engine_label +
      (row.suite ? " (" + row.suite + ")" : "");
    host.appendChild(title);

    const table = document.createElement("table");
    const head = document.createElement("thead");
    const cols = row.kind === "endpoint"
      ? ["Task", "Result", "Failure", "Latency", "$/task", "Prompt tok", "Completion tok", "Preview"]
      : ["Task", "Result", "Failure", "Wall Time", "Exit Code", "Cost Status"];
    const htr = document.createElement("tr");
    for (const c of cols) { const th = document.createElement("th"); th.textContent = c; htr.appendChild(th); }
    head.appendChild(htr);
    table.appendChild(head);

    const body = document.createElement("tbody");
    for (const t of row.tasks) {
      const tr = document.createElement("tr");
      const result = document.createElement("td");
      result.textContent = t.passed === true ? "pass" : (t.passed === false ? "fail" : "-");
      result.className = t.passed === true ? "pass" : (t.passed === false ? "fail" : "");
      if (row.kind === "endpoint") {
        const preview = document.createElement("td");
        preview.className = "preview";
        preview.textContent = t.raw_response_preview || "";
        tr.append(
          cell(t.task_id), result, cell(t.failure_reason || "-"),
          cell(num(t.latency_seconds), true), cell(num(t.cost_usd, 6), true),
          cell(t.prompt_tokens === null ? "-" : t.prompt_tokens, true),
          cell(t.completion_tokens === null ? "-" : t.completion_tokens, true), preview,
        );
      } else {
        tr.append(
          cell(t.task_id), result, cell(t.failure_reason || "-"),
          cell(num(t.wall_time_seconds), true),
          cell(t.exit_code === null ? "-" : t.exit_code, true), cell(t.cost_status || "-"),
        );
      }
      body.appendChild(tr);
    }
    if (!row.tasks.length) fillEmpty(body, cols.length, "No tasks recorded.");
    table.appendChild(body);
    host.appendChild(table);
  }

  function renderRunHistory() {
    const tbody = document.getElementById("run-history");
    tbody.innerHTML = "";
    const rows = DATA.runs || [];
    if (!rows.length) { fillEmpty(tbody, 8, "No runs yet."); return; }
    for (const r of rows) {
      const actors = (r.models || []).concat(r.agents || []);
      const speed = r.median_latency_seconds !== null && r.median_latency_seconds !== undefined
        ? r.median_latency_seconds : r.median_wall_time_seconds;
      const tr = document.createElement("tr");
      tr.append(
        cell(r.source), cell(r.timestamp || "-"), cell(actors.join(", ") || "-"),
        cell((r.engines || []).join(", ") || "unknown (legacy)"),
        cell((r.suites || []).join(", ") || "-"), cell(r.task_count, true),
        cell(pct(r.pass_rate), true), cell(num(speed), true),
      );
      tbody.appendChild(tr);
    }
  }

  function renderSweep(rows) {
    const tbody = document.getElementById("sweep");
    tbody.innerHTML = "";
    if (!rows.length) { fillEmpty(tbody, 5, "No sweep records yet."); return; }
    for (const r of rows) {
      const tr = document.createElement("tr");
      tr.append(
        cell(r.model), cell(r.engine_label || "unknown (legacy)"),
        cell(r.context_tokens, true),
        cell(num(r.ttft_seconds), true), cell(num(r.prefill_tokens_per_second), true),
      );
      tbody.appendChild(tr);
    }
  }

  function renderWarnings(items) {
    const list = document.getElementById("warnings");
    const title = document.getElementById("warnings-title");
    list.innerHTML = "";
    title.hidden = items.length === 0;
    for (const w of items) {
      const li = document.createElement("li");
      li.className = "warn";
      const where = w.line === null ? w.source : (w.source + ":" + w.line);
      li.textContent = where + " - " + w.message;
      list.appendChild(li);
    }
  }

  function renderAll() {
    renderLeaderboard();
    renderDrilldown();
    renderRunHistory();
    renderSweep(DATA.sweep_points || []);
    renderWarnings(DATA.warnings || []);
  }

  document.getElementById("leaderboard-filter").addEventListener("input", renderLeaderboard);
  for (const th of document.querySelectorAll("th[data-sort-key]")) {
    th.addEventListener("click", () => {
      const key = th.getAttribute("data-sort-key");
      SORT = { key, dir: SORT.key === key ? -SORT.dir : -1 };
      renderLeaderboard();
    });
  }

  async function refresh() {
    try {
      const res = await fetch("/api/data");
      DATA = await res.json();
      renderAll();
      document.getElementById("updated").textContent = "Refreshed";
    } catch (e) {
      document.getElementById("updated").textContent = "data unavailable: " + e;
    }
  }

  // Expose so the Run monitor can pull the new JSONL the instant a run finishes.
  window.refreshResults = refresh;
  refresh();
  setInterval(refresh, 3000);
})();

// Run section: thin client over /api/catalog (selectors) and /api/run (launch).
// All launch authority lives in the orchestrator; this only composes the request
// and surfaces the server's verdict.
(function () {
  const modelSel = document.getElementById("run-model");
  const infSel = document.getElementById("run-inferencer");
  const suitesBox = document.getElementById("run-suites");
  const launchBtn = document.getElementById("run-launch");
  const loadErr = document.getElementById("run-load-err");
  const warn = document.getElementById("run-warn");
  const msg = document.getElementById("run-msg");
  let MODELS = [];

  function setMsg(text, kind) {
    msg.textContent = text || "";
    msg.className = kind || "";
  }

  function option(value, label) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label;
    return opt;
  }

  async function load() {
    try {
      const res = await fetch("/api/catalog");
      const data = await res.json();
      MODELS = data.models || [];
      renderModels(MODELS);
      renderInferencers(data.inferencers || []);
      renderSuites(data.suites || []);
      updateWarning();
    } catch (e) {
      loadErr.textContent = "catalog unavailable: " + e;
    }
  }

  function renderModels(models) {
    modelSel.innerHTML = "";
    modelSel.appendChild(option("", models.length ? "Select a model…" : "No models configured"));
    for (const m of models) modelSel.appendChild(option(m.name, m.name));
  }

  function renderInferencers(items) {
    infSel.innerHTML = "";
    infSel.appendChild(option("", items.length ? "Select an inferencer…" : "No inferencers configured"));
    for (const it of items) {
      const suffix = it.lifecycle === "app" ? " (app — not launchable)" : "";
      const opt = option(it.name, it.name + suffix);
      if (it.lifecycle === "app") opt.disabled = true;
      infSel.appendChild(opt);
    }
  }

  function renderSuites(suites) {
    suitesBox.innerHTML = "";
    if (!suites.length) {
      const span = document.createElement("span");
      span.className = "empty";
      span.textContent = "No suites available.";
      suitesBox.appendChild(span);
      return;
    }
    for (const s of suites) {
      const label = document.createElement("label");
      const box = document.createElement("input");
      box.type = "checkbox";
      box.value = s.id;
      box.className = "suite-box";
      box.disabled = !s.available;
      label.appendChild(box);
      const count = s.task_count === null || s.task_count === undefined ? "" : " (" + s.task_count + ")";
      const name = document.createElement("span");
      name.textContent = (s.label || s.id) + count;
      label.appendChild(name);
      if (!s.available) {
        label.classList.add("disabled");
        const reason = document.createElement("span");
        reason.className = "reason";
        reason.textContent = "— " + (s.reason || "unavailable");
        label.appendChild(reason);
      }
      suitesBox.appendChild(label);
    }
  }

  function selectedSuites() {
    return Array.from(suitesBox.querySelectorAll(".suite-box:checked")).map((b) => b.value);
  }

  function updateWarning() {
    warn.textContent = "";
    const model = MODELS.find((m) => m.name === modelSel.value);
    const inf = infSel.value;
    if (model && inf && model.inferencer && model.inferencer !== inf) {
      warn.textContent =
        "Heads up: " + model.name + " declares inferencer '" + model.inferencer +
        "' but you picked '" + inf + "'. Launching anyway will run against '" + inf + "'.";
    }
  }

  function validate() {
    if (!modelSel.value) return "Select a model before launching.";
    if (!infSel.value) return "Select an inferencer before launching.";
    if (!selectedSuites().length) return "Select at least one test suite before launching.";
    return null;
  }

  async function launch(confirm, force) {
    const problem = validate();
    if (problem) { setMsg(problem, "bad"); return; }
    setMsg("Launching…", "");
    launchBtn.disabled = true;
    try {
      const res = await fetch("/api/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: modelSel.value,
          inferencer: infSel.value,
          suites: selectedSuites(),
          confirm: !!confirm,
          force: !!force,
        }),
      });
      let body = {};
      try { body = await res.json(); } catch (e) { body = {}; }
      if (res.status === 202) {
        setMsg("Run accepted (id " + (body.run_id || "?") + "). Watch the Results section.", "ok");
      } else if (res.status === 409 && body.needs_confirmation) {
        setMsg((body.message || "A server is already running.") + " Launch again to stop it and proceed.", "bad");
        launchBtn.dataset.confirm = "1";
        return;
      } else {
        setMsg(body.message || body.error || ("launch failed (" + res.status + ")"), "bad");
      }
    } catch (e) {
      setMsg("launch failed: " + e, "bad");
    } finally {
      launchBtn.disabled = false;
    }
  }

  // Exposed so the Inventory section can pre-fill the launcher with a chosen
  // download and a compatible inferencer (story 11.5-001, AC3). The model is only
  // selected when the registry actually offers it as an option; the inferencer is
  // always set so a downloaded repo maps onto an engine that can serve it.
  window.prefillRun = function (model, inferencer) {
    if (inferencer) infSel.value = inferencer;
    if (model && Array.from(modelSel.options).some((o) => o.value === model)) {
      modelSel.value = model;
    }
    updateWarning();
  };

  modelSel.addEventListener("change", updateWarning);
  infSel.addEventListener("change", updateWarning);
  launchBtn.addEventListener("click", () => {
    const confirm = launchBtn.dataset.confirm === "1";
    launchBtn.dataset.confirm = "";
    launch(confirm, false);
  });

  load();
})();

// Run section: live monitor over /api/runs (story 09.4-001). Polls run progress,
// surfaces terminal status + failure reason, and refreshes Results on completion.
(function () {
  const tbody = document.getElementById("runs");
  const err = document.getElementById("run-err");
  const finished = new Set();  // run ids already pushed to Results, refresh once each

  function num(value, digits) {
    if (value === null || value === undefined) return "-";
    return Number(value).toFixed(digits === undefined ? 1 : digits);
  }
  function td(text, cls) {
    const cell = document.createElement("td");
    if (cls) cell.className = cls;
    cell.textContent = text;
    return cell;
  }

  function render(runs) {
    tbody.innerHTML = "";
    if (!runs.length) {
      const tr = document.createElement("tr");
      const cell = td("No runs launched yet.", "empty");
      cell.colSpan = 9;
      tr.appendChild(cell);
      tbody.appendChild(tr);
      return;
    }
    for (const r of runs) {
      const remaining = r.remaining !== null && r.remaining !== undefined
        ? r.remaining : Math.max((r.total || 0) - (r.completed || 0), 0);
      const progress = (r.passed || 0) + " passed / " + (r.failed || 0) + " failed / "
        + remaining + " left";
      const cost = r.cost_usd === null || r.cost_usd === undefined
        ? "-" : "$" + num(r.cost_usd, 6);
      const terminal = r.status === "completed" || r.status === "failed";
      const statusClass = r.status === "completed" ? "pass" : (r.status === "failed" ? "fail" : "");
      const reason = r.status === "failed" ? (r.error || "failed (no reason given)") : "";
      const tr = document.createElement("tr");
      tr.append(
        td(r.run_id), td(r.model), td((r.suites || []).join(", ")),
        td(r.status, statusClass), td(progress), td(r.last_event || "-"),
        td(num(r.decode_tokens_per_second), "num"), td(cost, "num"),
        td(reason, reason ? "fail" : ""),
      );
      tbody.appendChild(tr);
      if (terminal && !finished.has(r.run_id)) {
        finished.add(r.run_id);
        if (window.refreshResults) window.refreshResults();  // AC2: reflect new JSONL
      }
    }
  }

  async function refresh() {
    try {
      const res = await fetch("/api/runs");
      const data = await res.json();
      err.textContent = "";
      render(data.runs || []);
    } catch (e) {
      err.textContent = "run status unavailable: " + e;
    }
  }

  refresh();
  setInterval(refresh, 2000);
})();

// Chat section: a thin client over /api/chat/catalog (local selectors) and /api/chat (SSE stream).
// Multi-turn state lives here and is posted whole each turn — there is no server DB.
// Streaming is read incrementally from the fetch body and cancelled via AbortController.
(function () {
  const modelSel = document.getElementById("chat-model");
  const infSel = document.getElementById("chat-inferencer");
  const systemBox = document.getElementById("chat-system");
  const tempBox = document.getElementById("chat-temperature");
  const maxBox = document.getElementById("chat-max-tokens");
  const pane = document.getElementById("chat-messages");
  const input = document.getElementById("chat-input");
  const sendBtn = document.getElementById("chat-send");
  const stopBtn = document.getElementById("chat-stop");
  const startBtn = document.getElementById("chat-start");
  const loadErr = document.getElementById("chat-load-err");
  const err = document.getElementById("chat-err");
  const history = [];  // {role, content} turns, posted whole each send
  let controller = null;
  let MODELS = [];
  let INFERENCERS = [];

  function option(value, label) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label;
    return opt;
  }

  function selectedModel() {
    return MODELS.find((m) => m.name === modelSel.value) || null;
  }

  function selectedInferencer() {
    return INFERENCERS.find((it) => it.name === infSel.value) || null;
  }

  function filteredModels() {
    const inf = infSel.value;
    return inf ? MODELS.filter((m) => (m.inferencers || []).includes(inf)) : MODELS;
  }

  function filteredInferencers() {
    const model = selectedModel();
    if (!model) return INFERENCERS;
    const allowed = new Set(model.inferencers || []);
    return INFERENCERS.filter((it) => allowed.has(it.name));
  }

  function inferencerLabel(it) {
    if (!it.available) return it.name + " (no models)";
    const state = it.running ? "running" : "stopped";
    const count = it.model_count || 0;
    return it.name + " (" + state + ", " + count + " model" + (count === 1 ? "" : "s") + ")";
  }

  function renderModels() {
    const current = modelSel.value;
    const models = filteredModels();
    modelSel.innerHTML = "";
    modelSel.appendChild(option("", models.length ? "Select a model…" : "No models for this inferencer"));
    for (const m of models) modelSel.appendChild(option(m.name, m.name));
    if (models.some((m) => m.name === current)) modelSel.value = current;
  }

  function renderInferencers() {
    const current = infSel.value;
    const infs = filteredInferencers();
    infSel.innerHTML = "";
    infSel.appendChild(option("", infs.length ? "Select an inferencer…" : "No compatible inferencers"));
    for (const it of infs) {
      const opt = option(it.name, inferencerLabel(it));
      opt.disabled = !it.available;
      infSel.appendChild(opt);
    }
    if (infs.some((it) => it.name === current && it.available)) infSel.value = current;
  }

  function updateStartButton() {
    const it = selectedInferencer();
    if (!it || !it.available) {
      startBtn.disabled = true;
      startBtn.textContent = "Start selected inferencer";
      return;
    }
    if (it.running) {
      startBtn.disabled = true;
      startBtn.textContent = it.name + " is running";
      return;
    }
    startBtn.disabled = false;
    startBtn.textContent = "Start " + it.name;
  }

  async function load() {
    try {
      const res = await fetch("/api/chat/catalog");
      const data = await res.json();
      MODELS = data.models || [];
      INFERENCERS = data.inferencers || [];
      renderModels();
      renderInferencers();
      updateStartButton();
    } catch (e) {
      loadErr.textContent = "catalog unavailable: " + e;
    }
  }

  function bubble(role) {
    const div = document.createElement("div");
    div.className = "chat-msg " + role;
    const tag = document.createElement("span");
    tag.className = "role";
    tag.textContent = role;
    const text = document.createElement("span");
    div.append(tag, text);
    pane.appendChild(div);
    pane.scrollTop = pane.scrollHeight;
    return text;
  }

  function duration(seconds) {
    if (seconds === null || seconds === undefined) return "unavailable";
    const value = Number(seconds);
    if (!Number.isFinite(value)) return "unavailable";
    if (value < 1) return (value * 1000).toFixed(1) + "ms";
    const minutes = Math.floor(value / 60);
    const rest = value - minutes * 60;
    return minutes ? minutes + "m" + rest.toFixed(3) + "s" : value.toFixed(3) + "s";
  }

  function count(value) {
    return value === null || value === undefined ? "unavailable" : String(value) + " token(s)";
  }

  function rate(value) {
    return value === null || value === undefined ? "unavailable" : Number(value).toFixed(2) + " tokens/s";
  }

  function renderMetrics(textEl, metrics) {
    if (!metrics) return;
    const rows = [
      ["total duration", duration(metrics.total_duration_seconds)],
      ["load duration", duration(metrics.load_duration_seconds)],
      ["prompt eval count", count(metrics.prompt_eval_count)],
      ["prompt eval duration", duration(metrics.prompt_eval_duration_seconds)],
      ["prompt eval rate", rate(metrics.prompt_eval_rate)],
      ["eval count", count(metrics.eval_count)],
      ["eval duration", duration(metrics.eval_duration_seconds)],
      ["eval rate", rate(metrics.eval_rate)],
    ];
    const box = document.createElement("div");
    box.className = "chat-metrics";
    for (const row of rows) {
      const label = document.createElement("span");
      label.textContent = row[0] + ":";
      const value = document.createElement("span");
      value.textContent = row[1];
      box.append(label, value);
    }
    textEl.parentElement.appendChild(box);
    pane.scrollTop = pane.scrollHeight;
  }

  function streaming(on) {
    sendBtn.disabled = on;
    stopBtn.disabled = !on;
    input.disabled = on;
  }

  function stop() {
    if (controller) controller.abort();
  }

  async function send() {
    err.textContent = "";
    const content = input.value.trim();
    if (!content) return;
    if (!modelSel.value) { err.textContent = "Select a model first."; return; }
    if (!infSel.value) { err.textContent = "Select an inferencer first."; return; }
    history.push({ role: "user", content });
    bubble("user").textContent = content;
    input.value = "";

    const max = parseInt(maxBox.value, 10);
    const payload = {
      model: modelSel.value,
      inferencer: infSel.value || undefined,
      messages: history,
      system: systemBox.value.trim() || undefined,
      temperature: Number(tempBox.value),
      max_tokens: Number.isFinite(max) ? max : undefined,
    };
    const out = bubble("assistant");
    let reply = "";
    controller = new AbortController();
    streaming(true);
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
      if (!res.ok || !res.body) {
        let body = {};
        try { body = await res.json(); } catch (e) { body = {}; }
        out.textContent = body.error || ("chat failed (" + res.status + ")");
        history.pop();  // drop the user turn that produced no usable reply
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let nl;
        while ((nl = buf.indexOf("\\n\\n")) !== -1) {
          const frame = buf.slice(0, nl);
          buf = buf.slice(nl + 2);
          const line = frame.split("\\n").find((l) => l.startsWith("data:"));
          if (!line) continue;
          let ev = {};
          try { ev = JSON.parse(line.slice(5).trim()); } catch (e) { continue; }
          if (ev.delta) { reply += ev.delta; out.textContent = reply; pane.scrollTop = pane.scrollHeight; }
          if (ev.error) { err.textContent = ev.error; }
          if (ev.done && ev.metrics) { renderMetrics(out, ev.metrics); }
        }
      }
      if (reply) history.push({ role: "assistant", content: reply });
      else history.pop();
    } catch (e) {
      if (e && e.name === "AbortError") {
        out.textContent = reply + " [stopped]";
        if (reply) history.push({ role: "assistant", content: reply });
        else history.pop();
      } else {
        err.textContent = "chat failed: " + e;
        history.pop();
      }
    } finally {
      streaming(false);
      controller = null;
    }
  }

  sendBtn.addEventListener("click", send);
  stopBtn.addEventListener("click", stop);
  startBtn.addEventListener("click", () => {
    const it = selectedInferencer();
    if (!it || !window.startInferencer) return;
    startBtn.disabled = true;
    window.startInferencer(it.name, load, modelSel.value);
  });
  infSel.addEventListener("change", () => {
    renderModels();
    updateStartButton();
  });
  modelSel.addEventListener("change", () => {
    renderInferencers();
    renderModels();
    updateStartButton();
  });
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); send(); }
  });

  load();
})();

// Inventory section: a thin client over /api/inventory (story 11.5-001). Renders
// downloads grouped by inferencer + format, and the shared-model sets; a row-click
// jumps to the Run launcher pre-filled with a compatible inferencer.
(function () {
  const modelsBody = document.getElementById("inv-models");
  const sharedBody = document.getElementById("inv-shared");
  const err = document.getElementById("inv-err");

  function humanSize(bytes) {
    if (bytes === null || bytes === undefined) return "-";
    let n = Number(bytes);
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return (i === 0 ? n : n.toFixed(1)) + " " + units[i];
  }
  function cell(text, numeric) {
    const td = document.createElement("td");
    if (numeric) td.className = "num";
    td.textContent = text;
    return td;
  }
  function fillEmpty(tbody, cols, label) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = cols;
    td.className = "empty";
    td.textContent = label;
    tr.appendChild(td);
    tbody.appendChild(tr);
  }

  function renderModels(models) {
    modelsBody.innerHTML = "";
    if (!models.length) { fillEmpty(modelsBody, 6, "No downloaded models found."); return; }
    // Group by inferencer then format so the table reads per-engine, per-format.
    const sorted = models.slice().sort((a, b) =>
      (a.inferencer || "").localeCompare(b.inferencer || "") ||
      (a.format || "").localeCompare(b.format || "") ||
      (a.name || "").localeCompare(b.name || ""));
    for (const m of sorted) {
      const tr = document.createElement("tr");
      tr.className = "row-clickable";
      tr.append(
        cell(m.inferencer), cell(m.format), cell(m.name),
        cell(m.quant || "-"), cell(m.provider || "-"), cell(humanSize(m.size_bytes), true),
      );
      tr.addEventListener("click", () => {
        if (window.showSection) window.showSection("run");
        if (window.prefillRun) window.prefillRun(m.name, m.inferencer);
      });
      modelsBody.appendChild(tr);
    }
  }

  function renderShared(shared) {
    sharedBody.innerHTML = "";
    if (!shared.length) { fillEmpty(sharedBody, 5, "No models shared across inferencers."); return; }
    for (const s of shared) {
      const tr = document.createElement("tr");
      tr.append(
        cell(s.format), cell(s.name), cell(s.quant || "-"),
        cell(humanSize(s.size_bytes), true), cell((s.inferencers || []).join(", ")),
      );
      sharedBody.appendChild(tr);
    }
  }

  async function refresh() {
    try {
      const res = await fetch("/api/inventory");
      const data = await res.json();
      err.textContent = "";
      renderModels(data.models || []);
      renderShared(data.shared || []);
    } catch (e) {
      err.textContent = "inventory unavailable: " + e;
    }
  }

  refresh();
})();

// Storage-tier view (story 12.6-002): a thin client over /api/tiers (tier
// badges), /api/promote + /api/demote (verified moves), and /api/tier-plan +
// /api/tier-apply (auto-tiering). The server holds all move/eviction authority;
// this only renders tiers and surfaces the server's verdict. Move actions are
// disabled when the SSD is offline (AC4).
(function () {
  const modelsBody = document.getElementById("tier-models");
  const planBody = document.getElementById("tier-plan");
  const status = document.getElementById("tier-status");
  const planStatus = document.getElementById("tier-plan-status");
  const err = document.getElementById("tier-err");
  const planMsg = document.getElementById("tier-plan-msg");
  const applyBtn = document.getElementById("tier-apply");
  let busy = false;

  function humanSize(bytes) {
    if (bytes === null || bytes === undefined) return "-";
    let n = Number(bytes);
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return (i === 0 ? n : n.toFixed(1)) + " " + units[i];
  }
  function cell(text, numeric) {
    const td = document.createElement("td");
    if (numeric) td.className = "num";
    td.textContent = text;
    return td;
  }
  function fillEmpty(tbody, cols, label) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = cols;
    td.className = "empty";
    td.textContent = label;
    tr.appendChild(td);
    tbody.appendChild(tr);
  }

  function tierBadge(model, offline) {
    const onLocal = (model.tiers || []).includes("local");
    const onExternal = (model.tiers || []).includes("external");
    if (model.present_in_both) return "local + external (redundant)";
    if (onExternal && !onLocal) return offline ? "external (offline)" : "external";
    if (onLocal) return "local";
    return (model.tiers || []).join(", ") || "-";
  }

  function actionCell(model, offline) {
    const onLocal = (model.tiers || []).includes("local");
    const onExternal = (model.tiers || []).includes("external");
    const td = document.createElement("td");
    const attrs = `data-name="${encodeURIComponent(model.name)}" data-format="${model.format}"`;
    const lock = busy ? " disabled" : "";
    if (onExternal && !onLocal) {
      td.innerHTML = offline
        ? '<span class="empty">SSD offline</span>'
        : `<button class="act" data-promote ${attrs}${lock}>Promote</button>`;
    } else if (onLocal) {
      td.innerHTML = offline
        ? '<span class="empty">demote disabled — SSD offline</span>'
        : `<button class="act danger" data-demote ${attrs}${lock}>Demote</button>`;
    }
    return td;
  }

  function renderModels(data) {
    const offline = data.external_availability === "offline";
    const models = data.models || [];
    modelsBody.innerHTML = "";
    if (!models.length) { fillEmpty(modelsBody, 8, "No models found."); }
    const sorted = models.slice().sort((a, b) =>
      (a.format || "").localeCompare(b.format || "") ||
      (a.name || "").localeCompare(b.name || ""));
    for (const m of sorted) {
      const tr = document.createElement("tr");
      tr.append(
        cell(m.format), cell(m.name), cell(m.quant || "-"), cell(m.provider || "-"),
        cell(humanSize(m.size_bytes), true), cell(tierBadge(m, offline)),
        cell((m.inferencers || []).join(", ") || "-"),
      );
      tr.appendChild(actionCell(m, offline));
      modelsBody.appendChild(tr);
    }
    const avail = offline ? "offline — plug in the SSD to enable moves" : "mounted";
    const cached = data.external_cached ? " (showing last-known catalog)" : "";
    status.classList.remove("progress");
    status.textContent = "External SSD: " + avail + cached +
      ". Reclaimable across tiers: " + humanSize(data.reclaimable_bytes) +
      " of " + humanSize(data.total_bytes) + " total.";
  }

  function renderPlan(data) {
    planBody.innerHTML = "";
    const evictions = data.evictions || [];
    if (!data.configured) {
      planStatus.textContent = "No disk budget configured — add an auto_tier block to enable.";
      fillEmpty(planBody, 3, "Auto-tiering is not configured.");
      applyBtn.disabled = true;
      return;
    }
    if (!evictions.length) {
      fillEmpty(planBody, 3, data.paused
        ? "Auto-tiering paused — the SSD is offline."
        : "Nothing to evict — the local tier is within budget.");
    }
    for (const e of evictions) {
      const tr = document.createElement("tr");
      tr.append(cell(e.name), cell(e.format), cell(humanSize(e.size_bytes), true));
      planBody.appendChild(tr);
    }
    const parts = [];
    if (data.paused) parts.push("Paused: the SSD is offline.");
    parts.push("Would reclaim " + humanSize(data.bytes_reclaimed) +
      " (over budget by " + humanSize(data.bytes_to_reclaim) + ").");
    if ((data.pinned || []).length) parts.push("Pinned (never evicted): " + data.pinned.join(", ") + ".");
    for (const w of data.warnings || []) parts.push(w);
    planStatus.textContent = parts.join(" ");
    applyBtn.disabled = data.paused || !evictions.length;
  }

  async function post(url) {
    const res = await fetch(url, { method: "POST" });
    let body = {};
    try { body = await res.json(); } catch (e) { body = {}; }
    return { status: res.status, body };
  }

  async function refresh() {
    try {
      const res = await fetch("/api/tiers");
      const data = await res.json();
      err.textContent = "";
      renderModels(data);
    } catch (e) {
      err.textContent = "tier inventory unavailable: " + e;
    }
    try {
      const res = await fetch("/api/tier-plan");
      renderPlan(await res.json());
    } catch (e) {
      planStatus.textContent = "auto-tiering plan unavailable: " + e;
    }
  }

  async function move(verb, name, format) {
    if (busy) return;
    busy = true;
    err.textContent = "";
    planMsg.textContent = "";
    status.classList.add("progress");
    status.textContent = verb === "promote" ? "Promoting…" : "Demoting…";
    try {
      const url = "/api/" + verb + "?name=" + encodeURIComponent(name) +
        "&format=" + encodeURIComponent(format);
      const { status: code, body } = await post(url);
      if (code >= 400) {
        err.textContent = body.error || (verb + " failed (" + code + ")");
        busy = false;
        refresh();
        return;
      }
      // 202: the move runs in a background worker (story 12.6-003) — poll for
      // live progress instead of holding a request open; the UI stays usable.
      watchMove();
    } catch (e) {
      err.textContent = verb + " failed: " + e;
      busy = false;
      refresh();
    }
  }

  function moveLabel(job) {
    const verbing = job.verb === "promote" ? "Promoting" : "Demoting";
    let text = verbing + " " + job.name + "… " +
      humanSize(job.bytes_done) + " of " + humanSize(job.bytes_total);
    if (job.bytes_total) {
      text += " (" + Math.round(100 * job.bytes_done / job.bytes_total) + "%)";
    }
    return text + ", " + Math.round(job.elapsed_seconds) + "s elapsed.";
  }

  async function watchMove() {
    // Polls /api/move-status until the background move finishes, keeping the
    // status line live; also resumes after a page reload mid-move. Ends with a
    // refresh so the model's tier updates on completion (AC2).
    for (;;) {
      let job = null;
      try {
        const res = await fetch("/api/move-status");
        job = (await res.json()).job;
      } catch (e) {
        err.textContent = "move status unavailable: " + e;
        break;
      }
      if (!job) break;
      if (job.state === "running") {
        busy = true;
        status.classList.add("progress");
        status.textContent = moveLabel(job);
        await new Promise((resolve) => setTimeout(resolve, 1000));
        continue;
      }
      if (job.state === "error") err.textContent = job.error || "move failed";
      break;
    }
    status.classList.remove("progress");
    busy = false;
    refresh();
  }

  modelsBody.addEventListener("click", (ev) => {
    const btn = ev.target.closest("button");
    if (!btn) return;
    const name = decodeURIComponent(btn.getAttribute("data-name") || "");
    const format = btn.getAttribute("data-format") || "";
    if (btn.hasAttribute("data-promote")) move("promote", name, format);
    if (btn.hasAttribute("data-demote")) move("demote", name, format);
  });

  applyBtn.addEventListener("click", async () => {
    if (busy) return;
    busy = true;
    planMsg.textContent = "Applying eviction plan…";
    try {
      const { status: code, body } = await post("/api/tier-apply");
      planMsg.textContent = code >= 400
        ? (body.error || ("apply failed (" + code + ")"))
        : ("Evicted " + (body.count || 0) + " model(s).");
    } catch (e) {
      planMsg.textContent = "apply failed: " + e;
    } finally {
      busy = false;
      refresh();
    }
  });

  // watchMove resumes progress if a move is already running (page reload
  // mid-move) and falls through to the initial refresh otherwise.
  watchMove();
})();

// Settings section: aggregate over /api/settings (story 15.1-001). Renders
// whatever groups/items/fields the server sends — all interpretation (env
// indicators, protocol locks, per-group load errors, which groups are editable)
// happens server-side. Groups flagged editable get a YAML editor over the
// validated write path at /api/settings/config (story 15.3-003).
(function () {
  const host = document.getElementById("settings-groups");
  const err = document.getElementById("settings-err");

  function valueCell(f) {
    const td = document.createElement("td");
    td.textContent = f.value === null || f.value === undefined ? "-" : String(f.value);
    if (f.is_set === true || f.is_set === false) {
      const badge = document.createElement("span");
      badge.className = f.is_set ? "flag-set" : "flag-unset";
      badge.textContent = f.is_set ? " (set)" : " (unset)";
      td.appendChild(badge);
    }
    return td;
  }

  function noteCell(f) {
    const td = document.createElement("td");
    if (f.locked) {
      td.className = "lock-note";
      td.textContent = "read-only \\u2014 " + (f.rationale || "");
    }
    return td;
  }

  function renderGroup(g) {
    const title = document.createElement("h3");
    title.textContent = g.label;
    const src = document.createElement("span");
    src.className = "settings-source";
    src.textContent = "from " + g.source;
    title.appendChild(src);
    host.appendChild(title);
    if (g.error) {
      const p = document.createElement("p");
      p.className = "err";
      p.textContent = g.source + ": " + g.error;
      host.appendChild(p);
      return;
    }
    const table = document.createElement("table");
    const thead = document.createElement("thead");
    const htr = document.createElement("tr");
    for (const label of ["Item", "Setting", "Value", ""]) {
      const th = document.createElement("th");
      th.textContent = label;
      htr.appendChild(th);
    }
    thead.appendChild(htr);
    table.appendChild(thead);
    const tbody = document.createElement("tbody");
    for (const item of g.items || []) {
      (item.fields || []).forEach((f, i) => {
        const tr = document.createElement("tr");
        const nameTd = document.createElement("td");
        nameTd.textContent = i === 0 ? item.name : "";
        const settingTd = document.createElement("td");
        settingTd.textContent = f.label;
        tr.append(nameTd, settingTd, valueCell(f), noteCell(f));
        tbody.appendChild(tr);
      });
    }
    if (!(g.items || []).length) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 4;
      td.className = "empty";
      td.textContent = "Nothing configured.";
      tr.appendChild(td);
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    host.appendChild(table);
    if (g.editable) attachEditor(g);
  }

  // One YAML editor per editable group (story 15.3-003): load content + hash,
  // save through the validated write path. The server rejects invalid edits
  // (422), stale forms (409, edit the reloaded file), and unregistered files
  // (404); a save may return warnings (e.g. a removed suite id still used by a
  // saved launcher selection) which are shown but never block the write.
  function attachEditor(g) {
    const details = document.createElement("details");
    details.className = "settings-editor";
    const summary = document.createElement("summary");
    summary.textContent = "Edit " + g.source;
    const note = document.createElement("p");
    note.className = "editor-note";
    note.textContent = g.editable_note || "";
    const area = document.createElement("textarea");
    area.spellcheck = false;
    const save = document.createElement("button");
    save.textContent = "Save";
    const reload = document.createElement("button");
    reload.textContent = "Reload";
    const status = document.createElement("p");
    const warnings = document.createElement("ul");
    warnings.className = "editor-warnings";
    details.append(summary, note, area, save, reload, status, warnings);
    host.appendChild(details);
    let hash = null;

    function fail(message) {
      status.textContent = message;
      status.className = "err";
    }

    async function load() {
      warnings.innerHTML = "";
      status.className = "";
      status.textContent = "";
      try {
        const res = await fetch("/api/settings/config?id=" + encodeURIComponent(g.id));
        const data = await res.json();
        if (!res.ok) { fail(data.error || ("load failed (" + res.status + ")")); return; }
        area.value = data.content;
        hash = data.content_hash;
      } catch (e) { fail("load failed: " + e); }
    }

    details.addEventListener("toggle", () => {
      if (details.open) load();
      else refresh();  // closing the editor lets the tables catch up
    });
    reload.addEventListener("click", load);
    save.addEventListener("click", async () => {
      warnings.innerHTML = "";
      status.className = "";
      status.textContent = "saving...";
      try {
        const res = await fetch("/api/settings/config?id=" + encodeURIComponent(g.id), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content: area.value, expected_hash: hash }),
        });
        const data = await res.json();
        if (!res.ok) { fail(data.error || ("save failed (" + res.status + ")")); return; }
        hash = data.content_hash;
        status.textContent = "saved (backup: " + data.backup + ")";
        for (const w of data.warnings || []) {
          const li = document.createElement("li");
          li.className = "warn";
          li.textContent = w;
          warnings.appendChild(li);
        }
      } catch (e) { fail("save failed: " + e); }
    });
  }

  async function refresh() {
    // Never re-render over an open editor: it would wipe unsaved edits.
    if (host.querySelector(".settings-editor[open]")) return;
    try {
      const res = await fetch("/api/settings");
      const data = await res.json();
      host.innerHTML = "";
      (data.groups || []).forEach(renderGroup);
      err.textContent = "";
    } catch (e) {
      err.textContent = "settings unavailable: " + e;
    }
  }

  refresh();
  setInterval(refresh, 10000);
})();

// Benchmarks section (story 17.2-001): a thin client over /api/compare/axes
// (the picker) and /api/compare/report (one axis as report data). The hero /
// kicker / chip / stat-tile builders are the reusable "report idiom" primitives;
// charts are inline SVG painted only through the chart tokens, so they re-color
// live when the theme toggles.
(function () {
  const picker = document.getElementById("bench-axis");
  const host = document.getElementById("bench-report");
  const err = document.getElementById("bench-err");
  const SIDE_CLASSES = 4;
  const METRICS = [
    ["prefill_tokens_per_second", "prefill", fmtSpeed],
    ["decode_tokens_per_second", "decode", fmtSpeed],
    ["ttft_seconds", "TTFT", fmtSecs],
    ["pass_at_1", "pass@1", fmtPct],
    ["cost_per_task_usd", "$/task", fmtCost],
  ];

  function fmtPct(v) { return (v * 100).toFixed(0) + "%"; }
  function fmtSpeed(v) { return v.toFixed(0) + " tok/s"; }
  function fmtSecs(v) { return v.toFixed(2) + " s"; }
  function fmtCost(v) { return "$" + v.toFixed(4); }
  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function sideClass(side) {
    if (side === null || side === undefined) return "";
    return "side-" + ((side % SIDE_CLASSES) + 1);
  }
  function sidePaint(side) {
    if (side === null || side === undefined) return "var(--chart-grey-3)";
    return "var(--cmp-side-" + ((side % SIDE_CLASSES) + 1) + ")";
  }
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // --- report idiom primitives (reused by future report surfaces) ---
  function kicker(text) { return el("p", "report-kicker", text); }
  function hero(sides) {
    const row = el("div", "report-hero");
    sides.forEach((side, i) => {
      if (i > 0) row.appendChild(el("span", "hero-vs", "vs"));
      row.appendChild(el("span", "hero-side " + sideClass(side.index), side.name));
    });
    return row;
  }
  function subtitle(text) { return el("p", "report-subtitle", text); }
  function chipRow(chips) {
    const row = el("div", "chip-row");
    for (const chip of chips) {
      const node = el("span", "chip");
      node.appendChild(el("b", "", chip.label));
      node.appendChild(document.createTextNode(" " + chip.value));
      row.appendChild(node);
    }
    return row;
  }
  function statTile(member, maxima) {
    const panel = el("div", "stat-panel " + sideClass(member.side));
    const heading = el("h4", "", member.model + " [" + member.engine_label + "]");
    if (member.controlled) {
      const badge = el("span", "badge cmp-badge", "controlled pair");
      badge.title = member.controlled.reason || "";
      heading.appendChild(document.createTextNode(" "));
      heading.appendChild(badge);
    }
    panel.appendChild(heading);
    let meta = member.side_name;
    if (member.suite) {
      meta += " · " + member.suite + (member.suite_version ? " v" + member.suite_version : "");
    }
    panel.appendChild(el("p", "panel-meta", meta));
    for (const [key, label, fmt] of METRICS) {
      const value = member.stats[key];
      const row = el("div", "stat-row");
      row.appendChild(el("span", "", label));
      const bar = el("div", "stat-bar");
      const fill = el("i");
      const max = maxima[key];
      const has = value !== null && value !== undefined;
      fill.style.width = has && max ? Math.max(2, (value / max) * 100) + "%" : "0";
      bar.appendChild(fill);
      row.appendChild(bar);
      row.appendChild(el("span", "num", has ? fmt(value) : "—"));
      panel.appendChild(row);
    }
    return panel;
  }

  // --- SVG charts (geometry mirrors the static dashboard's chart module) ---
  const W = 480, H = 300, L = 62, R = W - 16, T = 18, B = H - 46;
  function scale(v, lo, hi, loPx, hiPx) {
    if (hi === lo) return (loPx + hiPx) / 2;
    return loPx + ((v - lo) / (hi - lo)) * (hiPx - loPx);
  }
  function bounds(values) {
    let lo = Math.min.apply(null, values), hi = Math.max.apply(null, values);
    if (lo === hi) { const pad = Math.abs(lo) * 0.1 || 1.0; return [lo - pad, hi + pad]; }
    return [lo, hi];
  }
  function axesSvg(xTitle, yTitle, xLo, xHi, yLo, yHi, xFmt, yFmt) {
    const midY = ((T + B) / 2).toFixed(0);
    return [
      '<line x1="' + L + '" y1="' + T + '" x2="' + R + '" y2="' + T + '" class="grid"/>',
      '<line x1="' + L + '" y1="' + midY + '" x2="' + R + '" y2="' + midY + '" class="grid"/>',
      '<line x1="' + L + '" y1="' + B + '" x2="' + R + '" y2="' + B + '" class="axis"/>',
      '<line x1="' + L + '" y1="' + T + '" x2="' + L + '" y2="' + B + '" class="axis"/>',
      '<text x="' + L + '" y="' + (B + 14) + '" class="tick">' + esc(xFmt(xLo)) + "</text>",
      '<text x="' + R + '" y="' + (B + 14) + '" class="tick" text-anchor="end">' + esc(xFmt(xHi)) + "</text>",
      '<text x="' + (L - 6) + '" y="' + B + '" class="tick" text-anchor="end">' + esc(yFmt(yLo)) + "</text>",
      '<text x="' + (L - 6) + '" y="' + (T + 8) + '" class="tick" text-anchor="end">' + esc(yFmt(yHi)) + "</text>",
      '<text x="' + ((L + R) / 2).toFixed(0) + '" y="' + (H - 8) + '" class="axis-title" text-anchor="middle">' + esc(xTitle) + "</text>",
      '<text x="14" y="' + ((T + B) / 2).toFixed(0) + '" class="axis-title" text-anchor="middle" ' +
        'transform="rotate(-90 14 ' + ((T + B) / 2).toFixed(0) + ')">' + esc(yTitle) + "</text>",
    ];
  }
  function svgChart(parts, label) {
    const wrap = el("div");
    wrap.innerHTML = '<svg viewBox="0 0 ' + W + " " + H + '" class="chart-svg" role="img" aria-label="' +
      esc(label) + '">' + parts.join("") + "</svg>";
    return wrap.firstChild;
  }

  function frontierChart(points) {
    const [xLo, xHi] = bounds(points.map((p) => p.decode_tokens_per_second));
    const yLo = 0, yHi = 1;
    const parts = axesSvg("Median decode (tok/s)", "pass@1", xLo, xHi, yLo, yHi, fmtSpeed, fmtPct);
    // Accent-marked frontier: connect the Pareto-optimal points, then draw
    // every configuration as a memory-sized, side-colored point.
    const optimal = points.filter((p) => p.frontier)
      .sort((a, b) => a.decode_tokens_per_second - b.decode_tokens_per_second);
    if (optimal.length > 1) {
      const line = optimal.map((p) =>
        scale(p.decode_tokens_per_second, xLo, xHi, L, R).toFixed(2) + "," +
        scale(p.pass_at_1, yLo, yHi, B, T).toFixed(2)).join(" ");
      parts.push('<polyline points="' + line + '" fill="none" style="stroke:var(--accent)" ' +
        'stroke-width="1.5" stroke-dasharray="4 3"/>');
    }
    const maxMem = Math.max.apply(null, points.map((p) => p.memory_bytes || 0));
    for (const p of points) {
      const cx = scale(p.decode_tokens_per_second, xLo, xHi, L, R).toFixed(2);
      const cy = scale(p.pass_at_1, yLo, yHi, B, T).toFixed(2);
      const r = p.memory_bytes && maxMem ? 4 + 6 * Math.sqrt(p.memory_bytes / maxMem) : 4;
      const tip = p.label + ": " + fmtSpeed(p.decode_tokens_per_second) + ", " + fmtPct(p.pass_at_1);
      parts.push('<circle cx="' + cx + '" cy="' + cy + '" r="' + r.toFixed(1) +
        '" style="fill:' + sidePaint(p.side) + '"><title>' + esc(tip) + "</title></circle>");
      if (p.frontier) {
        parts.push('<circle cx="' + cx + '" cy="' + cy + '" r="' + (r + 2.5).toFixed(1) +
          '" fill="none" style="stroke:var(--accent)" stroke-width="1.5"/>');
      }
    }
    return svgChart(parts, "Pareto frontier — pass@1 vs decode tok/s");
  }

  function contextChart(series) {
    const all = series.flatMap((s) => s.points).filter((p) => p.prefill_tokens_per_second !== null);
    const [xLo, xHi] = bounds(all.map((p) => p.context_tokens));
    const yHi = Math.max.apply(null, all.map((p) => p.prefill_tokens_per_second)) || 1;
    const parts = axesSvg("Context tokens", "Prefill (tok/s)", xLo, xHi, 0, yHi,
      (v) => Math.round(v).toLocaleString(), fmtSpeed);
    const legend = el("ul", "legend");
    for (const s of series) {
      const coords = s.points.filter((p) => p.prefill_tokens_per_second !== null).map((p) => [
        scale(p.context_tokens, xLo, xHi, L, R),
        scale(p.prefill_tokens_per_second, 0, yHi, B, T),
      ]);
      if (coords.length > 1) {
        parts.push('<polyline points="' +
          coords.map((c) => c[0].toFixed(2) + "," + c[1].toFixed(2)).join(" ") +
          '" fill="none" style="stroke:' + sidePaint(s.side) + '" stroke-width="2"/>');
      }
      for (const c of coords) {
        parts.push('<circle cx="' + c[0].toFixed(2) + '" cy="' + c[1].toFixed(2) +
          '" r="3.5" style="fill:' + sidePaint(s.side) + '"/>');
      }
      const item = el("li");
      const swatch = el("span", "swatch", "●");
      swatch.style.color = sidePaint(s.side);
      item.appendChild(swatch);
      item.appendChild(document.createTextNode(s.label));
      legend.appendChild(item);
    }
    const wrap = el("div");
    wrap.appendChild(svgChart(parts, "Context scaling — prefill tok/s by context size"));
    wrap.appendChild(legend);
    return wrap;
  }

  function renderReport(data) {
    host.innerHTML = "";
    host.appendChild(kicker(data.axis.title));
    host.appendChild(hero(data.sides));
    host.appendChild(subtitle(data.subtitle));
    if (data.axis.description) host.appendChild(el("p", "note", data.axis.description));
    if (data.chips.length) host.appendChild(chipRow(data.chips));

    if (!data.data_ready) {
      host.appendChild(el("h3", "", "No comparable runs yet"));
      for (const side of data.sides) {
        if (!side.models_to_run.length) continue;
        host.appendChild(el("p", "empty " + sideClass(side.index),
          side.name + ": run " + side.models_to_run.join(", ")));
      }
    }

    if (data.members.length) {
      host.appendChild(el("h3", "", "Paired stats"));
      const maxima = {};
      for (const [key] of METRICS) {
        maxima[key] = Math.max.apply(null,
          data.members.map((m) => m.stats[key]).filter((v) => v !== null && v !== undefined).concat(0));
      }
      const grid = el("div", "panel-grid");
      for (const member of data.members) grid.appendChild(statTile(member, maxima));
      host.appendChild(grid);
    }

    if (data.frontier.length) {
      host.appendChild(el("h3", "", "Pareto frontier — pass@1 vs decode tok/s"));
      host.appendChild(el("p", "empty",
        "Every configuration with data; point size tracks memory footprint, the accent ring marks the frontier."));
      host.appendChild(frontierChart(data.frontier));
    }

    if (data.context_scaling.length) {
      host.appendChild(el("h3", "", "Context scaling"));
      host.appendChild(contextChart(data.context_scaling));
    }
  }

  async function renderAxis(id) {
    if (!id) return;
    try {
      const res = await fetch("/api/compare/report?axis=" + encodeURIComponent(id));
      const data = await res.json();
      if (!res.ok) { err.textContent = data.error || "report unavailable"; return; }
      err.textContent = "";
      renderReport(data);
    } catch (e) {
      err.textContent = "report unavailable: " + e;
    }
  }

  async function loadAxes() {
    try {
      const res = await fetch("/api/compare/axes");
      const data = await res.json();
      picker.innerHTML = "";
      for (const axis of data.axes || []) {
        const option = document.createElement("option");
        option.value = axis.id;
        option.textContent = axis.title + (axis.data_ready ? "" : " — no data yet");
        picker.appendChild(option);
      }
      err.textContent = (data.errors || []).join("; ");
      if (picker.value) renderAxis(picker.value);
    } catch (e) {
      err.textContent = "axis catalog unavailable: " + e;
    }
  }

  picker.addEventListener("change", () => renderAxis(picker.value));
  loadAxes();
})();
</script>
</body>
</html>
"""

# Inject the shared token layer + base styles (story 16.1-001) and the
# pre-paint script + theme toggle chrome (story 16.1-002) into the page.
_PAGE = (
    _PAGE.replace("/*__THEME_CSS__*/", THEME_CSS)
    .replace("<!--__THEME_HEAD__-->", THEME_HEAD_SNIPPET)
    .replace("<!--__THEME_TOGGLE__-->", THEME_TOGGLE_SNIPPET)
)
