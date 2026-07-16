from __future__ import annotations

import io
import json
import urllib.error

import pytest

from local_code_bench.config import ModelConfig, TokenPrices
from local_code_bench.provider import (
    AnthropicStreamingProvider,
    ChatRequest,
    OpenAIStreamingProvider,
    ProviderError,
    _api_key,
    _decode_lines,
    _load_env_file,
    _redact,
    parse_anthropic_sse_lines,
    parse_openai_sse_lines,
    provider_for_model,
)


class _FakeResponse:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def __iter__(self):
        return iter(self._lines)


def _openai_model() -> ModelConfig:
    return ModelConfig(
        name="cloud",
        type="openai",
        base_url="https://example.test/v1",
        model_id="qwen",
        pinned_revision="manual",
        price_per_1k_tokens=TokenPrices(input=0.0, output=0.0),
    )


def _capture_openai_body(monkeypatch, request: ChatRequest, model: ModelConfig | None = None) -> dict:
    captured: dict = {}

    def fake_urlopen(http_request, timeout=None):
        captured["body"] = json.loads(http_request.data.decode("utf-8"))
        return _FakeResponse([b'data: {"choices":[{"delta":{"content":"x"}}]}\n', b"data: [DONE]\n"])

    monkeypatch.setattr("local_code_bench.provider.urllib.request.urlopen", fake_urlopen)
    list(OpenAIStreamingProvider(model or _openai_model()).stream_chat(request))
    return captured["body"]


def test_openai_provider_sends_max_tokens_when_set(monkeypatch) -> None:
    body = _capture_openai_body(monkeypatch, ChatRequest(prompt="hi", max_tokens=256))

    assert body["max_tokens"] == 256


def test_openai_provider_omits_max_tokens_when_unset(monkeypatch) -> None:
    body = _capture_openai_body(monkeypatch, ChatRequest(prompt="hi"))

    assert "max_tokens" not in body


def test_openai_provider_merges_extra_body(monkeypatch) -> None:
    model = ModelConfig(
        name="cloud",
        type="openai",
        base_url="https://example.test/v1",
        model_id="qwen",
        pinned_revision="manual",
        price_per_1k_tokens=TokenPrices(input=0.0, output=0.0),
        extra_body={"reasoning": {"enabled": False}},
    )

    body = _capture_openai_body(monkeypatch, ChatRequest(prompt="hi"), model)

    assert body["reasoning"] == {"enabled": False}


def test_parse_openai_sse_lines_extracts_content_and_usage() -> None:
    events = list(
        parse_openai_sse_lines(
            [
                'data: {"choices":[{"delta":{"content":"hi"}}]}\n',
                'data: {"choices":[],"usage":{"prompt_tokens":4,"completion_tokens":1}}\n',
                "data: [DONE]\n",
            ]
        )
    )

    assert [event.content for event in events] == ["hi", ""]
    assert events[-1].prompt_tokens == 4
    assert events[-1].completion_tokens == 1


def test_parse_openai_sse_lines_extracts_reasoning_delta() -> None:
    events = list(
        parse_openai_sse_lines(
            [
                'data: {"choices":[{"delta":{"reasoning":"Thinking"}}]}\n',
                'data: {"choices":[{"delta":{"reasoning_content":" more"}}]}\n',
                "data: [DONE]\n",
            ]
        )
    )

    assert [event.content for event in events] == ["Thinking", " more"]


def test_parse_openai_sse_lines_ignores_noise_and_supports_text_choices() -> None:
    events = list(
        parse_openai_sse_lines(
            [
                "\n",
                ": keepalive\n",
                "event: ping\n",
                'data: {"choices":[{"text":"legacy text"}]}\n',
                'data: {"usage":{"prompt_tokens":"bad","completion_tokens":2}}\n',
                "data: [DONE]\n",
                'data: {"choices":[{"delta":{"content":"ignored after done"}}]}\n',
            ]
        )
    )

    assert [event.content for event in events] == ["legacy text", ""]
    assert events[-1].prompt_tokens is None
    assert events[-1].completion_tokens == 2


