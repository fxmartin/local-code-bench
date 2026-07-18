"""Story 15.4-001: settings change log + which-domains-changed refresh signal."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from local_code_bench.settings_changes import (
    CHANGELOG_FILENAME,
    DOMAIN_PANELS,
    FULL_DOCUMENT_SUMMARY,
    SettingsChangeLog,
    affected_panels,
    summarize_updates,
)

# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------

_FIXED_NOW = datetime(2026, 7, 18, 9, 15, 30, tzinfo=UTC)


def _log(tmp_path: Path, **kwargs) -> SettingsChangeLog:
    return SettingsChangeLog(tmp_path / "state", now=lambda: _FIXED_NOW, **kwargs)


def _record(log: SettingsChangeLog, index: int = 0) -> dict:
    return log.record(
        file="models.yaml",
        domain="models",
        summary=f"updated 1 setting(s): models.{index}.concurrency",
        backup=f"models.yaml.20260718T091530-{index}",
    )


# ---------------------------------------------------------------------------
# AC3: one JSONL line per write — timestamp, file, domain, summary, backup
# ---------------------------------------------------------------------------


def test_record_appends_one_json_line(tmp_path: Path) -> None:
    log = _log(tmp_path)
    entry = _record(log)

    lines = (tmp_path / "state" / CHANGELOG_FILENAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == entry
    assert entry["timestamp"] == _FIXED_NOW.isoformat()
    assert entry["file"] == "models.yaml"
    assert entry["domain"] == "models"
    assert entry["summary"].startswith("updated 1 setting(s)")


def test_each_entry_links_its_backup_snapshot(tmp_path: Path) -> None:
    # AC4: the entry names the snapshot a manual restore would copy back
    entry = _record(_log(tmp_path), index=3)
    assert entry["backup"] == "models.yaml.20260718T091530-3"


def test_entries_returns_newest_first_with_limit(tmp_path: Path) -> None:
    log = _log(tmp_path)
    for index in range(3):
        _record(log, index)

    entries = log.entries()
    assert [entry["backup"][-1] for entry in entries] == ["2", "1", "0"]
    assert log.entries(limit=2) == entries[:2]


def test_missing_log_degrades_to_empty(tmp_path: Path) -> None:
    assert _log(tmp_path).entries() == []


def test_malformed_lines_are_skipped_not_fatal(tmp_path: Path) -> None:
    log = _log(tmp_path)
    _record(log)
    path = tmp_path / "state" / CHANGELOG_FILENAME
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")
        handle.write('"a bare string"\n')
    _record(log, index=1)

    entries = log.entries()
    assert len(entries) == 2
    assert all(isinstance(entry, dict) for entry in entries)


# ---------------------------------------------------------------------------
# bounded by simple rotation
# ---------------------------------------------------------------------------


def test_rotation_bounds_the_live_file(tmp_path: Path) -> None:
    log = _log(tmp_path, max_entries=3)
    for index in range(4):
        _record(log, index)

    live = tmp_path / "state" / CHANGELOG_FILENAME
    rotated = live.with_name(CHANGELOG_FILENAME + ".1")
    assert len(live.read_text(encoding="utf-8").splitlines()) == 1
    assert len(rotated.read_text(encoding="utf-8").splitlines()) == 3


def test_entries_still_span_the_rotated_file(tmp_path: Path) -> None:
    log = _log(tmp_path, max_entries=2)
    for index in range(3):
        _record(log, index)

    assert [entry["backup"][-1] for entry in log.entries()] == ["2", "1", "0"]


def test_second_rotation_drops_the_oldest_generation(tmp_path: Path) -> None:
    log = _log(tmp_path, max_entries=1)
    for index in range(3):
        _record(log, index)

    # at most two generations exist: the live file plus one rotated file
    state_dir = tmp_path / "state"
    assert sorted(p.name for p in state_dir.iterdir()) == [
        CHANGELOG_FILENAME,
        CHANGELOG_FILENAME + ".1",
    ]
    assert [entry["backup"][-1] for entry in log.entries()] == ["2", "1"]


def test_max_entries_must_be_positive(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        _log(tmp_path, max_entries=0)


# ---------------------------------------------------------------------------
# AC3: the log records *what kind of* change happened, never secret values
# ---------------------------------------------------------------------------


def test_summarize_updates_names_paths_not_values() -> None:
    summary = summarize_updates(
        {"models.0.concurrency": 4, "models.1.api_key_env": "SUPER_SECRET_VALUE"}
    )
    assert "models.0.concurrency" in summary
    assert "models.1.api_key_env" in summary
    assert "4" not in summary
    assert "SUPER_SECRET_VALUE" not in summary


def test_full_document_summary_carries_no_content() -> None:
    assert FULL_DOCUMENT_SUMMARY == "full-document edit"


# ---------------------------------------------------------------------------
# AC2: which-domains-changed signal → panels that consume each domain
# ---------------------------------------------------------------------------


def test_domain_panels_cover_every_registered_config() -> None:
    assert set(DOMAIN_PANELS) == {"models", "inferencers", "agents", "suites"}


def test_models_domain_names_the_consuming_panels() -> None:
    panels = DOMAIN_PANELS["models"]
    for panel in ("models list", "launcher", "tier view", "inventory"):
        assert panel in panels


def test_affected_panels_deduplicates_across_domains() -> None:
    panels = affected_panels(["models", "inferencers"])
    assert len(panels) == len(set(panels))
    assert "launcher" in panels


def test_affected_panels_ignores_unknown_domains() -> None:
    assert affected_panels(["nope"]) == []
