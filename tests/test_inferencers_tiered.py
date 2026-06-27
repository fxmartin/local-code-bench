"""Epic-12 Story 12.2-001 — tier-aware inventory merging local and external."""

from __future__ import annotations

import json
from pathlib import Path

from local_code_bench.config import (
    DEFAULT_VOLUME_MARKER,
    ExternalRepoConfig,
    InferencerConfig,
    StoreFormat,
)
from local_code_bench.inferencers.external import TierAvailability
from local_code_bench.inferencers.inventory import (
    LocalModel,
    StoredModel,
    normalize,
    normalize_all,
    scan_store,
)
from local_code_bench.inferencers.tiered import (
    TieredInventory,
    build_tiered_inventory,
    external_catalog_path,
    merge_tiers,
    read_external_catalog,
    scan_external_tier,
    write_external_catalog,
)


# --- Helpers ---------------------------------------------------------------


def _inferencer(name: str, store_format: StoreFormat) -> InferencerConfig:
    return InferencerConfig(
        name=name,
        lifecycle="server",
        detect_kind="binary",
        detect_target=name,
        port=1234,
        health_url="http://127.0.0.1:{port}/health",
        model_store=("~/store",),
        store_format=store_format,
    )


def _external_cfg(root: Path) -> ExternalRepoConfig:
    return ExternalRepoConfig(root=str(root))


def _mount_external(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / DEFAULT_VOLUME_MARKER).write_text("marker", encoding="utf-8")


def _write_gguf(base: Path, stem: str, size: int = 4096) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{stem}.gguf"
    path.write_bytes(b"\0" * size)
    return path


def _local(
    name: str,
    identity: str,
    *,
    store_format: StoreFormat = "gguf",
    inferencer: str = "llama.cpp",
    size_bytes: int = 4096,
    tier: str = "local",
) -> LocalModel:
    return LocalModel(
        inferencer=inferencer,
        store_format=store_format,
        name=name,
        path=f"/store/{identity}",
        size_bytes=size_bytes,
        quant=None,
        provider=None,
        identity=identity,
        tier=tier,  # type: ignore[arg-type]
    )


# --- LocalModel.tier field (backward-compatible) ---------------------------


def test_normalize_defaults_to_local_tier() -> None:
    stored = StoredModel("llama.cpp", "gguf", "Qwen-7B", "/store/qwen.gguf", 10)
    assert normalize(stored).tier == "local"


def test_normalize_can_mark_external_tier() -> None:
    stored = StoredModel("llama.cpp", "gguf", "Qwen-7B", "/ext/qwen.gguf", 10)
    assert normalize(stored, tier="external").tier == "external"
    assert normalize_all([stored], tier="external")[0].tier == "external"


# --- scan_store (the unit shared by local and external scans) --------------


def test_scan_store_reads_a_single_dir(tmp_path) -> None:
    _write_gguf(tmp_path / "gguf", "model-Q4_K_M", size=128)

    found = scan_store(tmp_path / "gguf", "gguf", "llama.cpp")

    assert [(m.name, m.inferencer, m.size_bytes) for m in found] == [
        ("model-Q4_K_M", "llama.cpp", 128)
    ]


def test_scan_store_missing_dir_yields_nothing(tmp_path) -> None:
    assert scan_store(tmp_path / "absent", "gguf", "llama.cpp") == []


# --- scan_external_tier ----------------------------------------------------


def test_scan_external_lists_models_with_serving_inferencer(tmp_path) -> None:
    root = tmp_path / "ext"
    _mount_external(root)
    _write_gguf(root / "gguf", "Qwen2.5-Coder-7B-Q4_K_M", size=2048)
    cfg = _external_cfg(root)

    models = scan_external_tier(cfg, [_inferencer("llama.cpp", "gguf")])

    assert len(models) == 1
    model = models[0]
    assert model.tier == "external"
    assert model.name == "Qwen2.5-Coder-7B-Q4_K_M"
    assert model.inferencer == "llama.cpp"
    assert model.quant == "Q4_K_M"
    assert model.size_bytes == 2048


def test_scan_external_fans_out_to_every_compatible_engine(tmp_path) -> None:
    root = tmp_path / "ext"
    _mount_external(root)
    _write_gguf(root / "gguf", "shared-model")
    cfg = _external_cfg(root)

    models = scan_external_tier(
        cfg, [_inferencer("llama.cpp", "gguf"), _inferencer("gpt4all", "gguf")]
    )

    assert {m.inferencer for m in models} == {"llama.cpp", "gpt4all"}


def test_scan_external_skips_formats_no_engine_can_serve(tmp_path) -> None:
    root = tmp_path / "ext"
    _mount_external(root)
    _write_gguf(root / "gguf", "orphan-model")  # no gguf engine configured
    cfg = _external_cfg(root)

    assert scan_external_tier(cfg, [_inferencer("mlx", "mlx")]) == []


