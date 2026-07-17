"""Tests for the format-aware local model-store scanner (Story 11.1-001).

Covers the config surface (`model_store` + `format` on InferencerConfig) and the
two scan strategies (HF hub cache, Ollama blob store), plus the graceful no-rows
behaviour for missing/empty stores and `~` expansion against an injected home.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from local_code_bench.config import ConfigError, InferencerConfig, load_inferencers
from local_code_bench.inferencers.inventory import (
    StoredModel,
    expand_store_path,
    scan_inferencer,
    scan_inferencers,
)


# --- Config parsing -------------------------------------------------------


def _write(tmp_path, body: str):
    path = tmp_path / "inferencers.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def _base(name: str, **extra: Any) -> InferencerConfig:
    """Build a minimal app-lifecycle config, overriding store fields per test."""

    return InferencerConfig(
        name=name,
        lifecycle="app",
        detect_kind="app",
        detect_target=f"{name}.app",
        port=1234,
        health_url="http://127.0.0.1:{port}/v1/models",
        **extra,
    )


def test_config_parses_model_store_list_and_format(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
inferencers:
  - name: mlx-lm
    lifecycle: server
    detect:
      binary: mlx_lm.server
    port: 8080
    health_url: http://127.0.0.1:{port}/v1/models
    start: ["mlx_lm.server"]
    model_store: ["~/hub", "/opt/hf-cache"]
    format: hf-safetensors
""",
    )

    cfg = load_inferencers(path)["mlx-lm"]

    assert cfg.model_store == ("~/hub", "/opt/hf-cache")
    assert cfg.store_format == "hf-safetensors"


def test_config_parses_single_string_model_store(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
inferencers:
  - name: ollama
    lifecycle: server
    detect:
      binary: ollama
    port: 11434
    health_url: http://127.0.0.1:{port}/api/tags
    start: ["ollama", "serve"]
    model_store: ~/.ollama/models
    format: ollama
""",
    )

    cfg = load_inferencers(path)["ollama"]

    # A bare string is normalized to a single-element tuple.
    assert cfg.model_store == ("~/.ollama/models",)
    assert cfg.store_format == "ollama"


def test_config_defaults_store_to_none_when_absent(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
inferencers:
  - name: dflash
    lifecycle: server
    detect:
      binary: dflash
    port: 8000
    health_url: http://127.0.0.1:{port}/v1/models
    start: ["dflash", "serve"]
""",
    )

    cfg = load_inferencers(path)["dflash"]

    assert cfg.model_store is None
    assert cfg.store_format is None


def test_config_rejects_store_without_format(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
inferencers:
  - name: llama-cpp
    lifecycle: server
    detect:
      binary: llama-server
    port: 8081
    health_url: http://127.0.0.1:{port}/v1/models
    start: ["llama-server"]
    model_store: ~/models
""",
    )

    with pytest.raises(ConfigError, match="set together"):
        load_inferencers(path)


def test_config_rejects_format_without_store(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
inferencers:
  - name: mlx-lm
    lifecycle: server
    detect:
      binary: mlx_lm.server
    port: 8080
    health_url: http://127.0.0.1:{port}/v1/models
    start: ["mlx_lm.server"]
    format: hf-safetensors
""",
    )

    with pytest.raises(ConfigError, match="set together"):
        load_inferencers(path)


def test_config_rejects_unknown_format(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
inferencers:
  - name: llama-cpp
    lifecycle: server
    detect:
      binary: llama-server
    port: 8081
    health_url: http://127.0.0.1:{port}/v1/models
    start: ["llama-server"]
    model_store: ~/models
    format: onnx
""",
    )

    with pytest.raises(ConfigError, match="format must be one of"):
        load_inferencers(path)


def test_config_rejects_empty_model_store_list(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
inferencers:
  - name: llama-cpp
    lifecycle: server
    detect:
      binary: llama-server
    port: 8081
    health_url: http://127.0.0.1:{port}/v1/models
    start: ["llama-server"]
    model_store: []
    format: ollama
""",
    )

    with pytest.raises(ConfigError, match="non-empty list of paths"):
        load_inferencers(path)


def test_config_rejects_blank_model_store_entry(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
inferencers:
  - name: llama-cpp
    lifecycle: server
    detect:
      binary: llama-server
    port: 8081
    health_url: http://127.0.0.1:{port}/v1/models
    start: ["llama-server"]
    model_store: ["   "]
    format: ollama
""",
    )

    with pytest.raises(ConfigError, match="non-empty list of paths"):
        load_inferencers(path)


def test_config_rejects_non_string_format(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
inferencers:
  - name: llama-cpp
    lifecycle: server
    detect:
      binary: llama-server
    port: 8081
    health_url: http://127.0.0.1:{port}/v1/models
    start: ["llama-server"]
    model_store: ~/models
    format: 7
""",
    )

    with pytest.raises(ConfigError, match="format must be one of"):
        load_inferencers(path)


