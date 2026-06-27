"""Tests for the disk-footprint and duplicate-download report (Story 11.6-001).

A summarisation over the normalized inventory + sharing sets. It reports total
bytes per format and per engine, and distinguishes the two ways a base model can
appear more than once:

- **shared** — one stored artifact (same format + content identity) reachable by
  several engines. Good: counted once, never flagged duplicate.
- **duplicated** — the same base model materialised more than once on disk (as
  GGUF *and* MLX, or copied across stores). Reclaimable: flagged with the bytes
  consolidation would save (keeping one copy, removing the redundant ones).
"""

from __future__ import annotations

import pytest

from local_code_bench.config import StoreFormat
from local_code_bench.inferencers.inventory import (
    DiskReport,
    DuplicateGroup,
    EngineUsage,
    FormatUsage,
    LocalModel,
    base_model_key,
    disk_report,
)


def _local(
    *,
    inferencer: str,
    identity: str,
    name: str = "mlx-community/Qwen2.5-Coder-7B",
    store_format: StoreFormat = "hf-safetensors",
    size_bytes: int = 100,
    quant: str | None = None,
    provider: str | None = None,
    path: str | None = None,
) -> LocalModel:
    return LocalModel(
        inferencer=inferencer,
        store_format=store_format,
        name=name,
        path=path or f"/store/{identity}",
        size_bytes=size_bytes,
        quant=quant,
        provider=provider,
        identity=identity,
    )


# --- Totals per format and per engine -------------------------------------


def test_total_bytes_counts_each_distinct_artifact_once() -> None:
    # A shared artifact (same format + identity, two engines) is one copy on disk.
    models = [
        _local(inferencer="dflash", identity="/cache/repo", size_bytes=4000),
        _local(inferencer="mlx-lm", identity="/cache/repo", size_bytes=4000),
        _local(inferencer="llama-cpp", identity="/m.gguf", store_format="gguf", size_bytes=1500),
    ]

    report = disk_report(models)

    assert isinstance(report, DiskReport)
    assert report.total_bytes == 5500  # 4000 (shared, once) + 1500


def test_by_format_sums_distinct_artifacts_per_format() -> None:
    models = [
        _local(inferencer="dflash", identity="/cache/a", size_bytes=4000),
        _local(inferencer="mlx-lm", identity="/cache/a", size_bytes=4000),  # shared, once
        _local(inferencer="mlx-lm", identity="/cache/b", size_bytes=2000),
        _local(inferencer="llama-cpp", identity="/m.gguf", store_format="gguf", size_bytes=1500),
    ]

    report = disk_report(models)

    by_format = {u.store_format: u.size_bytes for u in report.by_format}
    assert by_format == {"hf-safetensors": 6000, "gguf": 1500}
    assert all(isinstance(u, FormatUsage) for u in report.by_format)
    # Sorted by format name for stable output.
    assert [u.store_format for u in report.by_format] == ["gguf", "hf-safetensors"]


def test_by_engine_attributes_a_shared_artifact_to_each_serving_engine() -> None:
    # A shared artifact counts toward every engine that can serve it, so the
    # per-engine totals can exceed the de-duplicated grand total.
    models = [
        _local(inferencer="dflash", identity="/cache/repo", size_bytes=4000),
        _local(inferencer="mlx-lm", identity="/cache/repo", size_bytes=4000),
    ]

    report = disk_report(models)

    by_engine = {u.inferencer: u.size_bytes for u in report.by_engine}
    assert by_engine == {"dflash": 4000, "mlx-lm": 4000}
    assert all(isinstance(u, EngineUsage) for u in report.by_engine)
    assert [u.inferencer for u in report.by_engine] == ["dflash", "mlx-lm"]  # sorted


def test_one_engine_reaching_an_artifact_twice_counts_it_once_per_engine() -> None:
    models = [
        _local(
            inferencer="llama-cpp",
            identity="/m.gguf",
            store_format="gguf",
            size_bytes=1500,
            path="/a/m.gguf",
        ),
        _local(
            inferencer="llama-cpp",
            identity="/m.gguf",
            store_format="gguf",
            size_bytes=1500,
            path="/b/m.gguf",
        ),
    ]

    report = disk_report(models)

    by_engine = {u.inferencer: u.size_bytes for u in report.by_engine}
    assert by_engine == {"llama-cpp": 1500}
    assert report.total_bytes == 1500


# --- Duplicate detection --------------------------------------------------


