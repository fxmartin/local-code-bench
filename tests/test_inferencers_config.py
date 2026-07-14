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


def test_load_inferencers_parses_optional_url(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
inferencers:
  - name: mtplx
    lifecycle: server
    detect:
      binary: mtplx
    port: 8003
    health_url: http://127.0.0.1:{port}/v1/models
    start: ["mtplx", "serve", "--port", "8003"]
    url: https://github.com/youssofal/mtplx
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

    # A reference link is optional; present on mtplx, absent (None) on dflash.
    assert inferencers["mtplx"].url == "https://github.com/youssofal/mtplx"
    assert inferencers["dflash"].url is None


def test_load_inferencers_rejects_blank_url(tmp_path) -> None:
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
    url: "   "
""",
    )

    with pytest.raises(ConfigError, match="url"):
        load_inferencers(path)


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

    # The benchmark roster is just the two engines FX runs: mlx-lm + ollama.
    assert len(inferencers) == 2
    assert set(inferencers) == {"mlx-lm", "ollama"}


def test_default_inferencers_register_mlx_lm_server() -> None:
    inferencers = load_inferencers("configs/inferencers.yaml")

    mlx_lm = inferencers["mlx-lm"]
    assert mlx_lm.lifecycle == "server"
    assert mlx_lm.detect_kind == "module"
    assert mlx_lm.detect_target == "mlx_lm"
    assert mlx_lm.port == 8080
    assert mlx_lm.start == ("mlx_lm.server", "--port", "8080")
    assert resolve_health_url(mlx_lm) == "http://127.0.0.1:8080/v1/models"
    assert mlx_lm.url == "https://github.com/ml-explore/mlx-lm"


def test_default_inferencers_register_ollama_server() -> None:
    inferencers = load_inferencers("configs/inferencers.yaml")

    ollama = inferencers["ollama"]
    assert ollama.lifecycle == "server"
    assert ollama.detect_kind == "binary"
    assert ollama.detect_target == "ollama"
    assert ollama.port == 11434
    assert ollama.start == ("ollama", "serve")
    assert ollama.stop == ("ollama", "stop")
    assert resolve_health_url(ollama) == "http://127.0.0.1:11434/api/tags"
    assert ollama.url == "https://ollama.com"


def test_default_inferencers_all_carry_reference_url() -> None:
    inferencers = load_inferencers("configs/inferencers.yaml")

    # Every engine carries a website/GitHub link for manual, link-guided install.
    assert all(cfg.url for cfg in inferencers.values())


def test_default_inferencers_have_no_port_collisions() -> None:
    inferencers = load_inferencers("configs/inferencers.yaml")

    ports = [cfg.port for cfg in inferencers.values()]
    assert len(ports) == len(set(ports))