# --- Path expansion -------------------------------------------------------


def test_expand_store_path_uses_injected_home(tmp_path) -> None:
    assert expand_store_path("~/models", home=tmp_path) == tmp_path / "models"


def test_expand_store_path_bare_tilde(tmp_path) -> None:
    assert expand_store_path("~", home=tmp_path) == tmp_path


def test_expand_store_path_absolute_unchanged(tmp_path) -> None:
    absolute = tmp_path / "abs"
    assert expand_store_path(str(absolute), home=tmp_path) == absolute


def test_expand_store_path_falls_back_to_real_home(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: tmp_path))
    assert expand_store_path("~/x") == tmp_path / "x"


# --- HF hub cache strategy -------------------------------------------------


def test_scan_hf_cache_decodes_repo_names(tmp_path) -> None:
    store = tmp_path / "hub"
    repo = store / "models--mlx-community--Qwen2.5-Coder-7B" / "snapshots" / "abc"
    repo.mkdir(parents=True)
    (repo / "model.safetensors").write_bytes(b"w" * 50)
    # A non-models-- entry (e.g. a lock dir) is skipped.
    (store / ".locks").mkdir()

    cfg = _base("mlx-lm", model_store=(str(store),), store_format="hf-safetensors")
    models = scan_inferencer(cfg)

    assert len(models) == 1
    assert models[0].name == "mlx-community/Qwen2.5-Coder-7B"
    assert models[0].path == str(repo)
    assert models[0].size_bytes == 50


def test_scan_hf_cache_counts_symlinked_blob_once(tmp_path) -> None:
    store = tmp_path / "hub"
    repo = store / "models--mlx-community--Qwen3.6-27B-4bit"
    snapshot = repo / "snapshots" / "abc"
    blobs = repo / "blobs"
    snapshot.mkdir(parents=True)
    blobs.mkdir()
    blob = blobs / "weights"
    blob.write_bytes(b"w" * 50)
    (snapshot / "model.safetensors").symlink_to(blob)
    (snapshot / "duplicate.safetensors").symlink_to(blob)

    cfg = _base("mlx-lm", model_store=(str(store),), store_format="hf-safetensors")
    models = scan_inferencer(cfg)

    assert len(models) == 1
    assert models[0].size_bytes == 50


def test_scan_hf_cache_skips_incomplete_indexed_snapshot(tmp_path) -> None:
    store = tmp_path / "hub"
    repo = store / "models--mlx-community--Broken" / "snapshots" / "abc"
    repo.mkdir(parents=True)
    (repo / "model.safetensors.index.json").write_text(
        json.dumps(
            {
                "weight_map": {
                    "a": "model-00001-of-00002.safetensors",
                    "b": "model-00002-of-00002.safetensors",
                }
            }
        ),
        encoding="utf-8",
    )
    (repo / "model-00002-of-00002.safetensors").write_bytes(b"w" * 50)

    cfg = _base("mlx-lm", model_store=(str(store),), store_format="hf-safetensors")

    assert scan_inferencer(cfg) == []


def test_scan_hf_local_dir_decodes_provider_model_layout(tmp_path) -> None:
    store = tmp_path / "models" / "mlx"
    repo = store / "mlx-community" / "Ornith-1.0-9B-4bit"
    repo.mkdir(parents=True)
    (repo / "config.json").write_text("{}", encoding="utf-8")
    (repo / "model-00001-of-00002.safetensors").write_bytes(b"w" * 50)

    cfg = _base("mlx-lm", model_store=(str(store),), store_format="hf-safetensors")
    models = scan_inferencer(cfg)

    assert len(models) == 1
    assert models[0].name == "mlx-community/Ornith-1.0-9B-4bit"
    assert models[0].path == str(repo)
    assert models[0].size_bytes > 0


