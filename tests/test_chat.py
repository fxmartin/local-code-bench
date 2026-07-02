from __future__ import annotations

import json

import pytest

from local_code_bench import chat
from local_code_bench.config import ModelConfig, TokenPrices
from local_code_bench.metrics import StreamEvent
from local_code_bench.provider import ChatRequest, Message, ProviderError


# ---------------------------------------------------------------------------
# fixtures / builders
# ---------------------------------------------------------------------------


def _model(name: str = "qwen", *, max_tokens: int | None = None) -> ModelConfig:
    return ModelConfig(
        name=name,
        type="openai",
        base_url="http://127.0.0.1:8000/v1",
        model_id=f"{name}-id",
        pinned_revision="main",
        price_per_1k_tokens=TokenPrices(input=0.0, output=0.0),
        max_tokens=max_tokens,
        inferencer="dflash",
    )


def _models() -> dict[str, ModelConfig]:
    return {"qwen": _model("qwen")}


class _FakeProvider:
    """Mirrors tests/test_provider.py: a stand-in for a streaming provider."""

    def __init__(self, model: ModelConfig, *, events=None, error=None) -> None:
        self.model = model
        self._events = events or []
        self._error = error
        self.captured: ChatRequest | None = None

    def stream_chat(self, request: ChatRequest):
        self.captured = request
        for event in self._events:
            yield event
        if self._error is not None:
            raise self._error


def _factory(provider: _FakeProvider):
    return lambda model: provider


def _collect(response: chat.ChatStreamResponse) -> list[dict]:
    """Decode the SSE chunk stream into the list of JSON data payloads."""

    payloads = []
    for chunk in response.events:
        assert chunk.startswith("data: ")
        assert chunk.endswith("\n\n")
        payloads.append(json.loads(chunk[len("data: ") : -2]))
    return payloads


# ---------------------------------------------------------------------------
# build_chat_request: multi-turn assembly + applied params + defaults
# ---------------------------------------------------------------------------


def test_build_chat_request_assembles_multi_turn_messages() -> None:
    body = {
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "more"},
        ]
    }

    request = chat.build_chat_request(body, model=_model())

    assert request.messages == (
        Message("user", "hi"),
        Message("assistant", "hello"),
        Message("user", "more"),
    )


def test_build_chat_request_applies_system_temperature_and_max_tokens() -> None:
    body = {
        "messages": [{"role": "user", "content": "hi"}],
        "system": "Be terse.",
        "temperature": 0.5,
        "max_tokens": 256,
    }

    request = chat.build_chat_request(body, model=_model())

    assert request.system == "Be terse."
    assert request.temperature == 0.5
    assert request.max_tokens == 256


def test_build_chat_request_defaults_are_sensible() -> None:
    request = chat.build_chat_request(
        {"messages": [{"role": "user", "content": "hi"}]}, model=_model()
    )

    assert request.temperature == chat.DEFAULT_TEMPERATURE
    assert request.max_tokens == chat.DEFAULT_MAX_TOKENS
    assert request.system is None


def test_build_chat_request_max_tokens_defaults_to_model_cap() -> None:
    request = chat.build_chat_request(
        {"messages": [{"role": "user", "content": "hi"}]}, model=_model(max_tokens=64)
    )

    assert request.max_tokens == 64


def test_build_chat_request_rejects_empty_messages() -> None:
    with pytest.raises(chat.ChatError):
        chat.build_chat_request({"messages": []}, model=_model())


def test_build_chat_request_rejects_missing_messages() -> None:
    with pytest.raises(chat.ChatError):
        chat.build_chat_request({}, model=_model())


def test_build_chat_request_rejects_unknown_role() -> None:
    with pytest.raises(chat.ChatError):
        chat.build_chat_request(
            {"messages": [{"role": "wizard", "content": "hi"}]}, model=_model()
        )


def test_build_chat_request_rejects_non_string_content() -> None:
    with pytest.raises(chat.ChatError):
        chat.build_chat_request(
            {"messages": [{"role": "user", "content": 42}]}, model=_model()
        )


def test_build_chat_request_rejects_out_of_range_temperature() -> None:
    with pytest.raises(chat.ChatError):
        chat.build_chat_request(
            {"messages": [{"role": "user", "content": "hi"}], "temperature": 9.0},
            model=_model(),
        )


def test_build_chat_request_rejects_non_positive_max_tokens() -> None:
    with pytest.raises(chat.ChatError):
        chat.build_chat_request(
            {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 0},
            model=_model(),
        )


def test_build_chat_request_rejects_non_mapping_turn() -> None:
    with pytest.raises(chat.ChatError, match="must be a mapping"):
        chat.build_chat_request({"messages": ["not-a-dict"]}, model=_model())


def test_build_chat_request_rejects_non_string_system() -> None:
    with pytest.raises(chat.ChatError, match="system must be a string"):
        chat.build_chat_request(
            {"messages": [{"role": "user", "content": "hi"}], "system": 42},
            model=_model(),
        )


def test_build_chat_request_rejects_non_numeric_temperature() -> None:
    with pytest.raises(chat.ChatError, match="temperature must be a number"):
        chat.build_chat_request(
            {"messages": [{"role": "user", "content": "hi"}], "temperature": True},
            model=_model(),
        )


# ---------------------------------------------------------------------------
# chat_action: validation + streaming
# ---------------------------------------------------------------------------


