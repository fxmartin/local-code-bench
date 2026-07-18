"""Story 15.1-001: read-only settings aggregation over every config surface."""

from __future__ import annotations

import json
from pathlib import Path

from local_code_bench import settings_panel
from local_code_bench.settings_store import content_hash

# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------

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
  - name: cloud-coder
    type: openai
    base_url: http://api.example.test/v1
    model_id: cloud-model
    pinned_revision: r1
    api_key_env: EXAMPLE_API_KEY
    concurrency: 8
    max_tokens: 2048
    price_per_1k_tokens:
      input: 0.001
      output: 0.002
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
    model_store:
      - ~/.cache/huggingface/hub
    format: hf-safetensors
external_repo:
  root: /Volumes/SSD/repo
auto_tier:
  max_local_gb: 100
  pins:
    - local-mlx
"""

_INFERENCERS_YAML_SINGLE_TIER = """\
inferencers:
  - name: mlx-lm
    lifecycle: server
    detect:
      module: mlx_lm
    port: 8080
    health_url: http://127.0.0.1:{port}/v1/models
    start: ["mlx_lm.server"]
"""

_AGENTS_YAML = """\
agents:
  - name: codex
    type: codex
    command: codex
    sandbox: workspace-write
    timeout_seconds: 600
    api_key_env: CODEX_KEY_ENV
