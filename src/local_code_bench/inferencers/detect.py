"""Installed-engine detection for inferencer configs.

Detection is read-only and platform-aware: binaries via `shutil.which`, Python
modules via `importlib.util.find_spec`, and macOS `.app` bundles via the standard
Application directories. Off Darwin, app engines report not-installed rather than
raising, mirroring the Darwin guard in `power.py`.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path

from ..config import InferencerConfig, OptimizerConfig


def _app_dirs() -> list[Path]:
    """macOS bundle search roots; module-level so tests can monkeypatch it."""

    return [Path("/Applications"), Path.home() / "Applications"]


def is_installed(cfg: InferencerConfig | OptimizerConfig) -> bool:
    """Report whether the engine or proxy `cfg` describes is installed on this machine.

    Optimizer proxies (Epic-13) share the same detect kinds as inferencers, so
    the same read-only checks apply — nothing is ever installed on a miss.
    """

    if cfg.detect_kind == "binary":
        return shutil.which(cfg.detect_target) is not None
    if cfg.detect_kind == "module":
        try:
            return importlib.util.find_spec(cfg.detect_target) is not None
        except (ImportError, ValueError):
            # Broken namespace packages raise instead of returning None.
            return False
    if cfg.detect_kind == "app":
        if sys.platform != "darwin":
            return False
        return any((directory / cfg.detect_target).exists() for directory in _app_dirs())
    return False


def detect_all(configs: Mapping[str, InferencerConfig | OptimizerConfig]) -> dict[str, bool]:
    """Map each inferencer or optimizer name to its installed state."""

    return {name: is_installed(cfg) for name, cfg in configs.items()}
