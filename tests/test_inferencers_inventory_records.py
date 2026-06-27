"""Tests for normalized LocalModel records with provenance (Story 11.2-001).

Covers turning a raw :class:`StoredModel` (Story 11.1-001) into a frozen
:class:`LocalModel` that carries quant/provider provenance and a symlink-stable
content identity, plus the standalone provenance parsers and the graceful
degrade-to-null behaviour when nothing is recognisable.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from local_code_bench.inferencers.inventory import (
    LocalModel,
    StoredModel,
    content_identity,
    normalize,
    normalize_all,
    parse_provider,
    parse_quant,
)


# --- Quant parsing --------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Qwen2.5-Coder-7B-Q4_K_M", "Q4_K_M"),
        ("Llama-3.2-3B-IQ3_XXS", "IQ3_XXS"),
        ("model-Q8_0.gguf", "Q8_0"),
        ("Mistral-7B-Q5_K_S", "Q5_K_S"),
        ("Phi-3-IQ2_M", "IQ2_M"),
        ("mlx-community/Llama-3.2-3B-4bit", "4bit"),
        ("Qwen-7B-8-bit", "8-bit"),
        ("BigModel-F16", "F16"),
        ("BigModel-BF16", "BF16"),
    ],
)
def test_parse_quant_extracts_known_tokens(text: str, expected: str) -> None:
    assert parse_quant(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Qwen2.5-Coder-7B",  # base model, no quant token
        "Llama-3.2-3B-Instruct",
        "",
        "just-a-name",
    ],
)
def test_parse_quant_returns_none_when_absent(text: str) -> None:
    assert parse_quant(text) is None


def test_parse_quant_does_not_match_param_count_or_version() -> None:
    # "7B" parameter size and "2.5" version must not be read as quant tokens.
    assert parse_quant("Qwen2.5-Coder-7B-Instruct") is None


# --- Provider parsing -----------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/home/fx/models/unsloth/Qwen2.5-Coder-7B-Q4_K_M.gguf", "unsloth"),
        ("bartowski/Llama-3.2-3B-IQ3_XXS.gguf", "bartowski"),
        ("models--mlx-community--Qwen2.5-Coder-7B", "mlx-community"),
        ("models--lmstudio-community--Phi-3", "lmstudio-community"),
        ("TheBloke/Mistral-7B-GGUF", "TheBloke"),
        ("UNSLOTH/whatever", "unsloth"),  # case-insensitive match
    ],
)
def test_parse_provider_extracts_known_publishers(text: str, expected: str) -> None:
    assert parse_provider(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "/home/fx/models/Qwen2.5-Coder-7B-Q4_K_M.gguf",
        "some-unknown-org/model",
        "",
    ],
)
def test_parse_provider_returns_none_when_absent(text: str) -> None:
    assert parse_provider(text) is None


# --- Normalization --------------------------------------------------------


def _stored(tmp_path: Path, *, name: str, store_format: str = "gguf") -> StoredModel:
    target = tmp_path / f"{name}.gguf"
    target.write_bytes(b"x" * 12)
    return StoredModel(
        inferencer="llama-cpp",
        store_format=store_format,  # type: ignore[arg-type]
        name=name,
        path=str(target),
        size_bytes=12,
    )


def test_normalize_carries_all_provenance_fields(tmp_path) -> None:
    store = tmp_path / "unsloth"
    store.mkdir()
    target = store / "Qwen2.5-Coder-7B-Q4_K_M.gguf"
    target.write_bytes(b"x" * 12)
    stored = StoredModel(
        inferencer="llama-cpp",
        store_format="gguf",
        name="Qwen2.5-Coder-7B-Q4_K_M",
        path=str(target),
        size_bytes=12,
    )

    model = normalize(stored)

    assert isinstance(model, LocalModel)
    assert model.inferencer == "llama-cpp"
    assert model.store_format == "gguf"
    assert model.name == "Qwen2.5-Coder-7B-Q4_K_M"
    assert model.path == str(target)
    assert model.size_bytes == 12
    assert model.quant == "Q4_K_M"
    assert model.provider == "unsloth"  # parsed from the parent dir in the path
    assert model.identity == os.path.realpath(str(target))


def test_normalize_degrades_quant_and_provider_to_none(tmp_path) -> None:
    stored = _stored(tmp_path, name="Qwen2.5-Coder-7B-Instruct")

    model = normalize(stored)

    assert model.quant is None
    assert model.provider is None
    # A model with no recognisable provenance still normalizes (no failure).
    assert model.identity == os.path.realpath(stored.path)


def test_normalize_identity_is_realpath_stable_across_scans(tmp_path) -> None:
    stored = _stored(tmp_path, name="model-Q4_K_M")

    first = normalize(stored)
    second = normalize(stored)

    assert first.identity == second.identity == os.path.realpath(stored.path)


def test_normalize_identity_follows_symlinks(tmp_path) -> None:
    real = tmp_path / "real" / "model-Q4_K_M.gguf"
    real.parent.mkdir()
    real.write_bytes(b"x" * 4)
    link = tmp_path / "link.gguf"
    link.symlink_to(real)

    stored = StoredModel(
        inferencer="llama-cpp",
        store_format="gguf",
        name="link",
        path=str(link),
        size_bytes=4,
    )

    # The symlinked path resolves to the same identity as the real file.
    assert normalize(stored).identity == os.path.realpath(str(real))


def test_normalize_quant_from_name_falls_back_to_path(tmp_path) -> None:
    # The quant lives in the directory name, not the model `name` field.
    store = tmp_path / "Q5_K_M-build"
    store.mkdir()
    target = store / "weights.safetensors"
    target.write_bytes(b"w" * 6)
    stored = StoredModel(
        inferencer="mlx-lm",
        store_format="mlx",
        name="org/weights",
        path=str(target),
        size_bytes=6,
    )

    assert normalize(stored).quant == "Q5_K_M"


def test_local_model_is_frozen(tmp_path) -> None:
    model = normalize(_stored(tmp_path, name="m"))
    with pytest.raises(AttributeError):
        model.size_bytes = 99  # type: ignore[misc]


def test_normalize_all_maps_every_stored_model(tmp_path) -> None:
    a = _stored(tmp_path, name="a-Q4_K_M")
    b = _stored(tmp_path, name="b-IQ3_XXS")

    models = normalize_all([a, b])

    assert [m.name for m in models] == ["a-Q4_K_M", "b-IQ3_XXS"]
    assert [m.quant for m in models] == ["Q4_K_M", "IQ3_XXS"]
    assert all(isinstance(m, LocalModel) for m in models)


# --- Ollama content identity ----------------------------------------------


def test_content_identity_uses_ollama_model_blob_sha(tmp_path) -> None:
    store = tmp_path / "ollama"
    manifest_dir = store / "manifests" / "registry.ollama.ai" / "library" / "llama3.1"
    manifest_dir.mkdir(parents=True)
    manifest = manifest_dir / "8b"
    doc = {
        "layers": [
            {
                "digest": "sha256:weights",
                "size": 30,
                "mediaType": "application/vnd.ollama.image.model",
            },
            {
                "digest": "sha256:params",
                "size": 5,
                "mediaType": "application/vnd.ollama.image.params",
            },
        ],
    }
    manifest.write_text(json.dumps(doc), encoding="utf-8")

    stored = StoredModel(
        inferencer="ollama",
        store_format="ollama",
        name="llama3.1:8b",
        path=str(manifest),
        size_bytes=35,
    )

    # Identity is the model-weights blob digest, not the manifest realpath.
    assert content_identity(stored) == "sha256:weights"
    assert normalize(stored).identity == "sha256:weights"


def test_content_identity_ollama_falls_back_to_realpath(tmp_path) -> None:
    # A manifest without a model-weights layer (or unreadable) degrades to realpath.
    store = tmp_path / "ollama"
    manifest_dir = store / "manifests" / "library" / "qwen"
    manifest_dir.mkdir(parents=True)
    manifest = manifest_dir / "7b"
    manifest.write_text(json.dumps({"layers": [{"digest": "sha256:x"}]}), encoding="utf-8")

    stored = StoredModel(
        inferencer="ollama",
        store_format="ollama",
        name="qwen:7b",
        path=str(manifest),
        size_bytes=0,
    )

    assert content_identity(stored) == os.path.realpath(str(manifest))


def test_content_identity_ollama_non_dict_manifest_is_realpath(tmp_path) -> None:
    store = tmp_path / "ollama" / "manifests" / "library" / "weird"
    store.mkdir(parents=True)
    manifest = store / "1b"
    manifest.write_text("[]", encoding="utf-8")  # valid JSON, but not a dict

    stored = StoredModel(
        inferencer="ollama",
        store_format="ollama",
        name="weird:1b",
        path=str(manifest),
        size_bytes=0,
    )

    assert content_identity(stored) == os.path.realpath(str(manifest))


def test_content_identity_ollama_skips_non_dict_layer(tmp_path) -> None:
    store = tmp_path / "ollama" / "manifests" / "library" / "mixed"
    store.mkdir(parents=True)
    manifest = store / "3b"
    doc = {
        "layers": [
            "not-a-dict",  # skipped without raising
            {"digest": "sha256:w", "mediaType": "application/vnd.ollama.image.model"},
        ]
    }
    manifest.write_text(json.dumps(doc), encoding="utf-8")

    stored = StoredModel(
        inferencer="ollama",
        store_format="ollama",
        name="mixed:3b",
        path=str(manifest),
        size_bytes=0,
    )

    assert content_identity(stored) == "sha256:w"


def test_content_identity_ollama_missing_manifest_is_realpath(tmp_path) -> None:
    gone = tmp_path / "ollama" / "manifests" / "x" / "y"
    stored = StoredModel(
        inferencer="ollama",
        store_format="ollama",
        name="x:y",
        path=str(gone),
        size_bytes=0,
    )

    # realpath of a non-existent path does not raise and stays deterministic.
    assert content_identity(stored) == os.path.realpath(str(gone))
