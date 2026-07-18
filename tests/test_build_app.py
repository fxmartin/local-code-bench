# ABOUTME: Tests for the reproducible signed build pipeline (Story 18.3-001):
# ABOUTME: configs/build.yaml pins, entitlements, and scripts/build-macos-app.sh.
"""Build pipeline tests.

The build script itself needs a Mac with network and a Swift toolchain, so
these tests exercise the parts that are checkable offline: the build config
declares every pin the script depends on (Epic-15 principle), the script
hardcodes none of them, the entitlements file is a valid plist with the keys
an embedded CPython needs under the hardened runtime, and the script's
``--print-config`` mode resolves exactly what the YAML declares.
"""

from __future__ import annotations

import os
import plistlib
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_CONFIG = REPO_ROOT / "configs" / "build.yaml"
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build-macos-app.sh"

REQUIRED_CONFIG_KEYS = {
    "app_name",
    "bundle_id",
    "min_macos",
    "pbs_tag",
    "pbs_python",
    "pbs_arch",
    "pbs_sha256",
    "codesign_identity",
    "entitlements",
}

# Embedded CPython under the hardened runtime: ctypes/libffi needs writable
# executable memory, and extension modules are not team-signed.
REQUIRED_ENTITLEMENTS = {
    "com.apple.security.cs.allow-unsigned-executable-memory",
    "com.apple.security.cs.disable-library-validation",
}


def load_build_config() -> dict:
    return yaml.safe_load(BUILD_CONFIG.read_text())


def print_config(env: dict[str, str] | None = None) -> dict[str, str]:
    """Run the script's --print-config mode and parse KEY=VALUE lines."""
    result = subprocess.run(
        ["bash", str(BUILD_SCRIPT), "--print-config"],
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


class TestBuildConfig:
    def test_config_declares_every_required_key(self):
        config = load_build_config()
        assert REQUIRED_CONFIG_KEYS <= set(config), (
            f"missing keys: {REQUIRED_CONFIG_KEYS - set(config)}"
        )

    def test_pbs_checksum_is_sha256_hex(self):
        config = load_build_config()
        assert re.fullmatch(r"[0-9a-f]{64}", str(config["pbs_sha256"]))

    def test_entitlements_file_exists_with_required_keys(self):
        config = load_build_config()
        entitlements_path = REPO_ROOT / config["entitlements"]
        assert entitlements_path.is_file()
        entitlements = plistlib.loads(entitlements_path.read_bytes())
        for key in REQUIRED_ENTITLEMENTS:
            assert entitlements.get(key) is True, f"{key} must be true"

    def test_config_values_are_scalars(self):
        # The bash-side parser handles flat scalar keys only; nesting would
        # silently resolve to nothing.
        config = load_build_config()
        for key, value in config.items():
            assert isinstance(value, (str, int, float)), f"{key} must be a flat scalar"


class TestBuildScript:
    def test_script_is_valid_bash(self):
        result = subprocess.run(
            ["bash", "-n", str(BUILD_SCRIPT)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr

    def test_script_reads_pins_from_config_not_hardcoded(self):
        script = BUILD_SCRIPT.read_text()
        config = load_build_config()
        assert "configs/build.yaml" in script
        for key in ("pbs_tag", "pbs_python", "pbs_sha256", "bundle_id", "min_macos"):
            assert str(config[key]) not in script, (
                f"{key}={config[key]} is hardcoded in the script; it must come "
                "from configs/build.yaml"
            )

    def test_script_signs_hardened_and_verifies(self):
        script = BUILD_SCRIPT.read_text()
        assert "--options runtime" in script, "hardened runtime signing required"
        assert "--entitlements" in script
        assert re.search(r"codesign --verify --deep", script), (
            "the emitted .app must be verified"
        )

    def test_script_labels_adhoc_fallback(self):
        script = BUILD_SCRIPT.read_text()
        assert "unsigned-for-distribution" in script.lower().replace(" ", "-"), (
            "ad-hoc fallback must be clearly labeled unsigned for distribution"
        )

    def test_script_verifies_archive_checksum(self):
        script = BUILD_SCRIPT.read_text()
        assert "shasum" in script, "the CPython archive must be checksum-verified"


@pytest.mark.skipif(sys.platform != "darwin", reason="uses macOS security tooling")
class TestPrintConfig:
    def test_resolved_config_matches_yaml(self):
        config = load_build_config()
        resolved = print_config()
        assert resolved["APP_NAME"] == config["app_name"]
        assert resolved["BUNDLE_ID"] == config["bundle_id"]
        assert resolved["MIN_MACOS"] == str(config["min_macos"])
        assert resolved["PBS_TAG"] == str(config["pbs_tag"])
        assert resolved["PBS_PYTHON"] == str(config["pbs_python"])
        assert resolved["PBS_ARCH"] == config["pbs_arch"]
        assert resolved["PBS_SHA256"] == config["pbs_sha256"]
        assert resolved["ENTITLEMENTS"].endswith(config["entitlements"])

    def test_app_version_mirrors_pyproject(self):
        pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
        resolved = print_config()
        assert resolved["APP_VERSION"] == pyproject["project"]["version"]

    def test_signing_mode_is_resolved(self):
        resolved = print_config()
        assert resolved["SIGNING_MODE"] in {"developer-id", "ad-hoc"}
        if resolved["SIGNING_MODE"] == "ad-hoc":
            assert resolved["CODESIGN_IDENTITY"] == "-"

    def test_env_can_override_config_pins(self):
        resolved = print_config(env={"PBS_TAG": "99990101"})
        assert resolved["PBS_TAG"] == "99990101"
