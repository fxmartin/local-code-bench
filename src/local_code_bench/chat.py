"""Streaming chat endpoint for the unified dashboard (story 09.7-001).

A single ``POST /api/chat`` handler that lets FX smoke-test a model from the
dashboard without writing a benchmark. It is a thin client over the existing
OpenAI-compatible streaming provider (:mod:`local_code_bench.provider`), not a new
inference path:

1. Parse a posted multi-turn message list (plus optional system prompt,
   temperature, and max-tokens) into a :class:`~local_code_bench.provider.ChatRequest`.
2. Stream the reply through :func:`provider_for_model` token-by-token, serialized as
   Server-Sent Events so the browser mirrors the harness's own SSE parsing.

Multi-turn state lives client-side and is posted on every turn -- there is no server
DB, keeping with the stdlib-first, single-user model. The handler **never** starts an
inference server: chat talks to whichever engine is already serving the model's
``base_url``, so the one-active invariant Epic-08 enforces is respected by
construction (story 09.7-001 AC3). Streamed text, token counts, and error reasons are
the only fields sent to the browser, and error reasons are sanitized of host paths --
no API keys, ``.env`` contents, or host paths leak (AC5).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from .config import ModelConfig
from .metrics import StreamEvent
from .provider import ChatRequest, Message, ProviderError, provider_for_model

# Sensible interactive defaults: a little sampling for a smoke test, and a cap that
# keeps a chatty local model from running away (mirrors the suite-run default).
DEFAULT_TEMPERATURE = 0.7
DEFAULT_MAX_TOKENS = 1024

_VALID_ROLES = frozenset({"system", "user", "assistant"})

ProviderFactory = Callable[[ModelConfig], object]


class ChatError(ValueError):
    """Raised when a posted chat composition is invalid."""


@dataclass(frozen=True)
class ChatStreamResponse:
    """A ready-to-serve SSE reply: HTTP status plus an iterator of ``data:`` chunks."""

    status: int
    events: Iterator[str]


def build_chat_request(body: dict[str, object], *, model: ModelConfig) -> ChatRequest:
    """Parse a posted chat body into a :class:`ChatRequest`, raising :class:`ChatError`.

    Validation mirrors the harness's config-validation tone: clear, actionable
    messages and nothing launched on bad input. Defaults are sensible -- a small
    sampling temperature and the model's own ``max_tokens`` cap (else
    :data:`DEFAULT_MAX_TOKENS`).
    """

    raw_messages = body.get("messages")
    if not isinstance(raw_messages, list) or not raw_messages:
        raise ChatError("messages must be a non-empty list of {role, content} turns")

    messages: list[Message] = []
    for index, turn in enumerate(raw_messages):
        if not isinstance(turn, dict):
            raise ChatError(f"messages[{index}] must be a mapping")
        role = turn.get("role")
        content = turn.get("content")
        if role not in _VALID_ROLES:
            raise ChatError(f"messages[{index}].role must be one of {sorted(_VALID_ROLES)}")
        if not isinstance(content, str):
            raise ChatError(f"messages[{index}].content must be a string")
        messages.append(Message(role, content))

    return ChatRequest(
        messages=tuple(messages),
        system=_parse_system(body.get("system")),
        temperature=_parse_temperature(body.get("temperature")),
        max_tokens=_parse_max_tokens(body.get("max_tokens"), model),
    )


def chat_action(
    body: object,
    models: dict[str, ModelConfig],
    *,
    provider_factory: ProviderFactory | None = None,
) -> tuple[int, dict[str, object]] | ChatStreamResponse:
    """Validate a chat request and return either a 400 error or an SSE stream.

    On success the reply streams as :class:`ChatStreamResponse`; the model's reply is
    produced lazily so the caller controls (and can cancel) the stream. The provider is
    resolved through :func:`provider_for_model` unless ``provider_factory`` overrides it.
    """

    if not isinstance(body, dict):
        return 400, {"error": "request body must be a JSON object"}
    name = body.get("model")
    if not isinstance(name, str) or name not in models:
        return 400, {"error": f"unknown model: {name!r}"}
    model = models[name]
    try:
        request = build_chat_request(body, model=model)
    except ChatError as exc:
        return 400, {"error": str(exc)}

    factory = provider_factory or provider_for_model
    provider = factory(model)
    events = provider.stream_chat(request)  # type: ignore[attr-defined]
    return ChatStreamResponse(200, sse_chat_events(events))


def sse_chat_events(
    events: Iterable[StreamEvent], *, clock: Callable[[], float] = perf_counter
) -> Iterator[str]:
    """Serialize stream events as SSE ``data:`` chunks, token-by-token.

    Content deltas are emitted as ``{"delta": ...}``; the stream closes with a
    ``{"done": true, ...}`` event carrying token usage. A provider failure mid-stream
    becomes a sanitized ``{"error": ...}`` event rather than a dropped connection.
    """

    started_at = clock()
    first_token_at: float | None = None
    finished_at = started_at
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    try:
        for event in events:
            now = clock()
            finished_at = now
            if event.content:
                if first_token_at is None:
                    first_token_at = now
                yield _sse({"delta": event.content})
            if event.prompt_tokens is not None:
                prompt_tokens = event.prompt_tokens
            if event.completion_tokens is not None:
                completion_tokens = event.completion_tokens
        finished_at = clock()
        yield _sse(
            {
                "done": True,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "metrics": _openai_endpoint_metrics(
                    started_at=started_at,
                    first_token_at=first_token_at,
                    finished_at=finished_at,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                ),
            }
        )
    except ProviderError as exc:
        yield _sse({"error": _sanitize(str(exc))})


def _openai_endpoint_metrics(
    *,
    started_at: float,
    first_token_at: float | None,
    finished_at: float,
    prompt_tokens: int | None,
    completion_tokens: int | None,
) -> dict[str, float | int | None]:
    total_duration = max(finished_at - started_at, 0.0)
    prompt_duration = (
        None if first_token_at is None else max(first_token_at - started_at, 0.0)
    )
    eval_duration = None if first_token_at is None else max(finished_at - first_token_at, 0.0)
    return {
        "total_duration_seconds": total_duration,
        # OpenAI-compatible chat/completions does not expose model load timing.
        "load_duration_seconds": None,
        "prompt_eval_count": prompt_tokens,
        "prompt_eval_duration_seconds": prompt_duration,
        "prompt_eval_rate": _rate(prompt_tokens, prompt_duration),
        "eval_count": completion_tokens,
        "eval_duration_seconds": eval_duration,
        "eval_rate": _rate(completion_tokens, eval_duration),
    }


def _rate(tokens: int | None, seconds: float | None) -> float | None:
    if tokens is None or seconds is None or seconds <= 0:
        return None
    return tokens / seconds


def _sse(payload: dict[str, object]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _sanitize(text: str) -> str:
    """Strip the user's home path from a message so no host path reaches the browser."""

    return text.replace(str(Path.home()), "~")


def _parse_system(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ChatError("system must be a string when set")
    return value or None


def _parse_temperature(value: object) -> float:
    if value is None:
        return DEFAULT_TEMPERATURE
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ChatError("temperature must be a number between 0 and 2")
    if not 0.0 <= value <= 2.0:
        raise ChatError("temperature must be between 0 and 2")
    return float(value)


def _parse_max_tokens(value: object, model: ModelConfig) -> int:
    if value is None:
        return model.max_tokens or DEFAULT_MAX_TOKENS
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ChatError("max_tokens must be a positive integer when set")
    return value
