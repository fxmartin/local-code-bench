"""External (second-tier) model repository: availability + first-time init.

Epic-12, Story 12.1-001. The external tier is a model repository on an attached
USB/Thunderbolt SSD that may be mounted or unplugged at any time. This module
answers two filesystem-only questions, kept pure for testability (point them at a
temporary tree via ``home`` exactly like the Epic-11 scanner and ``power.py``):

* **Availability** — is the repo currently reachable? It is ``mounted`` only when
  the configured root exists *and* carries its volume marker file, so a
  coincidentally-present empty mount path is reported ``offline`` rather than
  mistaken for the real repo. Availability never raises: every read path degrades
  gracefully when the drive is unplugged.
* **First-time init** — write the marker and the per-format directory skeleton so
  subsequent runs recognise the repo. Init refuses when the volume itself is not
  mounted, so the repo is never silently created on the internal disk.

The external root mirrors the local per-format store layout (one subdir per
format), so Epic-11's scan/move strategies apply unchanged against this root.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ..config import STORE_FORMATS, ExternalRepoConfig
from .inventory import expand_store_path

__all__ = [
    "TierAvailability",
    "ExternalRepoStatus",
    "ExternalRepoError",
    "external_root",
    "marker_path",
    "format_dir",
    "check_availability",
    "initialize_repo",
    "MARKER_CONTENT",
]

#: Body written into a freshly-initialised marker file. Identifying, fixed (no
#: timestamps), and never relied upon for content — only the file's presence
#: matters to availability.
MARKER_CONTENT = "local-code-bench external model repository (Epic-12 tiered storage)\n"


class TierAvailability(str, Enum):
    """Whether the external tier is currently reachable."""

    MOUNTED = "mounted"
    OFFLINE = "offline"


class ExternalRepoError(RuntimeError):
    """Raised when an external-repo operation cannot proceed (e.g. init offline)."""


@dataclass(frozen=True)
class ExternalRepoStatus:
    """Result of an availability check: the verdict plus the resolved paths."""

    availability: TierAvailability
    root: Path
    marker: Path

    @property
    def is_mounted(self) -> bool:
        return self.availability is TierAvailability.MOUNTED


def external_root(cfg: ExternalRepoConfig, *, home: Path | None = None) -> Path:
    """Resolve the repo root, expanding a leading ``~`` against ``home``."""

    return expand_store_path(cfg.root, home=home)


def marker_path(cfg: ExternalRepoConfig, *, home: Path | None = None) -> Path:
    """Resolve the volume-marker file path inside the repo root."""

    return external_root(cfg, home=home) / cfg.volume_marker


def format_dir(
    cfg: ExternalRepoConfig,
    store_format: str,
    *,
    home: Path | None = None,
) -> Path:
    """Resolve the per-format store directory under the repo root."""

    return external_root(cfg, home=home) / cfg.subpaths[store_format]


def check_availability(
    cfg: ExternalRepoConfig,
    *,
    home: Path | None = None,
) -> ExternalRepoStatus:
    """Report whether the external tier is ``mounted`` or ``offline``.

    Filesystem-only and total: ``mounted`` requires the root directory to exist
    and the volume-marker *file* to be present inside it; anything else (absent
    root, missing marker, marker-is-a-directory, unreadable path) is ``offline``.
    Never raises and never scans or moves.
    """

    root = external_root(cfg, home=home)
    marker = root / cfg.volume_marker
    try:
        mounted = root.is_dir() and marker.is_file()
    except OSError:
        mounted = False
    availability = TierAvailability.MOUNTED if mounted else TierAvailability.OFFLINE
    return ExternalRepoStatus(availability=availability, root=root, marker=marker)


def initialize_repo(
    cfg: ExternalRepoConfig,
    *,
    home: Path | None = None,
) -> ExternalRepoStatus:
    """Create the marker and per-format directory skeleton (idempotent).

    Requires the volume mountpoint (the root's parent) to already exist, so the
    repo is never silently created on the internal disk when the SSD is unplugged
    — that case raises :class:`ExternalRepoError`. Existing content and an
    existing marker are preserved. Returns the post-init availability.
    """

    root = external_root(cfg, home=home)
    if not root.parent.is_dir():
        raise ExternalRepoError(
            f"external volume not mounted: {root.parent} is absent — "
            "plug in the SSD before initialising the external repo"
        )

    root.mkdir(parents=True, exist_ok=True)
    for store_format in sorted(STORE_FORMATS):
        (root / cfg.subpaths[store_format]).mkdir(parents=True, exist_ok=True)

    marker = root / cfg.volume_marker
    if not marker.exists():
        marker.write_text(MARKER_CONTENT, encoding="utf-8")

    return check_availability(cfg, home=home)
