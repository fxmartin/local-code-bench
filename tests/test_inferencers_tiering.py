"""Epic-12 Story 12.3-001 — promote a model from external to local (atomic).

Drives :mod:`local_code_bench.inferencers.tiering`. Every side effect (external
availability, running-engine state, local free space, the copy itself) is exercised
against a temp tree with injected seams, so the full promote flow — happy path,
every up-front refusal, and every mid-copy abort — is covered without a real SSD,
live processes, or a disk-full condition.
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
    DemoteError,
    DemotePlan,
    DemoteResult,
    PromoteError,
    PromotePlan,
    PromoteResult,
    demote_model,
    plan_demotion,
    plan_promotion,
    promote_model,
    serving_blockers,
)
from local_code_bench.inferencers import tiering


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


def _external_model(path: Path, *, name: str = "qwen", store_format: StoreFormat = "gguf") -> LocalModel:
    return LocalModel(
        inferencer="external-scan",
        store_format=store_format,
        name=name,
        path=str(path),
        size_bytes=_tree_size(path),
        quant=None,
        provider=None,
        identity=str(path),
        tier="external",
    )


def _local_model(path: Path, *, name: str = "qwen", store_format: StoreFormat = "gguf") -> LocalModel:
    return LocalModel(
        inferencer="local-scan",
        store_format=store_format,
        name=name,
        path=str(path),
        size_bytes=_tree_size(path),
        quant=None,
        provider=None,
        identity=str(path),
        tier="local",
    )


def _tree_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _write_gguf(base: Path, stem: str, payload: bytes = b"weights" * 1000) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{stem}.gguf"
    path.write_bytes(payload)
    return path


def _write_model_dir(base: Path, name: str) -> Path:
    model = base / name
    model.mkdir(parents=True, exist_ok=True)
    (model / "config.json").write_text('{"hidden": 4096}', encoding="utf-8")
    (model / "model.safetensors").write_bytes(b"tensor-bytes" * 500)
    # A nested subdirectory so tree walks see a non-file child (e.g. tokenizer assets).
    nested = model / "tokenizer"
    nested.mkdir(exist_ok=True)
    (nested / "tokenizer.json").write_text('{"vocab": 1}', encoding="utf-8")
    return model


def _not_running(cfg: InferencerConfig, state_dir) -> InferencerStatus:
    return InferencerStatus(cfg.name, True, cfg.lifecycle, False, None, cfg.port, False, "down")


def _running(cfg: InferencerConfig, state_dir) -> InferencerStatus:
    return InferencerStatus(cfg.name, True, cfg.lifecycle, True, 4242, cfg.port, True, "up")


def _plenty(_path: Path) -> int:
    return 1 << 40  # 1 TiB free


# --- plan_promotion --------------------------------------------------------


def test_plan_resolves_destination_under_engine_store(tmp_path: Path) -> None:
    source = _write_gguf(tmp_path / "ext" / "gguf", "qwen")
    local = tmp_path / "local" / "gguf"
    cfg = _inferencer("llama", local)

    plan = plan_promotion(_external_model(source), cfg)

    assert plan == PromotePlan(
        name="qwen",
        store_format="gguf",
        source=source,
        destination=local / "qwen.gguf",
        size_bytes=source.stat().st_size,
    )


def test_plan_expands_home_in_store_path(tmp_path: Path) -> None:
    source = _write_gguf(tmp_path / "ext", "m")
    cfg = _inferencer("llama", Path("~/store/gguf"))

    plan = plan_promotion(_external_model(source), cfg, home=tmp_path)

    assert plan.destination == tmp_path / "store" / "gguf" / "m.gguf"


def test_plan_rejects_non_external_source(tmp_path: Path) -> None:
    source = _write_gguf(tmp_path / "ext", "m")
    local_record = LocalModel(
        inferencer="x", store_format="gguf", name="m", path=str(source),
        size_bytes=1, quant=None, provider=None, identity="i", tier="local",
    )
    with pytest.raises(PromoteError, match="not external"):
        plan_promotion(local_record, _inferencer("llama", tmp_path / "local"))


def test_plan_rejects_engine_without_store(tmp_path: Path) -> None:
    source = _write_gguf(tmp_path / "ext", "m")
    cfg = InferencerConfig(
        name="appy", lifecycle="app", detect_kind="app", detect_target="A",
        port=1, health_url="http://127.0.0.1:{port}/",
    )
    with pytest.raises(PromoteError, match="no local model store"):
        plan_promotion(_external_model(source), cfg)


# --- serving_blockers ------------------------------------------------------


def test_serving_blockers_flags_running_same_format(tmp_path: Path) -> None:
    source = _write_gguf(tmp_path / "ext", "m")
    configs = {
        "llama": _inferencer("llama", tmp_path / "l", "gguf"),
        "mlx": _inferencer("mlx", tmp_path / "m", "mlx"),
    }
    status = {"llama": _running, "mlx": _running}

    blockers = serving_blockers(
        _external_model(source), configs, tmp_path,
        status_fn=lambda cfg, sd: status[cfg.name](cfg, sd),
    )

    assert blockers == ["llama"]  # mlx is a different format, never a blocker


def test_serving_blockers_empty_when_idle(tmp_path: Path) -> None:
    source = _write_gguf(tmp_path / "ext", "m")
    configs = {"llama": _inferencer("llama", tmp_path / "l", "gguf")}

    assert serving_blockers(_external_model(source), configs, tmp_path, status_fn=_not_running) == []


# --- promote_model: happy paths --------------------------------------------


def test_promote_file_copies_verifies_and_publishes(tmp_path: Path) -> None:
    ext_root = tmp_path / "ext"
    _mount(ext_root)
    source = _write_gguf(ext_root / "gguf", "qwen")
    local = tmp_path / "local" / "gguf"
    cfg = _inferencer("llama", local)

    result = promote_model(
        _external_model(source), cfg, _external_cfg(ext_root), {"llama": cfg}, tmp_path,
        free_bytes=_plenty, status_fn=_not_running,
    )

    dest = local / "qwen.gguf"
    assert isinstance(result, PromoteResult)
    assert result.verified is True
    assert result.destination == dest
    assert result.bytes_copied == source.stat().st_size
    # Published copy is byte-identical and the external source is untouched.
    assert dest.read_bytes() == source.read_bytes()
    assert source.exists()
    # No staging artifact left behind.
    assert not (local / "qwen.gguf.promote-tmp").exists()


def test_promote_directory_model_copies_tree(tmp_path: Path) -> None:
    ext_root = tmp_path / "ext"
    _mount(ext_root)
    source = _write_model_dir(ext_root / "mlx", "Qwen3")
    local = tmp_path / "local" / "mlx"
    cfg = _inferencer("mlx-engine", local, "mlx")

    result = promote_model(
        _external_model(source, name="Qwen3", store_format="mlx"),
        cfg, _external_cfg(ext_root), {"mlx-engine": cfg}, tmp_path,
        free_bytes=_plenty, status_fn=_not_running,
    )

    dest = local / "Qwen3"
    assert result.verified is True
    assert (dest / "model.safetensors").read_bytes() == (source / "model.safetensors").read_bytes()
    assert (dest / "config.json").read_text() == (source / "config.json").read_text()
    assert source.exists()


def test_promote_uses_default_seams_for_idle_local_engine(tmp_path: Path) -> None:
    # Exercise the real default free_bytes (shutil.disk_usage) and a real manager
    # status lookup with no state file (engine reported down) — no injection.
    ext_root = tmp_path / "ext"
    _mount(ext_root)
    source = _write_gguf(ext_root / "gguf", "tiny", payload=b"x" * 64)
    local = tmp_path / "local" / "gguf"
    cfg = _inferencer("llama", local)

    result = promote_model(
        _external_model(source), cfg, _external_cfg(ext_root), {"llama": cfg},
        tmp_path / "state",
    )

    assert (local / "tiny.gguf").exists()
    assert result.verified is True


# --- promote_model: up-front refusals (no bytes moved) ---------------------


def test_promote_refuses_when_external_offline(tmp_path: Path) -> None:
    ext_root = tmp_path / "ext"  # not mounted: no marker
    ext_root.mkdir()
    source = _write_gguf(ext_root / "gguf", "qwen")
    local = tmp_path / "local" / "gguf"
    cfg = _inferencer("llama", local)

    with pytest.raises(PromoteError, match="offline"):
        promote_model(
            _external_model(source), cfg, _external_cfg(ext_root), {"llama": cfg}, tmp_path,
            free_bytes=_plenty, status_fn=_not_running,
        )
    assert not local.exists()


def test_promote_refuses_when_source_missing(tmp_path: Path) -> None:
    ext_root = tmp_path / "ext"
    _mount(ext_root)
    missing = ext_root / "gguf" / "ghost.gguf"
    cfg = _inferencer("llama", tmp_path / "local")
    model = LocalModel(
        inferencer="s", store_format="gguf", name="ghost", path=str(missing),
        size_bytes=10, quant=None, provider=None, identity=str(missing), tier="external",
    )

    with pytest.raises(PromoteError, match="missing"):
        promote_model(
            model, cfg, _external_cfg(ext_root), {"llama": cfg}, tmp_path,
            free_bytes=_plenty, status_fn=_not_running,
        )


def test_promote_refuses_when_serving_engine_running(tmp_path: Path) -> None:
    ext_root = tmp_path / "ext"
    _mount(ext_root)
    source = _write_gguf(ext_root / "gguf", "qwen")
    local = tmp_path / "local" / "gguf"
    cfg = _inferencer("llama", local)

    with pytest.raises(PromoteError, match="is running"):
        promote_model(
            _external_model(source), cfg, _external_cfg(ext_root), {"llama": cfg}, tmp_path,
            free_bytes=_plenty, status_fn=_running,
        )
    assert not local.exists()  # nothing copied


def test_promote_refuses_when_already_present_locally(tmp_path: Path) -> None:
    ext_root = tmp_path / "ext"
    _mount(ext_root)
    source = _write_gguf(ext_root / "gguf", "qwen")
    local = tmp_path / "local" / "gguf"
    existing = _write_gguf(local, "qwen", payload=b"old-bytes")
    cfg = _inferencer("llama", local)

    with pytest.raises(PromoteError, match="already present"):
        promote_model(
            _external_model(source), cfg, _external_cfg(ext_root), {"llama": cfg}, tmp_path,
            free_bytes=_plenty, status_fn=_not_running,
        )
    # The pre-existing local copy is left exactly as it was.
    assert existing.read_bytes() == b"old-bytes"


def test_promote_refuses_on_insufficient_free_space(tmp_path: Path) -> None:
    ext_root = tmp_path / "ext"
    _mount(ext_root)
    source = _write_gguf(ext_root / "gguf", "qwen", payload=b"w" * 10_000)
    local = tmp_path / "local" / "gguf"
    cfg = _inferencer("llama", local)

    with pytest.raises(PromoteError) as exc:
        promote_model(
            _external_model(source), cfg, _external_cfg(ext_root), {"llama": cfg}, tmp_path,
            free_bytes=lambda _p: 4_000, status_fn=_not_running,
        )

    message = str(exc.value)
    assert "insufficient local free space" in message
    assert "free at least" in message  # suggests an amount to free
    assert not local.exists()  # both tiers untouched
    assert source.exists()


# --- promote_model: mid-copy aborts (clean up, source intact) --------------


def test_promote_aborts_on_integrity_mismatch(tmp_path: Path) -> None:
    ext_root = tmp_path / "ext"
    _mount(ext_root)
    source = _write_gguf(ext_root / "gguf", "qwen", payload=b"real" * 1000)
    local = tmp_path / "local" / "gguf"
    cfg = _inferencer("llama", local)

    def corrupt_copy(src: Path, dst: Path) -> None:
        # Same byte length as the source, but different content -> hash mismatch.
        dst.write_bytes(b"junk" * 1000)

    with pytest.raises(PromoteError, match="integrity check failed"):
        promote_model(
            _external_model(source), cfg, _external_cfg(ext_root), {"llama": cfg}, tmp_path,
            free_bytes=_plenty, status_fn=_not_running, copy_fn=corrupt_copy,
        )

    assert not (local / "qwen.gguf").exists()  # nothing published
    assert not (local / "qwen.gguf.promote-tmp").exists()  # staging cleaned up
    assert source.read_bytes() == b"real" * 1000  # source intact


def test_promote_aborts_on_truncated_copy(tmp_path: Path) -> None:
    ext_root = tmp_path / "ext"
    _mount(ext_root)
    source = _write_gguf(ext_root / "gguf", "qwen", payload=b"real" * 1000)
    local = tmp_path / "local" / "gguf"
    cfg = _inferencer("llama", local)

    def short_copy(src: Path, dst: Path) -> None:
        dst.write_bytes(b"short")  # wrong size triggers the size check

    with pytest.raises(PromoteError, match="integrity check failed"):
        promote_model(
            _external_model(source), cfg, _external_cfg(ext_root), {"llama": cfg}, tmp_path,
            free_bytes=_plenty, status_fn=_not_running, copy_fn=short_copy,
        )
    assert not (local / "qwen.gguf.promote-tmp").exists()


def test_promote_aborts_and_cleans_up_on_io_error(tmp_path: Path) -> None:
    ext_root = tmp_path / "ext"
    _mount(ext_root)
    source = _write_gguf(ext_root / "gguf", "qwen")
    local = tmp_path / "local" / "gguf"
    cfg = _inferencer("llama", local)

    def failing_copy(src: Path, dst: Path) -> None:
        dst.write_bytes(b"partial")  # leave a partial staging artifact...
        raise OSError("disk exploded")  # ...then fail

    with pytest.raises(PromoteError, match="partial copy removed"):
        promote_model(
            _external_model(source), cfg, _external_cfg(ext_root), {"llama": cfg}, tmp_path,
            free_bytes=_plenty, status_fn=_not_running, copy_fn=failing_copy,
        )

    assert not (local / "qwen.gguf").exists()
    assert not (local / "qwen.gguf.promote-tmp").exists()  # partial removed
    assert source.exists()


def test_promote_clears_stale_staging_before_copy(tmp_path: Path) -> None:
    ext_root = tmp_path / "ext"
    _mount(ext_root)
    source = _write_gguf(ext_root / "gguf", "qwen")
    local = tmp_path / "local" / "gguf"
    local.mkdir(parents=True)
    # A leftover staging file from a previously-killed promote.
    (local / "qwen.gguf.promote-tmp").write_bytes(b"stale-garbage")
    cfg = _inferencer("llama", local)

    result = promote_model(
        _external_model(source), cfg, _external_cfg(ext_root), {"llama": cfg}, tmp_path,
        free_bytes=_plenty, status_fn=_not_running,
    )

    assert result.verified is True
    assert (local / "qwen.gguf").read_bytes() == source.read_bytes()


# --- internal helpers ------------------------------------------------------


def test_default_copy_handles_file_and_directory(tmp_path: Path) -> None:
    src_file = tmp_path / "a.bin"
    src_file.write_bytes(b"hello")
    tiering._copy_path(src_file, tmp_path / "a-copy.bin")
    assert (tmp_path / "a-copy.bin").read_bytes() == b"hello"

    src_dir = _write_model_dir(tmp_path / "src", "M")
    tiering._copy_path(src_dir, tmp_path / "dir-copy")
    assert (tmp_path / "dir-copy" / "config.json").exists()


def test_remove_path_handles_missing_file_dir_and_errors(tmp_path: Path) -> None:
    tiering._remove_path(tmp_path / "nope")  # missing: no-op, no raise

    f = tmp_path / "f"
    f.write_text("x")
    tiering._remove_path(f)
    assert not f.exists()

    d = _write_model_dir(tmp_path / "tree", "M")
    tiering._remove_path(d.parent)
    assert not d.parent.exists()


def test_remove_path_swallows_unlink_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # An OSError from the unlink itself (e.g. a permission/IO fault, not "missing")
    # must be swallowed so cleanup never masks the real failure being handled.
    f = tmp_path / "f"
    f.write_text("x")

    def boom(self: Path, *args: object, **kwargs: object) -> None:
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "unlink", boom)
    tiering._remove_path(f)  # does not raise


def test_existing_ancestor_walks_up_to_an_existing_dir(tmp_path: Path) -> None:
    deep = tmp_path / "x" / "y" / "z" / "w"
    assert tiering._existing_ancestor(deep) == tmp_path


def test_existing_ancestor_stops_at_filesystem_root(monkeypatch: pytest.MonkeyPatch) -> None:
    # When nothing along the chain exists, the walk halts at the root (parent ==
    # self) rather than looping forever, returning that topmost path.
    monkeypatch.setattr(Path, "exists", lambda self: False)
    root = Path("/a/b/c").anchor
    assert tiering._existing_ancestor(Path("/a/b/c")) == Path(root)


def test_content_hash_distinguishes_tree_changes(tmp_path: Path) -> None:
    a = _write_model_dir(tmp_path / "a", "M")
    b = _write_model_dir(tmp_path / "b", "M")
    assert tiering._content_hash(a) == tiering._content_hash(b)

    (b / "extra.txt").write_text("surprise")
    assert tiering._content_hash(a) != tiering._content_hash(b)


def test_path_size_counts_file_and_tree(tmp_path: Path) -> None:
    f = tmp_path / "f.bin"
    f.write_bytes(b"1234567890")
    assert tiering._path_size(f) == 10

    d = _write_model_dir(tmp_path / "d", "M")
    assert tiering._path_size(d) == _tree_size(d)


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (512, "512 B"),
        (1024, "1.0 KiB"),
        (1536, "1.5 KiB"),
        (5 * 1024 * 1024, "5.0 MiB"),
        (3 * 1024**3, "3.0 GiB"),
        (2 * 1024**4, "2.0 TiB"),
        (5 * 1024**5, "5120.0 TiB"),  # saturates at the largest unit
    ],
)
def test_human_bytes_scales_units(count: int, expected: str) -> None:
    assert tiering._human_bytes(count) == expected


def test_human_bytes_falls_back_when_no_units(monkeypatch: pytest.MonkeyPatch) -> None:
    # Defensive tail: with an empty unit table the scaling loop never runs, so the
    # raw byte count is returned rather than crashing.
    monkeypatch.setattr(tiering, "_UNITS", ())
    assert tiering._human_bytes(42) == "42 B"


# ===========================================================================
# Story 12.3-002 — demote / evict a model from local to external
# ===========================================================================


# --- plan_demotion ---------------------------------------------------------


def test_demote_plan_resolves_destination_under_external_format_dir(tmp_path: Path) -> None:
    source = _write_gguf(tmp_path / "local" / "gguf", "qwen")
    ext_root = tmp_path / "ext"
    cfg = _external_cfg(ext_root)

    plan = plan_demotion(_local_model(source), cfg)

    assert plan == DemotePlan(
        name="qwen",
        store_format="gguf",
        source=source,
        destination=ext_root / "gguf" / "qwen.gguf",
        size_bytes=source.stat().st_size,
    )


def test_demote_plan_expands_home_in_external_root(tmp_path: Path) -> None:
    source = _write_gguf(tmp_path / "local", "m")
    cfg = ExternalRepoConfig(root="~/ext")

    plan = plan_demotion(_local_model(source), cfg, home=tmp_path)

    assert plan.destination == tmp_path / "ext" / "gguf" / "m.gguf"


def test_demote_plan_rejects_non_local_source(tmp_path: Path) -> None:
    source = _write_gguf(tmp_path / "local", "m")
    with pytest.raises(DemoteError, match="not local"):
        plan_demotion(_external_model(source), _external_cfg(tmp_path / "ext"))


# --- demote_model: happy paths ---------------------------------------------


def test_demote_file_copies_verifies_and_removes_local(tmp_path: Path) -> None:
    ext_root = tmp_path / "ext"
    _mount(ext_root)
    source = _write_gguf(tmp_path / "local" / "gguf", "qwen")
    original = source.read_bytes()
    cfg = _inferencer("llama", tmp_path / "local" / "gguf")

    result = demote_model(
        _local_model(source), _external_cfg(ext_root), {"llama": cfg}, tmp_path,
        free_bytes=_plenty, status_fn=_not_running,
    )

    dest = ext_root / "gguf" / "qwen.gguf"
    assert isinstance(result, DemoteResult)
    assert result.verified is True
    assert result.reused_existing is False
    assert result.destination == dest
    assert result.bytes_reclaimed == len(original)
    # External copy is byte-identical and the local source is gone (space reclaimed).
    assert dest.read_bytes() == original
    assert not source.exists()
    # No staging artifact left behind.
    assert not (ext_root / "gguf" / "qwen.gguf.promote-tmp").exists()


def test_demote_directory_model_copies_tree_and_removes_local(tmp_path: Path) -> None:
    ext_root = tmp_path / "ext"
    _mount(ext_root)
    source = _write_model_dir(tmp_path / "local" / "mlx", "Qwen3")
    cfg = _inferencer("mlx-engine", tmp_path / "local" / "mlx", "mlx")

    result = demote_model(
        _local_model(source, name="Qwen3", store_format="mlx"),
        _external_cfg(ext_root), {"mlx-engine": cfg}, tmp_path,
        free_bytes=_plenty, status_fn=_not_running,
    )

    dest = ext_root / "mlx" / "Qwen3"
    assert result.verified is True
    assert (dest / "model.safetensors").exists()
    assert (dest / "tokenizer" / "tokenizer.json").exists()
    assert not source.exists()


def test_demote_reuses_verified_existing_external_copy(tmp_path: Path) -> None:
    ext_root = tmp_path / "ext"
    _mount(ext_root)
    payload = b"weights" * 1000
    source = _write_gguf(tmp_path / "local" / "gguf", "qwen", payload=payload)
    # An identical copy already lives on external (a present-in-both redundancy).
    existing = _write_gguf(ext_root / "gguf", "qwen", payload=payload)
    cfg = _inferencer("llama", tmp_path / "local" / "gguf")

    # A copy_fn that explodes proves no re-copy happens on the reuse path.
    def must_not_copy(src: Path, dst: Path) -> None:
        raise AssertionError("demote re-copied despite a verified external copy")

    result = demote_model(
        _local_model(source), _external_cfg(ext_root), {"llama": cfg}, tmp_path,
        free_bytes=_plenty, status_fn=_not_running, copy_fn=must_not_copy,
    )

    assert result.reused_existing is True
    assert result.verified is True
    assert result.bytes_reclaimed == len(payload)
    assert existing.read_bytes() == payload  # external untouched
    assert not source.exists()  # local reclaimed immediately


def test_demote_reuses_verified_existing_external_directory_copy(tmp_path: Path) -> None:
    # The reuse fast-path compares a *directory tree* (size + order-stable tree
    # hash), not just a single file: an identical multi-file model already on
    # external is reused with no re-copy, and the local tree is reclaimed.
    ext_root = tmp_path / "ext"
    _mount(ext_root)
    source = _write_model_dir(tmp_path / "local" / "mlx", "Qwen3")
    existing = _write_model_dir(ext_root / "mlx", "Qwen3")  # byte-identical tree
    cfg = _inferencer("mlx-engine", tmp_path / "local" / "mlx", "mlx")

    def must_not_copy(src: Path, dst: Path) -> None:
        raise AssertionError("demote re-copied despite a verified external tree")

    result = demote_model(
        _local_model(source, name="Qwen3", store_format="mlx"),
        _external_cfg(ext_root), {"mlx-engine": cfg}, tmp_path,
        free_bytes=_plenty, status_fn=_not_running, copy_fn=must_not_copy,
    )

    assert result.reused_existing is True
    assert result.verified is True
    assert result.bytes_reclaimed == _tree_size(existing)
    assert (existing / "tokenizer" / "tokenizer.json").exists()  # external untouched
    assert not source.exists()  # local tree reclaimed immediately


def test_demote_refuses_when_external_directory_copy_differs(tmp_path: Path) -> None:
    # A same-named external *tree* whose contents differ (one altered file) must
    # be refused, not clobbered: the tree hash diverges, so local and external
    # are both left exactly as they were.
    ext_root = tmp_path / "ext"
    _mount(ext_root)
    source = _write_model_dir(tmp_path / "local" / "mlx", "Qwen3")
    stale = _write_model_dir(ext_root / "mlx", "Qwen3")
    (stale / "model.safetensors").write_bytes(b"different-tensor" * 500)  # diverge
    cfg = _inferencer("mlx-engine", tmp_path / "local" / "mlx", "mlx")

    with pytest.raises(DemoteError, match="differs"):
        demote_model(
            _local_model(source, name="Qwen3", store_format="mlx"),
            _external_cfg(ext_root), {"mlx-engine": cfg}, tmp_path,
            free_bytes=_plenty, status_fn=_not_running,
        )

    assert (source / "model.safetensors").exists()  # local tree preserved
    assert (stale / "model.safetensors").read_bytes() == b"different-tensor" * 500


def test_demote_uses_default_seams_for_idle_local_engine(tmp_path: Path) -> None:
    # Exercise the real default free_bytes (shutil.disk_usage) and a real manager
    # status lookup with no state file (engine reported down) — no injection.
    ext_root = tmp_path / "ext"
    _mount(ext_root)
    source = _write_gguf(tmp_path / "local" / "gguf", "tiny", payload=b"x" * 64)
    cfg = _inferencer("llama", tmp_path / "local" / "gguf")

    result = demote_model(
        _local_model(source), _external_cfg(ext_root), {"llama": cfg}, tmp_path / "state",
    )

    assert (ext_root / "gguf" / "tiny.gguf").exists()
    assert not source.exists()
    assert result.verified is True


# --- demote_model: up-front refusals (local copy preserved) ----------------


def test_demote_refuses_when_external_offline(tmp_path: Path) -> None:
    ext_root = tmp_path / "ext"  # not mounted: no marker
    ext_root.mkdir()
    source = _write_gguf(tmp_path / "local" / "gguf", "qwen")
    cfg = _inferencer("llama", tmp_path / "local" / "gguf")

    with pytest.raises(DemoteError, match="offline"):
        demote_model(
            _local_model(source), _external_cfg(ext_root), {"llama": cfg}, tmp_path,
            free_bytes=_plenty, status_fn=_not_running,
        )
    assert source.exists()  # local copy preserved


def test_demote_refuses_when_source_missing(tmp_path: Path) -> None:
    ext_root = tmp_path / "ext"
    _mount(ext_root)
    missing = tmp_path / "local" / "gguf" / "ghost.gguf"
    cfg = _inferencer("llama", tmp_path / "local" / "gguf")
    model = LocalModel(
        inferencer="s", store_format="gguf", name="ghost", path=str(missing),
        size_bytes=10, quant=None, provider=None, identity=str(missing), tier="local",
    )

    with pytest.raises(DemoteError, match="missing"):
        demote_model(
            model, _external_cfg(ext_root), {"llama": cfg}, tmp_path,
            free_bytes=_plenty, status_fn=_not_running,
        )


def test_demote_refuses_when_serving_engine_running(tmp_path: Path) -> None:
    ext_root = tmp_path / "ext"
    _mount(ext_root)
    source = _write_gguf(tmp_path / "local" / "gguf", "qwen")
    cfg = _inferencer("llama", tmp_path / "local" / "gguf")

    with pytest.raises(DemoteError, match="is running"):
        demote_model(
            _local_model(source), _external_cfg(ext_root), {"llama": cfg}, tmp_path,
            free_bytes=_plenty, status_fn=_running,
        )
    assert source.exists()  # local copy preserved, nothing moved


def test_demote_refuses_on_insufficient_external_free_space(tmp_path: Path) -> None:
    ext_root = tmp_path / "ext"
    _mount(ext_root)
    source = _write_gguf(tmp_path / "local" / "gguf", "qwen", payload=b"w" * 10_000)
    cfg = _inferencer("llama", tmp_path / "local" / "gguf")

    with pytest.raises(DemoteError) as exc:
        demote_model(
            _local_model(source), _external_cfg(ext_root), {"llama": cfg}, tmp_path,
            free_bytes=lambda _p: 4_000, status_fn=_not_running,
        )

    message = str(exc.value)
    assert "insufficient external free space" in message
    assert "free at least" in message  # suggests an amount to free
    assert source.exists()  # local copy preserved
    assert not (ext_root / "gguf" / "qwen.gguf").exists()  # external untouched


def test_demote_refuses_when_external_copy_differs(tmp_path: Path) -> None:
    ext_root = tmp_path / "ext"
    _mount(ext_root)
    source = _write_gguf(tmp_path / "local" / "gguf", "qwen", payload=b"real" * 1000)
    # A same-named external copy that does NOT match the local source.
    stale = _write_gguf(ext_root / "gguf", "qwen", payload=b"junk" * 1000)
    cfg = _inferencer("llama", tmp_path / "local" / "gguf")

    with pytest.raises(DemoteError, match="differs"):
        demote_model(
            _local_model(source), _external_cfg(ext_root), {"llama": cfg}, tmp_path,
            free_bytes=_plenty, status_fn=_not_running,
        )
    assert source.exists()  # local copy preserved (no clobber, no delete)
    assert stale.read_bytes() == b"junk" * 1000  # external left exactly as it was


# --- demote_model: mid-copy aborts (local intact, no data loss) ------------


def test_demote_aborts_on_integrity_mismatch_local_intact(tmp_path: Path) -> None:
    ext_root = tmp_path / "ext"
    _mount(ext_root)
    source = _write_gguf(tmp_path / "local" / "gguf", "qwen", payload=b"real" * 1000)
    cfg = _inferencer("llama", tmp_path / "local" / "gguf")

    def corrupt_copy(src: Path, dst: Path) -> None:
        # Same byte length as the source, but different content -> hash mismatch.
        dst.write_bytes(b"junk" * 1000)

    with pytest.raises(DemoteError, match="integrity check failed"):
        demote_model(
            _local_model(source), _external_cfg(ext_root), {"llama": cfg}, tmp_path,
            free_bytes=_plenty, status_fn=_not_running, copy_fn=corrupt_copy,
        )

    assert not (ext_root / "gguf" / "qwen.gguf").exists()  # nothing published
    assert not (ext_root / "gguf" / "qwen.gguf.promote-tmp").exists()  # staging cleaned up
    assert source.read_bytes() == b"real" * 1000  # local source intact (no data loss)


def test_demote_aborts_and_cleans_up_on_io_error_local_intact(tmp_path: Path) -> None:
    ext_root = tmp_path / "ext"
    _mount(ext_root)
    source = _write_gguf(tmp_path / "local" / "gguf", "qwen")
    cfg = _inferencer("llama", tmp_path / "local" / "gguf")

    def failing_copy(src: Path, dst: Path) -> None:
        dst.write_bytes(b"partial")  # leave a partial staging artifact...
        raise OSError("disk exploded")  # ...then fail

    with pytest.raises(DemoteError, match="partial copy removed"):
        demote_model(
            _local_model(source), _external_cfg(ext_root), {"llama": cfg}, tmp_path,
            free_bytes=_plenty, status_fn=_not_running, copy_fn=failing_copy,
        )

    assert not (ext_root / "gguf" / "qwen.gguf").exists()
    assert not (ext_root / "gguf" / "qwen.gguf.promote-tmp").exists()  # partial removed
    assert source.exists()  # local source intact (no data loss)


def test_demote_clears_stale_staging_before_copy(tmp_path: Path) -> None:
    ext_root = tmp_path / "ext"
    _mount(ext_root)
    source = _write_gguf(tmp_path / "local" / "gguf", "qwen")
    dest_dir = ext_root / "gguf"
    dest_dir.mkdir(parents=True, exist_ok=True)
    # A leftover staging file from a previously-killed demote.
    (dest_dir / "qwen.gguf.promote-tmp").write_bytes(b"stale-garbage")
    cfg = _inferencer("llama", tmp_path / "local" / "gguf")

    result = demote_model(
        _local_model(source), _external_cfg(ext_root), {"llama": cfg}, tmp_path,
        free_bytes=_plenty, status_fn=_not_running,
    )

    assert result.verified is True
    assert (dest_dir / "qwen.gguf").exists()
    assert not source.exists()


# --- demote_model: source reclaim failure (external copy is safe) ----------


def test_demote_raises_when_local_source_cannot_be_reclaimed(tmp_path: Path) -> None:
    # The external copy is published and verified, but the local source cannot be
    # deleted (read-only parent dir). demote must NOT report success — that would
    # over-state reclaimed space and claim the local copy is gone when it is not.
    ext_root = tmp_path / "ext"
    _mount(ext_root)
    local_dir = tmp_path / "local" / "gguf"
    source = _write_gguf(local_dir, "qwen")
    cfg = _inferencer("llama", local_dir)
    local_dir.chmod(0o500)  # read + execute, no write -> unlink fails
    try:
        with pytest.raises(DemoteError, match="could not reclaim"):
            demote_model(
                _local_model(source), _external_cfg(ext_root), {"llama": cfg}, tmp_path,
                free_bytes=_plenty, status_fn=_not_running,
            )
    finally:
        local_dir.chmod(0o700)

    # External copy was still published and verified (no data loss either way).
    assert (ext_root / "gguf" / "qwen.gguf").exists()
    assert source.exists()  # local source still present, faithfully reported


def test_demote_reuse_raises_when_local_source_cannot_be_reclaimed(tmp_path: Path) -> None:
    # Same guarantee on the reuse short-circuit: a verified external copy already
    # exists, but the local source cannot be deleted -> refuse to claim success.
    ext_root = tmp_path / "ext"
    _mount(ext_root)
    payload = b"weights" * 1000
    local_dir = tmp_path / "local" / "gguf"
    source = _write_gguf(local_dir, "qwen", payload=payload)
    existing = _write_gguf(ext_root / "gguf", "qwen", payload=payload)
    cfg = _inferencer("llama", local_dir)
    local_dir.chmod(0o500)
    try:
        with pytest.raises(DemoteError, match="could not reclaim"):
            demote_model(
                _local_model(source), _external_cfg(ext_root), {"llama": cfg}, tmp_path,
                free_bytes=_plenty, status_fn=_not_running,
            )
    finally:
        local_dir.chmod(0o700)

    assert existing.read_bytes() == payload  # external untouched
    assert source.exists()  # local source still present
