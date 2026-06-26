from __future__ import annotations

from pathlib import Path

import pytest

from local_code_bench.config import ConfigError, ModelConfig, TokenPrices
from local_code_bench.metrics import StreamEvent
from local_code_bench.opencode.invoke import (
    OPENCODE_TASKS,
    OpenCodeOverrides,
    build_record,
    load_prompt,
    resolve_model,
    run_opencode,
)
from local_code_bench.provider import ChatRequest
from local_code_bench.results import read_jsonl


def _model(**overrides: object) -> ModelConfig:
    base = {
        "name": "local",
        "type": "openai",
        "base_url": "http://localhost:9000/v1",
        "model_id": "qwen",
        "pinned_revision": "abc123",
        "price_per_1k_tokens": TokenPrices(input=0.0, output=0.0),
    }
    base.update(overrides)
    return ModelConfig(**base)  # type: ignore[arg-type]


class _FakeProvider:
    """Captures requests and replays a fixed stream with usage tokens."""

    def __init__(self) -> None:
        self.requests: list[ChatRequest] = []

    def stream_chat(self, request: ChatRequest):
        self.requests.append(request)
        yield StreamEvent(content="hello ")
        yield StreamEvent(content="world", prompt_tokens=11, completion_tokens=2)


def _write_prompts(prompts_dir: Path) -> None:
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "task-a.md").write_text("Write the Go CLI.", encoding="utf-8")
    (prompts_dir / "task-b.md").write_text("Classify the log lines.", encoding="utf-8")


# --- resolve_model -------------------------------------------------------------


def test_resolve_model_applies_endpoint_override() -> None:
    resolved = resolve_model(
        _model(), OpenCodeOverrides(endpoint="http://127.0.0.1:1234/v1/"), mode="default"
    )

    assert resolved.base_url == "http://127.0.0.1:1234/v1"


def test_resolve_model_engine_override_sets_endpoint() -> None:
    resolved = resolve_model(_model(), OpenCodeOverrides(engine="ollama"), mode="default")

    assert resolved.base_url == "http://127.0.0.1:11434/v1"
    assert resolved.engine == "ollama"


def test_resolve_model_endpoint_beats_engine() -> None:
    resolved = resolve_model(
        _model(),
        OpenCodeOverrides(endpoint="http://example.test/v1", engine="ollama"),
        mode="default",
    )

    assert resolved.base_url == "http://example.test/v1"
    # The engine label is still recorded even though the explicit endpoint wins.
    assert resolved.engine == "ollama"


def test_resolve_model_keeps_configured_base_url_without_overrides() -> None:
    resolved = resolve_model(_model(engine="dflash"), OpenCodeOverrides(), mode="default")

    # A declared engine is provenance only; it does not remap the configured URL.
    assert resolved.base_url == "http://localhost:9000/v1"
    assert resolved.engine == "dflash"


def test_resolve_model_applies_provenance_overrides() -> None:
    resolved = resolve_model(
        _model(quant="Q4", provider="bartowski"),
        OpenCodeOverrides(quant="IQ3_XXS", provider="unsloth"),
        mode="default",
    )

    assert resolved.quant == "IQ3_XXS"
    assert resolved.provider == "unsloth"


def test_resolve_model_thinking_mode_merges_thinking_extra_body() -> None:
    model = _model(
        extra_body={"reasoning": {"enabled": False}},
        thinking_extra_body={"reasoning": {"effort": "high"}},
    )

    resolved = resolve_model(model, OpenCodeOverrides(), mode="thinking")

    assert resolved.extra_body == {"reasoning": {"effort": "high"}}


def test_resolve_model_default_mode_ignores_thinking_extra_body() -> None:
    model = _model(
        extra_body={"reasoning": {"enabled": False}},
        thinking_extra_body={"reasoning": {"effort": "high"}},
    )

    resolved = resolve_model(model, OpenCodeOverrides(), mode="default")

    assert resolved.extra_body == {"reasoning": {"enabled": False}}


# --- load_prompt ---------------------------------------------------------------


def test_load_prompt_reads_file(tmp_path: Path) -> None:
    _write_prompts(tmp_path)

    path, text = load_prompt(tmp_path, "task-a")

    assert path == tmp_path / "task-a.md"
    assert text == "Write the Go CLI."


def test_shipped_prompts_exist_and_load_for_every_task() -> None:
    # Prompts are version-controlled and read from disk; guard them as a regression.
    for task in OPENCODE_TASKS:
        path, text = load_prompt("prompts", task)
        assert path == Path("prompts") / f"{task}.md"
        assert text.strip()


