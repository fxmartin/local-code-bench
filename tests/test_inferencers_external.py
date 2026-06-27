"""Availability detection and first-time init for the external (tier-2) repo."""

from __future__ import annotations

import pytest

from local_code_bench.config import (
    DEFAULT_EXTERNAL_SUBPATHS,
    STORE_FORMATS,
    ExternalRepoConfig,
)
from local_code_bench.inferencers.external import (
    ExternalRepoError,
    TierAvailability,
    check_availability,
    external_root,
    format_dir,
    initialize_repo,
    marker_path,
)


def _cfg(root: str, *, marker: str = ".lcb-external") -> ExternalRepoConfig:
    return ExternalRepoConfig(root=root, volume_marker=marker)


def test_check_availability_mounted_when_root_and_marker_present(tmp_path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".lcb-external").write_text("marker", encoding="utf-8")

    status = check_availability(_cfg(str(root)))

    assert status.availability is TierAvailability.MOUNTED
    assert status.is_mounted is True
    assert status.root == root


def test_check_availability_offline_when_root_absent(tmp_path) -> None:
    status = check_availability(_cfg(str(tmp_path / "missing")))

    assert status.availability is TierAvailability.OFFLINE
    assert status.is_mounted is False


def test_check_availability_offline_when_marker_missing(tmp_path) -> None:
    # An empty mount path that just happens to exist is NOT the real repo.
    root = tmp_path / "repo"
    root.mkdir()

    status = check_availability(_cfg(str(root)))

    assert status.availability is TierAvailability.OFFLINE


def test_check_availability_offline_when_marker_is_a_directory(tmp_path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".lcb-external").mkdir()  # marker must be a file, not a dir

    status = check_availability(_cfg(str(root)))

    assert status.availability is TierAvailability.OFFLINE


def test_check_availability_does_not_raise_on_bad_path() -> None:
    # Filesystem-only and total: never raises, just reports offline.
    status = check_availability(_cfg("/nonexistent/volume/repo"))

    assert status.availability is TierAvailability.OFFLINE


def test_tilde_root_expands_against_home(tmp_path) -> None:
    home = tmp_path / "home"
    root = home / "ExternalModels"
    root.mkdir(parents=True)
    (root / ".lcb-external").write_text("marker", encoding="utf-8")

    cfg = _cfg("~/ExternalModels")

    assert external_root(cfg, home=home) == root
    assert marker_path(cfg, home=home) == root / ".lcb-external"
    assert check_availability(cfg, home=home).availability is TierAvailability.MOUNTED


def test_format_dir_resolves_per_format_subpath(tmp_path) -> None:
    cfg = _cfg(str(tmp_path / "repo"))

    for fmt in STORE_FORMATS:
        assert format_dir(cfg, fmt) == tmp_path / "repo" / DEFAULT_EXTERNAL_SUBPATHS[fmt]


def test_initialize_repo_creates_marker_and_format_skeleton(tmp_path) -> None:
    # Volume mountpoint exists (SSD plugged in); the repo dir does not yet.
    volume = tmp_path / "Volumes" / "SSD"
    volume.mkdir(parents=True)
    root = volume / "local-code-bench"
    cfg = _cfg(str(root))

    status = initialize_repo(cfg)

    assert status.availability is TierAvailability.MOUNTED
    assert (root / ".lcb-external").is_file()
    for fmt in STORE_FORMATS:
        assert (root / DEFAULT_EXTERNAL_SUBPATHS[fmt]).is_dir()
    # A subsequent run recognises the repo.
    assert check_availability(cfg).availability is TierAvailability.MOUNTED


def test_initialize_repo_is_idempotent(tmp_path) -> None:
    volume = tmp_path / "Volumes" / "SSD"
    volume.mkdir(parents=True)
    root = volume / "repo"
    cfg = _cfg(str(root))

    initialize_repo(cfg)
    (root / DEFAULT_EXTERNAL_SUBPATHS["gguf"] / "keep.gguf").write_text("x", encoding="utf-8")

    # Re-initialising must not wipe existing content or the marker.
    initialize_repo(cfg)

    assert (root / DEFAULT_EXTERNAL_SUBPATHS["gguf"] / "keep.gguf").is_file()
    assert (root / ".lcb-external").is_file()


def test_initialize_repo_refuses_when_volume_not_mounted(tmp_path) -> None:
    # Parent (the volume mountpoint) is absent -> SSD unplugged: refuse, do not
    # silently create the repo on the internal disk.
    root = tmp_path / "Volumes" / "SSD" / "repo"
    cfg = _cfg(str(root))

    with pytest.raises(ExternalRepoError, match="not mounted"):
        initialize_repo(cfg)

    assert not root.exists()
