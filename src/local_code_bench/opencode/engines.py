"""Default `/v1` endpoints for the local inference engines (`--engine`).

Maps the local inferencer engines whose default ports were locked with FX to their
OpenAI-compatible base URL, so `run-bench.sh --engine ollama` resolves to the
right loopback endpoint without spelling out a full `--endpoint` URL. Ports are
kept in lock-step with `configs/inferencers.yaml` (asserted in the tests).
"""

from __future__ import annotations

from local_code_bench.config import ConfigError

# engine name -> OpenAI-compatible `/v1` base URL (loopback, default port).
ENGINE_ENDPOINTS: dict[str, str] = {
    "mlx-lm": "http://127.0.0.1:8080/v1",
    "ollama": "http://127.0.0.1:11434/v1",
}


def endpoint_for_engine(name: str) -> str:
    """Return the default `/v1` base URL for a known engine, else raise."""

    try:
        return ENGINE_ENDPOINTS[name]
    except KeyError as exc:
        available = ", ".join(sorted(ENGINE_ENDPOINTS))
        raise ConfigError(f"unknown engine '{name}'. Available engines: {available}") from exc
