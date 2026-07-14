"""Config parsing for the Epic-12 external (second-tier) model repository."""

from __future__ import annotations

import pytest

from local_code_bench.config import (
    DEFAULT_EXTERNAL_SUBPATHS,
    DEFAULT_VOLUME_MARKER,
    STORE_FORMATS,
    ConfigError,
    ExternalRepoConfig,
    load_external_repo,
)


def _write(tmp_path, body: str):
    path = tmp_path / "inferencers.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_load_external_repo_absent_is_none(tmp_path) -> None:
    # A single-tier config (no external_repo key) stays valid: returns None.
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

    assert load_external_repo(path) is None


def test_load_external_repo_minimal_uses_defaults(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
external_repo:
  root: /Volumes/ModelsSSD/local-code-bench
""",
    )

    cfg = load_external_repo(path)

    assert isinstance(cfg, ExternalRepoConfig)
    assert cfg.root == "/Volumes/ModelsSSD/local-code-bench"
    assert cfg.volume_marker == DEFAULT_VOLUME_MARKER
    # Per-format subpaths mirror the local store layout (one subdir per format).
    assert cfg.subpaths == dict(DEFAULT_EXTERNAL_SUBPATHS)
    assert set(cfg.subpaths) == set(STORE_FORMATS)


def test_load_external_repo_preserves_tilde_for_later_expansion(tmp_path) -> None:
    # The raw path keeps `~`; expansion happens at availability-check time.
    path = _write(
        tmp_path,
        """
external_repo:
  root: ~/ExternalModels
""",
    )

    cfg = load_external_repo(path)

    assert cfg is not None
    assert cfg.root == "~/ExternalModels"


def test_load_external_repo_custom_marker_and_subpaths(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
external_repo:
  root: /Volumes/SSD/repo
  volume_marker: .my-repo-marker
  subpaths:
    hf-safetensors: hf-cache
""",
    )

    cfg = load_external_repo(path)

    assert cfg is not None
    assert cfg.volume_marker == ".my-repo-marker"
    # Overridden formats take the new value; unspecified formats keep the default.
    assert cfg.subpaths["hf-safetensors"] == "hf-cache"
    assert cfg.subpaths["ollama"] == DEFAULT_EXTERNAL_SUBPATHS["ollama"]


def test_load_external_repo_rejects_non_mapping(tmp_path) -> None:
    path = _write(tmp_path, "external_repo: just-a-string\n")

    with pytest.raises(ConfigError, match="external_repo must be a mapping"):
        load_external_repo(path)


def test_load_external_repo_rejects_missing_root(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
external_repo:
  volume_marker: .marker
""",
    )

    with pytest.raises(ConfigError, match="external_repo.root"):
        load_external_repo(path)


def test_load_external_repo_rejects_blank_root(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
external_repo:
  root: "   "
""",
    )

    with pytest.raises(ConfigError, match="external_repo.root"):
        load_external_repo(path)


def test_load_external_repo_rejects_marker_with_slash(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
external_repo:
  root: /Volumes/SSD/repo
  volume_marker: nested/marker
""",
    )

    with pytest.raises(ConfigError, match="volume_marker"):
        load_external_repo(path)


def test_load_external_repo_rejects_unknown_subpath_format(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
external_repo:
  root: /Volumes/SSD/repo
  subpaths:
    bogus: somewhere
""",
    )

    with pytest.raises(ConfigError, match="subpaths"):
        load_external_repo(path)


def test_load_external_repo_rejects_absolute_subpath(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
external_repo:
  root: /Volumes/SSD/repo
  subpaths:
    hf-safetensors: /absolute/path
""",
    )

    with pytest.raises(ConfigError, match="subpaths"):
        load_external_repo(path)


def test_load_external_repo_empty_file_is_none(tmp_path) -> None:
    # An empty YAML document (parses to None) is a valid single-tier config.
    path = _write(tmp_path, "")

    assert load_external_repo(path) is None


def test_load_external_repo_rejects_non_mapping_document(tmp_path) -> None:
    # A top-level list/scalar is not a config mapping.
    path = _write(tmp_path, "- just\n- a\n- list\n")

    with pytest.raises(ConfigError, match="top-level mapping"):
        load_external_repo(path)


def test_load_external_repo_rejects_non_mapping_subpaths(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
external_repo:
  root: /Volumes/SSD/repo
  subpaths: just-a-string
""",
    )

    with pytest.raises(ConfigError, match="subpaths must be a mapping"):
        load_external_repo(path)


def test_load_external_repo_missing_file(tmp_path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_external_repo(tmp_path / "nope.yaml")


def test_load_external_repo_rejects_invalid_yaml(tmp_path) -> None:
    path = _write(tmp_path, "external_repo: [unterminated\n")

    with pytest.raises(ConfigError, match="invalid YAML"):
        load_external_repo(path)


def test_default_inferencers_config_has_no_external_repo() -> None:
    # The shipped default is single-tier; the external SSD path is per-machine.
    assert load_external_repo("configs/inferencers.yaml") is None
