"""Validated, atomic, comment-preserving settings writes (Story 15.2-001).

One shared store for every Feature-15.3 editor: read (content + hash) /
validate / write (atomic + backup) / conflict check. The write path resolves
its own file paths from a fixed registry and never trusts a client-supplied
one, so a request naming anything outside the registered config set is
rejected outright.

Every proposed edit is validated by running the *same loader the harness
uses* (``config.load_models`` et al.) against a temporary copy, so the
dashboard can never produce a config the CLI would reject. Valid edits are
written atomically (temp file + ``os.replace``) with a timestamped backup of
the previous version under a bounded backup directory (default
``<config_dir>/.backups``, gitignored). Structured edits go through
:meth:`SettingsStore.apply_updates`, which round-trips the document with
``ruamel.yaml`` so existing comments and key order survive.

The store is pure and filesystem-injectable (``now`` / ``read_back`` hooks),
mirroring the tiering modules; :func:`default_settings_store` wires the
operational defaults from ``configs/settings.yaml`` (``settings_backup.dir``
/ ``settings_backup.retention``).
"""

from __future__ import annotations

import hashlib
import io
import os
import tempfile
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from .config import ConfigError, load_agents, load_inferencers, load_models
from .settings import Settings, get_settings
from .suite_catalog import load_custom_suites

#: Default backups kept per config file (mirrors ``settings_backup.retention``).
DEFAULT_BACKUP_RETENTION = 10

#: Backup directory name used when the caller does not inject one.
DEFAULT_BACKUP_DIR_NAME = ".backups"

#: The registered config set: id -> (file name, the harness's own loader).
#: The store only ever writes these files; ids are opaque tokens, not paths.
_REGISTRY: dict[str, tuple[str, Callable[[Path], object]]] = {
    "models": ("models.yaml", load_models),
    "inferencers": ("inferencers.yaml", load_inferencers),
    "agents": ("agents.yaml", load_agents),
    "suites": ("suites.yaml", load_custom_suites),
}


class SettingsStoreError(Exception):
    """Base class for every settings-store failure."""


class UnknownConfigError(SettingsStoreError):
    """Raised for any id outside the registered config set."""


class SettingsValidationError(SettingsStoreError):
    """Raised when the harness's own loader rejects a proposed edit."""


class ConflictError(SettingsStoreError):
    """Raised when the file on disk no longer matches the submitted hash."""

    def __init__(self, message: str, *, current_hash: str | None = None) -> None:
        super().__init__(message)
        self.current_hash = current_hash


class WriteFailedError(SettingsStoreError):
    """Raised when a write aborts mid-way; the original file is restored."""

    def __init__(self, message: str, *, backup_path: Path | None = None) -> None:
        super().__init__(message)
        self.backup_path = backup_path


def content_hash(content: str) -> str:
    """SHA-256 hex digest of the document text — the conflict-detection token."""

    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SettingsDocument:
    """A registered config file as loaded for editing: content + hash."""

    config_id: str
    path: Path
    content: str
    content_hash: str


@dataclass(frozen=True)
class WriteResult:
    """Outcome of a successful write: new hash + where the backup landed."""

    config_id: str
    path: Path
    content_hash: str
    backup_path: Path


