from __future__ import annotations

import pytest

from local_code_bench.config import (
    ConfigError,
    load_inferencers,
    resolve_health_url,
)


def _write(tmp_path, body: str):
    path = tmp_path / "inferencers.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_load_inferencers_parses_server_entry(tmp_path) -> None:
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

    inferencers = load_inferencers(path)

    cfg = inferencers["dflash"]
    assert cfg.lifecycle == "server"
    assert cfg.detect_kind == "binary"
    assert cfg.detect_target == "dflash"
    assert cfg.port == 8000
    assert cfg.start == ("dflash", "serve")
    assert cfg.stop is None
    assert resolve_health_url(cfg) == "http://127.0.0.1:8000/v1/models"


def test_load_inferencers_parses_app_entry_without_lifecycle_commands(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
inferencers:
  - name: lm-studio
    lifecycle: app
    detect:
      app: "LM Studio.app"
    port: 1234
    health_url: http://127.0.0.1:{port}/v1/models
""",
    )

    cfg = load_inferencers(path)["lm-studio"]

    assert cfg.lifecycle == "app"
    assert cfg.detect_kind == "app"
    assert cfg.detect_target == "LM Studio.app"
    assert cfg.start is None
    assert cfg.stop is None


def test_load_inferencers_parses_custom_stop(tmp_path) -> None:
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
    stop: ["ollama", "stop"]
""",
    )

    cfg = load_inferencers(path)["ollama"]

    assert cfg.stop == ("ollama", "stop")
    assert resolve_health_url(cfg) == "http://127.0.0.1:11434/api/tags"


def test_load_inferencers_rejects_duplicate_names(tmp_path) -> None:
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
  - name: dflash
    lifecycle: server
    detect:
      binary: dflash
    port: 8000
    health_url: http://127.0.0.1:{port}/v1/models
    start: ["dflash", "serve"]
""",
    )

    with pytest.raises(ConfigError, match="duplicates"):
        load_inferencers(path)


def test_load_inferencers_rejects_zero_detect_kinds(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
inferencers:
  - name: dflash
    lifecycle: server
    detect: {}
    port: 8000
    health_url: http://127.0.0.1:{port}/v1/models
    start: ["dflash", "serve"]
""",
    )

    with pytest.raises(ConfigError, match=r"inferencers\[0\].detect"):
        load_inferencers(path)


def test_load_inferencers_rejects_multiple_detect_kinds(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
inferencers:
  - name: dflash
    lifecycle: server
    detect:
      binary: dflash
      module: dflash
    port: 8000
    health_url: http://127.0.0.1:{port}/v1/models
    start: ["dflash", "serve"]
""",
    )

    with pytest.raises(ConfigError, match=r"inferencers\[0\].detect"):
        load_inferencers(path)


def test_load_inferencers_rejects_server_without_start(tmp_path) -> None:
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
""",
    )

    with pytest.raises(ConfigError, match="start"):
        load_inferencers(path)


def test_load_inferencers_rejects_app_with_lifecycle_commands(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
inferencers:
  - name: lm-studio
    lifecycle: app
    detect:
      app: "LM Studio.app"
    port: 1234
    health_url: http://127.0.0.1:{port}/v1/models
    start: ["open", "-a", "LM Studio"]
""",
    )

    with pytest.raises(ConfigError, match="app"):
        load_inferencers(path)


def test_load_inferencers_rejects_unknown_lifecycle(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
inferencers:
  - name: dflash
    lifecycle: daemon
    detect:
      binary: dflash
    port: 8000
    health_url: http://127.0.0.1:{port}/v1/models
""",
    )

    with pytest.raises(ConfigError, match="lifecycle"):
        load_inferencers(path)


def test_load_inferencers_rejects_invalid_port(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
inferencers:
  - name: dflash
    lifecycle: server
    detect:
      binary: dflash
    port: 0
    health_url: http://127.0.0.1:{port}/v1/models
    start: ["dflash", "serve"]
""",
    )

    with pytest.raises(ConfigError, match="port"):
        load_inferencers(path)


def test_load_inferencers_missing_file(tmp_path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_inferencers(tmp_path / "nope.yaml")


def test_load_inferencers_rejects_invalid_yaml(tmp_path) -> None:
    path = _write(tmp_path, "inferencers: [unterminated\n")

    with pytest.raises(ConfigError, match="invalid YAML"):
        load_inferencers(path)


def test_load_inferencers_rejects_non_list_root(tmp_path) -> None:
    path = _write(tmp_path, "inferencers: not-a-list\n")

    with pytest.raises(ConfigError, match="must be a list"):
        load_inferencers(path)


def test_load_inferencers_rejects_non_mapping_entry(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
inferencers:
  - just-a-string
""",
    )

    with pytest.raises(ConfigError, match=r"inferencers\[0\] must be a mapping"):
        load_inferencers(path)


def test_load_inferencers_rejects_non_mapping_detect(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
inferencers:
  - name: dflash
    lifecycle: server
    detect: binary
    port: 8000
    health_url: http://127.0.0.1:{port}/v1/models
    start: ["dflash", "serve"]
""",
    )

    with pytest.raises(ConfigError, match=r"inferencers\[0\].detect must be a mapping"):
        load_inferencers(path)


def test_load_inferencers_rejects_non_list_start(tmp_path) -> None:
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
    start: dflash serve
""",
    )

    with pytest.raises(ConfigError, match=r"start must be a non-empty list"):
        load_inferencers(path)


def test_load_inferencers_rejects_blank_start_arg(tmp_path) -> None:
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
    start: ["dflash", ""]
""",
    )

    with pytest.raises(ConfigError, match=r"start must be a non-empty list"):
        load_inferencers(path)


def test_default_inferencers_config_loads() -> None:
    inferencers = load_inferencers("configs/inferencers.yaml")

    # 8 headless servers + 2 detect-only GUI apps.
    assert len(inferencers) == 10
    assert inferencers["dflash"].port == 8000
    assert inferencers["turboquant"].port == 8002
    assert inferencers["vllm-mlx"].port == 8001  # off 8000 to avoid colliding with dflash
    assert inferencers["ollama"].stop == ("ollama", "stop")
    assert inferencers["lm-studio"].lifecycle == "app"
    assert inferencers["gpt4all"].lifecycle == "app"
    assert resolve_health_url(inferencers["ollama"]).endswith("/api/tags")
