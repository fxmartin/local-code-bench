"""Default `/v1` endpoints for the local inference engines (`--engine`).

Maps the ten Epic-08 engines whose default ports were locked with FX to their
OpenAI-compatible base URL, so `run-bench.sh --engine ollama` resolves to the
right loopback endpoint without spelling out a full `--endpoint` URL. Ports are
kept in lock-step with `configs/inferencers.yaml` (asserted in the tests).
"""

from __future__ import annotations

from local_code_bench.config import ConfigError

# engine name -> OpenAI-compatible `/v1` base URL (loopback, default port).
ENGINE_ENDPOINTS: dict[str, str] = {
    "dflash": "http://127.0.0.1:8000/v1",
    "turboquant": "http://127.0.0.1:8002/v1",
    "mlx-lm": "http://127.0.0.1:8080/v1",
    "llama-cpp": "http://127.0.0.1:8081/v1",
    "ollama": "http://127.0.0.1:11434/v1",
    "mlc-llm": "http://127.0.0.1:8082/v1",
    "vllm-mlx": "http://127.0.0.1:8001/v1",
    "exo": "http://127.0.0.1:52415/v1",
    "lm-studio": "http://127.0.0.1:1234/v1",
    "gpt4all": "http://127.0.0.1:4891/v1",
}


def endpoint_for_engine(name: str) -> str:
    """Return the default `/v1` base URL for a known engine, else raise."""

    try:
        return ENGINE_ENDPOINTS[name]
    except KeyError as exc:
        available = ", ".join(sorted(ENGINE_ENDPOINTS))
        raise ConfigError(f"unknown engine '{name}'. Available engines: {available}") from exc
