"""Disk-budget + LRU auto-tiering policy (Epic-12, Story 12.4-001).

Epic-12 lets the local tier overflow onto an external SSD. This module keeps the
local tier *under a disk budget on its own*: it picks the least-recently-used
local models and evicts them to the external tier until the budget is met, while
never touching a **pinned** model.

The design is a **pure planner** plus a thin **apply** step:

* :func:`plan_autotier` is side-effect-free — given the local inventory, a
  :class:`DiskBudget`, the pin list, the external tier's availability, and a
  last-used signal, it returns an :class:`AutoTierPlan` describing exactly which
  models it *would* evict and how many bytes that reclaims. It moves nothing, so a
  ``--dry-run`` (the safe default for the CLI/dashboard) is just "plan and print".
  When the external tier is offline it reports the plan as :attr:`AutoTierPlan.paused`
  and selects no evictions — auto-tiering needs a destination.
* :func:`apply_plan` is the only step that touches disk: it replays the plan's
  evictions through the verified :func:`tiering.demote_model` path (copy → verify →
  remove-local, never an unsafe delete) and records the move in the last-used store.

The last-used signal is pluggable. :class:`LastUsedStore` persists recorded
benchmark/serve timestamps keyed by content identity and falls back to file mtime
when a model has no recorded use yet (the fallback the story allows). Everything is
filesystem-only and testable against a temp tree with injected seams.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from ..config import AutoTierConfig, ExternalRepoConfig, InferencerConfig, StoreFormat
from . import tiering
from .inventory import LocalModel, group_models

__all__ = [
    "DiskBudget",
    "Eviction",
    "AutoTierPlan",
    "AutoTierError",
    "LastUsedStore",
    "LAST_USED_FILENAME",
    "budget_from_config",
    "mtime_last_used",
    "plan_autotier",
    "apply_plan",
]

#: One gibibyte, matching the GiB units the disk report and human formatter use.
_BYTES_PER_GIB = 1024**3

#: Filename of the persisted last-used timestamps under the state directory.
LAST_USED_FILENAME = "model-last-used.json"


class AutoTierError(RuntimeError):
    """Raised when an auto-tiering apply cannot proceed (e.g. paused/offline)."""


@dataclass(frozen=True)
class DiskBudget:
    """A local disk budget in bytes: a max footprint and/or a min free floor.

    ``max_local_bytes`` caps the total bytes the local-tier models may occupy;
    ``min_free_bytes`` is the least free space to keep on the local volume. Either,
    both, or neither may be set; when both are set the planner reclaims enough to
    satisfy the stricter of the two.
    """

    max_local_bytes: int | None = None
    min_free_bytes: int | None = None

    @property
    def is_set(self) -> bool:
        """True when at least one budget dimension constrains the local tier."""

        return self.max_local_bytes is not None or self.min_free_bytes is not None


@dataclass(frozen=True)
class Eviction:
    """One local model the policy would evict, with the bytes it reclaims.

    ``model`` is the representative local record handed to :func:`tiering.demote_model`
    when the plan is applied; ``last_used`` is the signal value that ranked it.
    """

    name: str
    store_format: StoreFormat
    identity: str
    size_bytes: int
    last_used: float
    model: LocalModel


@dataclass(frozen=True)
class AutoTierPlan:
    """The eviction plan: what to demote, how much it reclaims, and any caveats.

    ``bytes_to_reclaim`` is how far the local tier is over budget; ``bytes_reclaimed``
    is what the selected ``evictions`` actually free. ``satisfied`` is ``True`` when
    the plan fully meets the budget. ``paused`` is ``True`` when the external tier is
    offline (no evictions are selectable). ``pinned`` lists the protected model names,
    and ``warnings`` explains any shortfall (e.g. pins blocking the budget).
    """

    evictions: tuple[Eviction, ...]
    bytes_to_reclaim: int
    bytes_reclaimed: int
    local_total_bytes: int
    satisfied: bool
    paused: bool
    pinned: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def is_empty(self) -> bool:
        """True when the plan would move nothing."""

        return not self.evictions


def budget_from_config(cfg: AutoTierConfig) -> DiskBudget:
    """Convert a GiB-denominated :class:`AutoTierConfig` budget into a byte budget."""

    return DiskBudget(
        max_local_bytes=(
            None if cfg.max_local_gb is None else int(cfg.max_local_gb * _BYTES_PER_GIB)
        ),
        min_free_bytes=(None if cfg.min_free_gb is None else int(cfg.min_free_gb * _BYTES_PER_GIB)),
    )


def mtime_last_used(model: LocalModel) -> float:
    """Fallback last-used signal: the model file/dir mtime (0.0 if unreadable)."""

    try:
        return Path(model.path).stat().st_mtime
    except OSError:
        return 0.0


def plan_autotier(
    local_models: Iterable[LocalModel],
    budget: DiskBudget,
    *,
    pins: Iterable[str] = (),
    external_available: bool = True,
    free_bytes: int | None = None,
    last_used: Callable[[LocalModel], float] = mtime_last_used,
) -> AutoTierPlan:
    """Plan which local models to evict to keep the local tier under ``budget``.

    Pure and deterministic. Local models are first collapsed to logical models
    (Epic-11 ``(store_format, identity)`` grouping) so a shared on-disk artifact is
    counted — and evicted — once. The shortfall is the stricter of the
    ``max_local_bytes`` overage and the ``min_free_bytes`` floor (the latter needs
    ``free_bytes``, the current local free space). Non-pinned models are ranked
    least-recently-used first (ties broken by name for determinism) and selected
    until the shortfall is covered. Pinned models are never selected, even if that
    leaves the budget unmet — surfaced as a warning. When ``external_available`` is
    ``False`` the plan is :attr:`AutoTierPlan.paused` with no evictions.
    """

    pin_set = set(pins)
    logical = _logical_models(local_models, pin_set, last_used)
    local_total = sum(item.size_bytes for item in logical)
    need = _shortfall(budget, local_total, free_bytes)
    pinned_names = tuple(sorted({item.name for item in logical if item.pinned}))

    if not external_available:
        warnings = (
            ("auto-tiering paused: external repo offline — plug in the SSD to evict",)
            if need > 0
            else ()
        )
        return AutoTierPlan(
            evictions=(),
            bytes_to_reclaim=need,
            bytes_reclaimed=0,
            local_total_bytes=local_total,
            satisfied=need <= 0,
            paused=True,
            pinned=pinned_names,
            warnings=warnings,
        )

    candidates = sorted(
        (item for item in logical if not item.pinned),
        key=lambda item: (item.last_used, item.name),
    )

    evictions: list[Eviction] = []
    reclaimed = 0
    for item in candidates:
        if reclaimed >= need:
            break
        evictions.append(
            Eviction(
                name=item.name,
                store_format=item.model.store_format,
                identity=item.identity,
                size_bytes=item.size_bytes,
                last_used=item.last_used,
                model=item.model,
            )
        )
        reclaimed += item.size_bytes

    satisfied = reclaimed >= need
    warnings: tuple[str, ...] = ()
    if not satisfied:
        shortfall = need - reclaimed
        if pinned_names:
            warnings = (
                f"budget not fully met: {_gib(shortfall)} still over budget; "
                f"pinned models protected from eviction: {', '.join(pinned_names)}",
            )
        else:
            warnings = (
                f"budget not fully met: {_gib(shortfall)} still over budget — "
                "no more evictable models",
            )

    return AutoTierPlan(
        evictions=tuple(evictions),
        bytes_to_reclaim=need,
        bytes_reclaimed=reclaimed,
        local_total_bytes=local_total,
        satisfied=satisfied,
        paused=False,
        pinned=pinned_names,
        warnings=warnings,
    )


def apply_plan(
    plan: AutoTierPlan,
    external_cfg: ExternalRepoConfig,
    configs: Mapping[str, InferencerConfig],
    state_dir: str | Path,
    *,
    home: Path | None = None,
    now: float,
    demote_fn: Callable[..., tiering.DemoteResult] = tiering.demote_model,
    last_used_store: LastUsedStore | None = None,
) -> list[tiering.DemoteResult]:
    """Apply ``plan`` by evicting each model through the verified demote path.

    The only step that touches disk. Refuses to run a :attr:`AutoTierPlan.paused`
    plan (the external tier is offline). Each eviction is replayed through
    ``demote_fn`` (:func:`tiering.demote_model` by default — copy → verify →
    remove-local, never an unsafe delete) and its ``now`` timestamp recorded in
    ``last_used_store`` so the LRU history reflects the move. Returns the per-model
    :class:`tiering.DemoteResult` list in eviction order.
    """

    if plan.paused:
        raise AutoTierError(
            "auto-tiering is paused: the external repo is offline — no changes made"
        )

    results: list[tiering.DemoteResult] = []
    for eviction in plan.evictions:
        result = demote_fn(eviction.model, external_cfg, configs, state_dir, home=home)
        results.append(result)
        if last_used_store is not None:
            last_used_store.record(eviction.identity, now)
    return results


# --- Last-used signal (recorded benchmark/serve events; mtime fallback) -----


class LastUsedStore:
    """Persisted last-used timestamps keyed by Epic-11 content identity.

    A small JSON cache of when each logical model was last used (a benchmark run or
    serve event). :meth:`last_used` is the LRU signal the planner consumes: it
    prefers a recorded timestamp and falls back to file mtime for a model never
    recorded. A missing or unreadable file degrades to empty rather than raising.
    """

    def __init__(self, state_dir: str | Path) -> None:
        self._path = Path(state_dir) / LAST_USED_FILENAME
        self._data = self._load()

    def _load(self) -> dict[str, float]:
        try:
            doc = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(doc, dict):
            return {}
        data: dict[str, float] = {}
        for identity, value in doc.items():
            try:
                data[str(identity)] = float(value)
            except (TypeError, ValueError):
                continue
        return data

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8")

    def get(self, identity: str) -> float | None:
        """The recorded last-used timestamp for ``identity``, or ``None``."""

        return self._data.get(identity)

    def record(self, identity: str, timestamp: float) -> None:
        """Record ``timestamp`` as the last-used time for ``identity`` and persist."""

        self._data[identity] = float(timestamp)
        self._save()

    def last_used(self, model: LocalModel) -> float:
        """LRU signal for ``model``: recorded timestamp, else file mtime."""

        recorded = self._data.get(model.identity)
        return recorded if recorded is not None else mtime_last_used(model)


# --- Internal helpers ------------------------------------------------------


@dataclass(frozen=True)
class _LogicalModel:
    """A deduplicated local logical model with the fields the planner ranks on."""

    name: str
    identity: str
    size_bytes: int
    last_used: float
    pinned: bool
    model: LocalModel


def _logical_models(
    local_models: Iterable[LocalModel],
    pin_set: set[str],
    last_used: Callable[[LocalModel], float],
) -> list[_LogicalModel]:
    """Collapse local models to one entry per shared on-disk artifact.

    A logical model is pinned when *any* of its names is pinned, and ranked by the
    most-recent use across the engines that hold it (so a model used recently by any
    engine is not treated as least-recently-used).
    """

    logical: list[_LogicalModel] = []
    for group in group_models(local_models):
        rep = group.models[0]
        names = {member.name for member in group.models}
        recency = max((last_used(member) for member in group.models), default=0.0)
        logical.append(
            _LogicalModel(
                name=rep.name,
                identity=rep.identity,
                size_bytes=rep.size_bytes,
                last_used=recency,
                pinned=bool(names & pin_set),
                model=rep,
            )
        )
    return logical


def _shortfall(budget: DiskBudget, local_total: int, free_bytes: int | None) -> int:
    """Bytes to reclaim to satisfy ``budget`` — the stricter of its dimensions."""

    need = 0
    if budget.max_local_bytes is not None:
        need = max(need, local_total - budget.max_local_bytes)
    if budget.min_free_bytes is not None and free_bytes is not None:
        need = max(need, budget.min_free_bytes - free_bytes)
    return max(need, 0)


def _gib(count: int) -> str:
    """Render a byte count as a compact GiB string (e.g. ``1.50 GiB``)."""

    return f"{count / _BYTES_PER_GIB:.2f} GiB"
