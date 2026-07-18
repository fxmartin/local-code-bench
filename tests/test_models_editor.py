"""Story 15.3-001: Models editor — thin form actions over the 15.2-001 pipeline."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from local_code_bench import models_editor
from local_code_bench import unified_dashboard as ud
from local_code_bench.config import load_models
from local_code_bench.settings_store import SettingsStore, content_hash

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
    inferencer: mlx-lm
    quant: 4bit
    price_per_1k_tokens:
      input: 0.0
      output: 0.0
  - name: cloud-coder
    type: openai
    base_url: https://api.example.test/v1
    model_id: cloud-model
    pinned_revision: r1
    api_key_env: EXAMPLE_API_KEY
    concurrency: 8
    max_tokens: 2048
    extra_body:
      reasoning:
        enabled: false
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
"""

_AGENTS_YAML = """\
agents:
  - name: codex
    type: codex
    command: codex
    sandbox: workspace-write
    timeout_seconds: 600
"""

_SUITES_YAML = "suites: []\n"

_FIXED_NOW = datetime(2026, 7, 18, 9, 0, 0, tzinfo=UTC)


def _config_dir(tmp_path: Path) -> Path:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    (config_dir / "models.yaml").write_text(_MODELS_YAML, encoding="utf-8")
    (config_dir / "agents.yaml").write_text(_AGENTS_YAML, encoding="utf-8")
    (config_dir / "inferencers.yaml").write_text(_INFERENCERS_YAML, encoding="utf-8")
    (config_dir / "suites.yaml").write_text(_SUITES_YAML, encoding="utf-8")
    return config_dir


def _store(tmp_path: Path) -> SettingsStore:
    return SettingsStore(_config_dir(tmp_path), now=lambda: _FIXED_NOW)


def _cloud_entry(**overrides) -> dict:
    entry = {
        "name": "new-cloud",
        "type": "openai",
        "endpoint_url": "https://api.example.test/v1",
        "model_id": "new-model",
        "pinned_revision": "r2",
        "key_env": "EXAMPLE_API_KEY",
        "concurrency": 4,
        "max_tokens": 1024,
        "extra_body": "reasoning:\n  enabled: false\n",
        "price_input": 0.001,
        "price_output": 0.002,
        "inferencer": None,
    }
    entry.update(overrides)
    return entry


def _payload(store: SettingsStore) -> dict:
    status, payload = models_editor.models_editor_payload(store)
    assert status == 200
    return payload


def _hash(store: SettingsStore) -> str:
    return _payload(store)["content_hash"]


# ---------------------------------------------------------------------------
# editor payload (form prefill)
# ---------------------------------------------------------------------------


def test_payload_exposes_every_form_field(tmp_path: Path) -> None:
    payload = _payload(_store(tmp_path))

    cloud = next(m for m in payload["models"] if m["name"] == "cloud-coder")
    assert cloud["type"] == "openai"
    assert cloud["endpoint_url"] == "https://api.example.test/v1"
    assert cloud["model_id"] == "cloud-model"
    assert cloud["pinned_revision"] == "r1"
    assert cloud["key_env"] == "EXAMPLE_API_KEY"
    assert cloud["concurrency"] == 8
    assert cloud["max_tokens"] == 2048
    assert "enabled: false" in cloud["extra_body"]
    assert cloud["price_input"] == 0.001
    assert cloud["price_output"] == 0.002
    assert cloud["inferencer"] is None
    assert payload["content_hash"] == content_hash(
        (tmp_path / "configs" / "models.yaml").read_text(encoding="utf-8")
    )


def test_payload_flags_local_entries_with_the_rationale(tmp_path: Path) -> None:
    payload = _payload(_store(tmp_path))

    local = next(m for m in payload["models"] if m["name"] == "local-mlx")
    cloud = next(m for m in payload["models"] if m["name"] == "cloud-coder")
    assert local["local"] is True
    assert cloud["local"] is False
    assert "prefill/decode" in payload["concurrency_rationale"]


def test_payload_lists_unmanaged_keys_the_form_preserves(tmp_path: Path) -> None:
    local = next(m for m in _payload(_store(tmp_path))["models"] if m["name"] == "local-mlx")

    assert local["other_keys"] == ["quant"]


