from __future__ import annotations

import dataclasses

import pytest

from local_code_bench.config import (
    ConfigError,
    load_optimizers,
    resolve_health_url,
    resolve_optimizer_start,
)


def _write(tmp_path, body: str):
    path = tmp_path / "optimizers.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_load_optimizers_parses_entry(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
optimizers:
  - name: headroom
    detect:
      binary: headroom
    port: 8787
    health_url: http://127.0.0.1:{port}/v1/models
    start: ["headroom", "proxy", "--port", "8787", "{upstream}"]
    url: https://headroom-docs.vercel.app/docs
""",
    )

    optimizers = load_optimizers(path)

    cfg = optimizers["headroom"]
    assert cfg.name == "headroom"
    assert cfg.detect_kind == "binary"
    assert cfg.detect_target == "headroom"
    assert cfg.port == 8787
    assert cfg.start == ("headroom", "proxy", "--port", "8787", "{upstream}")
    assert cfg.url == "https://headroom-docs.vercel.app/docs"
    assert resolve_health_url(cfg) == "http://127.0.0.1:8787/v1/models"


def test_optimizer_config_is_frozen(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
optimizers:
  - name: headroom
    detect:
      binary: headroom
    port: 8787
    health_url: http://127.0.0.1:{port}/v1/models
    start: ["headroom", "proxy", "--port", "8787", "{upstream}"]
""",
    )

    cfg = load_optimizers(path)["headroom"]

    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.port = 9999  # type: ignore[misc]


def test_load_optimizers_url_is_optional(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
optimizers:
  - name: headroom
    detect:
      binary: headroom
    port: 8787
    health_url: http://127.0.0.1:{port}/v1/models
    start: ["headroom", "proxy", "--port", "8787", "{upstream}"]
""",
    )

    assert load_optimizers(path)["headroom"].url is None


def test_load_optimizers_rejects_blank_url(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
optimizers:
  - name: headroom
    detect:
      binary: headroom
    port: 8787
    health_url: http://127.0.0.1:{port}/v1/models
    start: ["headroom", "proxy", "--port", "8787", "{upstream}"]
    url: "   "
""",
    )

    with pytest.raises(ConfigError, match="url"):
        load_optimizers(path)


def test_load_optimizers_rejects_duplicate_names(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
optimizers:
  - name: headroom
    detect:
      binary: headroom
    port: 8787
    health_url: http://127.0.0.1:{port}/v1/models
    start: ["headroom", "proxy", "--port", "8787", "{upstream}"]
  - name: headroom
    detect:
      binary: headroom
    port: 8788
    health_url: http://127.0.0.1:{port}/v1/models
    start: ["headroom", "proxy", "--port", "8788", "{upstream}"]
""",
    )

    with pytest.raises(ConfigError, match="duplicates"):
        load_optimizers(path)


def test_load_optimizers_rejects_zero_detect_kinds(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
optimizers:
  - name: headroom
    detect: {}
    port: 8787
    health_url: http://127.0.0.1:{port}/v1/models
    start: ["headroom", "proxy", "--port", "8787", "{upstream}"]
""",
    )

    with pytest.raises(ConfigError, match=r"optimizers\[0\].detect"):
        load_optimizers(path)


def test_load_optimizers_rejects_multiple_detect_kinds(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
optimizers:
  - name: headroom
    detect:
      binary: headroom
      module: headroom
    port: 8787
    health_url: http://127.0.0.1:{port}/v1/models
    start: ["headroom", "proxy", "--port", "8787", "{upstream}"]
""",
    )

    with pytest.raises(ConfigError, match=r"optimizers\[0\].detect"):
        load_optimizers(path)


def test_load_optimizers_detect_error_names_offending_index(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
optimizers:
  - name: headroom
    detect:
      binary: headroom
    port: 8787
    health_url: http://127.0.0.1:{port}/v1/models
    start: ["headroom", "proxy", "--port", "8787", "{upstream}"]
  - name: broken
    detect: {}
    port: 8788
    health_url: http://127.0.0.1:{port}/v1/models
    start: ["broken", "{upstream}"]
""",
    )

    with pytest.raises(ConfigError, match=r"optimizers\[1\].detect"):
        load_optimizers(path)


