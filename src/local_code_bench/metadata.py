"""Run metadata helpers."""

from __future__ import annotations

import platform
from datetime import UTC, datetime

from local_code_bench.config import ModelConfig


def run_metadata(
    *,
    models: list[ModelConfig],
    suite: str | None,
    temperature: float = 0.0,
    seed: int = 0,
    hardware_tag: str = "M3 Max 48 GB",
    tier: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build a run's metadata header.

    ``tier`` carries Epic-12 tier provenance (served tier, whether the model was
    promoted from external first, whether it was served in place from external —
    see :meth:`inferencers.tiering.TierResolution.metadata`). It is included only
    when supplied, so non-tiered runs keep their existing header shape and the
    leaderboard/dashboard can caveat external-served speed when present.
    """

    metadata: dict[str, object] = {
        "record_type": "metadata",
        "timestamp": datetime.now(tz=UTC).isoformat(),
        "seed": seed,
        "temperature": temperature,
        "suite": suite,
        "hardware_tag": hardware_tag,
        "python": platform.python_version(),
        "models": {
            model.name: {
                "type": model.type,
                "model_id": model.model_id,
                "pinned_revision": model.pinned_revision,
            }
            for model in models
        },
    }
    if tier is not None:
        metadata["tier"] = tier
    return metadata