def test_parse_openai_sse_lines_reports_malformed_json() -> None:
    with pytest.raises(ProviderError, match="malformed stream JSON"):
        list(parse_openai_sse_lines(["data: {bad json}\n"]))


def test_openai_provider_sends_multi_turn_messages(monkeypatch) -> None:
    from local_code_bench.provider import Message

    request = ChatRequest(
        messages=(
            Message("user", "hi"),
            Message("assistant", "hello"),
            Message("user", "more"),
        )
    )
    body = _capture_openai_body(monkeypatch, request)

    assert body["messages"] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "more"},
    ]


def test_openai_provider_prepends_system_message(monkeypatch) -> None:
    from local_code_bench.provider import Message

    request = ChatRequest(messages=(Message("user", "hi"),), system="Be terse.")
    body = _capture_openai_body(monkeypatch, request)

    assert body["messages"][0] == {"role": "system", "content": "Be terse."}
    assert body["messages"][1] == {"role": "user", "content": "hi"}
    assert body["temperature"] == 0.0


def test_openai_provider_falls_back_to_prompt_when_no_messages(monkeypatch) -> None:
    body = _capture_openai_body(monkeypatch, ChatRequest(prompt="just this"))

    assert body["messages"] == [{"role": "user", "content": "just this"}]


def test_anthropic_provider_lifts_system_out_of_messages(monkeypatch) -> None:
    from local_code_bench.provider import AnthropicStreamingProvider, Message

    model = ModelConfig(
        name="claude",
        type="anthropic",
        base_url="https://example.test/v1",
        model_id="claude-x",
        pinned_revision="manual",
        price_per_1k_tokens=TokenPrices(input=0.0, output=0.0),
    )
    captured: dict = {}

    def fake_urlopen(http_request, timeout=None):
        captured["body"] = json.loads(http_request.data.decode("utf-8"))
        return _FakeResponse([b'data: {"type":"message_stop"}\n'])

    monkeypatch.setattr("local_code_bench.provider.urllib.request.urlopen", fake_urlopen)
    request = ChatRequest(
        messages=(Message("user", "hi"), Message("assistant", "yo")), system="Be terse."
    )
    list(AnthropicStreamingProvider(model).stream_chat(request))

    assert captured["body"]["system"] == "Be terse."
    assert captured["body"]["messages"] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "yo"},
    ]


def test_openai_provider_rejects_non_openai_model() -> None:
    model = ModelConfig(
        name="claude",
        type="anthropic",
        base_url="https://example.com",
        model_id="claude",
        pinned_revision="manual",
        price_per_1k_tokens=TokenPrices(input=1, output=1),
    )

    with pytest.raises(ProviderError, match="not openai"):
        OpenAIStreamingProvider(model)


def test_anthropic_provider_rejects_non_anthropic_model() -> None:
    model = ModelConfig(
        name="openai",
        type="openai",
        base_url="https://example.com",
        model_id="gpt",
        pinned_revision="manual",
        price_per_1k_tokens=TokenPrices(input=1, output=1),
    )

    with pytest.raises(ProviderError, match="not anthropic"):
        AnthropicStreamingProvider(model)


def test_provider_for_model_selects_adapter() -> None:
    openai = ModelConfig(
        name="openai",
        type="openai",
        base_url="https://example.com",
        model_id="gpt",
        pinned_revision="manual",
        price_per_1k_tokens=TokenPrices(input=1, output=1),
    )
    anthropic = ModelConfig(
        name="anthropic",
        type="anthropic",
        base_url="https://example.com",
        model_id="claude",
        pinned_revision="manual",
        price_per_1k_tokens=TokenPrices(input=1, output=1),
    )
    unsupported = ModelConfig(
        name="bad",
        type="bad",
        base_url="https://example.com",
        model_id="bad",
        pinned_revision="manual",
        price_per_1k_tokens=TokenPrices(input=1, output=1),
    )

    assert isinstance(provider_for_model(openai), OpenAIStreamingProvider)
    assert isinstance(provider_for_model(anthropic), AnthropicStreamingProvider)
    with pytest.raises(ProviderError, match="unsupported provider type"):
        provider_for_model(unsupported)


