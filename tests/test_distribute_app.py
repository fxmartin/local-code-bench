# ABOUTME: Tests for the notarized distribution pipeline (Story 18.3-002):
# ABOUTME: configs/build.yaml distribution pins and scripts/distribute-macos-app.sh.
"""Distribution pipeline tests.

Notarization needs an Apple Developer account, a keychain profile, and
Apple's servers, so these tests exercise what is checkable offline: the
build config declares the distribution pins (notary profile, GitHub repo),
the script hardcodes none of them, the script performs every step the story
requires (notarytool submit via keychain profile, staple, DMG, spctl
assessment, release notes, version alignment), it refuses ad-hoc-signed
bundles, and ``--print-config`` resolves exactly what the YAML declares.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_CONFIG = REPO_ROOT / "configs" / "build.yaml"
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build-macos-app.sh"
DISTRIBUTE_SCRIPT = REPO_ROOT / "scripts" / "distribute-macos-app.sh"

REQUIRED_DISTRIBUTION_KEYS = {"notary_profile", "github_repo"}


def load_build_config() -> dict:
    return yaml.safe_load(BUILD_CONFIG.read_text())


def print_config(env: dict[str, str] | None = None) -> dict[str, str]:
    """Run the script's --print-config mode and parse KEY=VALUE lines."""
    result = subprocess.run(
        ["bash", str(DISTRIBUTE_SCRIPT), "--print-config"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={**os.environ, **(env or {})},
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    parsed = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            parsed[key] = value
    return parsed


class TestDistributionConfig:
    def test_config_declares_distribution_keys(self):
        config = load_build_config()
        assert REQUIRED_DISTRIBUTION_KEYS <= set(config), (
            f"missing keys: {REQUIRED_DISTRIBUTION_KEYS - set(config)}"
        )

    def test_github_repo_is_owner_slash_repo(self):
        config = load_build_config()
        assert re.fullmatch(r"[\w.-]+/[\w.-]+", str(config["github_repo"]))


class TestDistributeScript:
    def test_script_is_valid_bash(self):
        result = subprocess.run(
            ["bash", "-n", str(DISTRIBUTE_SCRIPT)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr

    def test_script_reads_pins_from_config_not_hardcoded(self):
        script = DISTRIBUTE_SCRIPT.read_text()
        config = load_build_config()
        assert "configs/build.yaml" in script
        for key in ("notary_profile", "github_repo"):
            assert str(config[key]) not in script, (
                f"{key}={config[key]} is hardcoded in the script; it must come "
                "from configs/build.yaml"
            )

    def test_script_notarizes_with_keychain_profile(self):
        script = DISTRIBUTE_SCRIPT.read_text()
        assert "notarytool submit" in script
        assert "--keychain-profile" in script
        assert "--wait" in script

    def test_script_requires_accepted_notarization_status(self):
        # `notarytool submit --wait` can return 0 for an Invalid submission;
        # the script must gate on the Accepted status, not the exit code.
        script = DISTRIBUTE_SCRIPT.read_text()
        assert "status: Accepted" in script

    def test_script_staples_the_ticket(self):
        script = DISTRIBUTE_SCRIPT.read_text()
        assert "stapler staple" in script

    def test_script_builds_a_dmg(self):
        script = DISTRIBUTE_SCRIPT.read_text()
        assert "hdiutil create" in script
        assert "UDZO" in script

    def test_script_runs_gatekeeper_assessment(self):
        # The distributable must pass Gatekeeper's own check, both as an
        # executable bundle and as an openable disk image.
        script = DISTRIBUTE_SCRIPT.read_text()
        assert "spctl --assess --type execute" in script
        assert "spctl --assess --type open" in script

    def test_script_refuses_adhoc_signed_bundles(self):
        # An ad-hoc build is labeled unsigned-for-distribution by the build
        # script; the distribution step must refuse it outright.
        script = DISTRIBUTE_SCRIPT.read_text()
        assert "Signature=adhoc" in script

    def test_script_writes_release_notes_with_harness_and_externals(self):
        script = DISTRIBUTE_SCRIPT.read_text()
        assert "RELEASE-NOTES" in script
        assert "harness-version" in script, (
            "release notes must state the bundled harness version recorded "
            "in the bundle"
        )
        assert "Detected, not bundled" in script, (
            "release notes must state the detected-not-bundled externals"
        )

    def test_script_enforces_release_version_alignment(self):
        # The published app must correspond to a tagged harness release: the
        # bundle's version, the bundled harness wheel, and pyproject.toml
        # (the PSR-managed version) must all agree before anything ships.
        script = DISTRIBUTE_SCRIPT.read_text()
        assert "uv version --short" in script
        assert "CFBundleShortVersionString" in script


class TestBuildScriptStampsRepo:
    def test_build_script_stamps_github_repo_into_info_plist(self):
        # The app's launch-time update check reads the repo from the bundle's
        # Info.plist (LCBGitHubRepo); the build script stamps it from config.
        script = BUILD_SCRIPT.read_text()
        config = load_build_config()
        assert "LCBGitHubRepo" in script
        assert str(config["github_repo"]) not in script, (
            "github_repo is hardcoded in the build script; it must come "
            "from configs/build.yaml"
        )


@pytest.mark.skipif(sys.platform != "darwin", reason="uses macOS tooling")
class TestDistributePrintConfig:
    def test_resolved_config_matches_yaml(self):
        config = load_build_config()
        resolved = print_config()
        assert resolved["APP_NAME"] == config["app_name"]
        assert resolved["NOTARY_PROFILE"] == config["notary_profile"]
        assert resolved["GITHUB_REPO"] == config["github_repo"]
        assert resolved["DMG_PATH"].endswith(".dmg")
        assert resolved["APP_VERSION"] in resolved["DMG_PATH"]

    def test_env_can_override_notary_profile(self):
        resolved = print_config(env={"NOTARY_PROFILE": "override-profile"})
        assert resolved["NOTARY_PROFILE"] == "override-profile"