def test_load_optimizers_rejects_non_mapping_detect(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
optimizers:
  - name: headroom
    detect: binary
    port: 8787
    health_url: http://127.0.0.1:{port}/v1/models
    start: ["headroom", "proxy", "--port", "8787", "{upstream}"]
""",
    )

    with pytest.raises(ConfigError, match=r"optimizers\[0\].detect must be a mapping"):
        load_optimizers(path)


def test_load_optimizers_requires_start(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
optimizers:
  - name: headroom
    detect:
      binary: headroom
    port: 8787
    health_url: http://127.0.0.1:{port}/v1/models
""",
    )

    with pytest.raises(ConfigError, match="start"):
        load_optimizers(path)


def test_load_optimizers_rejects_non_list_start(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
optimizers:
  - name: headroom
    detect:
      binary: headroom
    port: 8787
    health_url: http://127.0.0.1:{port}/v1/models
    start: headroom proxy
""",
    )

    with pytest.raises(ConfigError, match=r"optimizers\[0\].start must be a non-empty list"):
        load_optimizers(path)


def test_load_optimizers_rejects_invalid_port(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
optimizers:
  - name: headroom
    detect:
      binary: headroom
    port: 0
    health_url: http://127.0.0.1:{port}/v1/models
    start: ["headroom", "proxy", "--port", "8787", "{upstream}"]
""",
    )

    with pytest.raises(ConfigError, match="port"):
        load_optimizers(path)


def test_load_optimizers_missing_file(tmp_path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_optimizers(tmp_path / "nope.yaml")


def test_load_optimizers_rejects_invalid_yaml(tmp_path) -> None:
    path = _write(tmp_path, "optimizers: [unterminated\n")

    with pytest.raises(ConfigError, match="invalid YAML"):
        load_optimizers(path)


def test_load_optimizers_rejects_non_list_root(tmp_path) -> None:
    path = _write(tmp_path, "optimizers: not-a-list\n")

    with pytest.raises(ConfigError, match="must be a list"):
        load_optimizers(path)


def test_load_optimizers_rejects_non_mapping_entry(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
optimizers:
  - just-a-string
""",
    )

    with pytest.raises(ConfigError, match=r"optimizers\[0\] must be a mapping"):
        load_optimizers(path)


def test_resolve_optimizer_start_substitutes_port_and_upstream(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
optimizers:
  - name: proxy
    detect:
      binary: proxy
    port: 9000
    health_url: http://127.0.0.1:{port}/v1/models
    start: ["proxy", "--listen", "{port}", "--target", "{upstream}"]
""",
    )
    cfg = load_optimizers(path)["proxy"]

    argv = resolve_optimizer_start(cfg, upstream="http://127.0.0.1:8080/v1")

    # `{port}` is the proxy's own listen port; `{upstream}` is the active
    # inferencer's base URL — both must be filled so the proxy is wired to a
    # real engine.
    assert argv == ("proxy", "--listen", "9000", "--target", "http://127.0.0.1:8080/v1")


def test_default_optimizers_config_seeds_headroom() -> None:
    optimizers = load_optimizers("configs/optimizers.yaml")

    headroom = optimizers["headroom"]
    assert headroom.detect_kind == "binary"
    assert headroom.detect_target == "headroom"
    assert headroom.port == 8787
    assert headroom.start == ("headroom", "proxy", "--port", "8787", "{upstream}")
    assert headroom.url == "https://headroom-docs.vercel.app/docs"
    assert resolve_health_url(headroom) == "http://127.0.0.1:8787/v1/models"


def test_default_optimizers_start_templates_reference_upstream() -> None:
    optimizers = load_optimizers("configs/optimizers.yaml")

    # Every proxy must be wired to a real engine via `{upstream}` substitution.
    assert all("{upstream}" in " ".join(cfg.start) for cfg in optimizers.values())


def test_default_optimizers_all_carry_reference_url() -> None:
    optimizers = load_optimizers("configs/optimizers.yaml")

    # Every proxy carries a reference link for manual, link-guided install.
    assert all(cfg.url for cfg in optimizers.values())
