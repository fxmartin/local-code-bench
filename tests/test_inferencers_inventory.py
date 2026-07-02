"""Tests for the format-aware local model-store scanner (Story 11.1-001).

Covers the config surface (`model_store` + `format` on InferencerConfig) and the
four scan strategies, plus the graceful no-rows behaviour for missing/empty
stores and `~` expansion against an injected home.
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
  - name: llama-cpp
    lifecycle: server
    detect:
      binary: llama-server
    port: 8081
    health_url: http://127.0.0.1:{port}/v1/models
    start: ["llama-server"]
    model_store: ["~/models", "/opt/gguf"]
    format: gguf
""",
    )

    cfg = load_inferencers(path)["llama-cpp"]

    assert cfg.model_store == ("~/models", "/opt/gguf")
    assert cfg.store_format == "gguf"


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
  - name: llama-cpp
    lifecycle: server
    detect:
      binary: llama-server
    port: 8081
    health_url: http://127.0.0.1:{port}/v1/models
    start: ["llama-server"]
    format: gguf
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
    format: gguf
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
    format: gguf
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


# --- GGUF strategy --------------------------------------------------------


def test_scan_gguf_lists_files_recursively(tmp_path) -> None:
    store = tmp_path / "gguf"
    (store / "nested").mkdir(parents=True)
    top = store / "Qwen2.5-Coder-Q4_K_M.gguf"
    top.write_bytes(b"x" * 10)
    nested = store / "nested" / "Llama-3.2-3B-IQ3_XXS.gguf"
    nested.write_bytes(b"y" * 20)
    (store / "README.md").write_text("not a model", encoding="utf-8")

    cfg = _base("llama-cpp", model_store=(str(store),), store_format="gguf")
    models = scan_inferencer(cfg)

    by_name = {m.name: m for m in models}
    assert set(by_name) == {"Qwen2.5-Coder-Q4_K_M", "Llama-3.2-3B-IQ3_XXS"}
    assert by_name["Qwen2.5-Coder-Q4_K_M"].size_bytes == 10
    assert by_name["Llama-3.2-3B-IQ3_XXS"].size_bytes == 20
    assert all(m.store_format == "gguf" and m.inferencer == "llama-cpp" for m in models)


def test_scan_gguf_counts_split_shards_once(tmp_path) -> None:
    store = tmp_path / "gguf"
    store.mkdir()
    (store / "BigModel-00001-of-00003.gguf").write_bytes(b"a" * 5)
    (store / "BigModel-00002-of-00003.gguf").write_bytes(b"b" * 5)
    (store / "BigModel-00003-of-00003.gguf").write_bytes(b"c" * 5)

    cfg = _base("llama-cpp", model_store=(str(store),), store_format="gguf")
    models = scan_inferencer(cfg)

    assert [m.name for m in models] == ["BigModel-00001-of-00003"]


# --- MLX (publisher/model directory) strategy ------------------------------


def test_scan_mlx_lists_publisher_model_dirs(tmp_path) -> None:
    store = tmp_path / "mlx"
    model_dir = store / "mlx-community" / "Llama-3.2-3B-4bit"
    model_dir.mkdir(parents=True)
    (model_dir / "model.safetensors").write_bytes(b"z" * 100)
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    # A publisher dir with no safetensors model is ignored.
    (store / "empty-publisher").mkdir()

    cfg = _base("lm-studio", model_store=(str(store),), store_format="mlx")
    models = scan_inferencer(cfg)

    assert len(models) == 1
    assert models[0].name == "mlx-community/Llama-3.2-3B-4bit"
    assert models[0].size_bytes == 102  # 100 bytes weights + 2 bytes config
    assert models[0].store_format == "mlx"


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
    assert models[0].size_bytes == 50


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


def test_scan_ollama_includes_config_blob_and_declared_size(tmp_path) -> None:
    store = tmp_path / "ollama"
    manifest_dir = store / "manifests" / "registry.ollama.ai" / "library" / "qwen"
    manifest_dir.mkdir(parents=True)
    blobs = store / "blobs"
    blobs.mkdir()
    (blobs / "sha256-layer").write_bytes(b"l" * 7)
    doc = {
        "layers": [
            {"digest": "sha256:layer", "size": 7},
            # Missing blob falls back to the declared size.
            {"digest": "sha256:missing", "size": 100},
        ],
        "config": {"digest": "sha256:cfg", "size": 3},
    }
    (manifest_dir / "0.5b").write_text(json.dumps(doc), encoding="utf-8")
    (blobs / "sha256-cfg").write_bytes(b"c" * 3)

    cfg = _base("ollama", model_store=(str(store),), store_format="ollama")
    models = scan_inferencer(cfg)

    # 7 (on-disk layer) + 100 (declared, blob missing) + 3 (config blob) = 110.
    assert models[0].size_bytes == 110


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
        "llama-cpp",
        model_store=(str(tmp_path / "does-not-exist"),),
        store_format="gguf",
    )
    assert scan_inferencer(cfg) == []