def test_provider_for_model_honors_timeout_env(monkeypatch) -> None:
    monkeypatch.setenv("BENCH_PROVIDER_TIMEOUT_SECONDS", "12.5")
    model = ModelConfig(
        name="openai",
        type="openai",
        base_url="https://example.com",
        model_id="gpt",
        pinned_revision="manual",
        price_per_1k_tokens=TokenPrices(input=1, output=1),
    )

    provider = provider_for_model(model)

    assert isinstance(provider, OpenAIStreamingProvider)
    assert provider._timeout_seconds == 12.5


def test_provider_for_model_rejects_invalid_timeout_env(monkeypatch) -> None:
    monkeypatch.setenv("BENCH_PROVIDER_TIMEOUT_SECONDS", "bad")
    model = ModelConfig(
        name="openai",
        type="openai",
        base_url="https://example.com",
        model_id="gpt",
        pinned_revision="manual",
        price_per_1k_tokens=TokenPrices(input=1, output=1),
    )

    with pytest.raises(ProviderError, match="BENCH_PROVIDER_TIMEOUT_SECONDS must be a positive number"):
        provider_for_model(model)


def test_parse_anthropic_sse_lines_extracts_content_and_usage() -> None:
    events = list(
        parse_anthropic_sse_lines(
            [
                'data: {"type":"message_start","message":{"usage":{"input_tokens":5}}}\n',
                'data: {"type":"content_block_delta","delta":{"text":"hi"}}\n',
                'data: {"type":"message_delta","usage":{"output_tokens":2}}\n',
                'data: {"type":"message_stop"}\n',
            ]
        )
    )

    assert events[0].content == "hi"
    assert events[-1].prompt_tokens == 5
    assert events[-1].completion_tokens == 2


def test_parse_anthropic_sse_lines_ignores_noise_and_reports_malformed_json() -> None:
    assert list(parse_anthropic_sse_lines(["event: ping\n"])) == []
    with pytest.raises(ProviderError, match="malformed stream JSON"):
        list(parse_anthropic_sse_lines(["data: {bad json}\n"]))


