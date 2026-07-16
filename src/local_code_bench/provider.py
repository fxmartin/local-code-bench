"""Endpoint provider adapters."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from local_code_bench.config import ModelConfig
from local_code_bench.metrics import StreamEvent


class ProviderError(RuntimeError):
    """Raised when an endpoint request cannot be completed."""


@dataclass(frozen=True)
class Message:
    """One turn in a chat conversation."""

    role: str
    content: str


@dataclass(frozen=True)
class ChatRequest:
    prompt: str = ""
    temperature: float = 0.0
    max_tokens: int | None = None
    messages: tuple[Message, ...] | None = None
    system: str | None = None


def _chat_messages(request: ChatRequest) -> list[dict[str, str]]:
    """The conversation turns as OpenAI/Anthropic message dicts (no system role).

    A multi-turn ``request.messages`` is used verbatim; otherwise the single-turn
    ``request.prompt`` is wrapped as one user message, preserving the existing
    suite/sweep behaviour.
    """

    if request.messages is not None:
        return [{"role": m.role, "content": m.content} for m in request.messages]
    return [{"role": "user", "content": request.prompt}]


class OpenAIStreamingProvider:
    """Minimal OpenAI-compatible streaming `/v1/chat/completions` adapter."""

    def __init__(self, model: ModelConfig, *, timeout_seconds: float = 120.0) -> None:
        if model.type != "openai":
            raise ProviderError(f"model '{model.name}' is type '{model.type}', not openai")
        self._model = model
        self._timeout_seconds = timeout_seconds

    def stream_chat(self, request: ChatRequest) -> Iterable[StreamEvent]:
        api_key = _api_key(self._model)
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        messages = _chat_messages(request)
        if request.system:
            messages = [{"role": "system", "content": request.system}, *messages]
        body: dict[str, Any] = {
            "model": self._model.model_id,
            "messages": messages,
            "temperature": request.temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if request.max_tokens is not None:
            body["max_tokens"] = request.max_tokens
        if self._model.extra_body:
            body.update(self._model.extra_body)
        endpoint = f"{self._model.base_url}/chat/completions"
        http_request = urllib.request.Request(
            endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(http_request, timeout=self._timeout_seconds) as response:
                yield from parse_openai_sse_lines(_decode_lines(response))
        except urllib.error.HTTPError as exc:
            message = exc.read().decode("utf-8", errors="replace")
            raise ProviderError(f"{self._model.name} HTTP {exc.code}: {_redact(message, api_key)}") from exc
        except urllib.error.URLError as exc:
            raise ProviderError(f"{self._model.name} request failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise ProviderError(f"{self._model.name} request timed out") from exc


class AnthropicStreamingProvider:
    """Minimal Anthropic streaming adapter normalized to StreamEvent."""

    def __init__(self, model: ModelConfig, *, timeout_seconds: float = 120.0) -> None:
        if model.type != "anthropic":
            raise ProviderError(f"model '{model.name}' is type '{model.type}', not anthropic")
        self._model = model
        self._timeout_seconds = timeout_seconds

    def stream_chat(self, request: ChatRequest) -> Iterable[StreamEvent]:
        api_key = _api_key(self._model)
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "x-api-key": api_key or "",
            "anthropic-version": "2023-06-01",
        }
        body: dict[str, Any] = {
            "model": self._model.model_id,
            "max_tokens": request.max_tokens or 4096,
            "temperature": request.temperature,
            "stream": True,
            "messages": _chat_messages(request),
        }
        if request.system:
            body["system"] = request.system
        if self._model.extra_body:
            body.update(self._model.extra_body)
        http_request = urllib.request.Request(
            f"{self._model.base_url}/messages",
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_request, timeout=self._timeout_seconds) as response:
                yield from parse_anthropic_sse_lines(_decode_lines(response))
        except urllib.error.HTTPError as exc:
            message = exc.read().decode("utf-8", errors="replace")
            raise ProviderError(f"{self._model.name} HTTP {exc.code}: {_redact(message, api_key)}") from exc
        except urllib.error.URLError as exc:
            raise ProviderError(f"{self._model.name} request failed: {exc.reason}") from exc


def provider_for_model(model: ModelConfig) -> OpenAIStreamingProvider | AnthropicStreamingProvider:
    timeout_seconds = _provider_timeout_seconds()
    if model.type == "openai":
        return OpenAIStreamingProvider(model, timeout_seconds=timeout_seconds)
    if model.type == "anthropic":
        return AnthropicStreamingProvider(model, timeout_seconds=timeout_seconds)
    raise ProviderError(f"unsupported provider type: {model.type}")


def parse_openai_sse_lines(lines: Iterable[str]) -> Iterable[StreamEvent]:
    """Parse OpenAI-compatible SSE chunks into normalized stream events."""

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith(":"):
            continue
        if not stripped.startswith("data:"):
            continue

        payload = stripped.removeprefix("data:").strip()
        if payload == "[DONE]":
            break

        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"malformed stream JSON: {payload}") from exc

        content = _content_delta(chunk)
        usage = chunk.get("usage") if isinstance(chunk, dict) else None
        prompt_tokens, completion_tokens = _usage_tokens(usage)
        if content or prompt_tokens is not None or completion_tokens is not None:
            yield StreamEvent(
                content=content,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )


def parse_anthropic_sse_lines(lines: Iterable[str]) -> Iterable[StreamEvent]:
    input_tokens: int | None = None
    output_tokens: int | None = None
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("data:"):
            continue
        payload = stripped.removeprefix("data:").strip()
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"malformed stream JSON: {payload}") from exc
        event_type = chunk.get("type")
        if event_type == "message_start":
            usage = chunk.get("message", {}).get("usage", {})
            input_tokens = usage.get("input_tokens")
            output_tokens = usage.get("output_tokens")
        elif event_type == "content_block_delta":
            text = chunk.get("delta", {}).get("text", "")
            if text:
                yield StreamEvent(content=text)
        elif event_type == "message_delta":
            usage = chunk.get("usage", {})
            output_tokens = usage.get("output_tokens", output_tokens)
        elif event_type == "message_stop":
            yield StreamEvent(prompt_tokens=input_tokens, completion_tokens=output_tokens)


def _decode_lines(response: Any) -> Iterable[str]:
    for raw_line in response:
        if isinstance(raw_line, bytes):
            yield raw_line.decode("utf-8", errors="replace")
        else:
            yield str(raw_line)


def _content_delta(chunk: Any) -> str:
    if not isinstance(chunk, dict):
        return ""
    choices = chunk.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    delta = first.get("delta")
    if isinstance(delta, dict):
        content = delta.get("content")
        if isinstance(content, str):
            return content
        reasoning = delta.get("reasoning")
        if isinstance(reasoning, str):
            return reasoning
        reasoning_content = delta.get("reasoning_content")
        return reasoning_content if isinstance(reasoning_content, str) else ""
    text = first.get("text")
    return text if isinstance(text, str) else ""


def _usage_tokens(usage: Any) -> tuple[int | None, int | None]:
    if not isinstance(usage, dict):
        return None, None
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    return (
        prompt_tokens if isinstance(prompt_tokens, int) else None,
        completion_tokens if isinstance(completion_tokens, int) else None,
    )


def _api_key(model: ModelConfig) -> str | None:
    if model.api_key_env is None:
        return None
    _load_env_file()
    api_key = os.environ.get(model.api_key_env)
    if not api_key:
        raise ProviderError(f"{model.name} requires environment variable {model.api_key_env}")
    return api_key


def _provider_timeout_seconds() -> float:
    value = os.environ.get("BENCH_PROVIDER_TIMEOUT_SECONDS")
    if value is None:
        return 120.0
    try:
        timeout_seconds = float(value)
    except ValueError as exc:
        raise ProviderError("BENCH_PROVIDER_TIMEOUT_SECONDS must be a positive number") from exc
    if timeout_seconds <= 0:
        raise ProviderError("BENCH_PROVIDER_TIMEOUT_SECONDS must be a positive number")
    return timeout_seconds


@cache
def _load_env_file() -> None:
    load_dotenv(dotenv_path=Path.cwd() / ".env")


def _redact(message: str, secret: str | None) -> str:
    if not secret:
        return message
    return message.replace(secret, "[REDACTED]")