def test_payload_rejects_a_broken_models_file(tmp_path: Path) -> None:
    store = _store(tmp_path)
    (tmp_path / "configs" / "models.yaml").write_text("models: [broken", encoding="utf-8")

    status, payload = models_editor.models_editor_payload(store)

    assert status == 422
    assert "error" in payload


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------


def test_add_appends_a_loader_valid_entry(tmp_path: Path) -> None:
    store = _store(tmp_path)

    status, payload = models_editor.apply_models_action(
        store, {"op": "add", "entry": _cloud_entry(), "expected_hash": _hash(store)}
    )

    assert status == 200
    models = load_models(tmp_path / "configs" / "models.yaml")
    assert "new-cloud" in models
    assert models["new-cloud"].extra_body == {"reasoning": {"enabled": False}}
    assert models["new-cloud"].price_per_1k_tokens.input == 0.001
    # success responses return the fresh editor payload for one-round-trip refresh
    assert any(m["name"] == "new-cloud" for m in payload["models"])


def test_add_preserves_comments_in_the_document(tmp_path: Path) -> None:
    store = _store(tmp_path)

    status, _ = models_editor.apply_models_action(
        store, {"op": "add", "entry": _cloud_entry(), "expected_hash": _hash(store)}
    )

    assert status == 200
    content = (tmp_path / "configs" / "models.yaml").read_text(encoding="utf-8")
    assert "# Benchmark model matrix — protocol v1." in content
    assert "# Local MLX baseline (concurrency locked to 1 by protocol)." in content


