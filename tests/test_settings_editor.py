"""Story 15.3-003: suites & agents editor over the validated settings store."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from local_code_bench import settings_editor
from local_code_bench.settings_store import SettingsStore, content_hash

# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------

_SUITES_YAML = """\
# Custom benchmark suites.
suites:
  - id: logclass-cli
    label: Log classifier CLI
    source: datasets/logclass-cli.jsonl
  - id: calc-cli
    source: datasets/calc-cli.jsonl
"""

_AGENTS_YAML = """\
agents:
  - name: codex
    type: codex
    command: codex
    sandbox: workspace-write
    timeout_seconds: 600
"""

_MODELS_YAML = """\
models:
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

_FIXED_NOW = datetime(2026, 7, 18, 9, 15, 30, tzinfo=UTC)


def _store(tmp_path: Path, **kwargs) -> SettingsStore:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    (config_dir / "suites.yaml").write_text(_SUITES_YAML, encoding="utf-8")
    (config_dir / "agents.yaml").write_text(_AGENTS_YAML, encoding="utf-8")
    (config_dir / "models.yaml").write_text(_MODELS_YAML, encoding="utf-8")
    (config_dir / "inferencers.yaml").write_text(_INFERENCERS_YAML, encoding="utf-8")
    return SettingsStore(config_dir, now=lambda: _FIXED_NOW, **kwargs)


def _body(content: str, expected_hash: str) -> dict:
    return {"content": content, "expected_hash": expected_hash}


# ---------------------------------------------------------------------------
# read_action
# ---------------------------------------------------------------------------


def test_read_returns_content_and_hash_for_suites(tmp_path: Path) -> None:
    status, payload = settings_editor.read_action(_store(tmp_path), "suites")
    assert status == 200
    assert payload["config_id"] == "suites"
    assert payload["source"] == "suites.yaml"
    assert payload["content"] == _SUITES_YAML
    assert payload["content_hash"] == content_hash(_SUITES_YAML)


def test_read_returns_content_and_hash_for_agents(tmp_path: Path) -> None:
    status, payload = settings_editor.read_action(_store(tmp_path), "agents")
    assert status == 200
    assert payload["content"] == _AGENTS_YAML


def test_read_rejects_configs_outside_the_editable_set(tmp_path: Path) -> None:
    # models/inferencers are registered in the store but belong to the
    # 15.3-001/15.3-002 editors — this story only exposes suites & agents.
    store = _store(tmp_path)
    for config_id in ("models", "inferencers"):
        status, payload = settings_editor.read_action(store, config_id)
        assert status == 404
        assert "not editable" in payload["error"]


def test_read_unknown_config_id_is_404(tmp_path: Path) -> None:
    status, payload = settings_editor.read_action(_store(tmp_path), "bogus")
    assert status == 404
    assert "error" in payload


def test_read_missing_file_is_reported_as_conflict(tmp_path: Path) -> None:
    store = _store(tmp_path)
    (tmp_path / "configs" / "suites.yaml").unlink()
    status, payload = settings_editor.read_action(store, "suites")
    assert status == 409
    assert "error" in payload


# ---------------------------------------------------------------------------
# write_action: the shared validated pipeline
# ---------------------------------------------------------------------------


def test_valid_suites_write_persists_through_the_store(tmp_path: Path) -> None:
    store = _store(tmp_path)
    edited = _SUITES_YAML.replace("Log classifier CLI", "Log classifier CLI v2")
    status, payload = settings_editor.write_action(
        store, "suites", _body(edited, content_hash(_SUITES_YAML))
    )
    assert status == 200
    assert payload["config_id"] == "suites"
    assert payload["content_hash"] == content_hash(edited)
    assert payload["warnings"] == []
    assert (tmp_path / "configs" / "suites.yaml").read_text(encoding="utf-8") == edited


def test_valid_agents_write_persists_through_the_store(tmp_path: Path) -> None:
    store = _store(tmp_path)
    edited = _AGENTS_YAML.replace("timeout_seconds: 600", "timeout_seconds: 900")
    status, payload = settings_editor.write_action(
        store, "agents", _body(edited, content_hash(_AGENTS_YAML))
    )
    assert status == 200
    assert payload["warnings"] == []
    assert "timeout_seconds: 900" in (tmp_path / "configs" / "agents.yaml").read_text(
        encoding="utf-8"
    )


