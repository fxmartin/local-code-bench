"""Settings change log and domain-refresh signal (Story 15.4-001).

Two small pieces the 15.2-001 write pipeline plugs into:

- :class:`SettingsChangeLog` — an append-only JSONL under the state dir (same
  conventions as tiering's ``LastUsedStore``: injectable ``state_dir``/``now``,
  a missing or unreadable file degrades to empty). One line per settings write
  records *that* and *what kind of* change happened — timestamp, file, domain,
  summary, and the 15.2-001 backup snapshot a manual restore would copy back —
  never a setting's value, so no secret can land in the log. The file is
  bounded by simple rotation: once it holds ``max_entries`` lines it is renamed
  to ``<name>.1`` (replacing the previous generation) and a fresh file starts.

- :data:`DOMAIN_PANELS` / :func:`affected_panels` — the "which domains
  changed" signal: for each registered config domain, the dashboard panels
  that consume it and should refresh after a write. The panels already re-read
  their config surfaces per request, so naming them in the write response is
  all a client needs to refresh without a restart.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Log file name under the state dir (mirrors tiering's state-file convention).
CHANGELOG_FILENAME = "settings-changes.jsonl"

#: Live-file line bound before rotation; at most two generations are kept.
DEFAULT_MAX_ENTRIES = 500

#: Summary recorded for a whole-document write (no per-key paths to name).
FULL_DOCUMENT_SUMMARY = "full-document edit"

#: Registered config domain -> dashboard panels that consume it (story AC:
#: "models list, launcher, tier view, inventory"). Every domain includes the
#: settings tab itself, which renders all groups.
DOMAIN_PANELS: dict[str, tuple[str, ...]] = {
    "models": ("models list", "launcher", "chat", "tier view", "inventory", "settings"),
    "inferencers": ("inferencer control", "launcher", "inventory", "tier view", "settings"),
    "agents": ("launcher", "settings"),
    "suites": ("launcher", "settings"),
}


def affected_panels(domains: Iterable[str]) -> list[str]:
    """The deduplicated panel list to refresh for ``domains``, in mapping order."""

    panels: list[str] = []
    for domain in domains:
        for panel in DOMAIN_PANELS.get(domain, ()):
            if panel not in panels:
                panels.append(panel)
    return panels


def summarize_updates(updates: Mapping[str, Any]) -> str:
    """A change-kind summary naming the updated key paths — never their values."""

    paths = ", ".join(sorted(updates))
    return f"updated {len(updates)} setting(s): {paths}"


class SettingsChangeLog:
    """Append-only JSONL change log under the state dir, bounded by rotation.

    ``now`` is injectable for tests (stamps entry timestamps); reads degrade a
    missing/unreadable file to empty and skip malformed lines, mirroring
    ``LastUsedStore``.
    """

    def __init__(
        self,
        state_dir: str | Path,
        *,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        self._path = Path(state_dir) / CHANGELOG_FILENAME
        self._max_entries = max_entries
        self._now = now if now is not None else lambda: datetime.now(UTC)

    def record(self, *, file: str, domain: str, summary: str, backup: str) -> dict[str, str]:
        """Append one change line and return it; rotates the live file when full."""

        entry = {
            "timestamp": self._now().isoformat(),
            "file": file,
            "domain": domain,
            "summary": summary,
            "backup": backup,
        }
        self._rotate_if_full()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry

    def entries(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Recorded changes, newest first, spanning the rotated generation too."""

        lines = self._read_lines(self._rotated_path()) + self._read_lines(self._path)
        entries: list[dict[str, Any]] = []
        for line in lines:
            try:
                parsed = json.loads(line)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                entries.append(parsed)
        entries.reverse()
        return entries if limit is None else entries[:limit]

    def _rotated_path(self) -> Path:
        return self._path.with_name(self._path.name + ".1")

    def _rotate_if_full(self) -> None:
        if len(self._read_lines(self._path)) >= self._max_entries:
            os.replace(self._path, self._rotated_path())

    @staticmethod
    def _read_lines(path: Path) -> list[str]:
        try:
            return path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