def test_add_omits_unset_optional_fields(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entry = _cloud_entry(
        name="bare", key_env=None, max_tokens=None, extra_body="", inferencer=None
    )

    status, _ = models_editor.apply_models_action(
        store, {"op": "add", "entry": entry, "expected_hash": _hash(store)}
    )

    assert status == 200
    bare = load_models(tmp_path / "configs" / "models.yaml")["bare"]
    assert bare.api_key_env is None
    assert bare.max_tokens is None
    assert bare.extra_body is None
    assert bare.inferencer is None


def test_add_rejects_a_duplicate_name_before_the_write_pipeline(tmp_path: Path) -> None:
    store = _store(tmp_path)
    before = (tmp_path / "configs" / "models.yaml").read_text(encoding="utf-8")

    status, payload = models_editor.apply_models_action(
        store,
        {"op": "add", "entry": _cloud_entry(name="cloud-coder"), "expected_hash": _hash(store)},
    )

    assert status == 400
    assert "cloud-coder" in payload["error"]
    # rejected before the write pipeline: no bytes changed, no backup created
    assert (tmp_path / "configs" / "models.yaml").read_text(encoding="utf-8") == before
    assert not (tmp_path / "configs" / ".backups").exists()


# ---------------------------------------------------------------------------
# field pre-validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_price", ["free", None, -0.5, True])
def test_prices_must_be_non_negative_numbers(tmp_path: Path, bad_price) -> None:
    store = _store(tmp_path)

    status, payload = models_editor.apply_models_action(
        store,
        {"op": "add", "entry": _cloud_entry(price_input=bad_price), "expected_hash": _hash(store)},
    )

    assert status == 400
    assert any("price" in e for e in payload["errors"])


def test_missing_required_fields_are_reported_together(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entry = _cloud_entry(name="", endpoint_url="", model_id="")

    status, payload = models_editor.apply_models_action(
        store, {"op": "add", "entry": entry, "expected_hash": _hash(store)}
    )

    assert status == 400
    joined = " ".join(payload["errors"])
    assert "name" in joined and "base_url" in joined and "model_id" in joined


def test_type_must_be_a_known_model_type(tmp_path: Path) -> None:
    store = _store(tmp_path)

    status, payload = models_editor.apply_models_action(
        store,
        {"op": "add", "entry": _cloud_entry(type="magic"), "expected_hash": _hash(store)},
    )

    assert status == 400
    assert any("type" in e for e in payload["errors"])


@pytest.mark.parametrize("bad", ["zero", 0, -1, 1.5, True])
def test_concurrency_must_be_a_positive_integer(tmp_path: Path, bad) -> None:
    store = _store(tmp_path)

    status, payload = models_editor.apply_models_action(
        store,
        {"op": "add", "entry": _cloud_entry(concurrency=bad), "expected_hash": _hash(store)},
    )

    assert status == 400
    assert any("concurrency" in e for e in payload["errors"])


def test_extra_body_must_be_a_yaml_mapping(tmp_path: Path) -> None:
    store = _store(tmp_path)

    status, payload = models_editor.apply_models_action(
        store,
        {
            "op": "add",
            "entry": _cloud_entry(extra_body="- just\n- a list\n"),
            "expected_hash": _hash(store),
        },
    )

    assert status == 400
    assert any("extra_body" in e for e in payload["errors"])


def test_extra_body_invalid_yaml_is_named(tmp_path: Path) -> None:
    store = _store(tmp_path)

    status, payload = models_editor.apply_models_action(
        store,
        {
            "op": "add",
            "entry": _cloud_entry(extra_body="reasoning: [broken"),
            "expected_hash": _hash(store),
        },
    )

    assert status == 400
    assert any("extra_body" in e for e in payload["errors"])


# ---------------------------------------------------------------------------
# local concurrency lock
# ---------------------------------------------------------------------------


def test_local_inferencer_entry_locks_concurrency_at_one(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entry = _cloud_entry(name="new-local", inferencer="mlx-lm", concurrency=4)

    status, payload = models_editor.apply_models_action(
        store, {"op": "add", "entry": entry, "expected_hash": _hash(store)}
    )

    assert status == 400
    assert any("concurrency" in e and "prefill/decode" in e for e in payload["errors"])


def test_localhost_endpoint_entry_locks_concurrency_at_one(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entry = _cloud_entry(name="new-local", endpoint_url="http://localhost:8080/v1", concurrency=2)

    status, payload = models_editor.apply_models_action(
        store, {"op": "add", "entry": entry, "expected_hash": _hash(store)}
    )

    assert status == 400
    assert any("concurrency" in e for e in payload["errors"])


def test_local_entry_at_concurrency_one_is_accepted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entry = _cloud_entry(name="new-local", inferencer="mlx-lm", concurrency=1)

    status, _ = models_editor.apply_models_action(
        store, {"op": "add", "entry": entry, "expected_hash": _hash(store)}
    )

    assert status == 200


def test_cloud_entry_keeps_concurrency_editable(tmp_path: Path) -> None:
    store = _store(tmp_path)

    status, _ = models_editor.apply_models_action(
        store,
        {"op": "add", "entry": _cloud_entry(concurrency=32), "expected_hash": _hash(store)},
    )

    assert status == 200
    assert load_models(tmp_path / "configs" / "models.yaml")["new-cloud"].concurrency == 32


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


def test_update_edits_managed_fields_and_preserves_the_rest(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entry = _cloud_entry(
        name="cloud-coder",
        model_id="cloud-model-v2",
        concurrency=16,
        price_input=0.002,
        price_output=0.004,
    )

    status, _ = models_editor.apply_models_action(
        store,
        {"op": "update", "name": "cloud-coder", "entry": entry, "expected_hash": _hash(store)},
    )

    assert status == 200
    models = load_models(tmp_path / "configs" / "models.yaml")
    assert models["cloud-coder"].model_id == "cloud-model-v2"
    assert models["cloud-coder"].concurrency == 16
    assert models["cloud-coder"].price_per_1k_tokens.input == 0.002
    # unmanaged keys survive an edit untouched
    assert models["local-mlx"].quant == "4bit"
    content = (tmp_path / "configs" / "models.yaml").read_text(encoding="utf-8")
    assert "# Benchmark model matrix — protocol v1." in content


def test_update_can_rename_an_entry(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entry = _cloud_entry(name="cloud-coder-v2")

    status, _ = models_editor.apply_models_action(
        store,
        {"op": "update", "name": "cloud-coder", "entry": entry, "expected_hash": _hash(store)},
    )

    assert status == 200
    models = load_models(tmp_path / "configs" / "models.yaml")
    assert "cloud-coder-v2" in models and "cloud-coder" not in models


def test_update_rename_onto_an_existing_name_names_the_clash(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entry = _cloud_entry(name="local-mlx")

    status, payload = models_editor.apply_models_action(
        store,
        {"op": "update", "name": "cloud-coder", "entry": entry, "expected_hash": _hash(store)},
    )

    assert status == 400
    assert "local-mlx" in payload["error"]


def test_update_clearing_optional_fields_removes_their_keys(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entry = _cloud_entry(
        name="cloud-coder", key_env=None, max_tokens=None, extra_body=""
    )

    status, _ = models_editor.apply_models_action(
        store,
        {"op": "update", "name": "cloud-coder", "entry": entry, "expected_hash": _hash(store)},
    )

    assert status == 200
    cloud = load_models(tmp_path / "configs" / "models.yaml")["cloud-coder"]
    assert cloud.api_key_env is None
    assert cloud.max_tokens is None
    assert cloud.extra_body is None


def test_update_unknown_model_is_404(tmp_path: Path) -> None:
    store = _store(tmp_path)

    status, payload = models_editor.apply_models_action(
        store,
        {"op": "update", "name": "ghost", "entry": _cloud_entry(), "expected_hash": _hash(store)},
    )

    assert status == 404
    assert "ghost" in payload["error"]


# ---------------------------------------------------------------------------
# duplicate
# ---------------------------------------------------------------------------


def test_duplicate_copies_every_key_under_the_new_name(tmp_path: Path) -> None:
    store = _store(tmp_path)

    status, _ = models_editor.apply_models_action(
        store,
        {
            "op": "duplicate",
            "name": "local-mlx",
            "new_name": "local-mlx-copy",
            "expected_hash": _hash(store),
        },
    )

    assert status == 200
    models = load_models(tmp_path / "configs" / "models.yaml")
    copy = models["local-mlx-copy"]
    assert copy.model_id == "mlx-model"
    assert copy.inferencer == "mlx-lm"
    assert copy.quant == "4bit"  # unmanaged keys travel with the copy
    assert models["local-mlx"].quant == "4bit"


def test_duplicate_rejects_a_clashing_new_name(tmp_path: Path) -> None:
    store = _store(tmp_path)

    status, payload = models_editor.apply_models_action(
        store,
        {
            "op": "duplicate",
            "name": "local-mlx",
            "new_name": "cloud-coder",
            "expected_hash": _hash(store),
        },
    )

    assert status == 400
    assert "cloud-coder" in payload["error"]


def test_duplicate_requires_a_new_name(tmp_path: Path) -> None:
    store = _store(tmp_path)

    status, _ = models_editor.apply_models_action(
        store,
        {"op": "duplicate", "name": "local-mlx", "new_name": "", "expected_hash": _hash(store)},
    )

    assert status == 400


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


def test_remove_requires_explicit_confirmation(tmp_path: Path) -> None:
    store = _store(tmp_path)

    status, payload = models_editor.apply_models_action(
        store, {"op": "remove", "name": "cloud-coder", "expected_hash": _hash(store)}
    )

    assert status == 400
    assert "confirm" in payload["error"]
    assert "cloud-coder" in load_models(tmp_path / "configs" / "models.yaml")


def test_confirmed_remove_goes_through_the_validated_write_path(tmp_path: Path) -> None:
    store = _store(tmp_path)

    status, _ = models_editor.apply_models_action(
        store,
        {"op": "remove", "name": "cloud-coder", "confirm": True, "expected_hash": _hash(store)},
    )

    assert status == 200
    assert "cloud-coder" not in load_models(tmp_path / "configs" / "models.yaml")
    # the 15.2-001 pipeline ran: a timestamped backup of the previous version exists
    assert list((tmp_path / "configs" / ".backups").glob("models.yaml.*"))


def test_remove_unknown_model_is_404(tmp_path: Path) -> None:
    store = _store(tmp_path)

    status, _ = models_editor.apply_models_action(
        store, {"op": "remove", "name": "ghost", "confirm": True, "expected_hash": _hash(store)}
    )

    assert status == 404


# ---------------------------------------------------------------------------
# conflicts + malformed actions
# ---------------------------------------------------------------------------


def test_stale_hash_is_a_conflict(tmp_path: Path) -> None:
    store = _store(tmp_path)

    status, payload = models_editor.apply_models_action(
        store, {"op": "add", "entry": _cloud_entry(), "expected_hash": "0" * 64}
    )

    assert status == 409
    assert "error" in payload


def test_unknown_op_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)

    status, _ = models_editor.apply_models_action(
        store, {"op": "explode", "expected_hash": _hash(store)}
    )

    assert status == 400


def test_non_mapping_action_is_rejected(tmp_path: Path) -> None:
    status, _ = models_editor.apply_models_action(_store(tmp_path), ["not", "a", "dict"])

    assert status == 400


# ---------------------------------------------------------------------------
# dashboard endpoints (GET/POST /api/settings/models)
# ---------------------------------------------------------------------------


def _editor_ctx(tmp_path: Path) -> ud.DashboardContext:
    config_dir = _config_dir(tmp_path)
    models_path = config_dir / "models.yaml"
    return ud.DashboardContext(
        configs={},
        state_dir=tmp_path / "state",
        models=load_models(models_path),
        models_path=models_path,
        inferencers_path=config_dir / "inferencers.yaml",
        agents_path=config_dir / "agents.yaml",
        suites_path=config_dir / "suites.yaml",
        cache_dir=tmp_path / "no-cache",
        settings_store=SettingsStore(config_dir, now=lambda: _FIXED_NOW),
    )


def test_get_editor_endpoint_serves_the_form_payload(tmp_path: Path) -> None:
    resp = ud.handle_request("GET", "/api/settings/models", _editor_ctx(tmp_path))

    assert resp.status == 200
    payload = json.loads(resp.body)
    cloud = next(m for m in payload["models"] if m["name"] == "cloud-coder")
    # the form needs the endpoint URL and the env-var *name*; both ship under
    # editor-specific keys so they survive the 09.6-001 sanitize seam
    assert cloud["endpoint_url"] == "https://api.example.test/v1"
    assert cloud["key_env"] == "EXAMPLE_API_KEY"
    assert payload["content_hash"]


def test_post_add_updates_models_list_and_launcher_without_restart(tmp_path: Path) -> None:
    ctx = _editor_ctx(tmp_path)
    doc_hash = json.loads(
        ud.handle_request("GET", "/api/settings/models", ctx).body
    )["content_hash"]

    body = json.dumps(
        {"op": "add", "entry": _cloud_entry(), "expected_hash": doc_hash}
    ).encode("utf-8")
    resp = ud.handle_request("POST", "/api/settings/models", ctx, body)

    assert resp.status == 200
    # the in-memory registry refreshed in place: the launcher catalog sees it
    catalog = json.loads(ud.handle_request("GET", "/api/catalog", ctx).body)
    assert any(m["name"] == "new-cloud" for m in catalog["models"])
    # and the read-only Settings view re-reads the file per request
    settings = json.loads(ud.handle_request("GET", "/api/settings", ctx).body)
    models_group = next(g for g in settings["groups"] if g["id"] == "models")
    assert any(item["name"] == "new-cloud" for item in models_group["items"])


def test_post_rejection_leaves_models_list_unchanged(tmp_path: Path) -> None:
    ctx = _editor_ctx(tmp_path)
    doc_hash = json.loads(
        ud.handle_request("GET", "/api/settings/models", ctx).body
    )["content_hash"]

    body = json.dumps(
        {"op": "add", "entry": _cloud_entry(name="cloud-coder"), "expected_hash": doc_hash}
    ).encode("utf-8")
    resp = ud.handle_request("POST", "/api/settings/models", ctx, body)

    assert resp.status == 400
    assert set(ctx.models) == {"local-mlx", "cloud-coder"}


def test_post_stale_hash_is_409(tmp_path: Path) -> None:
    ctx = _editor_ctx(tmp_path)

    body = json.dumps(
        {"op": "add", "entry": _cloud_entry(), "expected_hash": "0" * 64}
    ).encode("utf-8")

    assert ud.handle_request("POST", "/api/settings/models", ctx, body).status == 409


def test_post_invalid_json_body_is_400(tmp_path: Path) -> None:
    resp = ud.handle_request("POST", "/api/settings/models", _editor_ctx(tmp_path), b"{nope")

    assert resp.status == 400


def test_page_ships_the_models_editor_form() -> None:
    body = ud.render_page()
    assert 'id="model-form"' in body
    assert "/api/settings/models" in body
    # a successful save tells the launcher to reload its catalog
    assert "models-changed" in body
