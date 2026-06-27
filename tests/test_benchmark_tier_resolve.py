"""Epic-12 Story 12.5-001 — serve directly from external, auto-promote-before-benchmark.

Drives :func:`local_code_bench.inferencers.tiering.resolve_benchmark_target`, the
pure tiering resolver the benchmark launch path calls to obtain its target model
across tiers. Every side effect (external availability, running-engine state, free
space, the copy itself) is exercised against a temp tree with injected seams, so
each launch decision — local-as-is, auto-promote, serve-from-external, and every
fail-fast refusal — is covered without a real SSD, live processes, or a disk-full
condition.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from local_code_bench.config import (
    DEFAULT_VOLUME_MARKER,
    ExternalRepoConfig,
    InferencerConfig,
    StoreFormat,
)
from local_code_bench.inferencers.inventory import LocalModel
from local_code_bench.inferencers.manager import InferencerStatus
from local_code_bench.inferencers.tiering import (
    PromoteError,
    PromoteResult,
    TierResolution,
    resolve_benchmark_target,
)


# --- Helpers ---------------------------------------------------------------


def _inferencer(name: str, store: Path, store_format: StoreFormat = "gguf") -> InferencerConfig:
    return InferencerConfig(
        name=name,
        lifecycle="server",
        detect_kind="binary",
        detect_target=name,
        port=1234,
        health_url="http://127.0.0.1:{port}/health",
        model_store=(str(store),),
        store_format=store_format,
    )


def _external_cfg(root: Path) -> ExternalRepoConfig:
    return ExternalRepoConfig(root=str(root))


def _mount(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / DEFAULT_VOLUME_MARKER).write_text("marker", encoding="utf-8")


def _write_gguf(base: Path, stem: str, payload: bytes = b"weights" * 1000) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{stem}.gguf"
    path.write_bytes(payload)
    return path


def _model(path: Path, *, name: str, tier: str, store_format: StoreFormat = "gguf") -> LocalModel:
    return LocalModel(
        inferencer=f"{tier}-scan",
        store_format=store_format,
        name=name,
        path=str(path),
        size_bytes=path.stat().st_size if path.is_file() else 0,
        quant=None,
        provider=None,
        identity=str(path),
        tier=tier,  # type: ignore[arg-type]
    )


def _not_running(cfg: InferencerConfig, state_dir) -> InferencerStatus:
    return InferencerStatus(cfg.name, True, cfg.lifecycle, False, None, cfg.port, False, "down")


def _running(cfg: InferencerConfig, state_dir) -> InferencerStatus:
    return InferencerStatus(cfg.name, True, cfg.lifecycle, True, 4242, cfg.port, True, "up")


def _plenty(_path: Path) -> int:
    return 1 << 40  # 1 TiB free


def _none_free(_path: Path) -> int:
    return 0


# --- local always preferred ------------------------------------------------


def test_local_model_served_as_is(tmp_path: Path) -> None:
    local_path = _write_gguf(tmp_path / "local" / "gguf", "qwen")
    cfg = _inferencer("llama", tmp_path / "local" / "gguf")

    resolution = resolve_benchmark_target(
        "qwen",
        [_model(local_path, name="qwen", tier="local")],
        [],
        cfg,
        _external_cfg(tmp_path / "ext"),
        {"llama": cfg},
        tmp_path,
        free_bytes=_plenty,
        status_fn=_not_running,
    )

    assert resolution == TierResolution(
        name="qwen",
        path=str(local_path),
        tier="local",
        promoted=False,
        served_from_external=False,
    )
    assert resolution.result is None


def test_local_preferred_even_when_also_external_and_offline(tmp_path: Path) -> None:
    # External tier is *offline* (never mounted) — local must still resolve, proving
    # the external tier is never consulted when a local copy exists.
    local_path = _write_gguf(tmp_path / "local" / "gguf", "qwen")
    ext_path = _write_gguf(tmp_path / "elsewhere", "qwen")
    cfg = _inferencer("llama", tmp_path / "local" / "gguf")

    resolution = resolve_benchmark_target(
        "qwen",
        [_model(local_path, name="qwen", tier="local")],
        [_model(ext_path, name="qwen", tier="external")],
        cfg,
        _external_cfg(tmp_path / "never-mounted"),
        {"llama": cfg},
        tmp_path,
    )

    assert resolution.tier == "local"
    assert resolution.promoted is False
    assert resolution.served_from_external is False
    assert resolution.path == str(local_path)


def test_target_found_past_non_matching_entries(tmp_path: Path) -> None:
    # A decoy precedes the target in each list, so the name scan must skip past a
    # non-matching entry on both tiers before resolving.
    ext_root = tmp_path / "ext"
    _mount(ext_root)
    decoy_local = _write_gguf(tmp_path / "local" / "gguf", "other")
    ext_path = _write_gguf(ext_root / "gguf", "qwen")
    cfg = _inferencer("llama", tmp_path / "local" / "gguf")

    resolution = resolve_benchmark_target(
        "qwen",
        [_model(decoy_local, name="other", tier="local")],
        [
            _model(_write_gguf(ext_root / "gguf", "decoy"), name="decoy", tier="external"),
            _model(ext_path, name="qwen", tier="external"),
        ],
        cfg,
        _external_cfg(ext_root),
        {"llama": cfg},
        tmp_path,
        serve_from_external=True,
    )

    assert resolution.tier == "external"
    assert resolution.path == str(ext_path)


# --- not found on either tier ----------------------------------------------


def test_unknown_target_raises(tmp_path: Path) -> None:
    cfg = _inferencer("llama", tmp_path / "local")
    with pytest.raises(PromoteError, match="not found on the local or external tier"):
        resolve_benchmark_target(
            "ghost",
            [],
            [],
            cfg,
            _external_cfg(tmp_path / "ext"),
            {"llama": cfg},
            tmp_path,
        )


# --- external-only, offline: fail fast regardless of the serve flag ---------


@pytest.mark.parametrize("serve_from_external", [False, True])
def test_external_only_offline_fails_fast(tmp_path: Path, serve_from_external: bool) -> None:
    ext_path = _write_gguf(tmp_path / "ext" / "gguf", "qwen")  # bytes exist, but tier unmounted
    cfg = _inferencer("llama", tmp_path / "local" / "gguf")

    with pytest.raises(
        PromoteError, match="external repo offline.*plug in the SSD or choose a local model"
    ):
        resolve_benchmark_target(
            "qwen",
            [],
            [_model(ext_path, name="qwen", tier="external")],
            cfg,
            _external_cfg(tmp_path / "ext"),  # no volume marker -> offline
            {"llama": cfg},
            tmp_path,
            serve_from_external=serve_from_external,
        )


# --- external-only, mounted, serve-from-external (opt-in) -------------------


def test_serve_from_external_points_at_external_path_without_copying(tmp_path: Path) -> None:
    ext_root = tmp_path / "ext"
    _mount(ext_root)
    ext_path = _write_gguf(ext_root / "gguf", "qwen")
    local_store = tmp_path / "local" / "gguf"
    cfg = _inferencer("llama", local_store)

    resolution = resolve_benchmark_target(
        "qwen",
        [],
        [_model(ext_path, name="qwen", tier="external")],
        cfg,
        _external_cfg(ext_root),
        {"llama": cfg},
        tmp_path,
        serve_from_external=True,
    )

    assert resolution == TierResolution(
        name="qwen",
        path=str(ext_path),
        tier="external",
        promoted=False,
        served_from_external=True,
    )
    # No copy was made into the local store.
    assert not (local_store / "qwen.gguf").exists()


# --- external-only, mounted, default: auto-promote first -------------------


def test_default_promotes_external_to_local(tmp_path: Path) -> None:
    ext_root = tmp_path / "ext"
    _mount(ext_root)
    ext_path = _write_gguf(ext_root / "gguf", "qwen")
    local_store = tmp_path / "local" / "gguf"
    cfg = _inferencer("llama", local_store)

    resolution = resolve_benchmark_target(
        "qwen",
        [],
        [_model(ext_path, name="qwen", tier="external")],
        cfg,
        _external_cfg(ext_root),
        {"llama": cfg},
        tmp_path,
        free_bytes=_plenty,
        status_fn=_not_running,
    )

    dest = local_store / "qwen.gguf"
    assert resolution.tier == "local"
    assert resolution.promoted is True
    assert resolution.served_from_external is False
    assert resolution.path == str(dest)
    assert isinstance(resolution.result, PromoteResult)
    assert resolution.result.verified is True
    # The model was actually copied local and the external source is untouched.
    assert dest.read_bytes() == ext_path.read_bytes()
    assert ext_path.exists()


# --- promote guards propagate through the resolver -------------------------


def test_default_promote_blocked_by_running_engine(tmp_path: Path) -> None:
    ext_root = tmp_path / "ext"
    _mount(ext_root)
    ext_path = _write_gguf(ext_root / "gguf", "qwen")
    cfg = _inferencer("llama", tmp_path / "local" / "gguf")

    with pytest.raises(PromoteError, match="is running and could be serving"):
        resolve_benchmark_target(
            "qwen",
            [],
            [_model(ext_path, name="qwen", tier="external")],
            cfg,
            _external_cfg(ext_root),
            {"llama": cfg},
            tmp_path,
            free_bytes=_plenty,
            status_fn=_running,
        )


def test_default_promote_blocked_by_insufficient_space(tmp_path: Path) -> None:
    ext_root = tmp_path / "ext"
    _mount(ext_root)
    ext_path = _write_gguf(ext_root / "gguf", "qwen")
    cfg = _inferencer("llama", tmp_path / "local" / "gguf")

    with pytest.raises(PromoteError, match="insufficient local free space"):
        resolve_benchmark_target(
            "qwen",
            [],
            [_model(ext_path, name="qwen", tier="external")],
            cfg,
            _external_cfg(ext_root),
            {"llama": cfg},
            tmp_path,
            free_bytes=_none_free,
            status_fn=_not_running,
        )


# --- TierResolution.metadata() provenance ----------------------------------


def test_metadata_for_local_served() -> None:
    resolution = TierResolution(
        name="m", path="/x", tier="local", promoted=False, served_from_external=False
    )
    assert resolution.metadata() == {
        "served_tier": "local",
        "promoted": False,
        "served_from_external": False,
    }


def test_metadata_for_promoted() -> None:
    resolution = TierResolution(
        name="m", path="/x", tier="local", promoted=True, served_from_external=False
    )
    assert resolution.metadata() == {
        "served_tier": "local",
        "promoted": True,
        "served_from_external": False,
    }


def test_metadata_for_served_from_external() -> None:
    resolution = TierResolution(
        name="m", path="/ext/m", tier="external", promoted=False, served_from_external=True
    )
    assert resolution.metadata() == {
        "served_tier": "external",
        "promoted": False,
        "served_from_external": True,
    }