"""


def _write_configs(
    tmp_path: Path,
    *,
    models: str = _MODELS_YAML,
    inferencers: str = _INFERENCERS_YAML,
    agents: str = _AGENTS_YAML,
) -> dict[str, Path]:
    paths = {
        "models": tmp_path / "models.yaml",
        "inferencers": tmp_path / "inferencers.yaml",
        "agents": tmp_path / "agents.yaml",
        "suites": tmp_path / "suites.yaml",
    }
    paths["models"].write_text(models, encoding="utf-8")
    paths["inferencers"].write_text(inferencers, encoding="utf-8")
    paths["agents"].write_text(agents, encoding="utf-8")
    return paths


def _payload(tmp_path: Path, environ: dict[str, str] | None = None, **overrides) -> dict:
    paths = _write_configs(tmp_path)
    return settings_panel.settings_payload(
        models_path=overrides.get("models_path", paths["models"]),
        inferencers_path=overrides.get("inferencers_path", paths["inferencers"]),
        agents_path=overrides.get("agents_path", paths["agents"]),
        suites_path=overrides.get("suites_path", paths["suites"]),
        cache_dir=tmp_path / "no-cache",
        environ=environ if environ is not None else {},
    )


def _group(payload: dict, group_id: str) -> dict:
    return next(group for group in payload["groups"] if group["id"] == group_id)


def _item(group: dict, name: str) -> dict:
    return next(item for item in group["items"] if item["name"] == name)


def _fields(item: dict) -> dict[str, dict]:
    return {field["label"]: field for field in item["fields"]}


# ---------------------------------------------------------------------------
# AC1: one document grouping every config surface, labelled with source files
# ---------------------------------------------------------------------------


def test_payload_groups_every_surface(tmp_path: Path) -> None:
    payload = _payload(tmp_path)

    assert [group["id"] for group in payload["groups"]] == [
        "models",
        "inferencers",
        "storage",
        "suites",
        "agents",
        "settings",
    ]
    for group in payload["groups"]:
        assert group["error"] is None
        assert group["label"]


def test_each_group_is_labelled_with_its_source_file(tmp_path: Path) -> None:
    payload = _payload(tmp_path)

    assert _group(payload, "models")["source"].endswith("models.yaml")
    assert _group(payload, "inferencers")["source"].endswith("inferencers.yaml")
    # the storage tiers (local stores + external_repo + auto_tier) live in the
    # inferencers YAML, so that is the storage group's source file
    assert _group(payload, "storage")["source"].endswith("inferencers.yaml")
    assert _group(payload, "suites")["source"].endswith("suites.yaml")
    assert _group(payload, "agents")["source"].endswith("agents.yaml")


def test_payload_is_json_serializable(tmp_path: Path) -> None:
    json.dumps(_payload(tmp_path))


def test_model_items_carry_identity_and_serving_knobs(tmp_path: Path) -> None:
    models = _group(_payload(tmp_path), "models")
    fields = _fields(_item(models, "cloud-coder"))

    assert fields["type"]["value"] == "openai"
    assert fields["model id"]["value"] == "cloud-model"
    assert fields["pinned revision"]["value"] == "r1"
    assert fields["concurrency"]["value"] == 8
    assert fields["max tokens"]["value"] == 2048


def test_storage_group_shows_local_stores_external_repo_and_auto_tier(tmp_path: Path) -> None:
    storage = _group(_payload(tmp_path), "storage")

    store = _fields(_item(storage, "mlx-lm local store"))
    assert store["format"]["value"] == "hf-safetensors"
    assert "~/.cache/huggingface/hub" in store["paths"]["value"]

    external = _fields(_item(storage, "external_repo"))
    assert external["root"]["value"] == "/Volumes/SSD/repo"

    auto_tier = _fields(_item(storage, "auto_tier"))
    assert auto_tier["max local GiB"]["value"] == 100.0
    assert "local-mlx" in auto_tier["pinned models"]["value"]


def test_storage_group_marks_optional_tiers_not_configured(tmp_path: Path) -> None:
    paths = _write_configs(tmp_path, inferencers=_INFERENCERS_YAML_SINGLE_TIER)
    payload = settings_panel.settings_payload(
        models_path=paths["models"],
        inferencers_path=paths["inferencers"],
        agents_path=paths["agents"],
        suites_path=paths["suites"],
        cache_dir=tmp_path / "no-cache",
        environ={},
    )
    storage = _group(payload, "storage")

    assert _fields(_item(storage, "external_repo"))["status"]["value"] == "not configured"
    assert _fields(_item(storage, "auto_tier"))["status"]["value"] == "not configured"


def test_suites_group_lists_builtin_suites(tmp_path: Path) -> None:
    suites = _group(_payload(tmp_path), "suites")
    names = [item["name"] for item in suites["items"]]

    assert "humaneval" in names
    assert "canary" in names


def test_agent_items_carry_harness_settings(tmp_path: Path) -> None:
    agents = _group(_payload(tmp_path), "agents")
    fields = _fields(_item(agents, "codex"))

    assert fields["type"]["value"] == "codex"
    assert fields["command"]["value"] == "codex"
    assert fields["sandbox"]["value"] == "workspace-write"
    assert fields["timeout seconds"]["value"] == 600.0


# ---------------------------------------------------------------------------
# AC2: env-var names + set/unset indicator, never a value
# ---------------------------------------------------------------------------


def test_env_field_shows_name_and_set_indicator(tmp_path: Path) -> None:
    payload = _payload(tmp_path, environ={"EXAMPLE_API_KEY": "sk-hunter2"})
    fields = _fields(_item(_group(payload, "models"), "cloud-coder"))

    key_field = fields["API key env"]
    assert key_field["value"] == "EXAMPLE_API_KEY"
    assert key_field["is_set"] is True


def test_env_field_reports_unset_when_variable_is_absent(tmp_path: Path) -> None:
    payload = _payload(tmp_path, environ={})
    fields = _fields(_item(_group(payload, "models"), "cloud-coder"))

    assert fields["API key env"]["is_set"] is False


def test_agent_env_field_gets_the_same_indicator(tmp_path: Path) -> None:
    payload = _payload(tmp_path, environ={"CODEX_KEY_ENV": "value"})
    fields = _fields(_item(_group(payload, "agents"), "codex"))

    assert fields["API key env"]["value"] == "CODEX_KEY_ENV"
    assert fields["API key env"]["is_set"] is True


def test_payload_never_contains_an_env_value(tmp_path: Path) -> None:
    payload = _payload(
        tmp_path,
        environ={"EXAMPLE_API_KEY": "sk-hunter2", "CODEX_KEY_ENV": "tok-hunter3"},
    )

    dumped = json.dumps(payload)
    assert "sk-hunter2" not in dumped
    assert "tok-hunter3" not in dumped


def test_payload_never_contains_base_urls(tmp_path: Path) -> None:
    # base URLs stay server-side, matching the dashboard's catalog posture
    dumped = json.dumps(_payload(tmp_path))
    assert "api.example.test" not in dumped
    assert "localhost:8080/v1" not in dumped


# ---------------------------------------------------------------------------
# AC3: a missing / unparsable file degrades only its own group
# ---------------------------------------------------------------------------


def test_broken_models_file_degrades_only_the_models_group(tmp_path: Path) -> None:
    broken = tmp_path / "broken-models.yaml"
    broken.write_text("models: [broken", encoding="utf-8")
    payload = _payload(tmp_path, models_path=broken)

    models = _group(payload, "models")
    assert models["error"] is not None
    assert "broken-models.yaml" in models["error"]
    assert models["items"] == []
    for group_id in ("inferencers", "storage", "suites", "agents"):
        assert _group(payload, group_id)["error"] is None


def test_missing_agents_file_degrades_with_the_loader_message(tmp_path: Path) -> None:
    payload = _payload(tmp_path, agents_path=tmp_path / "absent-agents.yaml")

    agents = _group(payload, "agents")
    assert agents["error"] is not None
    assert "absent-agents.yaml" in agents["error"]
    assert _group(payload, "models")["error"] is None


def test_broken_inferencers_file_degrades_inferencers_and_storage_groups(tmp_path: Path) -> None:
    broken = tmp_path / "broken-inferencers.yaml"
    broken.write_text("inferencers: {not: a list}", encoding="utf-8")
    payload = _payload(tmp_path, inferencers_path=broken)

    assert _group(payload, "inferencers")["error"] is not None
    assert _group(payload, "storage")["error"] is not None
    assert _group(payload, "models")["error"] is None


# ---------------------------------------------------------------------------
# AC4: protocol-locked values are marked read-only with a rationale
# ---------------------------------------------------------------------------


def test_local_model_concurrency_is_locked_with_rationale(tmp_path: Path) -> None:
    models = _group(_payload(tmp_path), "models")
    concurrency = _fields(_item(models, "local-mlx"))["concurrency"]

    assert concurrency["locked"] is True
    assert concurrency["rationale"]


def test_cloud_model_concurrency_is_not_locked(tmp_path: Path) -> None:
    models = _group(_payload(tmp_path), "models")

    assert "locked" not in _fields(_item(models, "cloud-coder"))["concurrency"]


def test_benchmark_temperature_and_seed_are_locked(tmp_path: Path) -> None:
    suites = _group(_payload(tmp_path), "suites")
    fields = _fields(_item(suites, "benchmark protocol"))

    assert fields["temperature"]["value"] == 0.0
    assert fields["temperature"]["locked"] is True
    assert fields["temperature"]["rationale"]
    assert fields["seed"]["value"] == 0
    assert fields["seed"]["locked"] is True


# ---------------------------------------------------------------------------
# Story 15.3-003: editable groups and runner-fixed agent fields
# ---------------------------------------------------------------------------


def test_agent_type_is_locked_as_runner_fixed(tmp_path: Path) -> None:
    agents = _group(_payload(tmp_path), "agents")
    type_field = _fields(_item(agents, "codex"))["type"]

    assert type_field["locked"] is True
    assert "codex" in type_field["rationale"]  # names the supported harness set


def test_suites_and_agents_groups_are_flagged_editable_with_note(tmp_path: Path) -> None:
    payload = _payload(tmp_path)

    for group_id in ("suites", "agents"):
        group = _group(payload, group_id)
        assert group["editable"] is True
        assert group["editable_note"]
    assert "code" in _group(payload, "suites")["editable_note"]


def test_read_only_groups_are_not_flagged_editable(tmp_path: Path) -> None:
    payload = _payload(tmp_path)

    for group_id in ("models", "inferencers", "storage"):
        group = _group(payload, group_id)
        assert group["editable"] is False
        assert group["editable_note"] is None


# ---------------------------------------------------------------------------
# story 15.4-001: per-group source-file hash for external-change detection
# ---------------------------------------------------------------------------


def test_each_group_carries_its_source_content_hash(tmp_path: Path) -> None:
    paths = _write_configs(tmp_path)
    payload = _payload(tmp_path)

    models = _group(payload, "models")
    assert models["content_hash"] == content_hash(paths["models"].read_text(encoding="utf-8"))
    # groups sharing a source file share its hash (inferencers + storage)
    inferencers = _group(payload, "inferencers")
    storage = _group(payload, "storage")
    assert inferencers["content_hash"] == storage["content_hash"] is not None


def test_content_hash_changes_when_the_file_changes(tmp_path: Path) -> None:
    touched = tmp_path / "touched-models.yaml"
    touched.write_text(_MODELS_YAML + "# touched\n", encoding="utf-8")
    before = _group(_payload(tmp_path), "models")["content_hash"]
    after = _group(_payload(tmp_path, models_path=touched), "models")["content_hash"]

    assert before != after


def test_missing_source_file_has_no_content_hash(tmp_path: Path) -> None:
    # suites.yaml is never written by the fixtures — the group still renders
    suites = _group(_payload(tmp_path), "suites")
    assert suites["content_hash"] is None


def test_broken_group_still_reports_its_content_hash(tmp_path: Path) -> None:
    # the hash is the poll token even when the loader rejects the file, so the
    # tab can flag an out-of-band edit that broke the group
    broken = tmp_path / "broken-models.yaml"
    broken.write_text("models: [broken", encoding="utf-8")
    models = _group(_payload(tmp_path, models_path=broken), "models")

    assert models["error"] is not None
    assert models["content_hash"] == content_hash(broken.read_text(encoding="utf-8"))