def test_api_key_loads_from_dotenv(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    _load_env_file.cache_clear()
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=dotenv-secret\n", encoding="utf-8")
    model = ModelConfig(
        name="openrouter",
        type="openai",
        base_url="https://openrouter.ai/api/v1",
        model_id="test",
        pinned_revision="manual",
        price_per_1k_tokens=TokenPrices(input=1, output=1),
        api_key_env="OPENROUTER_API_KEY",
    )

    assert _api_key(model) == "dotenv-secret"


def test_api_key_none_when_model_has_no_env() -> None:
    model = ModelConfig(
        name="local",
        type="openai",
        base_url="http://localhost:8000/v1",
        model_id="local",
        pinned_revision="manual",
        price_per_1k_tokens=TokenPrices(input=0, output=0),
    )

    assert _api_key(model) is None


def test_api_key_reports_missing_env(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    _load_env_file.cache_clear()
    model = ModelConfig(
        name="openrouter",
        type="openai",
        base_url="https://openrouter.ai/api/v1",
        model_id="test",
        pinned_revision="manual",
        price_per_1k_tokens=TokenPrices(input=1, output=1),
        api_key_env="OPENROUTER_API_KEY",
    )

    with pytest.raises(ProviderError, match="requires environment variable OPENROUTER_API_KEY"):
        _api_key(model)


def test_decode_lines_and_redact_helpers() -> None:
    assert list(_decode_lines([b"hello\n", "world\n"])) == ["hello\n", "world\n"]
    assert _redact("secret leaked", "secret") == "[REDACTED] leaked"
    assert _redact("plain", None) == "plain"


# ---------------------------------------------------------------------------
# stream_chat: auth header + error normalization
# ---------------------------------------------------------------------------


def _keyed_openai_model() -> ModelConfig:
    return ModelConfig(
        name="cloud",
        type="openai",
        base_url="https://example.test/v1",
        model_id="qwen",
        pinned_revision="manual",
        price_per_1k_tokens=TokenPrices(input=0.0, output=0.0),
        api_key_env="OPENROUTER_API_KEY",
    )


def _anthropic_model() -> ModelConfig:
    return ModelConfig(
        name="claude",
        type="anthropic",
        base_url="https://example.test/v1",
        model_id="claude-x",
        pinned_revision="manual",
        price_per_1k_tokens=TokenPrices(input=0.0, output=0.0),
    )


def _capture_openai_headers(monkeypatch, model: ModelConfig) -> dict:
    captured: dict = {}

    def fake_urlopen(http_request, timeout=None):
        captured["headers"] = dict(http_request.headers)
        return _FakeResponse([b"data: [DONE]\n"])

    monkeypatch.setattr("local_code_bench.provider.urllib.request.urlopen", fake_urlopen)
    list(OpenAIStreamingProvider(model).stream_chat(ChatRequest(prompt="hi")))
    return captured["headers"]


def test_openai_provider_sets_authorization_header(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")
    _load_env_file.cache_clear()
    headers = _capture_openai_headers(monkeypatch, _keyed_openai_model())

    assert headers["Authorization"] == "Bearer sk-test-123"


def _http_error(code: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://example.test/v1/chat/completions",
        code=code,
        msg="boom",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(body),
    )


def test_openai_provider_normalizes_http_error(monkeypatch) -> None:
    def fake_urlopen(http_request, timeout=None):
        raise _http_error(503, b"server unavailable")

    monkeypatch.setattr("local_code_bench.provider.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(ProviderError, match="HTTP 503"):
        list(OpenAIStreamingProvider(_openai_model()).stream_chat(ChatRequest(prompt="hi")))


def test_openai_provider_normalizes_url_error(monkeypatch) -> None:
    def fake_urlopen(http_request, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("local_code_bench.provider.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(ProviderError, match="request failed"):
        list(OpenAIStreamingProvider(_openai_model()).stream_chat(ChatRequest(prompt="hi")))


def test_openai_provider_normalizes_timeout(monkeypatch) -> None:
    def fake_urlopen(http_request, timeout=None):
        raise TimeoutError

    monkeypatch.setattr("local_code_bench.provider.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(ProviderError, match="timed out"):
        list(OpenAIStreamingProvider(_openai_model()).stream_chat(ChatRequest(prompt="hi")))


def test_anthropic_provider_merges_extra_body(monkeypatch) -> None:
    model = ModelConfig(
        name="claude",
        type="anthropic",
        base_url="https://example.test/v1",
        model_id="claude-x",
        pinned_revision="manual",
        price_per_1k_tokens=TokenPrices(input=0.0, output=0.0),
        extra_body={"thinking": {"type": "enabled"}},
    )
    captured: dict = {}

    def fake_urlopen(http_request, timeout=None):
        captured["body"] = json.loads(http_request.data.decode("utf-8"))
        return _FakeResponse([b'data: {"type":"message_stop"}\n'])

    monkeypatch.setattr("local_code_bench.provider.urllib.request.urlopen", fake_urlopen)
    list(AnthropicStreamingProvider(model).stream_chat(ChatRequest(prompt="hi")))

    assert captured["body"]["thinking"] == {"type": "enabled"}


def test_anthropic_provider_normalizes_http_error(monkeypatch) -> None:
    def fake_urlopen(http_request, timeout=None):
        raise _http_error(429, b"rate limited")

    monkeypatch.setattr("local_code_bench.provider.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(ProviderError, match="HTTP 429"):
        list(AnthropicStreamingProvider(_anthropic_model()).stream_chat(ChatRequest(prompt="hi")))


def test_anthropic_provider_normalizes_url_error(monkeypatch) -> None:
    def fake_urlopen(http_request, timeout=None):
        raise urllib.error.URLError("dns failure")

    monkeypatch.setattr("local_code_bench.provider.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(ProviderError, match="request failed"):
        list(AnthropicStreamingProvider(_anthropic_model()).stream_chat(ChatRequest(prompt="hi")))
