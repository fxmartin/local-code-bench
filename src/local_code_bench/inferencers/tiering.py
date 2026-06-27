"""Promote a model from the external tier to local disk (Epic-12, Story 12.3-001).

Epic-12 keeps models on two tiers: the fast internal disk (``local``) and an
attached SSD (``external``, see :mod:`.external`). *Promoting* copies a model from
the external SSD into the correct per-format local store so it can be served from
fast storage — without ever risking a corrupt or half-copied model.

The operation is **copy → verify → atomically publish**, never a move:

* The external source is only ever *read* — promote never deletes it, so a
  successful promote leaves the model present on both tiers (a redundancy the disk
  report can later flag). There is therefore no path to data loss on the source.
* The copy lands in a hidden staging path beside the destination and is verified
  (byte size **and** a content hash) against the source before it is published
  with a single atomic :func:`os.replace`. A reader of the local store never sees
  a partial model: the destination either does not exist or is the complete,
  verified copy.
* Any failure mid-copy — an I/O error or an integrity mismatch — cleans up the
  staging path and raises, leaving both tiers exactly as they were.

Promote refuses up front, moving no bytes, when the external tier is offline, when
an inferencer that could serve the model is currently running (reusing the Epic-08
active-engine state), when the model is already present locally, or when local
free space is insufficient (the error suggests how much to free).

Every side effect is injectable (``free_bytes``, ``status_fn``, ``copy_fn``) so
the whole flow is testable against a temp tree with no real SSD, processes, or
disk-full condition.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from ..config import ExternalRepoConfig, InferencerConfig
from . import manager
from .external import check_availability, format_dir
from .inventory import LocalModel, Tier, expand_store_path

__all__ = [
    "DemoteError",
    "DemotePlan",
    "DemoteResult",
    "PromoteError",
    "PromotePlan",
    "PromoteResult",
    "TierResolution",
    "demote_model",
    "plan_demotion",
    "plan_promotion",
    "promote_model",
    "resolve_benchmark_target",
    "serving_blockers",
]

#: Suffix of the hidden staging path a promote (or demote) copies into before
#: publishing. Shared by both moves so a stale temp from either is cleaned alike.
_STAGING_SUFFIX = ".promote-tmp"


class PromoteError(RuntimeError):
    """Raised when a promote cannot proceed or must abort.

    Carrying this distinct type lets a caller (CLI/web) distinguish an expected,
    explained refusal — offline tier, in-use model, no space, integrity mismatch —
    from an unexpected crash, and surface the message verbatim.
    """


@dataclass(frozen=True)
class PromotePlan:
    """The resolved source/destination of a promote, before any bytes move.

    Pure to compute from the model and its destination engine, so a caller can show
    *what would happen* (a dry run) without touching the disk.
    """

    name: str
    store_format: str
    source: Path
    destination: Path
    size_bytes: int


@dataclass(frozen=True)
class PromoteResult:
    """Outcome of a completed promote: the plan plus the verified published copy."""

    plan: PromotePlan
    destination: Path
    bytes_copied: int
    verified: bool


def plan_promotion(
    source: LocalModel,
    inferencer: InferencerConfig,
    *,
    home: Path | None = None,
) -> PromotePlan:
    """Resolve where an external model would land in ``inferencer``'s local store.

    The destination mirrors the source's on-disk basename under the engine's first
    configured ``model_store`` directory. Raises :class:`PromoteError` when the
    source is not an external-tier record or the engine declares no local store —
    the two preconditions that make a promote meaningless.
    """

    if source.tier != "external":
        raise PromoteError(
            f"{source.name} is on the {source.tier} tier, not external — nothing to promote"
        )
    if not inferencer.model_store:
        raise PromoteError(
            f"inferencer {inferencer.name} declares no local model store to promote into"
        )

    source_path = Path(source.path)
    store_dir = expand_store_path(inferencer.model_store[0], home=home)
    destination = store_dir / source_path.name
    return PromotePlan(
        name=source.name,
        store_format=source.store_format,
        source=source_path,
        destination=destination,
        size_bytes=source.size_bytes,
    )


def serving_blockers(
    source: LocalModel,
    configs: Mapping[str, InferencerConfig],
    state_dir: str | Path,
    *,
    status_fn: Callable[[InferencerConfig, str | Path], manager.InferencerStatus] = manager.status,
) -> list[str]:
    """Names of running engines that could be serving ``source`` right now.

    An engine could serve the model when it stores the same on-disk format; moving
    the bytes out from under a live server risks a torn read, so a running match
    blocks the promote. Reuses the Epic-08 per-engine state via ``status_fn``
    (injected so the in-use check is testable without real processes).
    """

    blockers: list[str] = []
    for name, cfg in configs.items():
        if cfg.store_format != source.store_format:
            continue
        if status_fn(cfg, state_dir).running:
            blockers.append(name)
    return blockers


def promote_model(
    source: LocalModel,
    inferencer: InferencerConfig,
    external_cfg: ExternalRepoConfig,
    configs: Mapping[str, InferencerConfig],
    state_dir: str | Path,
    *,
    home: Path | None = None,
    free_bytes: Callable[[Path], int] = lambda path: shutil.disk_usage(path).free,
    status_fn: Callable[[InferencerConfig, str | Path], manager.InferencerStatus] = manager.status,
    copy_fn: Callable[[Path, Path], None] | None = None,
) -> PromoteResult:
    """Promote ``source`` from the external tier into ``inferencer``'s local store.

    Copies the external model into a staging path, verifies it (size + content
    hash) against the source, and only then publishes it atomically as a local
    copy. Refuses up front — moving no bytes — when the external tier is offline,
    a serving engine is running, the model already exists locally, or local free
    space is insufficient; aborts and cleans up the partial copy on any I/O error
    or integrity mismatch, always leaving the external source intact.

    Raises :class:`PromoteError` for every refusal and abort.
    """

    plan = plan_promotion(source, inferencer, home=home)

    if not check_availability(external_cfg, home=home).is_mounted:
        raise PromoteError(
            f"external tier is offline — plug in the SSD before promoting {plan.name}"
        )

    if not plan.source.exists():
        raise PromoteError(f"external source for {plan.name} is missing: {plan.source}")

    blockers = serving_blockers(source, configs, state_dir, status_fn=status_fn)
    if blockers:
        joined = ", ".join(sorted(blockers))
        raise PromoteError(
            f"{joined} is running and could be serving {plan.name} — "
            "stop it before promoting so no bytes are moved under a live engine"
        )

    if plan.destination.exists():
        raise PromoteError(
            f"{plan.name} is already present locally at {plan.destination} — nothing to promote"
        )

    free = free_bytes(_existing_ancestor(plan.destination.parent))
    if free < plan.size_bytes:
        shortfall = plan.size_bytes - free
        raise PromoteError(
            f"insufficient local free space to promote {plan.name}: need "
            f"{_human_bytes(plan.size_bytes)}, have {_human_bytes(free)} — "
            f"free at least {_human_bytes(shortfall)} first (both tiers left untouched)"
        )

    source_hash = _content_hash(plan.source)
    staging = plan.destination.with_name(plan.destination.name + _STAGING_SUFFIX)
    copy = copy_fn or _copy_path
    plan.destination.parent.mkdir(parents=True, exist_ok=True)
    _remove_path(staging)

    try:
        copy(plan.source, staging)
        copied = _path_size(staging)
        if copied != plan.size_bytes or _content_hash(staging) != source_hash:
            raise PromoteError(
                f"integrity check failed promoting {plan.name}: the local copy does "
                "not match the external source — aborting, source left intact"
            )
        os.replace(staging, plan.destination)
    except PromoteError:
        _remove_path(staging)
        raise
    except OSError as exc:
        _remove_path(staging)
        raise PromoteError(
            f"failed to promote {plan.name}: {exc} — partial copy removed, source intact"
        ) from exc

    return PromoteResult(
        plan=plan,
        destination=plan.destination,
        bytes_copied=plan.size_bytes,
        verified=True,
    )


# --- Benchmark target resolution (Story 12.5-001) --------------------------


@dataclass(frozen=True)
class TierResolution:
    """How a benchmark launch should obtain its target model across tiers.

    ``path`` is the on-disk location the inferencer should be pointed at; ``tier``
    is where the model is served from after resolution. ``promoted`` is ``True``
    when an external-only model was copied local first (the default, for clean
    speed metrics); ``served_from_external`` is ``True`` when it is served in place
    from the external SSD (a speed caveat — its load time includes the external
    read). ``result`` carries the underlying :class:`PromoteResult` when a
    promotion occurred, else ``None``.
    """

    name: str
    path: str
    tier: Tier
    promoted: bool
    served_from_external: bool
    result: PromoteResult | None = None

    def metadata(self) -> dict[str, object]:
        """Tier provenance for the run-metadata header (see :func:`metadata.run_metadata`)."""

        return {
            "served_tier": self.tier,
            "promoted": self.promoted,
            "served_from_external": self.served_from_external,
        }


def resolve_benchmark_target(
    target: str,
    local_models: Iterable[LocalModel],
    external_models: Iterable[LocalModel],
    inferencer: InferencerConfig,
    external_cfg: ExternalRepoConfig,
    configs: Mapping[str, InferencerConfig],
    state_dir: str | Path,
    *,
    serve_from_external: bool = False,
    home: Path | None = None,
    free_bytes: Callable[[Path], int] = lambda path: shutil.disk_usage(path).free,
    status_fn: Callable[[InferencerConfig, str | Path], manager.InferencerStatus] = manager.status,
    copy_fn: Callable[[Path, Path], None] | None = None,
) -> TierResolution:
    """Decide how a benchmark should obtain its ``target`` model across tiers.

    Local is always preferred: when ``target`` has a local copy it is served as-is,
    with no promotion or external serving — and the external tier is never even
    consulted, so this works while the SSD is offline. Otherwise the model must be
    external-only:

    * **offline** — the external SSD holding the only copy is not mounted: fails
      fast with a clear error *before any model is loaded*, regardless of
      ``serve_from_external``.
    * **serve-from-external** (``serve_from_external=True``) — the inferencer is
      pointed at the external path with no copy; the result flags
      ``served_from_external`` so the run records that its speed includes external
      load and is not silently compared against local-loaded runs.
    * **default** — the model is promoted into ``inferencer``'s local store first
      (reusing :func:`promote_model`, with its in-use / free-space guards), and the
      result flags ``promoted``.

    Raises :class:`PromoteError` when ``target`` is on neither tier, when the only
    copy is on an offline external tier, or for any guard :func:`promote_model`
    enforces.
    """

    local = _first_named(local_models, target)
    if local is not None:
        return TierResolution(
            name=target,
            path=local.path,
            tier="local",
            promoted=False,
            served_from_external=False,
        )

    external = _first_named(external_models, target)
    if external is None:
        raise PromoteError(f"{target}: model not found on the local or external tier")

    status = check_availability(external_cfg, home=home)
    if not status.is_mounted:
        raise PromoteError(
            f"{target}: external repo offline at {status.root} — "
            "plug in the SSD or choose a local model"
        )

    if serve_from_external:
        return TierResolution(
            name=target,
            path=external.path,
            tier="external",
            promoted=False,
            served_from_external=True,
        )

    result = promote_model(
        external,
        inferencer,
        external_cfg,
        configs,
        state_dir,
        home=home,
        free_bytes=free_bytes,
        status_fn=status_fn,
        copy_fn=copy_fn,
    )
    return TierResolution(
        name=target,
        path=str(result.destination),
        tier="local",
        promoted=True,
        served_from_external=False,
        result=result,
    )


def _first_named(models: Iterable[LocalModel], target: str) -> LocalModel | None:
    """Return the first model whose ``name`` matches ``target``, else ``None``."""

    for model in models:
        if model.name == target:
            return model
    return None


# --- Demote (local -> external), Story 12.3-002 ----------------------------
#
# The mirror of promote: source and destination swapped (local -> external) and,
# crucially, the local copy *is* removed — but only ever after a verified external
# copy provably exists, so an interrupted demote has no path to data loss. When a
# matching copy already lives on external it is reused (no re-copy) and the local
# bytes are reclaimed immediately. Refuses up front, moving nothing, when the
# external tier is offline, a serving engine is running, external free space is
# insufficient, or a *different* model already occupies the destination name.


class DemoteError(RuntimeError):
    """Raised when a demote cannot proceed or must abort.

    The mirror of :class:`PromoteError`: a distinct type so a caller can surface an
    expected, explained refusal — offline tier, in-use model, no space, name
    collision, integrity mismatch — verbatim, distinct from an unexpected crash.
    """


@dataclass(frozen=True)
class DemotePlan:
    """The resolved source/destination of a demote, before any bytes move.

    Pure to compute from the model and the external config, so a caller can show
    *what would happen* (a dry run) without touching the disk.
    """

    name: str
    store_format: str
    source: Path
    destination: Path
    size_bytes: int


@dataclass(frozen=True)
class DemoteResult:
    """Outcome of a completed demote.

    ``reused_existing`` records whether a matching external copy already existed
    and was reused (no re-copy) before the local bytes were reclaimed;
    ``bytes_reclaimed`` is the local space freed.
    """

    plan: DemotePlan
    destination: Path
    bytes_reclaimed: int
    verified: bool
    reused_existing: bool


def plan_demotion(
    source: LocalModel,
    external_cfg: ExternalRepoConfig,
    *,
    home: Path | None = None,
) -> DemotePlan:
    """Resolve where a local model would land in the external per-format store.

    The destination mirrors the source's on-disk basename under the external
    tier's per-format directory. Raises :class:`DemoteError` when the source is
    not a local-tier record — the precondition that makes a demote meaningless.
    """

    if source.tier != "local":
        raise DemoteError(
            f"{source.name} is on the {source.tier} tier, not local — nothing to demote"
        )

    source_path = Path(source.path)
    store_dir = format_dir(external_cfg, source.store_format, home=home)
    destination = store_dir / source_path.name
    return DemotePlan(
        name=source.name,
        store_format=source.store_format,
        source=source_path,
        destination=destination,
        size_bytes=source.size_bytes,
    )


def demote_model(
    source: LocalModel,
    external_cfg: ExternalRepoConfig,
    configs: Mapping[str, InferencerConfig],
    state_dir: str | Path,
    *,
    home: Path | None = None,
    free_bytes: Callable[[Path], int] = lambda path: shutil.disk_usage(path).free,
    status_fn: Callable[[InferencerConfig, str | Path], manager.InferencerStatus] = manager.status,
    copy_fn: Callable[[Path, Path], None] | None = None,
) -> DemoteResult:
    """Demote ``source`` from local disk out to the external tier.

    Copies the local model into a staging path on external, verifies it (size +
    content hash) against the source, publishes it atomically, and only then
    removes the local copy — reclaiming the internal disk space. When a matching
    external copy already exists it is reused (no re-copy) and the local bytes are
    reclaimed immediately. Refuses up front — moving no bytes — when the external
    tier is offline, the source is missing, a serving engine is running, a
    *different* model already occupies the destination name, or external free
    space is insufficient; aborts and cleans up the partial copy on any I/O error
    or integrity mismatch, always leaving the local source intact.

    The local copy is removed only after a verified external copy provably exists,
    so an interrupted demote has no path to data loss.

    Raises :class:`DemoteError` for every refusal and abort.
    """

    plan = plan_demotion(source, external_cfg, home=home)

    if not check_availability(external_cfg, home=home).is_mounted:
        raise DemoteError(
            f"external tier is offline — plug in the SSD before demoting {plan.name}"
        )

    if not plan.source.exists():
        raise DemoteError(f"local source for {plan.name} is missing: {plan.source}")

    blockers = serving_blockers(source, configs, state_dir, status_fn=status_fn)
    if blockers:
        joined = ", ".join(sorted(blockers))
        raise DemoteError(
            f"{joined} is running and could be serving {plan.name} — "
            "stop it before demoting so no bytes are moved under a live engine"
        )

    source_hash = _content_hash(plan.source)

    # Reuse a redundant external copy when it already matches the source: skip the
    # re-copy and reclaim the local bytes immediately. A same-named external copy
    # that *differs* is refused rather than clobbered, so neither tier is harmed.
    if plan.destination.exists():
        if _path_size(plan.destination) == plan.size_bytes and (
            _content_hash(plan.destination) == source_hash
        ):
            _reclaim_source(plan)
            return DemoteResult(
                plan=plan,
                destination=plan.destination,
                bytes_reclaimed=plan.size_bytes,
                verified=True,
                reused_existing=True,
            )
        raise DemoteError(
            f"a different model already occupies {plan.destination} on external — "
            f"it differs from local {plan.name}; refusing to clobber it (local kept)"
        )

    free = free_bytes(_existing_ancestor(plan.destination.parent))
    if free < plan.size_bytes:
        shortfall = plan.size_bytes - free
        raise DemoteError(
            f"insufficient external free space to demote {plan.name}: need "
            f"{_human_bytes(plan.size_bytes)}, have {_human_bytes(free)} — "
            f"free at least {_human_bytes(shortfall)} first (local copy left untouched)"
        )

    staging = plan.destination.with_name(plan.destination.name + _STAGING_SUFFIX)
    copy = copy_fn or _copy_path
    plan.destination.parent.mkdir(parents=True, exist_ok=True)
    _remove_path(staging)

    try:
        copy(plan.source, staging)
        copied = _path_size(staging)
        if copied != plan.size_bytes or _content_hash(staging) != source_hash:
            raise DemoteError(
                f"integrity check failed demoting {plan.name}: the external copy does "
                "not match the local source — aborting, local source left intact"
            )
        os.replace(staging, plan.destination)
    except DemoteError:
        _remove_path(staging)
        raise
    except OSError as exc:
        _remove_path(staging)
        raise DemoteError(
            f"failed to demote {plan.name}: {exc} — partial copy removed, local source intact"
        ) from exc

    # Verified external copy now exists: safe to reclaim the local bytes.
    _reclaim_source(plan)

    return DemoteResult(
        plan=plan,
        destination=plan.destination,
        bytes_reclaimed=plan.size_bytes,
        verified=True,
        reused_existing=False,
    )


def _reclaim_source(plan: DemotePlan) -> None:
    """Remove the local source after a verified external copy exists.

    Called only once the destination provably holds a byte-faithful copy, so the
    deletion is the safe direction. ``_remove_path`` swallows I/O errors, so we
    confirm the source is actually gone and raise :class:`DemoteError` when it is
    not — refusing to report reclaimed space that was never freed. The external
    copy stands either way, so this is an honest partial success, not data loss.
    """

    _remove_path(plan.source)
    if plan.source.exists():
        raise DemoteError(
            f"demoted {plan.name} to {plan.destination} but could not reclaim the local "
            f"copy at {plan.source} — the external copy is safe; remove the local one by hand"
        )


# --- Filesystem helpers (pure given their inputs) --------------------------


def _copy_path(source: Path, destination: Path) -> None:
    """Copy a file or directory tree, preserving metadata."""

    if source.is_dir():
        shutil.copytree(source, destination)
    else:
        shutil.copy2(source, destination)


def _remove_path(path: Path) -> None:
    """Delete a file or directory tree if present; never raise."""

    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    except OSError:
        pass


def _existing_ancestor(path: Path) -> Path:
    """Nearest existing directory at or above ``path`` (for a free-space probe)."""

    current = path
    while not current.exists():
        parent = current.parent
        if parent == current:
            break
        current = parent
    return current


def _path_size(path: Path) -> int:
    """Total on-disk size of a file or directory tree (symlinks not followed)."""

    if path.is_file():
        return path.stat().st_size
    total = 0
    for child in path.rglob("*"):
        if child.is_file() and not child.is_symlink():
            total += child.stat().st_size
    return total


def _content_hash(path: Path) -> str:
    """Order-stable SHA-256 over a file's bytes or a tree's relative path + bytes.

    For a directory the digest folds in each file's path-relative-to-root and its
    contents in sorted order, so a byte-faithful copy hashes identically while a
    missing, extra, or altered file changes the digest.
    """

    digest = hashlib.sha256()
    if path.is_file():
        _absorb_file(digest, path)
        return digest.hexdigest()
    for child in sorted(path.rglob("*")):
        if child.is_file() and not child.is_symlink():
            digest.update(str(child.relative_to(path)).encode("utf-8"))
            digest.update(b"\0")
            _absorb_file(digest, child)
    return digest.hexdigest()


def _absorb_file(digest: "hashlib._Hash", path: Path) -> None:
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)


_UNITS = ("B", "KiB", "MiB", "GiB", "TiB")


def _human_bytes(count: int) -> str:
    """Render a byte count as a compact human-readable string (e.g. ``1.5 GiB``)."""

    size = float(count)
    for unit in _UNITS:
        if size < 1024 or unit == _UNITS[-1]:
            precision = 0 if unit == "B" else 1
            return f"{size:.{precision}f} {unit}"
        size /= 1024
    return f"{count} B"
