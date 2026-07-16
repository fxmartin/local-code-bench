"""Stable provider identity for endpoints without a local inference engine."""

from __future__ import annotations

from urllib.parse import urlsplit

from local_code_bench.config import ModelConfig


def endpoint_provider_name(model: ModelConfig) -> str | None:
    """Return a safe display/grouping identity for a non-inferencer endpoint."""

    if model.inferencer is not None:
        return None
    try:
        hostname = urlsplit(model.base_url).hostname
    except ValueError:
        return None
    if hostname is None:
        return None
    hostname = hostname.lower().rstrip(".")
    if hostname == "openrouter.ai" or hostname.endswith(".openrouter.ai"):
        return "openrouter.ai"
    if model.type == "anthropic" and hostname == "api.anthropic.com":
        return "anthropic"
    return hostname or None