def test_write_creates_a_backup_of_the_previous_version(tmp_path: Path) -> None:
    store = _store(tmp_path)
    edited = _SUITES_YAML + "  - id: extra\n    source: datasets/extra.jsonl\n"
    status, payload = settings_editor.write_action(
        store, "suites", _body(edited, content_hash(_SUITES_YAML))
    )
    assert status == 200
    backups = list((tmp_path / "configs" / ".backups").glob("suites.yaml.*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == _SUITES_YAML
    # The payload names the backup file only — never an absolute host path.
    assert payload["backup"] == backups[0].name


def test_invalid_suites_edit_is_rejected_by_the_suite_loader(tmp_path: Path) -> None:
    store = _store(tmp_path)
    duplicated = _SUITES_YAML.replace("id: calc-cli", "id: logclass-cli")
    status, payload = settings_editor.write_action(
        store, "suites", _body(duplicated, content_hash(_SUITES_YAML))
    )
    assert status == 422
    assert "duplicates" in payload["error"]
    assert (tmp_path / "configs" / "suites.yaml").read_text(encoding="utf-8") == _SUITES_YAML


def test_invalid_agents_edit_is_rejected_by_the_agent_loader(tmp_path: Path) -> None:
    store = _store(tmp_path)
    status, payload = settings_editor.write_action(
        store, "agents", _body("agents: not-a-list\n", content_hash(_AGENTS_YAML))
    )
    assert status == 422
    assert "agents" in payload["error"]


def test_stale_hash_is_a_conflict_with_current_hash(tmp_path: Path) -> None:
    store = _store(tmp_path)
    status, payload = settings_editor.write_action(
        store, "suites", _body(_SUITES_YAML, content_hash("something else"))
    )
    assert status == 409
    assert payload["current_hash"] == content_hash(_SUITES_YAML)


def test_write_rejects_configs_outside_the_editable_set(tmp_path: Path) -> None:
    status, payload = settings_editor.write_action(
        _store(tmp_path), "models", _body(_MODELS_YAML, content_hash(_MODELS_YAML))
    )
    assert status == 404
    assert "not editable" in payload["error"]


def test_write_rejects_malformed_bodies(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for body in (
        "not a dict",
        {},
        {"content": 42, "expected_hash": "x"},
        {"content": "suites: []\n", "expected_hash": 42},
        {"content": "suites: []\n"},
    ):
        status, payload = settings_editor.write_action(store, "suites", body)
        assert status == 400
        assert "error" in payload


def test_failed_write_maps_to_500(tmp_path: Path) -> None:
    store = _store(tmp_path, read_back=lambda _path: "tampered")
    status, payload = settings_editor.write_action(
        store, "suites", _body("suites: []\n", content_hash(_SUITES_YAML))
    )
    assert status == 500
    assert "error" in payload
    # The original file was restored by the store.
    assert (tmp_path / "configs" / "suites.yaml").read_text(encoding="utf-8") == _SUITES_YAML


# ---------------------------------------------------------------------------
# dangling suite references: warn but allow
# ---------------------------------------------------------------------------


def test_removing_a_referenced_suite_warns_but_writes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    without_logclass = """\
suites:
  - id: calc-cli
    source: datasets/calc-cli.jsonl
"""
    status, payload = settings_editor.write_action(
        store,
        "suites",
        _body(without_logclass, content_hash(_SUITES_YAML)),
        referenced_suites=lambda: {"logclass-cli", "humaneval"},
    )
    assert status == 200
    assert len(payload["warnings"]) == 1
    assert "logclass-cli" in payload["warnings"][0]
    saved = (tmp_path / "configs" / "suites.yaml").read_text(encoding="utf-8")
    assert saved == without_logclass


def test_removing_an_unreferenced_suite_produces_no_warning(tmp_path: Path) -> None:
    store = _store(tmp_path)
    without_calc = """\
suites:
  - id: logclass-cli
    source: datasets/logclass-cli.jsonl
"""
    status, payload = settings_editor.write_action(
        store,
        "suites",
        _body(without_calc, content_hash(_SUITES_YAML)),
        referenced_suites=lambda: {"logclass-cli"},
    )
    assert status == 200
    assert payload["warnings"] == []


def test_renaming_a_referenced_suite_counts_as_a_dangling_reference(tmp_path: Path) -> None:
    store = _store(tmp_path)
    renamed = _SUITES_YAML.replace("id: logclass-cli", "id: logclass-cli-v2")
    status, payload = settings_editor.write_action(
        store,
        "suites",
        _body(renamed, content_hash(_SUITES_YAML)),
        referenced_suites=lambda: {"logclass-cli"},
    )
    assert status == 200
    assert any("logclass-cli" in warning for warning in payload["warnings"])


def test_agents_writes_never_produce_suite_warnings(tmp_path: Path) -> None:
    store = _store(tmp_path)
    status, payload = settings_editor.write_action(
        store,
        "agents",
        _body(_AGENTS_YAML, content_hash(_AGENTS_YAML)),
        referenced_suites=lambda: {"codex"},
    )
    assert status == 200
    assert payload["warnings"] == []


def test_references_are_not_consulted_on_rejected_edits(tmp_path: Path) -> None:
    store = _store(tmp_path)

    def _boom() -> set[str]:
        raise AssertionError("references must not be scanned for a rejected edit")

    status, _payload = settings_editor.write_action(
        store,
        "suites",
        _body("suites: not-a-list\n", content_hash(_SUITES_YAML)),
        referenced_suites=_boom,
    )
    assert status == 422


# ---------------------------------------------------------------------------
# referenced_suite_ids: the saved-run-history scan
# ---------------------------------------------------------------------------


def test_referenced_suite_ids_collects_suites_from_result_files(tmp_path: Path) -> None:
    first = tmp_path / "run-1.jsonl"
    first.write_text(
        "\n".join(
            [
                json.dumps({"run_mode": "endpoint", "suite": "logclass-cli", "task_id": "t1"}),
                json.dumps({"run_mode": "endpoint", "suite": "humaneval", "task_id": "t2"}),
            ]
        ),
        encoding="utf-8",
    )
    second = tmp_path / "run-2.jsonl"
    second.write_text(
        json.dumps({"run_mode": "agent", "suite": "calc-cli", "task_id": "t3"}),
        encoding="utf-8",
    )
    assert settings_editor.referenced_suite_ids([first, second]) == {
        "logclass-cli",
        "humaneval",
        "calc-cli",
    }


def test_referenced_suite_ids_skips_malformed_and_missing_inputs(tmp_path: Path) -> None:
    messy = tmp_path / "messy.jsonl"
    messy.write_text(
        "\n".join(
            [
                "{not json",
                json.dumps(["a", "list"]),
                json.dumps({"suite": 42}),
                json.dumps({"suite": "calc-cli"}),
                "",
            ]
        ),
        encoding="utf-8",
    )
    missing = tmp_path / "absent.jsonl"
    assert settings_editor.referenced_suite_ids([messy, missing]) == {"calc-cli"}
