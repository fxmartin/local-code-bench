"""Story 16.4-001 — theme settings in ``configs/settings.yaml`` and the Settings tab.

Contract under test:

* A ``theme:`` block (``accent``, ``danger``, ``default_mode``) feeds the token
  layer; the shipped defaults (``#1e40af`` / ``#991b1b`` / ``system``) apply when
  the block is absent, so the file stays additive.
* Dark-mode tints are derived from whatever hues are configured (one hue per
  role survives customization) and the token block carries a computed
  ``--accent-contrast``.
* Invalid values (malformed hex, unknown mode) are rejected with a clear
  loader error — through the 15.2 store pipeline too — never rendered.
* The Settings tab's Harness/theme group edits the file through the validated
  write path and the dashboard reflects the change on next refresh.
* An accent with poor AA contrast against either mode's ``--bg`` warns on save
  but never blocks the write.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from local_code_bench import settings as settings_module
from local_code_bench import settings_editor, settings_panel, theme, unified_dashboard
from local_code_bench.settings import (
    Settings,
    SettingsError,
    load_settings,
    load_theme_config,
    theme_config,
)
from local_code_bench.settings_store import SettingsStore, SettingsValidationError
from local_code_bench.theme import ThemeConfig


def _write_settings(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "settings.yaml"
    path.write_text(body, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Loader — theme block, additive defaults, validation
# --------------------------------------------------------------------------- #


def test_shipped_defaults_are_story_values() -> None:
    defaults = Settings()
    assert defaults.theme_accent == "#1e40af"
    assert defaults.theme_danger == "#991b1b"
    assert defaults.theme_default_mode == "system"


def test_missing_theme_block_yields_defaults(tmp_path: Path) -> None:
    path = _write_settings(tmp_path, "chat:\n  temperature: 0.2\n")
    loaded = load_settings(path)
    assert loaded.theme_accent == theme.DEFAULT_ACCENT
    assert loaded.theme_danger == theme.DEFAULT_DANGER
    assert loaded.theme_default_mode == theme.DEFAULT_MODE


def test_theme_block_overrides_apply(tmp_path: Path) -> None:
    path = _write_settings(
        tmp_path,
        'theme:\n  accent: "#663399"\n  danger: "#8b0000"\n  default_mode: dark\n',
    )
    loaded = load_settings(path)
    assert loaded.theme_accent == "#663399"
    assert loaded.theme_danger == "#8b0000"
    assert loaded.theme_default_mode == "dark"


@pytest.mark.parametrize("value", ["663399", "#66339", "#xyzxyz", "#6639", "blue"])
def test_malformed_hex_rejected(tmp_path: Path, value: str) -> None:
    path = _write_settings(tmp_path, f'theme:\n  accent: "{value}"\n')
    with pytest.raises(SettingsError, match="theme.accent"):
        load_settings(path)


def test_malformed_danger_hex_rejected(tmp_path: Path) -> None:
    path = _write_settings(tmp_path, "theme:\n  danger: nope\n")
    with pytest.raises(SettingsError, match="theme.danger"):
        load_settings(path)


def test_unknown_mode_rejected(tmp_path: Path) -> None:
    path = _write_settings(tmp_path, "theme:\n  default_mode: sepia\n")
    with pytest.raises(SettingsError, match="theme.default_mode"):
        load_settings(path)


def test_non_string_theme_value_rejected(tmp_path: Path) -> None:
    path = _write_settings(tmp_path, "theme:\n  accent: 12\n")
    with pytest.raises(SettingsError, match="theme.accent"):
        load_settings(path)


def test_theme_config_bridges_settings_to_theme_layer() -> None:
    config = theme_config(Settings(theme_accent="#663399", theme_default_mode="light"))
    assert config == ThemeConfig(accent="#663399", default_mode="light")


def test_load_theme_config_falls_back_to_defaults_on_broken_file(tmp_path: Path) -> None:
    # A hand-edited invalid file surfaces as a loader error elsewhere; the
    # render path must still get a usable theme, never a broken one.
    path = _write_settings(tmp_path, "theme:\n  accent: broken\n")
    assert load_theme_config(path) == ThemeConfig()


def test_load_theme_config_reads_configured_values(tmp_path: Path) -> None:
    path = _write_settings(tmp_path, 'theme:\n  accent: "#663399"\n')
    assert load_theme_config(path).accent == "#663399"


# --------------------------------------------------------------------------- #
# Derivation — dark tints and the computed accent-contrast token
# --------------------------------------------------------------------------- #


def test_contrast_ratio_spans_the_wcag_range() -> None:
    assert theme.contrast_ratio("#ffffff", "#000000") == pytest.approx(21.0)
    assert theme.contrast_ratio("#808080", "#808080") == pytest.approx(1.0)


def test_dark_tint_meets_aa_against_dark_bg() -> None:
    for hue in ("#1e40af", "#991b1b", "#663399", "#004400"):
        tint = theme.dark_tint(hue)
        assert theme.contrast_ratio(tint, theme.BLACK) >= 4.5, hue


def test_dark_tint_keeps_an_already_readable_hue() -> None:
    assert theme.dark_tint("#7aa2ff") == "#7aa2ff"


def test_module_constants_derive_from_the_defaults() -> None:
    assert theme.ACCENT == theme.DEFAULT_ACCENT == "#1e40af"
    assert theme.DANGER == theme.DEFAULT_DANGER == "#991b1b"
    assert theme.ACCENT_DARK == theme.dark_tint(theme.DEFAULT_ACCENT)
    assert theme.DANGER_DARK == theme.dark_tint(theme.DEFAULT_DANGER)


def test_tokens_css_injects_configured_hues_and_derived_tints() -> None:
    config = ThemeConfig(accent="#663399", danger="#8b0000")
    css = theme.tokens_css(config)
    assert f"--accent: light-dark(#663399, {theme.dark_tint('#663399')});" in css
    assert f"--danger: light-dark(#8b0000, {theme.dark_tint('#8b0000')});" in css


def test_tokens_css_defaults_match_module_constant() -> None:
    assert theme.tokens_css() == theme.TOKENS_CSS
    assert "--accent: light-dark(#1e40af," in theme.TOKENS_CSS


def test_accent_contrast_token_is_computed_per_scheme() -> None:
    css = theme.tokens_css()
    # Dark blue accent carries white text in light mode; the anchor references
    # keep the token block free of extra literals.
    assert "--accent-contrast: light-dark(var(--white)," in css
    for stop, pick in (
        (theme.ACCENT, theme.accent_contrast(theme.ACCENT)),
        (theme.ACCENT_DARK, theme.accent_contrast(theme.ACCENT_DARK)),
    ):
        expected = max(
            (theme.WHITE, theme.BLACK), key=lambda anchor: theme.contrast_ratio(stop, anchor)
        )
        assert pick == ("var(--white)" if expected == theme.WHITE else "var(--black)")


# --------------------------------------------------------------------------- #
# Default mode — the pre-paint script honors the configured initial mode
# --------------------------------------------------------------------------- #


def test_head_snippet_system_mode_matches_the_legacy_snippet() -> None:
    assert theme.theme_head_snippet("system") == theme.THEME_HEAD_SNIPPET


def test_head_snippet_forces_configured_mode_when_nothing_stored() -> None:
    snippet = theme.theme_head_snippet("dark")
    assert '"dark"' in snippet
    assert "dataset.theme" in snippet
    # The stored per-browser preference still wins over the configured default.
    assert f'localStorage.getItem("{theme.THEME_STORAGE_KEY}")' in snippet


# --------------------------------------------------------------------------- #
# Contrast warnings — warn, never block
# --------------------------------------------------------------------------- #


def test_default_theme_produces_no_warnings() -> None:
    assert theme.contrast_warnings(ThemeConfig()) == []


def test_low_contrast_accent_warns_for_light_mode() -> None:
    warnings = theme.contrast_warnings(ThemeConfig(accent="#ffff00"))
    assert len(warnings) == 1
    assert "accent" in warnings[0]
    assert "light" in warnings[0]
    assert "4.5" in warnings[0]


def test_low_contrast_danger_warns_too() -> None:
    warnings = theme.contrast_warnings(ThemeConfig(danger="#ffc0cb"))
    assert warnings and "danger" in warnings[0]


# --------------------------------------------------------------------------- #
# 15.2 pipeline — settings.yaml is a registered, validated, editable config
# --------------------------------------------------------------------------- #


@pytest.fixture()
def store(tmp_path: Path) -> SettingsStore:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    (config_dir / "settings.yaml").write_text(
        'theme:\n  accent: "#1e40af"\n', encoding="utf-8"
    )
    return SettingsStore(config_dir, backup_dir=tmp_path / "backups")


def test_store_validates_theme_edits_with_the_loader(store: SettingsStore) -> None:
    document = store.read("settings")
    with pytest.raises(SettingsValidationError, match="theme.accent"):
        store.write(
            "settings", "theme:\n  accent: nope\n", expected_hash=document.content_hash
        )


def test_store_accepts_a_valid_theme_edit(store: SettingsStore) -> None:
    document = store.read("settings")
    result = store.write(
        "settings", 'theme:\n  accent: "#663399"\n', expected_hash=document.content_hash
    )
    assert load_settings(result.path).theme_accent == "#663399"


def test_editor_exposes_the_settings_config(store: SettingsStore) -> None:
    status, payload = settings_editor.read_action(store, "settings")
    assert status == 200
    assert payload["source"] == "settings.yaml"


def test_editor_write_rejects_invalid_theme_with_loader_error(store: SettingsStore) -> None:
    _, document = settings_editor.read_action(store, "settings")
    status, payload = settings_editor.write_action(
        store,
        "settings",
        {"content": "theme:\n  default_mode: sepia\n", "expected_hash": document["content_hash"]},
    )
    assert status == 422
    assert "theme.default_mode" in payload["error"]


def test_editor_write_warns_on_poor_contrast_but_saves(store: SettingsStore) -> None:
    _, document = settings_editor.read_action(store, "settings")
    status, payload = settings_editor.write_action(
        store,
        "settings",
        {
            "content": 'theme:\n  accent: "#ffff00"\n',
            "expected_hash": document["content_hash"],
        },
    )
    assert status == 200  # FX owns the final call — the write always lands
    assert any("accent" in warning for warning in payload["warnings"])


def test_editor_write_round_trips_without_warnings_for_good_contrast(
    store: SettingsStore,
) -> None:
    _, document = settings_editor.read_action(store, "settings")
    status, payload = settings_editor.write_action(
        store,
        "settings",
        {
            "content": 'theme:\n  accent: "#663399"\n',
            "expected_hash": document["content_hash"],
        },
    )
    assert status == 200
    assert payload["warnings"] == []


# --------------------------------------------------------------------------- #
# Settings tab — the Harness/theme group
# --------------------------------------------------------------------------- #


def _harness_group(payload: dict) -> dict:
    groups = {group["id"]: group for group in payload["groups"]}
    assert "settings" in groups, sorted(groups)
    return groups["settings"]


def test_settings_payload_carries_editable_harness_theme_group(tmp_path: Path) -> None:
    path = _write_settings(tmp_path, 'theme:\n  accent: "#663399"\n')
    group = _harness_group(settings_panel.settings_payload(settings_path=path))
    assert group["label"] == "Harness"
    assert group["editable"] is True
    assert group["error"] is None
    theme_item = next(item for item in group["items"] if item["name"] == "theme")
    values = {field["label"]: field["value"] for field in theme_item["fields"]}
    assert values["accent"] == "#663399"
    assert values["accent dark tint (derived)"] == theme.dark_tint("#663399")
    assert values["danger"] == theme.DEFAULT_DANGER
    assert values["default mode"] == theme.DEFAULT_MODE


def test_settings_payload_degrades_broken_settings_file_to_group_error(
    tmp_path: Path,
) -> None:
    path = _write_settings(tmp_path, "theme:\n  accent: broken\n")
    group = _harness_group(settings_panel.settings_payload(settings_path=path))
    assert group["error"] is not None
    assert "theme.accent" in group["error"]


# --------------------------------------------------------------------------- #
# Render path — the dashboard reflects an edit on next refresh, no restart
# --------------------------------------------------------------------------- #


def test_dashboard_reflects_saved_accent_on_next_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_settings(tmp_path, 'theme:\n  accent: "#663399"\n  default_mode: dark\n')
    monkeypatch.setattr(settings_module, "DEFAULT_SETTINGS_PATH", path)
    page = unified_dashboard.render_page()
    assert "--accent: light-dark(#663399," in page
    assert '"dark"' in page  # configured initial mode reaches the pre-paint script
    # The edit shows up on the next render of the same process — no restart.
    path.write_text('theme:\n  accent: "#004400"\n', encoding="utf-8")
    assert "--accent: light-dark(#004400," in unified_dashboard.render_page()


def test_dashboard_never_renders_a_broken_theme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_settings(tmp_path, "theme:\n  accent: broken\n")
    monkeypatch.setattr(settings_module, "DEFAULT_SETTINGS_PATH", path)
    page = unified_dashboard.render_page()
    assert f"--accent: light-dark({theme.DEFAULT_ACCENT}," in page


def test_settings_route_write_carries_contrast_warnings(tmp_path: Path) -> None:
    # End-to-end through the unified dashboard route table (POST body → 15.2
    # store → warnings), mirroring what the Settings tab's editor receives.
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    (config_dir / "settings.yaml").write_text("theme: {}\n", encoding="utf-8")
    store = SettingsStore(config_dir, backup_dir=tmp_path / "backups")
    ctx = unified_dashboard.DashboardContext(
        configs={}, state_dir=tmp_path / "state", settings_store=store
    )
    document = store.read("settings")
    body = json.dumps(
        {"content": 'theme:\n  accent: "#ffff00"\n', "expected_hash": document.content_hash}
    ).encode("utf-8")
    response = unified_dashboard.handle_request(
        "POST", "/api/settings/config?id=settings", ctx, body=body
    )
    assert response.status == 200
    payload = json.loads(response.body)
    assert any("accent" in warning for warning in payload["warnings"])