def test_load_prompt_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="prompt file not found"):
        load_prompt(tmp_path, "task-a")


def test_load_prompt_empty_file_raises(tmp_path: Path) -> None:
    (tmp_path / "task-a.md").write_text("   \n", encoding="utf-8")

    with pytest.raises(ConfigError, match="prompt file is empty"):
        load_prompt(tmp_path, "task-a")


# --- build_record --------------------------------------------------------------


def test_build_record_captures_metadata_and_metrics() -> None:
    from local_code_bench.metrics import CompletionMeasurement

    measurement = CompletionMeasurement(
        response="code",
        ttft_seconds=0.5,
        latency_seconds=2.0,
        prompt_tokens=10,
        completion_tokens=20,
        prefill_tokens_per_second=20.0,
        decode_tokens_per_second=13.0,
        token_counts_estimated=False,
    )
    model = _model(quant="IQ3_XXS", provider="unsloth", engine="dflash")

    record = build_record(
        task="task-a",
        model=model,
        mode="thinking",
        seed=7,
        temperature=0.0,
        prompt_file=Path("prompts/task-a.md"),
        measurement=measurement,
    )

    assert record["run_mode"] == "opencode"
    assert record["task"] == "task-a"
    assert record["model"] == "local"
    assert record["quant"] == "IQ3_XXS"
    assert record["provider"] == "unsloth"
    assert record["engine"] == "dflash"
    assert record["endpoint"] == "http://localhost:9000/v1"
    assert record["mode"] == "thinking"
    assert record["seed"] == 7
    assert record["temperature"] == 0.0
    assert record["prompt_file"] == "prompts/task-a.md"
    assert record["raw_response"] == "code"
    assert record["metrics"]["wall_clock_seconds"] == 2.0
    assert record["metrics"]["tokens_per_second"] == 10.0
    assert record["tokens"] == {"prompt": 10, "completion": 20, "estimated": False}


# --- run_opencode --------------------------------------------------------------


def test_run_opencode_invokes_both_tasks_and_writes_records(tmp_path: Path, monkeypatch) -> None:
    _write_prompts(tmp_path / "prompts")
    fake = _FakeProvider()
    monkeypatch.setattr("local_code_bench.opencode.invoke.provider_for_model", lambda _model: fake)

    messages: list[str] = []
    result_path, records = run_opencode(
        model=_model(),
        overrides=OpenCodeOverrides(),
        mode="default",
        prompts_dir=tmp_path / "prompts",
        results_dir=tmp_path / "results",
        progress=messages.append,
    )

    assert [task for task, _ in records] == list(OPENCODE_TASKS)
    assert any("task-a" in message and "invoking" in message for message in messages)
    written = read_jsonl(result_path)
    assert [r["task"] for r in written] == ["task-a", "task-b"]
    assert all(r["raw_response"] == "hello world" for r in written)
    assert all(r["tokens"]["prompt"] == 11 for r in written)


def test_run_opencode_pins_temperature_and_logs_seed(tmp_path: Path, monkeypatch) -> None:
    _write_prompts(tmp_path / "prompts")
    fake = _FakeProvider()
    monkeypatch.setattr("local_code_bench.opencode.invoke.provider_for_model", lambda _model: fake)

    result_path, _ = run_opencode(
        model=_model(),
        overrides=OpenCodeOverrides(),
        mode="default",
        prompts_dir=tmp_path / "prompts",
        results_dir=tmp_path / "results",
        seed=42,
    )

    assert all(req.temperature == 0.0 for req in fake.requests)
    written = read_jsonl(result_path)
    assert all(r["seed"] == 42 for r in written)
    assert all(r["temperature"] == 0.0 for r in written)


def test_run_opencode_sends_resolved_endpoint_and_max_tokens(tmp_path: Path, monkeypatch) -> None:
    _write_prompts(tmp_path / "prompts")
    fake = _FakeProvider()
    captured: dict[str, object] = {}

    def fake_provider_for_model(model: ModelConfig):
        captured["base_url"] = model.base_url
        return fake

    monkeypatch.setattr(
        "local_code_bench.opencode.invoke.provider_for_model", fake_provider_for_model
    )

    run_opencode(
        model=_model(),
        overrides=OpenCodeOverrides(engine="lm-studio"),
        mode="default",
        prompts_dir=tmp_path / "prompts",
        results_dir=tmp_path / "results",
        max_tokens=256,
    )

    assert captured["base_url"] == "http://127.0.0.1:1234/v1"
    assert all(req.max_tokens == 256 for req in fake.requests)