def test_scan_store_path_is_a_file_yields_no_rows(tmp_path) -> None:
    not_a_dir = tmp_path / "afile"
    not_a_dir.write_text("oops", encoding="utf-8")
    cfg = _base("llama-cpp", model_store=(str(not_a_dir),), store_format="gguf")
    assert scan_inferencer(cfg) == []


def test_scan_expands_tilde_against_home(tmp_path) -> None:
    store = tmp_path / "models"
    store.mkdir()
    (store / "m.gguf").write_bytes(b"x" * 4)

    cfg = _base("llama-cpp", model_store=("~/models",), store_format="gguf")
    models = scan_inferencer(cfg, home=tmp_path)

    assert [m.name for m in models] == ["m"]


def test_scan_multiple_store_paths_are_merged(tmp_path) -> None:
    store_a = tmp_path / "a"
    store_b = tmp_path / "b"
    store_a.mkdir()
    store_b.mkdir()
    (store_a / "one.gguf").write_bytes(b"1" * 3)
    (store_b / "two.gguf").write_bytes(b"2" * 3)

    cfg = _base(
        "llama-cpp",
        model_store=(str(store_a), str(store_b)),
        store_format="gguf",
    )
    models = scan_inferencer(cfg)

    assert sorted(m.name for m in models) == ["one", "two"]


def test_scan_inferencers_flattens_across_configs(tmp_path) -> None:
    gguf_store = tmp_path / "gguf"
    gguf_store.mkdir()
    (gguf_store / "a.gguf").write_bytes(b"x" * 2)
    mlx_store = tmp_path / "mlx" / "org" / "model"
    mlx_store.mkdir(parents=True)
    (mlx_store / "w.safetensors").write_bytes(b"y" * 8)

    configs = [
        _base("llama-cpp", model_store=(str(gguf_store),), store_format="gguf"),
        _base("mlx-lm", model_store=(str(tmp_path / "mlx"),), store_format="mlx"),
        _base("dflash"),  # no store -> contributes nothing
    ]
    models = scan_inferencers(configs)

    assert {(m.inferencer, m.name) for m in models} == {
        ("llama-cpp", "a"),
        ("mlx-lm", "org/model"),
    }


def test_scan_gguf_ignores_directory_named_like_a_model(tmp_path) -> None:
    store = tmp_path / "gguf"
    store.mkdir()
    # A directory whose name ends in .gguf must not be counted as a model file.
    (store / "weird.gguf").mkdir()
    (store / "real.gguf").write_bytes(b"x" * 4)

    cfg = _base("llama-cpp", model_store=(str(store),), store_format="gguf")
    models = scan_inferencer(cfg)

    assert [m.name for m in models] == ["real"]


def test_scan_mlx_skips_model_dir_without_safetensors(tmp_path) -> None:
    store = tmp_path / "mlx"
    publisher = store / "org"
    (publisher / "weights-model").mkdir(parents=True)
    (publisher / "weights-model" / "m.safetensors").write_bytes(b"w" * 6)
    # Sibling model dir with only metadata is ignored (no safetensors).
    (publisher / "config-only").mkdir()
    (publisher / "config-only" / "config.json").write_text("{}", encoding="utf-8")

    cfg = _base("lm-studio", model_store=(str(store),), store_format="mlx")
    models = scan_inferencer(cfg)

    assert [m.name for m in models] == ["org/weights-model"]


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


def test_scan_ollama_ignores_non_dict_layer_and_missing_blob(tmp_path) -> None:
    store = tmp_path / "ollama"
    manifest_dir = store / "manifests" / "library" / "qwen"
    manifest_dir.mkdir(parents=True)
    (store / "blobs").mkdir()
    doc = {
        "layers": [
            "not-a-dict",  # skipped
            {"digest": "sha256:gone"},  # missing blob, no declared size -> +0
            {"digest": "sha256:gone2", "size": 5},  # missing blob -> declared 5
        ]
    }
    (manifest_dir / "7b").write_text(json.dumps(doc), encoding="utf-8")

    cfg = _base("ollama", model_store=(str(store),), store_format="ollama")
    models = scan_inferencer(cfg)

    assert models[0].name == "qwen:7b"
    assert models[0].size_bytes == 5


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

    # MLX servers share the HuggingFace hub cache; ollama uses its blob store.
    assert inferencers["dflash"].model_store == ("~/.cache/huggingface/hub",)
    assert inferencers["dflash"].store_format == "hf-safetensors"
    assert inferencers["omlx"].model_store == ("~/.omlx/models",)
    assert inferencers["omlx"].store_format == "mlx"
    assert inferencers["ollama"].model_store == ("~/.ollama/models",)
    assert inferencers["ollama"].store_format == "ollama"
    assert inferencers["gpt4all"].store_format == "gguf"
    # llama-cpp has no fixed store dir and must stay store-less.
    assert inferencers["llama-cpp"].model_store is None
    assert inferencers["llama-cpp"].store_format is None


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
        store_format="gguf",
        name="m",
        path="/tmp/m.gguf",
        size_bytes=1,
    )
    with pytest.raises(AttributeError):
        model.size_bytes = 2  # type: ignore[misc]