class SettingsStore:
    """Validated, atomic, comment-preserving writes for registered configs.

    ``now`` and ``read_back`` are injectable for tests: ``now`` stamps backup
    names, ``read_back`` is the post-write verification read (defaults to
    re-reading the file from disk).
    """

    def __init__(
        self,
        config_dir: str | Path,
        *,
        backup_dir: str | Path | None = None,
        retention: int = DEFAULT_BACKUP_RETENTION,
        now: Callable[[], datetime] | None = None,
        read_back: Callable[[Path], str] | None = None,
    ) -> None:
        self._config_dir = Path(config_dir)
        self._backup_dir = (
            Path(backup_dir) if backup_dir is not None else self._config_dir / DEFAULT_BACKUP_DIR_NAME
        )
        if retention < 1:
            raise ValueError("retention must be at least 1")
        self._retention = retention
        self._now = now if now is not None else lambda: datetime.now(UTC)
        self._read_back = read_back if read_back is not None else _read_text

    def read(self, config_id: str) -> SettingsDocument:
        """Load a registered config for editing: content plus conflict hash."""

        path, _loader = self._resolve(config_id)
        content = self._current_content(config_id, path)
        return SettingsDocument(
            config_id=config_id, path=path, content=content, content_hash=content_hash(content)
        )

    def write(self, config_id: str, content: str, *, expected_hash: str) -> WriteResult:
        """Validate ``content`` with the harness loader and write it atomically.

        Order matters: conflict check first (a stale form reports the conflict,
        not a validation failure), then loader validation (no bytes written on
        rejection), then backup + atomic replace + post-write verification.
        """

        path, loader = self._resolve(config_id)
        current = self._check_conflict(config_id, path, expected_hash)
        self._validate(config_id, path, loader, content)

        backup_path = self._create_backup(path, current)
        try:
            _atomic_write(path, content)
        except OSError as exc:
            raise WriteFailedError(
                f"write to {path} failed ({exc}); previous version preserved at {backup_path}",
                backup_path=backup_path,
            ) from exc
        self._verify_write(path, content, current, backup_path)
        return WriteResult(
            config_id=config_id,
            path=path,
            content_hash=content_hash(content),
            backup_path=backup_path,
        )

    def apply_updates(
        self, config_id: str, updates: dict[str, Any], *, expected_hash: str
    ) -> WriteResult:
        """Apply dotted-path updates (``models.0.concurrency``) and write.

        The document is round-tripped with ``ruamel.yaml`` so comments and key
        order are preserved; the result then goes through :meth:`write`, which
        re-runs conflict detection and loader validation before any bytes land.
        """

        path, _loader = self._resolve(config_id)
        current = self._check_conflict(config_id, path, expected_hash)

        yaml_rt = _round_trip_yaml()
        try:
            document = yaml_rt.load(current)
        except YAMLError as exc:  # pragma: no cover - current content is on-disk valid
            raise SettingsValidationError(f"invalid YAML in {path}: {exc}") from exc
        for dotted, value in updates.items():
            _apply_update(document, dotted, value)
        buffer = io.StringIO()
        yaml_rt.dump(document, buffer)
        return self.write(config_id, buffer.getvalue(), expected_hash=expected_hash)

    def _resolve(self, config_id: str) -> tuple[Path, Callable[[Path], object]]:
        entry = _REGISTRY.get(config_id)
        if entry is None:
            registered = ", ".join(sorted(_REGISTRY))
            raise UnknownConfigError(
                f"unknown config '{config_id}' — the store only edits the "
                f"registered set: {registered}"
            )
        file_name, loader = entry
        return self._config_dir / file_name, loader

    def _current_content(self, config_id: str, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise ConflictError(
                f"registered config '{config_id}' is missing on disk ({path}) — "
                "reload before editing"
            ) from exc

    def _check_conflict(self, config_id: str, path: Path, expected_hash: str) -> str:
        current = self._current_content(config_id, path)
        current_hash = content_hash(current)
        if current_hash != expected_hash:
            raise ConflictError(
                f"{path} changed on disk after the form was loaded — reload and "
                "reapply the edit",
                current_hash=current_hash,
            )
        return current

    def _validate(
        self, config_id: str, path: Path, loader: Callable[[Path], object], content: str
    ) -> None:
        # The loader reads from a file path, so validate a temp copy; loader
        # messages that embed the temp path are rewritten to name the real file.
        with tempfile.TemporaryDirectory(prefix="settings-store-") as tmp_dir:
            candidate = Path(tmp_dir) / path.name
            candidate.write_text(content, encoding="utf-8")
            try:
                loader(candidate)
            except ConfigError as exc:
                message = str(exc).replace(str(candidate), str(path))
                raise SettingsValidationError(
                    f"rejected edit to '{config_id}': {message}"
                ) from exc

    def _create_backup(self, path: Path, current: str) -> Path:
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = self._now().strftime("%Y%m%dT%H%M%S")
        backup_path = self._backup_dir / f"{path.name}.{stamp}"
        counter = 0
        while backup_path.exists():
            counter += 1
            backup_path = self._backup_dir / f"{path.name}.{stamp}-{counter}"
        backup_path.write_text(current, encoding="utf-8")
        self._prune_backups(path.name)
        return backup_path

    def _prune_backups(self, file_name: str) -> None:
        backups = sorted(
            self._backup_dir.glob(f"{file_name}.*"),
            key=lambda p: (p.stat().st_mtime_ns, p.name),
        )
        for stale in backups[: -self._retention]:
            with suppress(OSError):
                stale.unlink()

    def _verify_write(self, path: Path, content: str, previous: str, backup_path: Path) -> None:
        try:
            observed = self._read_back(path)
        except Exception as exc:
            self._restore(path, previous)
            raise WriteFailedError(
                f"post-write verification of {path} failed ({exc}); original "
                f"restored from backup {backup_path}",
                backup_path=backup_path,
            ) from exc
        if observed != content:
            self._restore(path, previous)
            raise WriteFailedError(
                f"post-write content of {path} did not match the submitted edit; "
                f"original restored from backup {backup_path}",
                backup_path=backup_path,
            )

    def _restore(self, path: Path, previous: str) -> None:
        with suppress(OSError):
            _atomic_write(path, previous)


def default_settings_store(
    config_dir: str | Path = "configs", *, settings: Settings | None = None
) -> SettingsStore:
    """Store wired to the operational defaults (``settings_backup.*`` keys)."""

    resolved = settings if settings is not None else get_settings()
    return SettingsStore(
        config_dir,
        backup_dir=resolved.settings_backup_dir,
        retention=resolved.settings_backup_retention,
    )


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _atomic_write(path: Path, content: str) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with suppress(OSError):
            os.unlink(tmp_name)
        raise


def _round_trip_yaml() -> YAML:
    yaml_rt = YAML()
    yaml_rt.preserve_quotes = True
    yaml_rt.width = 4096
    yaml_rt.indent(mapping=2, sequence=4, offset=2)
    return yaml_rt


def _apply_update(document: Any, dotted: str, value: Any) -> None:
    """Set ``dotted`` (e.g. ``models.0.concurrency``) to ``value`` in place.

    Every intermediate node must exist; a list leaf must be an existing index.
    A dict leaf may be created (adding an optional key is a legitimate edit —
    the loader validates the result either way).
    """

    parts = dotted.split(".")
    node = document
    for part in parts[:-1]:
        node = _child(node, part, dotted)
    leaf = parts[-1]
    if isinstance(node, list):
        index = _list_index(node, leaf, dotted)
        node[index] = value
    elif isinstance(node, dict):
        node[leaf] = value
    else:
        raise SettingsValidationError(f"unknown settings path '{dotted}'")


def _child(node: Any, part: str, dotted: str) -> Any:
    if isinstance(node, list):
        return node[_list_index(node, part, dotted)]
    if isinstance(node, dict) and part in node:
        return node[part]
    raise SettingsValidationError(f"unknown settings path '{dotted}'")


def _list_index(node: list, part: str, dotted: str) -> int:
    try:
        index = int(part)
    except ValueError:
        raise SettingsValidationError(f"unknown settings path '{dotted}'") from None
    if not 0 <= index < len(node):
        raise SettingsValidationError(f"unknown settings path '{dotted}'")
    return index
