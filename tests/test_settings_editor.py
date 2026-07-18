"""Settings-tab editor tests: inferencers & storage (15.3-002) and suites & agents (15.3-003).

The 15.3-002 half covers the editable document the Settings tab renders
(display-only install facts vs editable store/tier fields, advisory path
warnings, running-engine flags) and the write path riding the 15.2-001
pipeline (editable-surface guard, conflict detection, loader validation of
every block the harness reads from ``inferencers.yaml``, restart-pending
notes). The 15.3-003 half covers the suites & agents read/write actions over
the validated settings store.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from local_code_bench import settings_editor
from local_code_bench.config import (
    DEFAULT_VOLUME_MARKER,
    load_autotier,
    load_external_repo,
    load_inferencers,
)
from local_code_bench.settings_store import SettingsStore, content_hash


_YAML = """\
# engines the harness manages (comment must survive editor writes)
inferencers:
  - name: mlx-lm
    lifecycle: server
    detect:
      module: mlx_lm
    port: 8080
    health_url: http://127.0.0.1:{port}/v1/models
    start: ["mlx_lm.server", "--port", "8080"]
    model_store:
      - ~/hub
    format: hf-safetensors
  - name: ollama
    lifecycle: server
    detect:
      binary: ollama
    port: 11434
    health_url: http://127.0.0.1:{port}/api/tags
    start: ["ollama", "serve"]
    stop: ["ollama", "stop"]
    model_store: ~/ollama-models
    format: ollama

external_repo:
  root: ~/external/repo
  volume_marker: .marker

auto_tier:
  max_local_gb: 200
  pins:
    - qwen2.5-coder
"""

_MINIMAL_YAML = """\
inferencers:
  - name: mlx-lm
    lifecycle: server
    detect:
      module: mlx_lm
    port: 8080
    health_url: http://127.0.0.1:{port}/v1/models
    start: ["mlx_lm.server"]
