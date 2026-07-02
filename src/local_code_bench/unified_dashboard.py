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
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import chat
from . import dashboard_server as results_panel
from . import launch
from .config import (
    AutoTierConfig,
    ConfigError,
    ExternalRepoConfig,
    InferencerConfig,
    ModelConfig,
    TokenPrices,
    load_autotier,
    load_external_repo,
    load_inferencers,
    load_models,
)
from .inferencers import autotier
from .inferencers import dashboard as inferencer_panel
from .inferencers import inventory
from .inferencers import tiered, tiering
from .inferencers.external import check_availability
from .suite_catalog import catalog_payload

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
    cache_dir: str | Path = ".cache/benchmarks"
    suites_path: str | Path = "configs/suites.yaml"
    results_dir: str | Path | None = None
    # Epic-12 tiered storage (story 12.6-002): the optional external SSD tier and
    # the auto-tiering policy drive the Inventory section's tier badges, the
    # promote/demote controls, and the auto-tiering plan. Both are optional so a
    # single-tier config (no ``external_repo`` / ``auto_tier`` block) still serves
    # the dashboard — the tier view then shows only local models with no controls.
    external_cfg: ExternalRepoConfig | None = None
    autotier_cfg: AutoTierConfig | None = None


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
    return ModelConfig(
        name=local_model.name,
        type="openai",
        base_url=f"http://127.0.0.1:{inferencer_cfg.port}/v1",
        model_id=local_model.name,
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


def promote_action(ctx: DashboardContext, name: str, store_format: str) -> tuple[int, dict]:
    """Promote an external-tier model into a compatible engine's local store.

    A thin seam over :func:`tiering.promote_model` (story 12.3-001). Refuses up
    front — moving no bytes — when no external tier is configured or the SSD is
    offline; 404s when the named external model or a compatible local store is
    absent. A :class:`tiering.PromoteError` (in-use / no space / integrity) is
    surfaced verbatim as a 409. The response carries only the new tier and the
    bytes copied — never the on-disk destination path (AC4).
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
        result = tiering.promote_model(source, target, ctx.external_cfg, ctx.configs, ctx.state_dir)
    except tiering.PromoteError as exc:
        return 409, {"error": str(exc)}
    return 200, {
        "promoted": {
            "name": name,
            "tier": "local",
            "bytes_copied": result.bytes_copied,
            "verified": result.verified,
        }
    }


def demote_action(ctx: DashboardContext, name: str, store_format: str) -> tuple[int, dict]:
    """Demote a local-tier model out to the external tier, reclaiming local disk.

    A thin seam over :func:`tiering.demote_model` (story 12.3-002). Refuses up
    front when no external tier is configured or the SSD is offline; 404s when the
    named local model is absent. A :class:`tiering.DemoteError` (in-use / no space
    / name collision / integrity) is surfaced verbatim as a 409. The response
    carries only the new tier and the bytes reclaimed — never an on-disk path (AC4).
    """

    if ctx.external_cfg is None:
        return 409, {"error": "no external tier is configured — nowhere to demote to"}
    if not check_availability(ctx.external_cfg).is_mounted:
        return 409, {"error": f"external repo offline — plug in the SSD before demoting {name}"}

    source = _find_local(ctx, name, store_format)
    if source is None:
        return 404, {"error": f"{name}: not found on the local tier"}

    try:
        result = tiering.demote_model(source, ctx.external_cfg, ctx.configs, ctx.state_dir)
    except tiering.DemoteError as exc:
        return 409, {"error": str(exc)}
    return 200, {
        "demoted": {
            "name": name,
            "tier": "external",
            "bytes_reclaimed": result.bytes_reclaimed,
            "verified": result.verified,
            "reused_existing": result.reused_existing,
        }
    }


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
        return _json(
            *inferencer_panel.start_action(
                name, ctx.configs, ctx.state_dir, confirm=confirm, force=force
            )
        )
    if method == "POST" and route == "/api/stop":
        return _json(*inferencer_panel.stop_action(name, ctx.configs, ctx.state_dir))
    if method == "GET" and route == "/api/data":
        return _json(*results_panel.data_action(_resolve_result_paths(ctx)))
    if method == "GET" and route == "/api/catalog":
        return _json(*catalog_action(ctx))
    if method == "GET" and route == "/api/chat/catalog":
        return _json(*chat_catalog_action(ctx))
    if method == "GET" and route == "/api/inventory":
        return _json(*inventory_action(ctx))
    if method == "GET" and route == "/api/tiers":
        return _json(*tier_inventory_action(ctx))
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
    host: str = "127.0.0.1",
    port: int = 8765,
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
    results_dir: str | Path = "results",
    cache_dir: str | Path = ".cache/benchmarks",
    suites_path: str | Path = "configs/suites.yaml",
    host: str = "127.0.0.1",
    port: int = 8765,
    progress: Callable[[str], None] | None = None,
) -> None:
    """Load the inferencer + model registries and serve the unified dashboard.

    Serves until interrupted. The launch orchestrator is wired here so the Run
    section's form (story 09.2-001) posts to the same single-run authority Epic-08's
    exclusive start lives behind (story 09.3-001). The model registry also powers the
    Chat section (story 09.7-001); it is loaded best-effort so a missing/invalid
    ``models.yaml`` disables chat (and leaves the launcher with no models) without
    taking the rest of the dashboard down.
    """

    configs = load_inferencers(config_path)
    models = _load_models_safe(models_path, progress)
    external_cfg, autotier_cfg = _load_tier_configs_safe(config_path, progress)
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
    )
    server = make_server(ctx, host=host, port=port)
    if progress is not None:
        progress(f"unified dashboard on http://{host}:{port} (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


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
<style>
  :root { color-scheme: light dark; }
  body { font-family: -apple-system, system-ui, sans-serif; margin: 0; }
  header { padding: 1.2rem 2rem 0; border-bottom: 1px solid #8884; }
  h1 { font-size: 1.3rem; margin: 0 0 0.8rem; }
  h2 { font-size: 1.05rem; margin-top: 1.6rem; }
  h3 { font-size: 0.95rem; margin: 0.8rem 0 0.2rem; }
  nav { display: flex; gap: 0.4rem; }
  nav button { font: inherit; padding: 0.4rem 0.9rem; cursor: pointer; border: 1px solid #8884;
    border-bottom: none; border-radius: 0.4rem 0.4rem 0 0; background: transparent; }
  nav button.active { font-weight: 600; background: #8881; }
  main { margin: 1.4rem 2rem 3rem; }
  table { border-collapse: collapse; width: 100%; max-width: 80rem; margin-top: 0.4rem; }
  th, td { text-align: left; padding: 0.35rem 0.6rem; border-bottom: 1px solid #8884; }
  th { font-weight: 600; }
  th[data-sort-key] { cursor: pointer; user-select: none; }
  th[data-sort-key]:hover { text-decoration: underline; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  tr.row-clickable { cursor: pointer; }
  tr.row-clickable:hover { background: #8881; }
  button.act { font: inherit; padding: 0.25rem 0.7rem; cursor: pointer; }
  button.act:disabled { opacity: 0.4; cursor: default; }
  .dot { display: inline-block; width: 0.7rem; height: 0.7rem; border-radius: 50%; }
  .up { background: #2e9e44; } .down { background: #999; }
  #inf-err { color: #c0392b; min-height: 1.2rem; }
  #modal { position: fixed; inset: 0; background: #0008; display: none;
           align-items: center; justify-content: center; }
  #modal.show { display: flex; }
  .card { background: Canvas; color: CanvasText; padding: 1.2rem 1.4rem; border-radius: 0.6rem;
          max-width: 26rem; box-shadow: 0 0.5rem 2rem #0006; }
  .card ul { margin: 0.5rem 0 1rem; }
  #leaderboard-filter { margin-top: 0.6rem; padding: 0.3rem 0.5rem; width: 22rem; max-width: 100%; }
  #drilldown { margin-top: 0.8rem; }
  #drilldown table { max-width: 100%; }
  #drilldown .preview { font-family: ui-monospace, monospace; font-size: 0.8rem;
    white-space: pre-wrap; max-width: 28rem; }
  .pass { color: #1e8449; } .fail { color: #c0392b; }
  #warnings { color: #c0392b; }
  #warnings li { font-family: ui-monospace, monospace; font-size: 0.85rem; }
  .empty { color: #888; }
  .note { color: #888; max-width: 44rem; line-height: 1.5; }
  .err { color: #c0392b; min-height: 1.2rem; }
  .warn { color: #b9770e; min-height: 1.2rem; }
  .run-grid { display: flex; gap: 2rem; flex-wrap: wrap; }
  .run-grid select { font: inherit; min-width: 18rem; padding: 0.3rem 0.4rem; }
  #run-suites { border: 1px solid #8884; border-radius: 0.4rem; padding: 0.6rem 0.9rem;
    max-width: 40rem; display: flex; flex-direction: column; gap: 0.3rem; }
  #run-suites label { display: flex; gap: 0.5rem; align-items: baseline; }
  #run-suites label.disabled { color: #888; }
  #run-suites .reason { color: #888; font-size: 0.85rem; }
  .run-actions { margin-top: 1rem; }
  #run-msg.ok { color: #1e8449; }
  #run-msg.bad { color: #c0392b; }
  .chat-grid { display: flex; gap: 2rem; flex-wrap: wrap; align-items: flex-end; }
  .chat-grid select, .chat-grid input { font: inherit; padding: 0.3rem 0.4rem; }
  .chat-grid select { min-width: 16rem; }
  .chat-grid input[type="number"] { width: 6rem; }
  #chat-system { font: inherit; width: 100%; max-width: 46rem; box-sizing: border-box;
    padding: 0.4rem 0.5rem; margin-top: 0.4rem; }
  #chat-messages { border: 1px solid #8884; border-radius: 0.4rem; padding: 0.8rem;
    max-width: 46rem; height: 24rem; overflow-y: auto; margin-top: 0.8rem;
    display: flex; flex-direction: column; gap: 0.6rem; }
  .chat-msg { padding: 0.5rem 0.7rem; border-radius: 0.5rem; max-width: 90%;
    white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.4; }
  .chat-msg.user { align-self: flex-end; background: #2e9e4422; }
  .chat-msg.assistant { align-self: flex-start; background: #8881; }
  .chat-msg .role { font-size: 0.75rem; color: #888; display: block; margin-bottom: 0.15rem; }
  .chat-metrics { margin-top: 0.45rem; padding-top: 0.4rem; border-top: 1px solid #8883;
    color: #666; font-family: ui-monospace, monospace; font-size: 0.78rem;
    white-space: normal; display: grid; grid-template-columns: auto auto; column-gap: 1rem; row-gap: 0.15rem; }
  .chat-metrics span:nth-child(odd) { color: #888; }
  .chat-compose { display: flex; gap: 0.5rem; margin-top: 0.8rem; max-width: 46rem; }
  #chat-input { font: inherit; flex: 1; padding: 0.4rem 0.5rem; resize: vertical; min-height: 2.4rem; }
  #chat-err { color: #c0392b; min-height: 1.2rem; }
</style>
</head>
<body>
<header>
  <h1>local-code-bench</h1>
  <nav id="nav">
    <button data-section="inferencers" class="active">Inferencers</button>
    <button data-section="results">Results</button>
    <button data-section="inventory">Inventory</button>
    <button data-section="run">Run</button>
    <button data-section="chat">Chat</button>
  </nav>
</header>
<main>

<section id="section-inferencers" class="section">
  <h2>Inferencer Control</h2>
  <p id="inf-err"></p>
  <table>
    <thead>
      <tr><th></th><th>Engine</th><th>Lifecycle</th><th>Port</th><th>PID</th><th>State</th><th></th></tr>
    </thead>
    <tbody id="rows"></tbody>
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
        <th>Run</th><th>Timestamp</th><th>Models / Agents</th><th>Suites</th>
        <th class="num">Tasks</th><th class="num">pass@1</th><th class="num">Median Speed</th>
      </tr>
    </thead>
    <tbody id="run-history"></tbody>
  </table>

  <h3>Sweep</h3>
  <table>
    <thead>
      <tr>
        <th>Model</th><th class="num">Context Tokens</th>
        <th class="num">TTFT</th><th class="num">Prefill tok/s</th>
      </tr>
    </thead>
    <tbody id="sweep"></tbody>
  </table>

  <h3 id="warnings-title" hidden>Data-quality warnings</h3>
  <ul id="warnings"></ul>
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
    <button class="act" id="tier-apply" disabled>Apply eviction plan</button>
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
  <p id="run-err" class="fail"></p>
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
  <p id="chat-err"></p>
</section>

</main>

<div id="modal">
  <div class="card">
    <p id="modal-msg"></p>
    <ul id="modal-list"></ul>
    <button class="act" id="modal-confirm">Stop them &amp; start</button>
    <button class="act" id="modal-cancel">Cancel</button>
  </div>
</div>

<script>
// Client-side section navigation: show one section, no reload, no build step.
(function () {
  const buttons = document.querySelectorAll("#nav button");
  const sections = {
    inferencers: document.getElementById("section-inferencers"),
    results: document.getElementById("section-results"),
    inventory: document.getElementById("section-inventory"),
    run: document.getElementById("section-run"),
    chat: document.getElementById("section-chat"),
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
      const tr = document.createElement("tr");
      const dot = it.running ? "up" : "down";
      const action = it.lifecycle === "app"
        ? "<span>manage in app</span>"
        : (it.running
            ? `<button class="act" data-stop="${it.name}">Stop</button>`
            : `<button class="act" data-start="${it.name}">Start</button>`);
      tr.innerHTML =
        `<td><span class="dot ${dot}"></span></td>` +
        `<td>${it.name}</td><td>${it.lifecycle}</td><td>${it.port}</td>` +
        `<td>${it.pid ?? ""}</td><td>${it.detail}</td><td>${action}</td>`;
      rows.appendChild(tr);
    }
  }

  async function post(url) {
    const res = await fetch(url, { method: "POST" });
    let body = {};
    try { body = await res.json(); } catch (e) { body = {}; }
    return { status: res.status, body };
  }

  async function startEngine(name, confirm, afterStart) {
    setError("");
    const url = "/api/start?name=" + encodeURIComponent(name) + (confirm ? "&confirm=1" : "");
    const { status, body } = await post(url);
    if (status === 409 && body.needs_confirmation) { openModal(name, body, afterStart); return; }
    if (status >= 400) setError(body.message || body.error || ("start failed (" + status + ")"));
    refresh();
    if (status < 400 && afterStart) afterStart();
  }

  function openModal(name, body, afterStart) {
    pending = { name, afterStart };
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
    if (item) startEngine(item.name, true, item.afterStart);
  };
  document.getElementById("modal-cancel").onclick = closeModal;

  window.startInferencer = function (name, afterStart) {
    return startEngine(name, false, afterStart);
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
        kind: "endpoint", name: m.model, run_mode: "endpoint", suite: m.suite,
        pass_rate: m.pass_rate, median_speed_seconds: m.median_latency_seconds,
        median_prefill_tokens_per_second: m.median_prefill_tokens_per_second,
        median_decode_tokens_per_second: m.median_decode_tokens_per_second,
        mean_cost_usd: m.mean_cost_usd, failure_count: m.failure_count, tasks: m.tasks || [],
      });
    }
    for (const a of DATA.agent_runs || []) {
      rows.push({
        kind: "agent", name: a.agent, run_mode: "agent", suite: a.suite,
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
        [r.name, r.run_mode, r.suite].some((v) => (v || "").toLowerCase().includes(q)));
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
    if (!rows.length) { fillEmpty(tbody, 9, "No leaderboard rows yet."); return; }
    for (const r of rows) {
      const tr = document.createElement("tr");
      tr.className = "row-clickable";
      tr.append(
        cell(r.name), cell(r.run_mode), cell(r.suite || "-"),
        cell(pct(r.pass_rate), true), cell(num(r.median_speed_seconds), true),
        cell(num(r.median_prefill_tokens_per_second), true),
        cell(num(r.median_decode_tokens_per_second), true),
        cell(r.mean_cost_usd === null ? "-" : num(r.mean_cost_usd, 6), true),
        cell(r.failure_count, true),
      );
      tr.addEventListener("click", () => {
        OPEN = { kind: r.kind, name: r.name, suite: r.suite };
        renderDrilldown();
      });
      tbody.appendChild(tr);
    }
  }

  function findRow(open) {
    return leaderboardRows().find(
      (r) => r.kind === open.kind && r.name === open.name && r.suite === open.suite);
  }

  function renderDrilldown() {
    const host = document.getElementById("drilldown");
    host.innerHTML = "";
    if (!OPEN) return;
    const row = findRow(OPEN);
    if (!row) { OPEN = null; return; }

    const title = document.createElement("h3");
    title.textContent = "Tasks - " + row.name + (row.suite ? " (" + row.suite + ")" : "");
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
    if (!rows.length) { fillEmpty(tbody, 7, "No runs yet."); return; }
    for (const r of rows) {
      const actors = (r.models || []).concat(r.agents || []);
      const speed = r.median_latency_seconds !== null && r.median_latency_seconds !== undefined
        ? r.median_latency_seconds : r.median_wall_time_seconds;
      const tr = document.createElement("tr");
      tr.append(
        cell(r.source), cell(r.timestamp || "-"), cell(actors.join(", ") || "-"),
        cell((r.suites || []).join(", ") || "-"), cell(r.task_count, true),
        cell(pct(r.pass_rate), true), cell(num(speed), true),
      );
      tbody.appendChild(tr);
    }
  }

  function renderSweep(rows) {
    const tbody = document.getElementById("sweep");
    tbody.innerHTML = "";
    if (!rows.length) { fillEmpty(tbody, 4, "No sweep records yet."); return; }
    for (const r of rows) {
      const tr = document.createElement("tr");
      tr.append(
        cell(r.model), cell(r.context_tokens, true),
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
    window.startInferencer(it.name, load);
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
    if (onExternal && !onLocal) {
      td.innerHTML = offline
        ? '<span class="empty">SSD offline</span>'
        : `<button class="act" data-promote ${attrs}>Promote</button>`;
    } else if (onLocal) {
      td.innerHTML = offline
        ? '<span class="empty">demote disabled — SSD offline</span>'
        : `<button class="act" data-demote ${attrs}>Demote</button>`;
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
    status.textContent = verb === "promote" ? "Promoting…" : "Demoting…";
    try {
      const url = "/api/" + verb + "?name=" + encodeURIComponent(name) +
        "&format=" + encodeURIComponent(format);
      const { status: code, body } = await post(url);
      if (code >= 400) err.textContent = body.error || (verb + " failed (" + code + ")");
    } catch (e) {
      err.textContent = verb + " failed: " + e;
    } finally {
      busy = false;
      refresh();  // AC2: the panel refreshes the model's tier on completion
    }
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

  refresh();
})();
</script>
</body>
</html>
"""