def test_same_base_model_as_gguf_and_mlx_is_flagged_duplicate() -> None:
    models = [
        _local(
            inferencer="llama-cpp",
            identity="/m.gguf",
            store_format="gguf",
            name="Qwen2.5-Coder-7B-Q4_K_M",
            size_bytes=4000,
        ),
        _local(
            inferencer="mlx-lm",
            identity="/cache/repo",
            store_format="mlx",
            name="mlx-community/Qwen2.5-Coder-7B",
            size_bytes=9000,
        ),
    ]

    report = disk_report(models)

    assert len(report.duplicates) == 1
    dup = report.duplicates[0]
    assert isinstance(dup, DuplicateGroup)
    assert dup.base == "qwen2.5-coder-7b"
    assert dup.total_bytes == 13000
    # Keep the single largest copy, reclaim the rest: 13000 - 9000.
    assert dup.reclaimable_bytes == 4000
    assert len(dup.artifacts) == 2


def test_same_base_model_duplicated_across_stores_same_format_is_flagged() -> None:
    # Two physical copies of one base model in different stores (distinct
    # identities, not symlinked) — reclaimable even within one format.
    models = [
        _local(
            inferencer="lm-studio",
            identity="/lmstudio/m.gguf",
            store_format="gguf",
            name="Llama-3.2-3B-Q4_K_M",
            size_bytes=2000,
            path="/lmstudio/m.gguf",
        ),
        _local(
            inferencer="gpt4all",
            identity="/gpt4all/m.gguf",
            store_format="gguf",
            name="Llama-3.2-3B-Q4_K_M",
            size_bytes=2000,
            path="/gpt4all/m.gguf",
        ),
    ]

    report = disk_report(models)

    assert len(report.duplicates) == 1
    dup = report.duplicates[0]
    assert dup.base == "llama-3.2-3b"
    assert dup.total_bytes == 4000
    assert dup.reclaimable_bytes == 2000  # keep one copy


def test_single_copy_is_not_flagged_as_duplicate() -> None:
    models = [
        _local(
            inferencer="mlx-lm",
            identity="/cache/repo",
            name="mlx-community/Qwen2.5-Coder-7B",
            size_bytes=4000,
        ),
    ]

    report = disk_report(models)

    assert report.duplicates == ()


def test_shared_artifact_is_not_a_duplicate() -> None:
    # One stored artifact reachable by two engines is shared, NOT duplicated:
    # there is a single copy on disk, so nothing is reclaimable.
    models = [
        _local(
            inferencer="dflash",
            identity="/cache/repo",
            name="mlx-community/Qwen2.5-Coder-7B",
            size_bytes=4000,
        ),
        _local(
            inferencer="mlx-lm",
            identity="/cache/repo",
            name="mlx-community/Qwen2.5-Coder-7B",
            size_bytes=4000,
        ),
    ]

    report = disk_report(models)

    assert report.duplicates == ()


def test_duplicates_are_sorted_by_base_key() -> None:
    models = [
        _local(
            inferencer="a",
            identity="/z1",
            store_format="gguf",
            name="Zeta-7B-Q4_K_M",
            size_bytes=10,
        ),
        _local(
            inferencer="b", identity="/z2", store_format="mlx", name="zeta/Zeta-7B", size_bytes=20
        ),
        _local(
            inferencer="a",
            identity="/a1",
            store_format="gguf",
            name="Alpha-3B-Q4_K_M",
            size_bytes=10,
        ),
        _local(
            inferencer="b", identity="/a2", store_format="mlx", name="alpha/Alpha-3B", size_bytes=20
        ),
    ]

    report = disk_report(models)

    assert [d.base for d in report.duplicates] == ["alpha-3b", "zeta-7b"]


# --- base_model_key normalization -----------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("mlx-community/Qwen2.5-Coder-7B", "qwen2.5-coder-7b"),
        ("Qwen2.5-Coder-7B-Q4_K_M", "qwen2.5-coder-7b"),
        ("bartowski/Qwen2.5-Coder-7B-GGUF", "qwen2.5-coder-7b"),
        ("Llama-3.2-3B-Instruct-IQ3_XXS", "llama-3.2-3b-instruct"),
        ("qwen2.5-coder:7b", "qwen2.5-coder-7b"),
        ("mlx-community/Llama-3.2-3B-mlx", "llama-3.2-3b"),
    ],
)
def test_base_model_key_normalizes_provider_quant_and_format(name: str, expected: str) -> None:
    assert base_model_key(name) == expected


def test_base_model_key_collapses_gguf_and_mlx_names_of_one_model() -> None:
    gguf = base_model_key("Qwen2.5-Coder-7B-Q4_K_M")
    mlx = base_model_key("mlx-community/Qwen2.5-Coder-7B")
    assert gguf == mlx


# --- Shape and edge cases -------------------------------------------------


def test_empty_inventory_yields_an_empty_report() -> None:
    report = disk_report([])

    assert report.total_bytes == 0
    assert report.by_format == ()
    assert report.by_engine == ()
    assert report.duplicates == ()


def test_report_dataclasses_are_frozen() -> None:
    report = disk_report([_local(inferencer="a", identity="/x")])
    with pytest.raises(AttributeError):
        report.total_bytes = 1  # type: ignore[misc]