"""


def _write_config(tmp_path: Path, text: str = _YAML) -> Path:
    config_dir = tmp_path / "configs"
    config_dir.mkdir(exist_ok=True)
    path = config_dir / "inferencers.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _inferencers_store(path: Path) -> SettingsStore:
    return SettingsStore(path.parent, now=lambda: datetime(2026, 7, 18, tzinfo=UTC))


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return home


# ---------------------------------------------------------------------------
# editor_payload: the editable document
# ---------------------------------------------------------------------------


def test_payload_lists_engines_with_display_facts_and_editable_store(tmp_path: Path) -> None:
    path = _write_config(tmp_path)

    payload = settings_editor.editor_payload(path, home=_home(tmp_path))

    assert payload["error"] is None
    assert payload["config_id"] == "inferencers"
    assert payload["content_hash"] == content_hash(path.read_text(encoding="utf-8"))
    assert [engine["name"] for engine in payload["engines"]] == ["mlx-lm", "ollama"]

    mlx = payload["engines"][0]
    display = {entry["label"]: entry["value"] for entry in mlx["display"]}
    assert display["lifecycle"] == "server"
    assert display["detect"] == "module: mlx_lm"
    assert display["port"] == 8080
    assert display["start command"] == "mlx_lm.server --port 8080"
    assert mlx["index"] == 0
    assert mlx["store"]["configured"] is True
    assert mlx["store"]["paths"] == ["~/hub"]
    assert mlx["store"]["format"] == "hf-safetensors"

    ollama = payload["engines"][1]
    assert {entry["label"] for entry in ollama["display"]} >= {"stop command"}
    assert payload["formats"] == ["hf-safetensors", "ollama"]


def test_payload_flags_running_engine_with_restart_note(tmp_path: Path) -> None:
    path = _write_config(tmp_path)

    payload = settings_editor.editor_payload(path, running={"ollama"}, home=_home(tmp_path))

    mlx, ollama = payload["engines"]
    assert mlx["running"] is False and mlx["restart_note"] is None
    assert ollama["running"] is True
    assert ollama["restart_note"] == settings_editor.RESTART_NOTE


def test_payload_warns_on_missing_store_path_but_not_on_existing(tmp_path: Path) -> None:
    path = _write_config(tmp_path)
    home = _home(tmp_path)
    (home / "hub").mkdir()

    payload = settings_editor.editor_payload(path, home=home)

    mlx, ollama = payload["engines"]
    assert mlx["store"]["warnings"] == []
    assert len(ollama["store"]["warnings"]) == 1
    assert "~/ollama-models" in ollama["store"]["warnings"][0]


def test_payload_external_warns_offline_and_clears_when_mounted(tmp_path: Path) -> None:
    path = _write_config(tmp_path)
    home = _home(tmp_path)

    offline = settings_editor.editor_payload(path, home=home)
    external = offline["storage"]["external_repo"]
    assert external["configured"] is True
    assert external["root"] == "~/external/repo"
    assert external["volume_marker"] == ".marker"
    assert len(external["warnings"]) == 1
    assert "~/external/repo" in external["warnings"][0]

    root = home / "external" / "repo"
    root.mkdir(parents=True)
    (root / ".marker").write_text("marker", encoding="utf-8")
    mounted = settings_editor.editor_payload(path, home=home)
    assert mounted["storage"]["external_repo"]["warnings"] == []


def test_payload_unconfigured_tier_blocks_carry_defaults(tmp_path: Path) -> None:
    path = _write_config(tmp_path, _MINIMAL_YAML)

    payload = settings_editor.editor_payload(path, home=_home(tmp_path))

    storage = payload["storage"]
    assert storage["external_repo"]["configured"] is False
    assert storage["external_repo"]["root"] == ""
    assert storage["external_repo"]["volume_marker"] == DEFAULT_VOLUME_MARKER
    assert set(storage["external_repo"]["subpaths"]) == {"hf-safetensors", "ollama"}
    assert storage["external_repo"]["warnings"] == []
    assert storage["auto_tier"] == {
        "configured": False,
        "max_local_gb": None,
        "min_free_gb": None,
        "pins": [],
    }
    assert payload["engines"][0]["store"]["configured"] is False


def test_payload_carries_auto_tier_policy_and_pin_suggestions(tmp_path: Path) -> None:
    path = _write_config(tmp_path)

    payload = settings_editor.editor_payload(
        path, pin_suggestions=["qwen2.5-coder", "glm-4"], home=_home(tmp_path)
    )

    auto_tier = payload["storage"]["auto_tier"]
    assert auto_tier == {
        "configured": True,
        "max_local_gb": 200.0,
        "min_free_gb": None,
        "pins": ["qwen2.5-coder"],
    }
    assert payload["storage"]["pin_suggestions"] == ["qwen2.5-coder", "glm-4"]


def test_payload_degrades_a_broken_file_to_an_inline_error(tmp_path: Path) -> None:
    path = _write_config(tmp_path, "inferencers: [broken")

    payload = settings_editor.editor_payload(path, home=_home(tmp_path))

    assert payload["error"] is not None
    assert payload["content_hash"] is None
    assert payload["engines"] == []
    assert payload["storage"] is None


# ---------------------------------------------------------------------------
# apply_edit: guarded writes through the 15.2-001 pipeline
# ---------------------------------------------------------------------------


def _hash(path: Path) -> str:
    return content_hash(path.read_text(encoding="utf-8"))


def test_apply_edits_store_paths_and_format_preserving_comments(tmp_path: Path) -> None:
    path = _write_config(tmp_path)

    status, payload = settings_editor.apply_edit(
        _inferencers_store(path),
        {
            "expected_hash": _hash(path),
            "updates": {
                "inferencers.0.model_store": ["~/new-hub", "~/shelf"],
                "inferencers.0.format": "hf-safetensors",
            },
        },
        home=_home(tmp_path),
    )

    assert status == 200
    assert payload["ok"] is True
    assert payload["content_hash"] == _hash(path)
    reloaded = load_inferencers(path)["mlx-lm"]
    assert reloaded.model_store == ("~/new-hub", "~/shelf")
    assert "# engines the harness manages" in path.read_text(encoding="utf-8")
    # the new paths do not exist yet: advisory warnings, never a block
    assert any("~/new-hub" in warning for warning in payload["warnings"])


def test_apply_rejects_display_only_fields(tmp_path: Path) -> None:
    path = _write_config(tmp_path)
    before = path.read_text(encoding="utf-8")

    status, payload = settings_editor.apply_edit(
        _inferencers_store(path),
        {"expected_hash": _hash(path), "updates": {"inferencers.0.port": 9999}},
        home=_home(tmp_path),
    )

    assert status == 400
    assert "not editable" in payload["error"]
    assert path.read_text(encoding="utf-8") == before


def test_apply_conflict_reports_current_hash(tmp_path: Path) -> None:
    path = _write_config(tmp_path)

    status, payload = settings_editor.apply_edit(
        _inferencers_store(path),
        {"expected_hash": "0" * 64, "updates": {"inferencers.0.format": "ollama"}},
        home=_home(tmp_path),
    )

    assert status == 409
    assert payload["current_hash"] == _hash(path)


def test_apply_rejects_a_value_the_loader_refuses(tmp_path: Path) -> None:
    path = _write_config(tmp_path)
    before = path.read_text(encoding="utf-8")

    status, payload = settings_editor.apply_edit(
        _inferencers_store(path),
        {"expected_hash": _hash(path), "updates": {"inferencers.0.format": "gguf"}},
        home=_home(tmp_path),
    )

    assert status == 400
    assert "format" in payload["error"]
    assert path.read_text(encoding="utf-8") == before


def test_apply_validates_the_tier_blocks_too(tmp_path: Path) -> None:
    # load_inferencers alone ignores external_repo/auto_tier; the editor's
    # pipeline must still refuse a tier block the tier loaders would reject.
    path = _write_config(tmp_path)
    before = path.read_text(encoding="utf-8")

    status, payload = settings_editor.apply_edit(
        _inferencers_store(path),
        {"expected_hash": _hash(path), "updates": {"external_repo": {"root": 123}}},
        home=_home(tmp_path),
    )

    assert status == 400
    assert "root" in payload["error"]
    assert path.read_text(encoding="utf-8") == before


def test_apply_edits_external_repo_and_auto_tier(tmp_path: Path) -> None:
    path = _write_config(tmp_path)

    status, payload = settings_editor.apply_edit(
        _inferencers_store(path),
        {
            "expected_hash": _hash(path),
            "updates": {
                "external_repo": {
                    "root": "~/ssd/repo",
                    "volume_marker": ".m",
                    "subpaths": {"hf-safetensors": "hf"},
                },
                "auto_tier": {"max_local_gb": 100, "min_free_gb": 25, "pins": ["glm-4"]},
            },
        },
        home=_home(tmp_path),
    )

    assert status == 200
    external = load_external_repo(path)
    assert external is not None
    assert external.root == "~/ssd/repo"
    assert external.subpaths["hf-safetensors"] == "hf"
    auto_tier = load_autotier(path)
    assert auto_tier is not None
    assert (auto_tier.max_local_gb, auto_tier.min_free_gb) == (100.0, 25.0)
    assert auto_tier.pins == ("glm-4",)
    # the new root is not mounted: an advisory warning, not an error
    assert any("~/ssd/repo" in warning for warning in payload["warnings"])


def test_apply_removes_an_optional_block_with_null(tmp_path: Path) -> None:
    path = _write_config(tmp_path)

    status, _ = settings_editor.apply_edit(
        _inferencers_store(path),
        {"expected_hash": _hash(path), "updates": {"auto_tier": None}},
        home=_home(tmp_path),
    )

    assert status == 200
    assert load_autotier(path) is None


def test_apply_reports_restart_pending_for_running_edited_engines(tmp_path: Path) -> None:
    path = _write_config(tmp_path)

    status, payload = settings_editor.apply_edit(
        _inferencers_store(path),
        {
            "expected_hash": _hash(path),
            "updates": {"inferencers.0.model_store": ["~/new-hub"]},
        },
        running={"mlx-lm", "ollama"},
        home=_home(tmp_path),
    )

    assert status == 200
    assert payload["restart_pending"] == ["mlx-lm"]


def test_apply_storage_only_edit_flags_no_restart(tmp_path: Path) -> None:
    path = _write_config(tmp_path)

    status, payload = settings_editor.apply_edit(
        _inferencers_store(path),
        {"expected_hash": _hash(path), "updates": {"auto_tier": {"max_local_gb": 50}}},
        running={"mlx-lm"},
        home=_home(tmp_path),
    )

    assert status == 200
    assert payload["restart_pending"] == []


def test_apply_rejects_malformed_bodies(tmp_path: Path) -> None:
    path = _write_config(tmp_path)
    store = _inferencers_store(path)
    home = _home(tmp_path)

    assert settings_editor.apply_edit(store, [], home=home)[0] == 400
    assert settings_editor.apply_edit(store, {"updates": {"auto_tier": None}}, home=home)[0] == 400
    assert settings_editor.apply_edit(store, {"expected_hash": _hash(path)}, home=home)[0] == 400
    assert (
        settings_editor.apply_edit(store, {"expected_hash": _hash(path), "updates": {}}, home=home)[
            0
        ]
        == 400
    )


# ===========================================================================
# Suites & agents editor (Story 15.3-003)
# ===========================================================================

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