def test_scan_hf_cache_skips_snapshot_with_incomplete_blob(tmp_path) -> None:
    # Hub-cache in-flight download: an `.incomplete` marker lives under the repo's
    # blobs/ dir while some shard symlinks already resolve (and index.json may
    # still be missing). The authoritative marker means the snapshot is partial.
    store = tmp_path / "hub"
    repo = store / "models--mlx-community--Downloading"
    snapshot = repo / "snapshots" / "abc"
    blobs = repo / "blobs"
    snapshot.mkdir(parents=True)
    blobs.mkdir()
    shard = blobs / "sha256-shard"
    shard.write_bytes(b"w" * 50)
    (snapshot / "model-00001-of-00002.safetensors").symlink_to(shard)
    # The second shard (and the index.json) are still downloading.
    (blobs / "sha256-inflight.incomplete").write_bytes(b"partial")

    cfg = _base("mlx-lm", model_store=(str(store),), store_format="hf-safetensors")

    assert scan_inferencer(cfg) == []


def test_scan_hf_cache_skips_dangling_safetensors_symlink(tmp_path) -> None:
    # A no-index single-file model whose only *.safetensors entry is a dangling
    # symlink (target blob absent) must not be offered — a glob-name match is not
    # enough; the entry has to resolve.
    store = tmp_path / "hub"
    repo = store / "models--mlx-community--Dangling"
    snapshot = repo / "snapshots" / "abc"
    blobs = repo / "blobs"
    snapshot.mkdir(parents=True)
    blobs.mkdir()
    (snapshot / "model.safetensors").symlink_to(blobs / "sha256-absent")

    cfg = _base("mlx-lm", model_store=(str(store),), store_format="hf-safetensors")

    assert scan_inferencer(cfg) == []


def test_scan_hf_local_dir_skips_incomplete_download_marker(tmp_path) -> None:
    # Shelf/local-dir layout: an in-flight `hf download --local-dir` leaves an
    # `.incomplete` marker under .cache/huggingface/download/ even after a shard
    # has landed. Exclude the repo until the download finishes.
    store = tmp_path / "models" / "mlx"
    repo = store / "mlx-community" / "Partial-9B-4bit"
    repo.mkdir(parents=True)
    (repo / "config.json").write_text("{}", encoding="utf-8")
    (repo / "model-00001-of-00002.safetensors").write_bytes(b"w" * 50)
    download_cache = repo / ".cache" / "huggingface" / "download"
    download_cache.mkdir(parents=True)
    (download_cache / "model-00002-of-00002.safetensors.incomplete").write_bytes(b"x")

    cfg = _base("mlx-lm", model_store=(str(store),), store_format="hf-safetensors")

    assert scan_inferencer(cfg) == []


# --- Ollama blob store strategy --------------------------------------------


def _write_ollama_model(store, model: str, tag: str, blob_sizes: dict[str, int]):
    """Create an Ollama manifest referencing blobs of the given sizes."""

    manifest_dir = store / "manifests" / "registry.ollama.ai" / "library" / model
    manifest_dir.mkdir(parents=True, exist_ok=True)
    blobs = store / "blobs"
    blobs.mkdir(exist_ok=True)
    layers = []
    for digest, size in blob_sizes.items():
        (blobs / digest.replace(":", "-")).write_bytes(b"q" * size)
        layers.append({"digest": digest, "size": size})
    (manifest_dir / tag).write_text(json.dumps({"layers": layers}), encoding="utf-8")


def test_scan_ollama_sums_blob_sizes(tmp_path) -> None:
    store = tmp_path / "ollama"
    _write_ollama_model(
        store,
        "llama3.1",
        "8b",
        {"sha256:aaa": 30, "sha256:bbb": 12},
    )

    cfg = _base("ollama", model_store=(str(store),), store_format="ollama")
    models = scan_inferencer(cfg)

    assert len(models) == 1
    assert models[0].name == "llama3.1:8b"
    assert models[0].size_bytes == 42
    assert models[0].store_format == "ollama"


def test_scan_ollama_excludes_model_with_missing_layer_blob(tmp_path) -> None:
    # A manifest that references a layer blob still absent on disk is a partial
    # pull: exclude it entirely rather than reporting the declared phantom size
    # (loading it would hang the engine).
    store = tmp_path / "ollama"
    manifest_dir = store / "manifests" / "registry.ollama.ai" / "library" / "qwen"
    manifest_dir.mkdir(parents=True)
    blobs = store / "blobs"
    blobs.mkdir()
    (blobs / "sha256-layer").write_bytes(b"l" * 7)
    doc = {
        "layers": [
            {"digest": "sha256:layer", "size": 7},
            # Blob absent on disk -> the whole model is a partial pull.
            {"digest": "sha256:missing", "size": 100},
        ],
        "config": {"digest": "sha256:cfg", "size": 3},
    }
    (manifest_dir / "0.5b").write_text(json.dumps(doc), encoding="utf-8")
    (blobs / "sha256-cfg").write_bytes(b"c" * 3)

    cfg = _base("ollama", model_store=(str(store),), store_format="ollama")

    assert scan_inferencer(cfg) == []