def test_chat_action_rejects_non_object_body() -> None:
    result = chat.chat_action(["not", "an", "object"], _models())

    assert result == (400, {"error": "request body must be a JSON object"})


def test_chat_action_rejects_unknown_model() -> None:
    body = {"model": "ghost", "messages": [{"role": "user", "content": "hi"}]}

    status, payload = chat.chat_action(body, _models())

    assert status == 400
    assert "ghost" in payload["error"]


def test_chat_action_rejects_invalid_composition() -> None:
    body = {"model": "qwen", "messages": []}

    status, payload = chat.chat_action(body, _models())

    assert status == 400


def test_chat_action_streams_token_by_token_with_usage() -> None:
    provider = _FakeProvider(
        _model(),
        events=[
            StreamEvent(content="Hel"),
            StreamEvent(content="lo"),
            StreamEvent(prompt_tokens=11, completion_tokens=2),
        ],
    )
    body = {"model": "qwen", "messages": [{"role": "user", "content": "hi"}]}

    response = chat.chat_action(body, _models(), provider_factory=_factory(provider))

    assert isinstance(response, chat.ChatStreamResponse)
    assert response.status == 200
    payloads = _collect(response)
    assert payloads[0] == {"delta": "Hel"}
    assert payloads[1] == {"delta": "lo"}
    assert payloads[-1]["done"] is True
    assert payloads[-1]["prompt_tokens"] == 11
    assert payloads[-1]["completion_tokens"] == 2


def test_sse_chat_events_reports_openai_endpoint_metrics() -> None:
    ticks = iter([10.0, 10.25, 11.0, 11.25, 11.5])
    events = [
        StreamEvent(content="Hel"),
        StreamEvent(content="lo"),
        StreamEvent(prompt_tokens=11, completion_tokens=2),
    ]

    payloads = [
        json.loads(chunk[len("data: ") : -2])
        for chunk in chat.sse_chat_events(events, clock=lambda: next(ticks))
    ]

    metrics = payloads[-1]["metrics"]
    assert metrics == {
        "total_duration_seconds": 1.5,
        "load_duration_seconds": None,
        "prompt_eval_count": 11,
        "prompt_eval_duration_seconds": 0.25,
        "prompt_eval_rate": 44.0,
        "eval_count": 2,
        "eval_duration_seconds": 1.25,
        "eval_rate": 1.6,
    }


def test_chat_action_forwards_assembled_request_to_provider() -> None:
    provider = _FakeProvider(_model(), events=[StreamEvent(content="x")])
    body = {
        "model": "qwen",
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "yo"},
            {"role": "user", "content": "again"},
        ],
        "system": "Be terse.",
        "temperature": 0.3,
        "max_tokens": 128,
    }

    response = chat.chat_action(body, _models(), provider_factory=_factory(provider))
    _collect(response)  # drive the generator so the provider captures the request

    request = provider.captured
    assert request is not None
    assert request.system == "Be terse."
    assert request.temperature == 0.3
    assert request.max_tokens == 128
    assert request.messages == (
        Message("user", "hi"),
        Message("assistant", "yo"),
        Message("user", "again"),
    )


def test_chat_action_targets_selected_model_without_starting_a_server() -> None:
    # AC3: chat talks to the configured endpoint; it never brings an engine up.
    seen: dict = {}

    def factory(model: ModelConfig):
        seen["base_url"] = model.base_url
        return _FakeProvider(model, events=[StreamEvent(content="x")])

    body = {"model": "qwen", "messages": [{"role": "user", "content": "hi"}]}
    response = chat.chat_action(body, _models(), provider_factory=factory)
    _collect(response)

    assert seen["base_url"] == "http://127.0.0.1:8000/v1"


# ---------------------------------------------------------------------------
# sse_chat_events: error handling, sanitization, clean cancellation
# ---------------------------------------------------------------------------


def test_sse_chat_events_emits_error_event_on_provider_error() -> None:
    provider = _FakeProvider(
        _model(),
        events=[StreamEvent(content="partial")],
        error=ProviderError("qwen request failed"),
    )

    payloads = _collect(
        chat.chat_action(
            {"model": "qwen", "messages": [{"role": "user", "content": "hi"}]},
            _models(),
            provider_factory=_factory(provider),
        )
    )

    assert payloads[0] == {"delta": "partial"}
    assert "error" in payloads[-1]
    assert "qwen request failed" in payloads[-1]["error"]


def test_sse_chat_events_sanitizes_host_paths(monkeypatch) -> None:
    monkeypatch.setattr(chat.Path, "home", classmethod(lambda cls: chat.Path("/Users/fx")))

    def boom():
        yield StreamEvent(content="ok")
        raise ProviderError("failed at /Users/fx/.env reading key")

    payloads = [json.loads(c[len("data: ") : -2]) for c in chat.sse_chat_events(boom())]

    assert "/Users/fx" not in payloads[-1]["error"]
    assert "~/.env" in payloads[-1]["error"]


def test_sse_chat_events_closes_cleanly_when_cancelled() -> None:
    # AC4: stopping a stream mid-flight must cancel cleanly, not raise.
    def endless():
        while True:
            yield StreamEvent(content="tok")

    stream = chat.sse_chat_events(endless())
    assert next(stream).startswith("data: ")
    stream.close()  # client hit "stop" -- generator closes without error
