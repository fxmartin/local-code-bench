"""Epic-12 Story 12.4-001 — disk-budget + LRU auto-tiering with pins + dry-run.

Drives :mod:`local_code_bench.inferencers.autotier`. The planner is pure and
deterministic, so it is exercised entirely in-memory with an injected last-used
signal; the apply step is checked against an injected demote so no real bytes move.
Covers budget shortfall maths (max-footprint, min-free, both), LRU ordering with
deterministic tie-breaks, pin protection (never evicted, surfaced as a warning),
the offline → paused path, shared-artifact de-duplication, and the last-used store.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from local_code_bench.config import (
    AutoTierConfig,
    ExternalRepoConfig,
    InferencerConfig,
    StoreFormat,
)
from local_code_bench.inferencers.autotier import (
    AutoTierError,
    DiskBudget,
    LastUsedStore,
    apply_plan,
    budget_from_config,
    mtime_last_used,
    plan_autotier,
)
from local_code_bench.inferencers.inventory import LocalModel
from local_code_bench.inferencers.tiering import DemotePlan, DemoteResult


# --- Helpers ---------------------------------------------------------------


def _model(
    name: str,
    size: int,
    *,
    identity: str | None = None,
    inferencer: str = "llama",
    store_format: StoreFormat = "gguf",
    path: str | None = None,
) -> LocalModel:
    return LocalModel(
        inferencer=inferencer,
        store_format=store_format,
        name=name,
        path=path or f"/local/{name}",
        size_bytes=size,
        quant=None,
        provider=None,
        identity=identity or f"id:{name}",
        tier="local",
    )


def _by_name(times: dict[str, float]):
    return lambda model: times.get(model.name, 0.0)


# --- plan_autotier: budget shortfall + LRU ordering ------------------------


def test_plan_evicts_lru_until_max_footprint_met() -> None:
    models = [
        _model("recent", 100),
        _model("old", 100),
        _model("oldest", 100),
    ]
    # local total = 300, budget 150 -> reclaim 150 -> two oldest models.
    times = {"recent": 300.0, "old": 200.0, "oldest": 100.0}

    plan = plan_autotier(models, DiskBudget(max_local_bytes=150), last_used=_by_name(times))

    assert [e.name for e in plan.evictions] == ["oldest", "old"]
    assert plan.bytes_to_reclaim == 150
    assert plan.bytes_reclaimed == 200
    assert plan.local_total_bytes == 300
    assert plan.satisfied is True
    assert plan.paused is False
    assert plan.warnings == ()


def test_plan_is_pure_and_moves_nothing(tmp_path: Path) -> None:
    f = tmp_path / "m.gguf"
    f.write_bytes(b"x" * 50)
    model = _model("m", 50, path=str(f))

    plan = plan_autotier([model], DiskBudget(max_local_bytes=0))

    assert [e.name for e in plan.evictions] == ["m"]
    # A dry run touches no disk: the file is still there.
    assert f.exists()


def test_plan_ties_broken_by_name_deterministically() -> None:
    models = [_model("b", 100), _model("a", 100)]
    times = {"a": 100.0, "b": 100.0}  # identical recency

    plan = plan_autotier(models, DiskBudget(max_local_bytes=100), last_used=_by_name(times))

    assert [e.name for e in plan.evictions] == ["a"]  # name tie-break, only one needed


def test_plan_empty_when_already_under_budget() -> None:
    models = [_model("a", 50), _model("b", 50)]

    plan = plan_autotier(models, DiskBudget(max_local_bytes=500))

    assert plan.is_empty is True
    assert plan.bytes_to_reclaim == 0
    assert plan.satisfied is True


# --- min-free and combined budgets -----------------------------------------


def test_plan_min_free_uses_current_free_space() -> None:
    models = [_model("old", 100), _model("new", 100)]
    times = {"old": 1.0, "new": 2.0}
    # Need 70 free, only 30 free -> reclaim 40 -> one model (100) suffices.
    plan = plan_autotier(
        models, DiskBudget(min_free_bytes=70), free_bytes=30, last_used=_by_name(times)
    )

    assert [e.name for e in plan.evictions] == ["old"]
    assert plan.bytes_to_reclaim == 40
    assert plan.satisfied is True


def test_plan_combined_budget_takes_stricter_requirement() -> None:
    models = [_model("a", 100), _model("b", 100), _model("c", 100)]
    times = {"a": 1.0, "b": 2.0, "c": 3.0}
    # max-footprint: 300 - 250 = 50 to reclaim. min-free: 200 - 10 = 190 to reclaim.
    plan = plan_autotier(
        models,
        DiskBudget(max_local_bytes=250, min_free_bytes=200),
        free_bytes=10,
        last_used=_by_name(times),
    )

    assert plan.bytes_to_reclaim == 190
    assert [e.name for e in plan.evictions] == ["a", "b"]


# --- pinning ---------------------------------------------------------------


def test_plan_never_evicts_pinned_even_when_budget_unmet() -> None:
    models = [_model("pinned", 100), _model("free", 100)]
    times = {"pinned": 1.0, "free": 2.0}  # pinned is LRU but protected

    plan = plan_autotier(
        models,
        DiskBudget(max_local_bytes=0),  # would need to evict everything
        pins=["pinned"],
        last_used=_by_name(times),
    )

    assert [e.name for e in plan.evictions] == ["free"]
    assert plan.pinned == ("pinned",)
    assert plan.satisfied is False
    assert plan.bytes_reclaimed == 100
    assert any("pinned models protected" in w for w in plan.warnings)


def test_plan_warns_when_no_more_candidates_without_pins() -> None:
    models = [_model("a", 100)]

    plan = plan_autotier(models, DiskBudget(max_local_bytes=0))

    assert [e.name for e in plan.evictions] == ["a"]
    assert plan.satisfied is True  # exactly met
    # Now a case that cannot be met at all.
    plan2 = plan_autotier([_model("a", 10)], DiskBudget(min_free_bytes=1000), free_bytes=0)
    assert plan2.satisfied is False
    assert any("no more evictable models" in w for w in plan2.warnings)


# --- offline → paused ------------------------------------------------------


def test_plan_paused_when_external_offline() -> None:
    models = [_model("a", 100), _model("b", 100)]

    plan = plan_autotier(models, DiskBudget(max_local_bytes=0), external_available=False)

    assert plan.paused is True
    assert plan.evictions == ()
    assert plan.satisfied is False
    assert any("paused" in w for w in plan.warnings)


def test_plan_paused_offline_no_warning_when_already_under_budget() -> None:
    plan = plan_autotier(
        [_model("a", 10)], DiskBudget(max_local_bytes=500), external_available=False
    )

    assert plan.paused is True
    assert plan.satisfied is True
    assert plan.warnings == ()


# --- shared-artifact de-duplication ----------------------------------------


def test_plan_counts_shared_artifact_once() -> None:
    # Two engines, one on-disk artifact (same identity) -> one logical model.
    shared_a = _model("shared", 100, identity="id:shared", inferencer="llama")
    shared_b = _model("shared", 100, identity="id:shared", inferencer="mlx-engine")
    solo = _model("solo", 100, identity="id:solo")
    times = {"shared": 1.0, "solo": 2.0}

    plan = plan_autotier(
        [shared_a, shared_b, solo],
        DiskBudget(max_local_bytes=100),
        last_used=_by_name(times),
    )

    # local total counts the shared artifact once: 200, not 300.
    assert plan.local_total_bytes == 200
    assert [e.name for e in plan.evictions] == ["shared"]
    assert plan.bytes_reclaimed == 100


# --- apply_plan ------------------------------------------------------------


def _inferencer(name: str, store: Path) -> InferencerConfig:
    return InferencerConfig(
        name=name,
        lifecycle="server",
        detect_kind="binary",
        detect_target=name,
        port=1,
        health_url="http://127.0.0.1:{port}/h",
        model_store=(str(store),),
        store_format="gguf",
    )


def _fake_result(model: LocalModel) -> DemoteResult:
    plan = DemotePlan(
        name=model.name,
        store_format=model.store_format,
        source=Path(model.path),
        destination=Path("/ext") / model.name,
        size_bytes=model.size_bytes,
    )
    return DemoteResult(
        plan=plan,
        destination=plan.destination,
        bytes_reclaimed=model.size_bytes,
        verified=True,
        reused_existing=False,
    )


def test_apply_delegates_each_eviction_to_demote_and_records_last_used(tmp_path: Path) -> None:
    models = [_model("a", 100, identity="id:a"), _model("b", 100, identity="id:b")]
    times = {"a": 1.0, "b": 2.0}
    plan = plan_autotier(models, DiskBudget(max_local_bytes=0), last_used=_by_name(times))

    calls: list[str] = []

    def _fake_demote(model, external_cfg, configs, state_dir, *, home=None) -> DemoteResult:
        calls.append(model.name)
        return _fake_result(model)

    store = LastUsedStore(tmp_path)
    results = apply_plan(
        plan,
        ExternalRepoConfig(root=str(tmp_path / "ext")),
        {"llama": _inferencer("llama", tmp_path / "l")},
        tmp_path,
        now=999.0,
        demote_fn=_fake_demote,
        last_used_store=store,
    )

    assert calls == ["a", "b"]  # both evictions, LRU order
    assert [r.plan.name for r in results] == ["a", "b"]
    # Last-used recorded for each moved model, and persisted.
    assert store.get("id:a") == 999.0
    assert store.get("id:b") == 999.0
    assert LastUsedStore(tmp_path).get("id:a") == 999.0


def test_apply_refuses_paused_plan(tmp_path: Path) -> None:
    plan = plan_autotier(
        [_model("a", 100)], DiskBudget(max_local_bytes=0), external_available=False
    )

    with pytest.raises(AutoTierError, match="paused"):
        apply_plan(
            plan,
            ExternalRepoConfig(root=str(tmp_path / "ext")),
            {},
            tmp_path,
            now=1.0,
        )


def test_apply_without_store_still_demotes(tmp_path: Path) -> None:
    plan = plan_autotier([_model("a", 100, identity="id:a")], DiskBudget(max_local_bytes=0))
    calls: list[str] = []

    def _fake_demote(model, external_cfg, configs, state_dir, *, home=None) -> DemoteResult:
        calls.append(model.name)
        return _fake_result(model)

    results = apply_plan(
        plan,
        ExternalRepoConfig(root=str(tmp_path / "ext")),
        {},
        tmp_path,
        now=1.0,
        demote_fn=_fake_demote,
    )
    assert calls == ["a"]
    assert len(results) == 1


# --- LastUsedStore + signal helpers ----------------------------------------


def test_last_used_store_prefers_recorded_over_mtime(tmp_path: Path) -> None:
    f = tmp_path / "m.gguf"
    f.write_bytes(b"x")
    model = _model("m", 1, identity="id:m", path=str(f))
    store = LastUsedStore(tmp_path)

    # No record yet -> falls back to mtime.
    assert store.last_used(model) == pytest.approx(f.stat().st_mtime)

    store.record("id:m", 42.0)
    assert store.last_used(model) == 42.0


def test_last_used_store_missing_file_degrades_to_empty(tmp_path: Path) -> None:
    store = LastUsedStore(tmp_path / "nope")
    assert store.get("id:x") is None


def test_last_used_store_ignores_malformed_file(tmp_path: Path) -> None:
    (tmp_path / "model-last-used.json").write_text("not json", encoding="utf-8")
    store = LastUsedStore(tmp_path)
    assert store.get("id:x") is None


def test_last_used_store_ignores_non_dict_and_bad_values(tmp_path: Path) -> None:
    (tmp_path / "model-last-used.json").write_text("[1, 2, 3]", encoding="utf-8")
    assert LastUsedStore(tmp_path).get("id:x") is None

    (tmp_path / "model-last-used.json").write_text(
        '{"id:ok": 5.0, "id:bad": "nope"}', encoding="utf-8"
    )
    store = LastUsedStore(tmp_path)
    assert store.get("id:ok") == 5.0
    assert store.get("id:bad") is None


def test_mtime_last_used_reads_file_mtime(tmp_path: Path) -> None:
    f = tmp_path / "m.gguf"
    f.write_bytes(b"x")
    assert mtime_last_used(_model("m", 1, path=str(f))) == pytest.approx(f.stat().st_mtime)


def test_mtime_last_used_missing_path_is_zero() -> None:
    assert mtime_last_used(_model("ghost", 1, path="/nope/ghost.gguf")) == 0.0


def test_budget_from_config_converts_gib() -> None:
    cfg = AutoTierConfig(max_local_gb=2.0, min_free_gb=0.5, pins=("x",))
    budget = budget_from_config(cfg)
    assert budget.max_local_bytes == 2 * 1024**3
    assert budget.min_free_bytes == int(0.5 * 1024**3)
    assert budget.is_set is True


def test_disk_budget_unset_is_not_set() -> None:
    assert DiskBudget().is_set is False
