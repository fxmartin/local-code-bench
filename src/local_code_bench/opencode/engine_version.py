"""Best-effort engine-version capture for the OpenCode scorecard (Story 10.5-001).

Each scorecard row records which engine *build* produced it, so a result can be
traced back to a specific Ollama/mlx-lm/... version — the run-mode and quant-source
lessons of the article are only reproducible if the engine version is pinned too.

Version data is read from the engine's own version endpoint *where one exists*
(Ollama exposes ``GET /api/version``); engines without a known endpoint simply
report ``None`` and render as ``-`` in the scorecard. Every failure mode — no
endpoint, unreachable server, malformed body — degrades to ``None`` rather than
breaking the run, because the version string is provenance, not a gate. The HTTP
fetch is injectable so the capture is unit-testable with no live server.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable

#: engine name -> (path appended to the host root, JSON key holding the version).
#: Only engines with a documented version endpoint are listed; others stay absent
#: and yield ``None``. Ollama's ``/api/version`` returns ``{"version": "0.x.y"}``.
ENGINE_VERSION_ENDPOINTS: dict[str, tuple[str, str]] = {
    "ollama": ("/api/version", "version"),
}

#: Short, so an unreachable or slow engine never stalls a benchmark sweep.
DEFAULT_TIMEOUT = 1.0

Fetch = Callable[[str, float], str | None]


def _default_fetch(url: str, timeout: float) -> str | None:
    """Fetch ``url`` body as text, returning ``None`` on any transport failure."""

    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 (loopback only)
        return response.read().decode("utf-8")


def _host_root(base_url: str) -> str:
    """Strip a trailing ``/v1`` (and slashes) so version paths hang off the host."""

    url = base_url.rstrip("/")
    if url.endswith("/v1"):
        url = url[: -len("/v1")]
    return url.rstrip("/")


def capture_engine_version(
    engine: str | None,
    base_url: str,
    *,
    fetch: Fetch | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> str | None:
    """Return the engine's version string, or ``None`` when it cannot be determined.

    Returns ``None`` immediately for an unset engine or one with no known version
    endpoint, so no network call is made in those (common) cases. For a known
    engine the version endpoint is queried off the host root derived from
    ``base_url``; any error or unparseable body degrades to ``None``.
    """

    if not engine:
        return None
    spec = ENGINE_VERSION_ENDPOINTS.get(engine)
    if spec is None:
        return None

    path, key = spec
    fetch = fetch or _default_fetch
    url = _host_root(base_url) + path
    try:
        body = fetch(url, timeout)
    except Exception:  # noqa: BLE001 (provenance is best-effort; never break the run)
        return None
    if not body:
        return None

    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return body.strip() or None
    if isinstance(data, dict):
        value = data.get(key)
        return str(value) if value is not None else None
    return None
