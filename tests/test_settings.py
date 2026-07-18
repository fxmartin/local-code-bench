"""Tests for the shared settings loader (story 15.5-001).

Precedence under test: env var > configs/settings.yaml > built-in fallback.
CLI flags sit above all three but are exercised through the CLI parser tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from local_code_bench import settings as settings_module
from local_code_bench.settings import (
    DEFAULT_SETTINGS_PATH,
    Settings,
    SettingsError,
    get_settings,
    load_settings,
)


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_missing_file_returns_builtin_fallbacks(tmp_path: Path) -> None:
    loaded = load_settings(tmp_path / "absent.yaml")
    assert loaded == Settings()


def test_shipped_yaml_equals_builtin_fallbacks() -> None:
    """The checked-in file must never drift from the coded fallbacks."""

    assert load_settings(DEFAULT_SETTINGS_PATH) == Settings()


def test_builtin_fallbacks_match_story_values() -> None:
    defaults = Settings()
    assert defaults.endpoint_max_tokens == 1024
    assert defaults.provider_timeout_seconds == 120.0
    assert defaults.chat_temperature == 0.7
    assert defaults.chat_max_tokens == 1024
    assert defaults.sandbox_timeout_seconds == 5.0
    assert defaults.dashboard_host == "127.0.0.1"
    assert defaults.dashboard_port == 8770
    assert defaults.unified_dashboard_port == 8765
    assert defaults.dashboard_state_file == ".runtime/dashboard.json"
    assert defaults.cache_dir == ".cache/benchmarks"
    assert defaults.results_dir == "results"
    assert defaults.inferencer_state_dir == ".runtime/inferencers"
    assert defaults.inferencer_start_timeout_seconds == 30.0
    assert defaults.inferencer_health_timeout_seconds == 1.0
    assert defaults.opencode_build_timeout_seconds == 60.0
    assert defaults.opencode_run_timeout_seconds == 10.0
    assert defaults.settings_backup_dir == ".runtime/settings-backups"
    assert defaults.settings_backup_retention == 10
    # Story 17.3-002: Chrome/Chromium detect candidates (detect-only, never
    # installed) and the per-render subprocess budget.
    assert defaults.pdf_renderer_candidates == (
        "google-chrome",
        "chromium",
        "Google Chrome.app/Contents/MacOS/Google Chrome",
        "Chromium.app/Contents/MacOS/Chromium",
    )
    assert defaults.pdf_render_timeout_seconds == 60.0


def test_yaml_overrides_apply(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text(
        "sandbox:\n  timeout_seconds: 9\nendpoint:\n  max_tokens: 2048\n",
        encoding="utf-8",
    )
    loaded = load_settings(path)
    assert loaded.sandbox_timeout_seconds == 9.0
    assert loaded.endpoint_max_tokens == 2048
    # untouched keys keep their fallbacks
    assert loaded.chat_temperature == 0.7


def test_partial_file_keeps_fallbacks(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text("chat:\n  temperature: 0.2\n", encoding="utf-8")
    loaded = load_settings(path)
    assert loaded.chat_temperature == 0.2
    assert loaded.chat_max_tokens == 1024


def test_empty_file_returns_fallbacks(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text("", encoding="utf-8")
    assert load_settings(path) == Settings()


def test_unknown_section_rejected(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text("typo_section:\n  key: 1\n", encoding="utf-8")
    with pytest.raises(SettingsError, match="typo_section"):
        load_settings(path)


def test_unknown_key_rejected(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text("sandbox:\n  timeout: 9\n", encoding="utf-8")
    with pytest.raises(SettingsError, match="sandbox.timeout"):
        load_settings(path)


def test_wrong_type_rejected(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text("endpoint:\n  max_tokens: lots\n", encoding="utf-8")
    with pytest.raises(SettingsError, match="endpoint.max_tokens"):
        load_settings(path)


def test_bool_is_not_an_int(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text("endpoint:\n  max_tokens: true\n", encoding="utf-8")
    with pytest.raises(SettingsError, match="endpoint.max_tokens"):
        load_settings(path)


def test_non_mapping_document_rejected(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(SettingsError, match="mapping"):
        load_settings(path)


def test_non_positive_numbers_rejected(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text("sandbox:\n  timeout_seconds: 0\n", encoding="utf-8")
    with pytest.raises(SettingsError, match="sandbox.timeout_seconds"):
        load_settings(path)


class TestPdfSection:
    """Story 17.3-002: renderer candidates are a configurable string list."""

    def test_renderer_candidates_override_applies(self, tmp_path: Path) -> None:
        path = tmp_path / "settings.yaml"
        path.write_text(
            "pdf:\n  renderer_candidates:\n    - brave-browser\n", encoding="utf-8"
        )
        loaded = load_settings(path)
        assert loaded.pdf_renderer_candidates == ("brave-browser",)

    def test_render_timeout_override_applies(self, tmp_path: Path) -> None:
        path = tmp_path / "settings.yaml"
        path.write_text("pdf:\n  render_timeout_seconds: 15\n", encoding="utf-8")
        assert load_settings(path).pdf_render_timeout_seconds == 15.0

    def test_non_list_candidates_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "settings.yaml"
        path.write_text("pdf:\n  renderer_candidates: chromium\n", encoding="utf-8")
        with pytest.raises(SettingsError, match="pdf.renderer_candidates"):
            load_settings(path)

    def test_non_string_candidate_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "settings.yaml"
        path.write_text("pdf:\n  renderer_candidates:\n    - 7\n", encoding="utf-8")
        with pytest.raises(SettingsError, match="pdf.renderer_candidates"):
            load_settings(path)

    def test_non_positive_render_timeout_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "settings.yaml"
        path.write_text("pdf:\n  render_timeout_seconds: 0\n", encoding="utf-8")
        with pytest.raises(SettingsError, match="pdf.render_timeout_seconds"):
            load_settings(path)


class TestProtocolSection:
    """The read-only measurement-protocol section refuses any override."""

    def test_matching_protocol_values_accepted(self, tmp_path: Path) -> None:
        path = tmp_path / "settings.yaml"
        path.write_text(
            "protocol:\n"
            "  benchmark_temperature: 0.0\n"
            "  benchmark_seed: 0\n"
            "  local_concurrency: 1\n",
            encoding="utf-8",
        )
        assert load_settings(path) == Settings()

    def test_protocol_override_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "settings.yaml"
        path.write_text("protocol:\n  benchmark_temperature: 0.7\n", encoding="utf-8")
        with pytest.raises(SettingsError, match="read-only"):
            load_settings(path)

    def test_protocol_unknown_key_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "settings.yaml"
        path.write_text("protocol:\n  canary_humaneval_ids: []\n", encoding="utf-8")
        with pytest.raises(SettingsError, match="protocol.canary_humaneval_ids"):
            load_settings(path)


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()


class TestConsumersUseSettings:
    """Constants at the audited call sites come from the shared loader."""

    def test_runner_endpoint_max_tokens(self) -> None:
        from local_code_bench import runner

        assert runner.DEFAULT_ENDPOINT_MAX_TOKENS == get_settings().endpoint_max_tokens

    def test_chat_defaults(self) -> None:
        from local_code_bench import chat

        assert chat.DEFAULT_TEMPERATURE == get_settings().chat_temperature
        assert chat.DEFAULT_MAX_TOKENS == get_settings().chat_max_tokens

    def test_sandbox_timeout(self) -> None:
        from local_code_bench import sandbox

        assert sandbox.DEFAULT_SANDBOX_TIMEOUT_SECONDS == get_settings().sandbox_timeout_seconds

    def test_provider_timeout_fallback_uses_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from local_code_bench import provider

        monkeypatch.delenv("BENCH_PROVIDER_TIMEOUT_SECONDS", raising=False)
        monkeypatch.setattr(
            provider,
            "get_settings",
            lambda: Settings(provider_timeout_seconds=42.0),
        )
        assert provider._provider_timeout_seconds() == 42.0

    def test_provider_timeout_env_wins_over_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from local_code_bench import provider

        monkeypatch.setenv("BENCH_PROVIDER_TIMEOUT_SECONDS", "12.5")
        monkeypatch.setattr(
            provider,
            "get_settings",
            lambda: Settings(provider_timeout_seconds=42.0),
        )
        assert provider._provider_timeout_seconds() == 12.5

    def test_inferencer_manager_timeouts(self) -> None:
        from local_code_bench.inferencers import manager

        assert manager.DEFAULT_START_TIMEOUT_SECONDS == (
            get_settings().inferencer_start_timeout_seconds
        )
        assert manager.DEFAULT_HEALTH_TIMEOUT_SECONDS == (
            get_settings().inferencer_health_timeout_seconds
        )

    def test_opencode_blackbox_timeouts(self) -> None:
        from local_code_bench.opencode import blackbox

        assert blackbox.DEFAULT_BUILD_TIMEOUT_SECONDS == (
            get_settings().opencode_build_timeout_seconds
        )
        assert blackbox.DEFAULT_RUN_TIMEOUT_SECONDS == (get_settings().opencode_run_timeout_seconds)

    def test_tasks_cache_dir(self) -> None:
        from local_code_bench import suite_catalog, tasks

        assert tasks.DEFAULT_CACHE_DIR == get_settings().cache_dir
        assert suite_catalog.DEFAULT_CACHE_DIR == get_settings().cache_dir

    def test_cli_defaults_come_from_settings(self) -> None:
        from local_code_bench.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([])
        loaded = get_settings()
        assert args.results_dir == loaded.results_dir
        assert args.cache_dir == loaded.cache_dir
        assert args.port == loaded.dashboard_port
        assert args.inferencer_state_dir == loaded.inferencer_state_dir

    def test_cli_dashboard_subcommand_defaults(self) -> None:
        from local_code_bench.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["dashboard"])
        loaded = get_settings()
        assert args.port == loaded.unified_dashboard_port
        assert args.state_dir == loaded.inferencer_state_dir
        assert str(args.state_file) == loaded.dashboard_state_file

    def test_cli_flag_overrides_settings(self) -> None:
        from local_code_bench.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["--port", "9999"])
        assert args.port == 9999


def test_settings_module_reexports_for_backfill_story() -> None:
    """15.2-001 (settings write backups) consumes these reserved keys."""

    loaded = settings_module.load_settings(DEFAULT_SETTINGS_PATH)
    assert loaded.settings_backup_dir == ".runtime/settings-backups"
    assert loaded.settings_backup_retention == 10
