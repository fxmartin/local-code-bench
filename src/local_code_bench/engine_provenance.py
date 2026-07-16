"""Exact inference-engine provenance for reproducible local benchmarks."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from local_code_bench.config import InferencerConfig

CaptureMethod = Literal["live-api", "managed-process", "manual-backfill"]
EngineFingerprint = tuple[str, tuple[tuple[str, str], ...]]
Fetch = Callable[[str, float], str]

_CAPTURE_METHODS = {"live-api", "managed-process", "manual-backfill"}
_MLX_PACKAGES = ("mlx-lm", "mlx")
_VERSION_TIMEOUT_SECONDS = 1.0
_PROCESS_TIMEOUT_SECONDS = 2.0


class EngineProvenanceError(ValueError):
    """Raised when exact local-engine provenance cannot be established."""


@dataclass(frozen=True)
class EngineProvenance:
    """Normalized engine identity stored with every local benchmark result."""

    name: str
    versions: dict[str, str]
    capture_method: CaptureMethod

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise EngineProvenanceError("engine provenance name must be non-empty")
        if not self.versions or any(
            not isinstance(key, str)
            or not key.strip()
            or not isinstance(value, str)
            or not value.strip()
            for key, value in self.versions.items()
        ):
            raise EngineProvenanceError(
                "engine provenance versions must be a non-empty string mapping"
            )
        if self.capture_method not in _CAPTURE_METHODS:
            raise EngineProvenanceError(
                f"unsupported engine provenance capture method: {self.capture_method!r}"
            )

    @classmethod
    def from_dict(cls, value: object) -> EngineProvenance:
        if not isinstance(value, Mapping):
            raise EngineProvenanceError("engine provenance must be a mapping")
        name = value.get("name")
        versions = value.get("versions")
        capture_method = value.get("capture_method")
        if not isinstance(name, str):
            raise EngineProvenanceError("engine provenance name must be a string")
        if not isinstance(versions, Mapping):
            raise EngineProvenanceError("engine provenance versions must be a mapping")
        if not isinstance(capture_method, str):
            raise EngineProvenanceError(
                "engine provenance capture_method must be a string"
            )
        normalized_versions: dict[str, str] = {}
        for component, version in versions.items():
            if not isinstance(component, str) or not isinstance(version, str):
                raise EngineProvenanceError(
                    "engine provenance versions must be a string mapping"
                )
            normalized_versions[component] = version
        return cls(
            name=name,
            versions=normalized_versions,
            capture_method=cast(CaptureMethod, capture_method),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "versions": dict(self.versions),
            "capture_method": self.capture_method,
        }

    @property
    def fingerprint(self) -> tuple[str, tuple[tuple[str, str], ...]]:
        """Stable comparison key; capture method does not split identical engines."""

        return self.name, tuple(sorted(self.versions.items()))

    @property
    def label(self) -> str:
        primary = self.versions.get(self.name)
        parts = [f"{self.name} {primary}"] if primary else [self.name]
        parts.extend(
            f"{component} {version}"
            for component, version in sorted(self.versions.items())
            if component != self.name
        )
        return " / ".join(parts)


def engine_fingerprint(value: object) -> EngineFingerprint:
    """Return a stable grouping key, including a distinct legacy sentinel."""

    try:
        return EngineProvenance.from_dict(value).fingerprint
    except EngineProvenanceError:
        return ("unknown (legacy)", ())


def engine_label(value: object) -> str:
    """Format provenance for comparison surfaces without rejecting legacy data."""

    try:
        return EngineProvenance.from_dict(value).label
    except EngineProvenanceError:
        return "unknown (legacy)"


def engine_capture_method(value: object) -> str | None:
    """Extract the capture method when normalized provenance is available."""

    try:
        return EngineProvenance.from_dict(value).capture_method
    except EngineProvenanceError:
        return None


def capture_ollama_provenance(
    base_url: str,
    *,
    fetch: Fetch | None = None,
    timeout: float = _VERSION_TIMEOUT_SECONDS,
) -> EngineProvenance:
    """Read the version from the live Ollama server, failing on missing provenance."""

    fetch = fetch or _default_fetch
    url = _host_root(base_url) + "/api/version"
    try:
        body = fetch(url, timeout)
    except Exception as exc:  # noqa: BLE001 - normalize transport implementations
        raise EngineProvenanceError(
            f"could not capture Ollama version from {url}: {exc}"
        ) from exc
    try:
        payload = json.loads(body)
    except (TypeError, ValueError) as exc:
        raise EngineProvenanceError("Ollama version response is not valid JSON") from exc
    version = payload.get("version") if isinstance(payload, dict) else None
    if not isinstance(version, str) or not version.strip():
        raise EngineProvenanceError("Ollama version response has no version")
    return EngineProvenance(
        name="ollama",
        versions={"ollama": version.strip()},
        capture_method="live-api",
    )


def capture_mlx_provenance(
    command: Sequence[str],
    *,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    timeout: float = _PROCESS_TIMEOUT_SECONDS,
) -> EngineProvenance:
    """Query MLX package metadata through the interpreter backing the launcher."""

    if not command:
        raise EngineProvenanceError("MLX-LM start command is empty")
    resolved = which(command[0])
    if resolved is None:
        raise EngineProvenanceError(f"could not resolve MLX-LM launcher: {command[0]}")
    interpreter = _launcher_interpreter(Path(resolved), which)
    script = (
        "import importlib.metadata as m, json; "
        f"print(json.dumps({{{', '.join(f'{name!r}: m.version({name!r})' for name in _MLX_PACKAGES)}}}))"
    )
    try:
        completed = run(
            [str(interpreter), "-c", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 - normalize subprocess failures
        raise EngineProvenanceError(f"could not capture MLX-LM versions: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"exit {completed.returncode}"
        raise EngineProvenanceError(f"could not capture MLX-LM versions: {detail}")
    try:
        payload = json.loads(completed.stdout)
    except (TypeError, ValueError) as exc:
        raise EngineProvenanceError("MLX-LM version query returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise EngineProvenanceError("MLX-LM version query returned no version mapping")
    versions: dict[str, str] = {}
    for package in _MLX_PACKAGES:
        version = payload.get(package)
        if not isinstance(version, str) or not version.strip():
            raise EngineProvenanceError(f"MLX-LM version query is missing {package}")
        versions[package] = version.strip()
    return EngineProvenance(
        name="mlx-lm",
        versions=versions,
        capture_method="managed-process",
    )


def capture_engine_provenance(
    engine: str,
    base_url: str,
    *,
    inferencer_config: InferencerConfig | None = None,
    state_dir: str | Path | None = None,
) -> EngineProvenance:
    """Capture one configured engine using its authoritative source."""

    if engine == "ollama":
        return capture_ollama_provenance(base_url)
    if engine == "mlx-lm":
        if inferencer_config is None or state_dir is None:
            raise EngineProvenanceError(
                "MLX-LM benchmarks require a harness-managed inferencer; "
                "run with --manage-inferencers"
            )
        from local_code_bench.inferencers import manager

        return manager.managed_engine_provenance(inferencer_config, state_dir)
    raise EngineProvenanceError(
        f"no exact version capture is configured for inferencer {engine!r}"
    )


def _default_fetch(url: str, timeout: float) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
        return response.read().decode("utf-8")


def _host_root(base_url: str) -> str:
    url = base_url.rstrip("/")
    if url.endswith("/v1"):
        url = url[: -len("/v1")]
    return url.rstrip("/")


def _launcher_interpreter(
    launcher: Path,
    which: Callable[[str], str | None],
) -> Path:
    resolved_launcher = launcher.resolve()
    if resolved_launcher.name.startswith("python"):
        return resolved_launcher
    try:
        first_line = resolved_launcher.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, UnicodeDecodeError, IndexError) as exc:
        raise EngineProvenanceError(
            f"could not read MLX-LM launcher shebang: {resolved_launcher}"
        ) from exc
    if not first_line.startswith("#!"):
        raise EngineProvenanceError(
            f"MLX-LM launcher has no Python shebang: {resolved_launcher}"
        )
    parts = shlex.split(first_line[2:].strip())
    if not parts:
        raise EngineProvenanceError(
            f"MLX-LM launcher has an empty shebang: {resolved_launcher}"
        )
    if Path(parts[0]).name == "env":
        executable = next((part for part in parts[1:] if not part.startswith("-")), None)
        resolved = which(executable) if executable else None
        if resolved is None:
            raise EngineProvenanceError(
                f"could not resolve MLX-LM shebang interpreter: {first_line}"
            )
        return Path(resolved).resolve()
    return Path(parts[0]).resolve()