def test_scan_ollama_excludes_model_with_missing_config_blob(tmp_path) -> None:
    # The config blob is referenced too: a missing config blob is just as much a
    # partial pull as a missing layer, so the model is excluded.
    store = tmp_path / "ollama"
    manifest_dir = store / "manifests" / "registry.ollama.ai" / "library" / "gemma"
    manifest_dir.mkdir(parents=True)
    blobs = store / "blobs"
    blobs.mkdir()
    (blobs / "sha256-weights").write_bytes(b"w" * 20)
    doc = {
        "layers": [{"digest": "sha256:weights", "size": 20}],
        "config": {"digest": "sha256:cfg-missing", "size": 3},
    }
    (manifest_dir / "2b").write_text(json.dumps(doc), encoding="utf-8")

    cfg = _base("ollama", model_store=(str(store),), store_format="ollama")

    assert scan_inferencer(cfg) == []


def test_scan_ollama_skips_malformed_manifest(tmp_path) -> None:
    store = tmp_path / "ollama"
    manifest_dir = store / "manifests" / "registry.ollama.ai" / "library" / "broken"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "bad").write_text("{not json", encoding="utf-8")
    (manifest_dir / "list-doc").write_text("[]", encoding="utf-8")  # not a dict
    (manifest_dir / "no-layers").write_text('{"config": {}}', encoding="utf-8")

    cfg = _base("ollama", model_store=(str(store),), store_format="ollama")

    assert scan_inferencer(cfg) == []


def test_scan_ollama_no_manifests_dir(tmp_path) -> None:
    store = tmp_path / "ollama"
    store.mkdir()  # exists but has no manifests/ subdir

    cfg = _base("ollama", model_store=(str(store),), store_format="ollama")

    assert scan_inferencer(cfg) == []


# --- No-rows / resilience behaviour ---------------------------------------


def test_scan_returns_empty_when_no_store_configured() -> None:
    cfg = _base("dflash")
    assert scan_inferencer(cfg) == []


def test_scan_missing_store_dir_yields_no_rows(tmp_path) -> None:
    cfg = _base(
        "mlx-lm",
        model_store=(str(tmp_path / "does-not-exist"),),
        store_format="hf-safetensors",
    )
    assert scan_inferencer(cfg) == []


def test_scan_store_path_is_a_file_yields_no_rows(tmp_path) -> None:
    not_a_dir = tmp_path / "afile"
    not_a_dir.write_text("oops", encoding="utf-8")
    cfg = _base("mlx-lm", model_store=(str(not_a_dir),), store_format="hf-safetensors")
    assert scan_inferencer(cfg) == []


def test_scan_expands_tilde_against_home(tmp_path) -> None:
    store = tmp_path / "models"
    _write_ollama_model(store, "m", "latest", {"sha256:aaa": 4})

    cfg = _base("ollama", model_store=("~/models",), store_format="ollama")
    models = scan_inferencer(cfg, home=tmp_path)

    assert [m.name for m in models] == ["m:latest"]


def test_scan_multiple_store_paths_are_merged(tmp_path) -> None:
    store_a = tmp_path / "a"
    store_b = tmp_path / "b"
    _write_ollama_model(store_a, "one", "latest", {"sha256:aaa": 3})
    _write_ollama_model(store_b, "two", "latest", {"sha256:bbb": 3})

    cfg = _base(
        "ollama",
        model_store=(str(store_a), str(store_b)),
        store_format="ollama",
    )
    models = scan_inferencer(cfg)

    assert sorted(m.name for m in models) == ["one:latest", "two:latest"]


def test_scan_inferencers_flattens_across_configs(tmp_path) -> None:
    hub_store = tmp_path / "hub"
    repo = hub_store / "models--org--model" / "snapshots" / "s"
    repo.mkdir(parents=True)
    (repo / "w.safetensors").write_bytes(b"y" * 8)
    ollama_store = tmp_path / "ollama"
    _write_ollama_model(ollama_store, "a", "latest", {"sha256:aaa": 2})

    configs = [
        _base("ollama", model_store=(str(ollama_store),), store_format="ollama"),
        _base("mlx-lm", model_store=(str(hub_store),), store_format="hf-safetensors"),
        _base("no-store"),  # no store -> contributes nothing
    ]
    models = scan_inferencers(configs)

    assert {(m.inferencer, m.name) for m in models} == {
        ("ollama", "a:latest"),
        ("mlx-lm", "org/model"),
    }


