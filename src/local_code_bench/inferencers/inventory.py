"""Format-aware local model-store scanner (Epic-11, Story 11.1-001).

Each inferencer keeps its downloaded models on disk differently — plain GGUF
files, Ollama's content-addressed blob store, the HuggingFace hub cache used by
MLX/safetensors engines, or a publisher/model directory tree (LM Studio MLX).
This module reads each store with the strategy that matches its configured
``format`` and yields a normalized :class:`StoredModel` per present model.

Every strategy is pure and filesystem-only: it takes a base directory, walks it
with :mod:`pathlib`, and never raises on a missing or empty store (it yields no
rows). Paths carrying ``~`` are expanded against the caller-supplied ``home`` so
tests can point the scanner at a temporary tree, mirroring the Darwin-aware path
handling in ``power.py``.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from ..config import InferencerConfig, StoreFormat

__all__ = [
    "StoredModel",
    "LocalModel",
    "scan_inferencer",
    "scan_inferencers",
    "expand_store_path",
    "normalize",
    "normalize_all",
    "parse_quant",
    "parse_provider",
    "content_identity",
]


@dataclass(frozen=True)
class StoredModel:
    """One downloaded model found in an inferencer's local store.

    ``name`` is the repository/model identifier (e.g. ``mlx-community/Llama-3.2``
    or ``llama3.1:8b``); ``path`` is the on-disk file or directory; ``size_bytes``
    is its total on-disk footprint as seen by the scanner.
    """

    inferencer: str
    store_format: StoreFormat
    name: str
    path: str
    size_bytes: int


def expand_store_path(raw: str, *, home: Path | None = None) -> Path:
    """Expand ``~`` in a configured store path against ``home`` (or ``Path.home()``)."""

    text = raw.strip()
    if text == "~" or text.startswith("~/"):
        base = home if home is not None else Path.home()
        return base / text[2:] if text.startswith("~/") else base
    return Path(text)


def scan_inferencers(
    configs: Iterable[InferencerConfig],
    *,
    home: Path | None = None,
) -> list[StoredModel]:
    """Scan every inferencer that declares a model store, flattened into one list."""

    models: list[StoredModel] = []
    for cfg in configs:
        models.extend(scan_inferencer(cfg, home=home))
    return models


def scan_inferencer(
    cfg: InferencerConfig,
    *,
    home: Path | None = None,
) -> list[StoredModel]:
    """List the models present in ``cfg``'s store using its format's strategy.

    Returns an empty list when the inferencer declares no store, or when none of
    its store paths exist (missing/empty store, or a non-Darwin path absent on
    this machine) — never raises for those cases.
    """

    if cfg.model_store is None or cfg.store_format is None:
        return []

    strategy = _STRATEGIES[cfg.store_format]
    models: list[StoredModel] = []
    for raw in cfg.model_store:
        base = expand_store_path(raw, home=home)
        if not base.is_dir():
            continue
        for name, path, size in strategy(base):
            models.append(
                StoredModel(
                    inferencer=cfg.name,
                    store_format=cfg.store_format,
                    name=name,
                    path=str(path),
                    size_bytes=size,
                )
            )
    return models


# --- Normalized inventory record (Story 11.2-001) -------------------------


@dataclass(frozen=True)
class LocalModel:
    """One discovered model normalized with provenance and a content identity.

    Built from a :class:`StoredModel` by :func:`normalize`, this is the shape
    inventory views and sharing detection (Story 11.3) consume. ``quant`` and
    ``provider`` are parsed from the path/name where present and degrade to
    ``None`` otherwise. ``identity`` is symlink-stable (``os.path.realpath`` of
    the model file/dir, or the Ollama model-weights blob sha) so two engines
    pointing at the same on-disk artifact resolve to one logical model.
    """

    inferencer: str
    store_format: StoreFormat
    name: str
    path: str
    size_bytes: int
    quant: str | None
    provider: str | None
    identity: str


def normalize(model: StoredModel) -> LocalModel:
    """Turn a raw :class:`StoredModel` into a provenance-carrying :class:`LocalModel`.

    Quant and provider are parsed from the model name first, then its path, so a
    token living in either a filename or a parent directory is recognised. Never
    raises: unparseable provenance degrades to ``None``.
    """

    quant = parse_quant(model.name) or parse_quant(model.path)
    provider = parse_provider(model.name) or parse_provider(model.path)
    return LocalModel(
        inferencer=model.inferencer,
        store_format=model.store_format,
        name=model.name,
        path=model.path,
        size_bytes=model.size_bytes,
        quant=quant,
        provider=provider,
        identity=content_identity(model),
    )


def normalize_all(models: Iterable[StoredModel]) -> list[LocalModel]:
    """Normalize every scanned model, preserving order."""

    return [normalize(model) for model in models]


#: Recognised GGUF / MLX quantization tokens, e.g. ``Q4_K_M``, ``IQ3_XXS``,
#: ``Q8_0``, ``F16``/``BF16``, and MLX bit suffixes like ``4bit`` / ``8-bit``.
_QUANT_RE = re.compile(
    r"(?<![A-Za-z0-9])("
    r"I?Q\d+(?:_[A-Za-z0-9]+)*"  # Q4_K_M, IQ3_XXS, Q8_0, Q6_K
    r"|B?F(?:16|32)"  # F16, F32, BF16
    r"|\d+-?bit"  # 4bit, 4-bit, 8bit
    r")(?![A-Za-z0-9])",
    re.IGNORECASE,
)

#: Known quant publishers — the Unsloth-vs-Bartowski provenance lesson (Epic-10).
#: Matched case-insensitively against the model path/name; canonical casing is
#: the HuggingFace org name so it lines up with the scorecard's ``provider``.
_KNOWN_PROVIDERS: tuple[str, ...] = (
    "unsloth",
    "bartowski",
    "mradermacher",
    "TheBloke",
    "mlx-community",
    "lmstudio-community",
)


def parse_quant(text: str) -> str | None:
    """Extract a quant token (``Q4_K_M``, ``IQ3_XXS``, ``4bit``) from ``text``.

    Returns the matched substring with its original casing, or ``None`` when no
    quant token is present. Parameter counts (``7B``) and versions (``2.5``) are
    not mistaken for quants.
    """

    match = _QUANT_RE.search(text)
    return match.group(1) if match else None


def parse_provider(text: str) -> str | None:
    """Extract a known quant publisher (Unsloth/Bartowski/...) from ``text``.

    Matches a curated set of publishers case-insensitively anywhere in the
    path/name and returns the canonical name; ``None`` when none is present.
    """

    lowered = text.lower()
    for provider in _KNOWN_PROVIDERS:
        if provider.lower() in lowered:
            return provider
    return None


def content_identity(model: StoredModel) -> str:
    """Symlink-stable identity used to recognise the same on-disk artifact.

    For file/dir stores this is ``os.path.realpath`` of the model path (stable
    across scans and collapsing symlinks). For Ollama — a content-addressed
    store — it is the model-weights blob sha from the manifest, falling back to
    the manifest realpath when that is unavailable.
    """

    if model.store_format == "ollama":
        sha = _ollama_model_blob_sha(Path(model.path))
        if sha is not None:
            return sha
    return os.path.realpath(model.path)


def _ollama_model_blob_sha(manifest: Path) -> str | None:
    """Return the ``sha256:`` digest of an Ollama manifest's model-weights layer."""

    try:
        doc = json.loads(manifest.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    if not isinstance(doc, dict):
        return None
    for layer in doc.get("layers") or []:
        if not isinstance(layer, dict):
            continue
        media_type = layer.get("mediaType")
        digest = layer.get("digest")
        if (
            isinstance(media_type, str)
            and media_type.endswith(".model")
            and isinstance(digest, str)
        ):
            return digest
    return None


# --- Per-format scan strategies -------------------------------------------
#
# Each yields (name, path, size_bytes) tuples for the models found under `base`.

_Found = tuple[str, Path, int]


def _scan_gguf(base: Path) -> Iterator[_Found]:
    """GGUF consumers (llama.cpp, GPT4All, LM Studio GGUF): glob ``*.gguf`` files."""

    for path in sorted(base.rglob("*.gguf")):
        if not path.is_file():
            continue
        # Skip llama.cpp split shards past the first so a model counts once.
        if _is_secondary_shard(path.name):
            continue
        yield path.stem, path, _file_size(path)


def _scan_mlx_dirs(base: Path) -> Iterator[_Found]:
    """LM Studio / publisher-model MLX layout: ``<publisher>/<model>/`` safetensors."""

    for publisher in sorted(_iter_dirs(base)):
        for model_dir in sorted(_iter_dirs(publisher)):
            if not any(model_dir.glob("*.safetensors")):
                continue
            name = f"{publisher.name}/{model_dir.name}"
            yield name, model_dir, _dir_size(model_dir)


def _scan_hf_cache(base: Path) -> Iterator[_Found]:
    """HuggingFace hub cache: ``models--<org>--<repo>`` directories."""

    for repo_dir in sorted(_iter_dirs(base)):
        if not repo_dir.name.startswith("models--"):
            continue
        name = repo_dir.name[len("models--") :].replace("--", "/")
        yield name, repo_dir, _dir_size(repo_dir)


def _scan_ollama(base: Path) -> Iterator[_Found]:
    """Ollama store: parse ``manifests/`` JSON, sum the ``blobs/`` layer sizes."""

    manifests = base / "manifests"
    if not manifests.is_dir():
        return
    for manifest in sorted(manifests.rglob("*")):
        if not manifest.is_file():
            continue
        try:
            doc = json.loads(manifest.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if not isinstance(doc, dict):
            continue
        layers = doc.get("layers")
        if not isinstance(layers, list):
            continue
        name = _ollama_name(manifest, manifests)
        size = _ollama_size(base, doc)
        yield name, manifest, size


# --- Filesystem helpers ----------------------------------------------------


def _iter_dirs(base: Path) -> Iterator[Path]:
    try:
        children = list(base.iterdir())
    except OSError:
        return
    for child in children:
        if child.is_dir():
            yield child


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _dir_size(base: Path) -> int:
    total = 0
    for path in base.rglob("*"):
        if path.is_file() and not path.is_symlink():
            total += _file_size(path)
    return total


def _is_secondary_shard(filename: str) -> bool:
    """True for GGUF split parts other than the first (``...-00002-of-00003.gguf``)."""

    stem = filename[: -len(".gguf")] if filename.endswith(".gguf") else filename
    parts = stem.rsplit("-", 4)
    # Pattern: <name>-<NNNNN>-of-<MMMMM>
    if len(parts) >= 4 and parts[-2] == "of" and parts[-3].isdigit() and parts[-1].isdigit():
        return int(parts[-3]) > 1
    return False


def _ollama_name(manifest: Path, manifests_root: Path) -> str:
    """Reconstruct ``model:tag`` from the manifest path under ``manifests/``."""

    rel = manifest.relative_to(manifests_root)
    parts = rel.parts
    # registry/.../<namespace>/<model>/<tag> -> the trailing model/tag is the name.
    if len(parts) >= 2:
        return f"{parts[-2]}:{parts[-1]}"
    return rel.name


def _ollama_size(base: Path, doc: dict) -> int:
    """Sum blob sizes for a manifest, preferring on-disk size over the declared one."""

    total = 0
    blobs = base / "blobs"
    entries = list(doc.get("layers") or [])
    config = doc.get("config")
    if isinstance(config, dict):
        entries.append(config)
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        digest = entry.get("digest")
        blob = _blob_path(blobs, digest) if isinstance(digest, str) else None
        if blob is not None and blob.is_file():
            total += _file_size(blob)
        elif isinstance(entry.get("size"), int):
            total += entry["size"]
    return total


def _blob_path(blobs: Path, digest: str) -> Path:
    """Map a ``sha256:<hex>`` digest to its ``blobs/sha256-<hex>`` file path."""

    return blobs / digest.replace(":", "-")


_STRATEGIES: dict[StoreFormat, Callable[[Path], Iterator[_Found]]] = {
    "gguf": _scan_gguf,
    "ollama": _scan_ollama,
    "hf-safetensors": _scan_hf_cache,
    "mlx": _scan_mlx_dirs,
}