def test_scan_external_ignores_inferencer_without_store_format(tmp_path) -> None:
    # An inferencer with no store_format (e.g. an API-only engine) contributes
    # no format bucket, so it is skipped without error.
    root = tmp_path / "ext"
    _mount_external(root)
    _write_gguf(root / "gguf", "Qwen-7B-Q4_K_M")
    cfg = _external_cfg(root)
    formatless = InferencerConfig(
        name="api-only",
        lifecycle="server",
        detect_kind="binary",
        detect_target="api-only",
        port=1234,
        health_url="http://127.0.0.1:{port}/health",
        model_store=None,
        store_format=None,
    )

    models = scan_external_tier(cfg, [formatless, _inferencer("llama.cpp", "gguf")])

    assert {m.inferencer for m in models} == {"llama.cpp"}


# --- merge_tiers -----------------------------------------------------------


def test_merge_present_in_both_collapses_to_one_row() -> None:
    # Same gguf model on both drives: different realpath, same (format, name).
    local = _local("Qwen-7B-Q4_K_M", "/local/qwen", tier="local")
    external = _local("Qwen-7B-Q4_K_M", "/ext/qwen", tier="external")

    merged = merge_tiers([local, external])

    assert len(merged) == 1
    row = merged[0]
    assert row.present_in_both is True
    assert row.tiers == ("external", "local")


def test_merge_ollama_joins_across_tiers_by_blob_sha() -> None:
    local = _local("llama:8b", "sha256:abc", store_format="ollama", tier="local")
    external = _local("llama:8b", "sha256:abc", store_format="ollama", tier="external")

    merged = merge_tiers([local, external])

    assert len(merged) == 1
    assert merged[0].present_in_both is True


def test_merge_external_only_model_is_tier_external() -> None:
    external = _local("Phi-3-Q8_0", "/ext/phi", inferencer="llama.cpp", tier="external")

    merged = merge_tiers([external])

    assert len(merged) == 1
    row = merged[0]
    assert row.tiers == ("external",)
    assert row.present_in_both is False
    assert row.inferencers == ("llama.cpp",)


def test_merge_prefers_local_copy_for_metadata() -> None:
    local = _local("model", "/local/m", size_bytes=100, tier="local")
    external = _local("model", "/ext/m", size_bytes=999, tier="external")

    merged = merge_tiers([external, local])  # external first on purpose

    assert merged[0].size_bytes == 100  # local is the live-scanned truth


def test_merge_collects_all_serving_inferencers() -> None:
    a = _local("model", "/local/m", inferencer="llama.cpp", tier="local")
    b = _local("model", "/local/m", inferencer="gpt4all", tier="local")

    merged = merge_tiers([a, b])

    assert merged[0].inferencers == ("gpt4all", "llama.cpp")


def test_merge_distinct_models_stay_separate() -> None:
    a = _local("model-a", "/local/a")
    b = _local("model-b", "/local/b")

    assert len(merge_tiers([a, b])) == 2


# --- External catalog (offline cache) --------------------------------------


def test_catalog_round_trips(tmp_path) -> None:
    models = [
        _local("Qwen-7B", "/ext/qwen", tier="external"),
        _local("Phi-3", "/ext/phi", tier="external"),
    ]

    write_external_catalog(tmp_path, models)
    restored = read_external_catalog(tmp_path)

    assert [m.name for m in restored] == ["Qwen-7B", "Phi-3"]
    assert all(m.tier == "external" for m in restored)


def test_read_catalog_absent_returns_empty(tmp_path) -> None:
    assert read_external_catalog(tmp_path) == []


def test_read_catalog_corrupt_returns_empty(tmp_path) -> None:
    external_catalog_path(tmp_path).write_text("{not json", encoding="utf-8")
    assert read_external_catalog(tmp_path) == []


def test_read_catalog_version_mismatch_returns_empty(tmp_path) -> None:
    external_catalog_path(tmp_path).write_text(
        json.dumps({"version": 999, "models": []}), encoding="utf-8"
    )
    assert read_external_catalog(tmp_path) == []


def test_read_catalog_models_not_a_list_returns_empty(tmp_path) -> None:
    # Right version, but ``models`` is not a list -> degrade to empty, not raise.
    external_catalog_path(tmp_path).write_text(
        json.dumps({"version": 1, "models": "oops"}), encoding="utf-8"
    )
    assert read_external_catalog(tmp_path) == []