def test_scan_hf_cache_ignores_stray_files(tmp_path) -> None:
    store = tmp_path / "hub"
    repo = store / "models--org--repo" / "snapshots" / "x"
    repo.mkdir(parents=True)
    (repo / "model.safetensors").write_bytes(b"w" * 9)
    # A loose file sitting beside the repo dirs must be skipped by the dir walk.
    (store / "version.txt").write_text("1", encoding="utf-8")

    cfg = _base("mlx-lm", model_store=(str(store),), store_format="hf-safetensors")
    models = scan_inferencer(cfg)

    assert [m.name for m in models] == ["org/repo"]


def test_scan_ollama_manifest_at_root_uses_filename(tmp_path) -> None:
    store = tmp_path / "ollama"
    manifests = store / "manifests"
    manifests.mkdir(parents=True)
    # A manifest directly under manifests/ has no namespace/model parts.
    (manifests / "solo").write_text('{"layers": []}', encoding="utf-8")

    cfg = _base("ollama", model_store=(str(store),), store_format="ollama")
    models = scan_inferencer(cfg)

    assert [m.name for m in models] == ["solo"]
    assert models[0].size_bytes == 0


def test_scan_ollama_ignores_non_dict_and_undigested_layers(tmp_path) -> None:
    # A complete pull whose manifest also carries a non-dict layer entry and a
    # dict layer without a digest: both are ignored for presence and sizing, and
    # the present blobs are summed. The model is still offered.
    store = tmp_path / "ollama"
    manifest_dir = store / "manifests" / "library" / "qwen"
    manifest_dir.mkdir(parents=True)
    blobs = store / "blobs"
    blobs.mkdir()
    (blobs / "sha256-weights").write_bytes(b"w" * 5)
    (blobs / "sha256-cfg").write_bytes(b"c" * 2)
    doc = {
        "layers": [
            "not-a-dict",  # skipped
            {"mediaType": "application/vnd.ollama.image.license"},  # no digest -> skipped
            {"digest": "sha256:weights", "size": 5},
        ],
        "config": {"digest": "sha256:cfg", "size": 2},
    }
    (manifest_dir / "7b").write_text(json.dumps(doc), encoding="utf-8")

    cfg = _base("ollama", model_store=(str(store),), store_format="ollama")
    models = scan_inferencer(cfg)

    assert models[0].name == "qwen:7b"
    # 5 (weights) + 2 (config); the non-dict / undigested entries add nothing.
    assert models[0].size_bytes == 7


def test_iter_dirs_swallows_oserror(monkeypatch, tmp_path) -> None:
    from local_code_bench.inferencers import inventory

    def boom(self):
        raise PermissionError("denied")

    monkeypatch.setattr("pathlib.Path.iterdir", boom)
    assert list(inventory._iter_dirs(tmp_path)) == []


def test_file_size_swallows_oserror(monkeypatch, tmp_path) -> None:
    from local_code_bench.inferencers import inventory

    target = tmp_path / "f"
    target.write_bytes(b"x")

    def boom(self):
        raise OSError("stat failed")

    monkeypatch.setattr("pathlib.Path.stat", boom)
    assert inventory._file_size(target) == 0


def test_default_config_carries_store_metadata() -> None:
    inferencers = load_inferencers("configs/inferencers.yaml")

    # mlx-lm scans the HuggingFace hub cache plus local-dir model shelf entries;
    # ollama uses its blob store.
    assert inferencers["mlx-lm"].model_store == (
        "~/.cache/huggingface/hub",
        "~/.cache/model-shelf/models/mlx",
    )
    assert inferencers["mlx-lm"].store_format == "hf-safetensors"
    assert inferencers["ollama"].model_store == ("~/.ollama/models",)
    assert inferencers["ollama"].store_format == "ollama"


def test_default_config_store_paths_are_scannable(tmp_path) -> None:
    # Every declared store points under home and yields no rows on a clean tree
    # (the scanner never raises for an absent store).
    inferencers = load_inferencers("configs/inferencers.yaml")
    stored = [cfg for cfg in inferencers.values() if cfg.model_store]

    assert stored  # at least the MLX/ollama engines declare stores
    for cfg in stored:
        # Pointed at an empty home, no model store exists -> no rows, no error.
        assert scan_inferencer(cfg, home=tmp_path) == []


def test_stored_model_is_frozen() -> None:
    model = StoredModel(
        inferencer="x",
        store_format="ollama",
        name="m",
        path="/tmp/m",
        size_bytes=1,
    )
    with pytest.raises(AttributeError):
        model.size_bytes = 2  # type: ignore[misc]
