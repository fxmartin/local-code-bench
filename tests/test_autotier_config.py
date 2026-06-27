"""Config parsing for the Epic-12 auto-tiering policy (Story 12.4-001)."""

from __future__ import annotations

import pytest

from local_code_bench.config import (
    AutoTierConfig,
    ConfigError,
    load_autotier,
)


def _write(tmp_path, body: str):
    path = tmp_path / "inferencers.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_load_autotier_absent_is_none(tmp_path) -> None:
    # A config without an auto_tier block leaves auto-tiering disabled.
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

    assert load_autotier(path) is None


def test_load_autotier_full_block(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
auto_tier:
  max_local_gb: 200
  min_free_gb: 50
  pins:
    - qwen2.5-coder
    - glm-4.6
""",
    )

    cfg = load_autotier(path)

    assert cfg == AutoTierConfig(
        max_local_gb=200.0,
        min_free_gb=50.0,
        pins=("qwen2.5-coder", "glm-4.6"),
    )


def test_load_autotier_max_only(tmp_path) -> None:
    path = _write(tmp_path, "auto_tier:\n  max_local_gb: 100\n")
    cfg = load_autotier(path)
    assert cfg == AutoTierConfig(max_local_gb=100.0, min_free_gb=None, pins=())


def test_load_autotier_min_free_only(tmp_path) -> None:
    path = _write(tmp_path, "auto_tier:\n  min_free_gb: 25.5\n")
    cfg = load_autotier(path)
    assert cfg == AutoTierConfig(max_local_gb=None, min_free_gb=25.5, pins=())


def test_load_autotier_requires_a_budget(tmp_path) -> None:
    path = _write(tmp_path, "auto_tier:\n  pins: [a]\n")
    with pytest.raises(ConfigError, match="at least one of max_local_gb or min_free_gb"):
        load_autotier(path)


def test_load_autotier_rejects_non_mapping_block(tmp_path) -> None:
    path = _write(tmp_path, "auto_tier: 5\n")
    with pytest.raises(ConfigError, match="auto_tier must be a mapping"):
        load_autotier(path)


@pytest.mark.parametrize("value", ["0", "-3", "false", "abc"])
def test_load_autotier_rejects_non_positive_budget(tmp_path, value: str) -> None:
    path = _write(tmp_path, f"auto_tier:\n  max_local_gb: {value}\n")
    with pytest.raises(ConfigError, match="must be a positive number of GiB"):
        load_autotier(path)


def test_load_autotier_rejects_bad_pins(tmp_path) -> None:
    path = _write(tmp_path, "auto_tier:\n  max_local_gb: 10\n  pins: notalist\n")
    with pytest.raises(ConfigError, match="pins must be a list"):
        load_autotier(path)

    path = _write(tmp_path, "auto_tier:\n  max_local_gb: 10\n  pins: ['', ok]\n")
    with pytest.raises(ConfigError, match="pins entries must be non-empty"):
        load_autotier(path)


def test_load_autotier_rejects_non_mapping_document(tmp_path) -> None:
    path = _write(tmp_path, "[1, 2, 3]\n")
    with pytest.raises(ConfigError, match="must contain a top-level mapping"):
        load_autotier(path)


def test_load_autotier_missing_file(tmp_path) -> None:
    with pytest.raises(ConfigError, match="auto-tier config not found"):
        load_autotier(tmp_path / "nope.yaml")


def test_load_autotier_invalid_yaml(tmp_path) -> None:
    path = _write(tmp_path, "auto_tier: : :\n")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_autotier(path)
