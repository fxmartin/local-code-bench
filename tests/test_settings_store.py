"""Story 15.2-001: validated, atomic, comment-preserving settings writes."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from local_code_bench.settings import Settings
from local_code_bench.settings_store import (
    ConflictError,
    SettingsStore,
    SettingsValidationError,
    UnknownConfigError,
    WriteFailedError,
    content_hash,
    default_settings_store,
)

# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------

_MODELS_YAML = """\
# Benchmark model matrix — protocol v1.
models:
  # Local MLX baseline (concurrency locked to 1 by protocol).
  - name: local-mlx
    type: openai
    base_url: http://localhost:8080/v1
    model_id: mlx-model
    pinned_revision: manual
    concurrency: 1
    price_per_1k_tokens:
      input: 0.0
      output: 0.0
"""

_AGENTS_YAML = """\
agents:
  - name: codex
    type: codex
    command: codex
    sandbox: workspace-write
    timeout_seconds: 600
"""

_INFERENCERS_YAML = """\
inferencers:
  - name: mlx-lm
    lifecycle: server
    detect:
      module: mlx_lm
    port: 8080
    health_url: http://127.0.0.1:{port}/v1/models
    start: ["mlx_lm.server", "--port", "8080"]
"""

_SUITES_YAML = """\
suites: []
"""

_FIXED_NOW = datetime(2026, 7, 17, 12, 30, 45, tzinfo=UTC)


def _make_config_dir(tmp_path: Path) -> Path:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    (config_dir / "models.yaml").write_text(_MODELS_YAML, encoding="utf-8")
    (config_dir / "agents.yaml").write_text(_AGENTS_YAML, encoding="utf-8")
    (config_dir / "inferencers.yaml").write_text(_INFERENCERS_YAML, encoding="utf-8")
    (config_dir / "suites.yaml").write_text(_SUITES_YAML, encoding="utf-8")
    return config_dir


def _store(tmp_path: Path, **kwargs) -> SettingsStore:
    return SettingsStore(_make_config_dir(tmp_path), now=lambda: _FIXED_NOW, **kwargs)


# ---------------------------------------------------------------------------
# read: content + hash
# ---------------------------------------------------------------------------


def test_read_returns_content_and_sha256_hash(tmp_path: Path) -> None:
    store = _store(tmp_path)
    doc = store.read("models")
    assert doc.config_id == "models"
    assert doc.content == _MODELS_YAML
    assert doc.content_hash == hashlib.sha256(_MODELS_YAML.encode("utf-8")).hexdigest()
    assert doc.path.name == "models.yaml"


def test_read_unknown_config_id_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(UnknownConfigError, match="pyproject.toml"):
        store.read("pyproject.toml")


def test_read_client_supplied_path_is_rejected(tmp_path: Path) -> None:
    """The store resolves its own paths — a path-shaped id is never honoured."""

    store = _store(tmp_path)
    with pytest.raises(UnknownConfigError):
        store.read("../pyproject.toml")
    with pytest.raises(UnknownConfigError):
        store.read(str(tmp_path / "configs" / "models.yaml"))


# ---------------------------------------------------------------------------
# validation: the harness's own loaders gate every write
# ---------------------------------------------------------------------------


def test_invalid_edit_is_rejected_with_loader_error_and_no_bytes_written(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    doc = store.read("models")
    bad = "models: not-a-list\n"
    with pytest.raises(SettingsValidationError, match="'models' must be a list"):
        store.write("models", bad, expected_hash=doc.content_hash)
    assert (tmp_path / "configs" / "models.yaml").read_text(encoding="utf-8") == _MODELS_YAML
    assert not (tmp_path / "configs" / ".backups").exists()


def test_invalid_yaml_is_rejected_before_any_write(tmp_path: Path) -> None:
    store = _store(tmp_path)
    doc = store.read("agents")
    with pytest.raises(SettingsValidationError, match="invalid YAML"):
        store.write("agents", "agents: [unclosed\n", expected_hash=doc.content_hash)
    assert (tmp_path / "configs" / "agents.yaml").read_text(encoding="utf-8") == _AGENTS_YAML


def test_validation_error_names_the_real_config_path(tmp_path: Path) -> None:
    """Loader messages mention the registered file, not the validation temp copy."""

    store = _store(tmp_path)
    doc = store.read("models")
    with pytest.raises(SettingsValidationError) as excinfo:
        store.write("models", "models:\n  - name: broken\n", expected_hash=doc.content_hash)
    assert "tmp" not in str(excinfo.value) or str(tmp_path) in str(excinfo.value)


def test_write_unknown_config_id_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(UnknownConfigError):
        store.write("secrets", "x: 1\n", expected_hash="0" * 64)


# ---------------------------------------------------------------------------
# valid writes: atomic + timestamped backup
# ---------------------------------------------------------------------------


def test_valid_write_replaces_file_and_creates_timestamped_backup(tmp_path: Path) -> None:
    store = _store(tmp_path)
    doc = store.read("models")
    new_content = _MODELS_YAML.replace("concurrency: 1", "concurrency: 1\n    max_tokens: 2048")
    result = store.write("models", new_content, expected_hash=doc.content_hash)

    assert (tmp_path / "configs" / "models.yaml").read_text(encoding="utf-8") == new_content
    assert result.content_hash == content_hash(new_content)
    assert result.backup_path is not None
    assert result.backup_path.parent == tmp_path / "configs" / ".backups"
    assert result.backup_path.name.startswith("models.yaml.20260717T123045")
    assert result.backup_path.read_text(encoding="utf-8") == _MODELS_YAML


def test_backup_names_do_not_collide_within_one_timestamp(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.write(
        "suites", "suites: []\n# touched once\n", expected_hash=store.read("suites").content_hash
    )
    second = store.write(
        "suites", "suites: []\n# touched twice\n", expected_hash=store.read("suites").content_hash
    )
    assert first.backup_path != second.backup_path
    assert first.backup_path.exists() and second.backup_path.exists()


def test_backup_retention_is_bounded(tmp_path: Path) -> None:
    store = _store(tmp_path, retention=3)
    for index in range(6):
        doc = store.read("suites")
        store.write("suites", f"suites: []\n# edit {index}\n", expected_hash=doc.content_hash)
    backups = list((tmp_path / "configs" / ".backups").glob("suites.yaml.*"))
    assert len(backups) == 3


def test_retention_prunes_only_backups_for_the_same_file(tmp_path: Path) -> None:
    store = _store(tmp_path, retention=2)
    store.write("agents", _AGENTS_YAML + "# edit\n", expected_hash=store.read("agents").content_hash)
    for index in range(4):
        doc = store.read("suites")
        store.write("suites", f"suites: []\n# edit {index}\n", expected_hash=doc.content_hash)
    backup_dir = tmp_path / "configs" / ".backups"
    assert len(list(backup_dir.glob("suites.yaml.*"))) == 2
    assert len(list(backup_dir.glob("agents.yaml.*"))) == 1


# ---------------------------------------------------------------------------
# conflict detection: stale hash never silently overwrites
# ---------------------------------------------------------------------------


def test_stale_hash_is_refused_with_conflict(tmp_path: Path) -> None:
    store = _store(tmp_path)
    doc = store.read("models")
    # External edit lands after the form was loaded.
    external = _MODELS_YAML + "# hand edit\n"
    (tmp_path / "configs" / "models.yaml").write_text(external, encoding="utf-8")

    with pytest.raises(ConflictError) as excinfo:
        store.write("models", _MODELS_YAML, expected_hash=doc.content_hash)
    assert excinfo.value.current_hash == content_hash(external)
    assert (tmp_path / "configs" / "models.yaml").read_text(encoding="utf-8") == external


def test_conflict_is_checked_before_validation(tmp_path: Path) -> None:
    """A stale submission reports the conflict, not a validation failure."""

    store = _store(tmp_path)
    doc = store.read("models")
    (tmp_path / "configs" / "models.yaml").write_text(_MODELS_YAML + "#\n", encoding="utf-8")
    with pytest.raises(ConflictError):
        store.write("models", "models: not-a-list\n", expected_hash=doc.content_hash)


# ---------------------------------------------------------------------------
# rollback: a failed write leaves the original intact
# ---------------------------------------------------------------------------


def test_failed_post_write_verification_restores_original(tmp_path: Path) -> None:
    def broken_read_back(path: Path) -> str:
        raise OSError("disk read failed")

    store = _store(tmp_path, read_back=broken_read_back)
    doc = store.read("models")
    with pytest.raises(WriteFailedError) as excinfo:
        store.write("models", _MODELS_YAML + "# edit\n", expected_hash=doc.content_hash)

    assert excinfo.value.backup_path is not None
    assert excinfo.value.backup_path.exists()
    assert (tmp_path / "configs" / "models.yaml").read_text(encoding="utf-8") == _MODELS_YAML


def test_mismatched_post_write_content_restores_original(tmp_path: Path) -> None:
    store = _store(tmp_path, read_back=lambda path: "corrupted")
    doc = store.read("models")
    with pytest.raises(WriteFailedError, match=r"\.backups"):
        store.write("models", _MODELS_YAML + "# edit\n", expected_hash=doc.content_hash)
    assert (tmp_path / "configs" / "models.yaml").read_text(encoding="utf-8") == _MODELS_YAML


# ---------------------------------------------------------------------------
# apply_updates: comment- and order-preserving structured edits
# ---------------------------------------------------------------------------


def test_apply_updates_preserves_comments_and_key_order(tmp_path: Path) -> None:
    store = _store(tmp_path)
    doc = store.read("models")
    store.apply_updates("models", {"models.0.concurrency": 4}, expected_hash=doc.content_hash)

    written = (tmp_path / "configs" / "models.yaml").read_text(encoding="utf-8")
    assert "# Benchmark model matrix — protocol v1." in written
    assert "# Local MLX baseline (concurrency locked to 1 by protocol)." in written
    assert "concurrency: 4" in written
    # Key order untouched: name still leads each entry, prices still trail.
    assert written.index("name: local-mlx") < written.index("concurrency: 4")
    assert written.index("concurrency: 4") < written.index("price_per_1k_tokens")


def test_apply_updates_validates_the_resulting_document(tmp_path: Path) -> None:
    store = _store(tmp_path)
    doc = store.read("models")
    with pytest.raises(SettingsValidationError):
        store.apply_updates("models", {"models.0.concurrency": "not-an-int"},
                            expected_hash=doc.content_hash)
    assert (tmp_path / "configs" / "models.yaml").read_text(encoding="utf-8") == _MODELS_YAML


def test_apply_updates_rejects_unknown_paths(tmp_path: Path) -> None:
    store = _store(tmp_path)
    doc = store.read("models")
    with pytest.raises(SettingsValidationError, match="models.7.concurrency"):
        store.apply_updates("models", {"models.7.concurrency": 2},
                            expected_hash=doc.content_hash)


def test_apply_updates_honours_conflict_detection(tmp_path: Path) -> None:
    store = _store(tmp_path)
    doc = store.read("models")
    (tmp_path / "configs" / "models.yaml").write_text(_MODELS_YAML + "#\n", encoding="utf-8")
    with pytest.raises(ConflictError):
        store.apply_updates("models", {"models.0.concurrency": 2},
                            expected_hash=doc.content_hash)


# ---------------------------------------------------------------------------
# registry coverage
# ---------------------------------------------------------------------------


def test_all_registered_configs_read_and_roundtrip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for config_id in ("models", "inferencers", "agents", "suites"):
        doc = store.read(config_id)
        result = store.write(config_id, doc.content, expected_hash=doc.content_hash)
        assert result.content_hash == doc.content_hash


def test_missing_registered_file_is_a_store_error(tmp_path: Path) -> None:
    store = _store(tmp_path)
    (tmp_path / "configs" / "suites.yaml").unlink()
    with pytest.raises(ConflictError):
        store.write("suites", _SUITES_YAML, expected_hash=content_hash(_SUITES_YAML))


# ---------------------------------------------------------------------------
# default wiring: settings_backup.* operational defaults
# ---------------------------------------------------------------------------


def test_default_settings_store_wires_backup_settings(tmp_path: Path) -> None:
    config_dir = _make_config_dir(tmp_path)
    backup_dir = tmp_path / "runtime-backups"
    settings = Settings(settings_backup_dir=str(backup_dir), settings_backup_retention=1)
    store = default_settings_store(config_dir, settings=settings)

    store.write("suites", "suites: []\n# edit 0\n", expected_hash=store.read("suites").content_hash)
    result = store.write(
        "suites", "suites: []\n# edit 1\n", expected_hash=store.read("suites").content_hash
    )
    assert result.backup_path.parent == backup_dir
    assert len(list(backup_dir.glob("suites.yaml.*"))) == 1