def test_read_catalog_skips_malformed_entries(tmp_path) -> None:
    # A non-dict entry and a dict missing a required key are both dropped,
    # while the well-formed entry survives.
    good = {
        "inferencer": "llama.cpp",
        "store_format": "gguf",
        "name": "Qwen-7B",
        "path": "/ext/qwen.gguf",
        "size_bytes": 4096,
        "quant": None,
        "provider": None,
        "identity": "/ext/qwen.gguf",
    }
    external_catalog_path(tmp_path).write_text(
        json.dumps(
            {
                "version": 1,
                "models": ["not-a-dict", {"name": "missing-fields"}, good],
            }
        ),
        encoding="utf-8",
    )

    restored = read_external_catalog(tmp_path)

    assert [m.name for m in restored] == ["Qwen-7B"]


# --- build_tiered_inventory (the integration) ------------------------------


def test_build_inventory_merges_both_tiers_when_mounted(tmp_path) -> None:
    root = tmp_path / "ext"
    _mount_external(root)
    _write_gguf(root / "gguf", "external-only")
    cfg = _external_cfg(root)
    local = [_local("local-only", "/local/x", tier="local")]

    inv = build_tiered_inventory(
        local, cfg, [_inferencer("llama.cpp", "gguf")], state_dir=tmp_path / "state"
    )

    assert isinstance(inv, TieredInventory)
    assert inv.external_availability is TierAvailability.MOUNTED
    assert inv.external_cached is False
    names = {m.name: m.tiers for m in inv.models}
    assert names["local-only"] == ("local",)
    assert names["external-only"] == ("external",)


def test_build_inventory_persists_catalog_when_mounted(tmp_path) -> None:
    root = tmp_path / "ext"
    _mount_external(root)
    _write_gguf(root / "gguf", "cached-model")
    cfg = _external_cfg(root)
    state = tmp_path / "state"

    build_tiered_inventory([], cfg, [_inferencer("llama.cpp", "gguf")], state_dir=state)

    assert [m.name for m in read_external_catalog(state)] == ["cached-model"]


def test_build_inventory_offline_uses_cache(tmp_path) -> None:
    state = tmp_path / "state"
    write_external_catalog(state, [_local("evicted", "/ext/e", tier="external")])
    # Root never mounted -> offline; cache should back the external view.
    cfg = _external_cfg(tmp_path / "never-plugged-in")
    local = [_local("here", "/local/h", tier="local")]

    inv = build_tiered_inventory(local, cfg, [_inferencer("llama.cpp", "gguf")], state_dir=state)

    assert inv.external_availability is TierAvailability.OFFLINE
    assert inv.external_cached is True
    names = {m.name: m.tiers for m in inv.models}
    assert names["here"] == ("local",)
    assert names["evicted"] == ("external",)


def test_build_inventory_offline_no_cache_omits_external(tmp_path) -> None:
    cfg = _external_cfg(tmp_path / "never-plugged-in")
    local = [_local("here", "/local/h", tier="local")]

    inv = build_tiered_inventory(local, cfg, [_inferencer("llama.cpp", "gguf")])

    assert inv.external_availability is TierAvailability.OFFLINE
    assert inv.external_cached is False
    assert [m.name for m in inv.models] == ["here"]


def test_build_inventory_mounted_ignores_stale_cache(tmp_path) -> None:
    # The live scan is the truth when mounted — a stale cache must not leak in.
    root = tmp_path / "ext"
    _mount_external(root)
    _write_gguf(root / "gguf", "live-model")
    cfg = _external_cfg(root)
    state = tmp_path / "state"
    write_external_catalog(state, [_local("stale", "/ext/stale", tier="external")])

    inv = build_tiered_inventory([], cfg, [_inferencer("llama.cpp", "gguf")], state_dir=state)

    names = {m.name for m in inv.models}
    assert "live-model" in names
    assert "stale" not in names


def test_build_inventory_mounted_without_state_dir_skips_catalog(tmp_path) -> None:
    # Mounted but no state_dir: the live scan still merges, no catalog is written.
    root = tmp_path / "ext"
    _mount_external(root)
    _write_gguf(root / "gguf", "external-only")
    cfg = _external_cfg(root)
    local = [_local("local-only", "/local/x", tier="local")]

    inv = build_tiered_inventory(local, cfg, [_inferencer("llama.cpp", "gguf")])

    assert inv.external_availability is TierAvailability.MOUNTED
    assert inv.external_cached is False
    names = {m.name: m.tiers for m in inv.models}
    assert names["external-only"] == ("external",)
    assert names["local-only"] == ("local",)


def test_build_inventory_no_external_configured() -> None:
    local = [_local("only", "/local/o", tier="local")]

    inv = build_tiered_inventory(local, None, [])

    assert inv.external_availability is TierAvailability.OFFLINE
    assert [m.name for m in inv.models] == ["only"]
