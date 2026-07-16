from __future__ import annotations

import json

import pytest

from local_code_bench.config import ModelConfig, TokenPrices
from local_code_bench.engine_provenance import EngineProvenance
from local_code_bench.metrics import StreamEvent
from local_code_bench.provider import ProviderError
from local_code_bench.results import read_jsonl
from local_code_bench.runner import completed_pairs, run_endpoint_suite, select_models
from local_code_bench.tasks import BenchmarkTask


class FakeProvider:
    def stream_chat(self, request):
        yield StreamEvent(content="def add(a, b):\n    return a + b", prompt_tokens=3, completion_tokens=8)


class RecordingProvider:
    """Records the max_tokens carried on each request; safe to share across threads."""

    def __init__(self) -> None:
        self.max_tokens: list[int | None] = []

    def stream_chat(self, request):
        self.max_tokens.append(request.max_tokens)
        yield StreamEvent(content="def add(a, b):\n    return a + b", prompt_tokens=3, completion_tokens=8)


class FailingProvider:
    def stream_chat(self, request):
        raise ProviderError("stream down")
        yield


def model(
    name: str,
    *,
    concurrency: int = 1,
    max_tokens: int | None = None,
    inferencer: str | None = None,
) -> ModelConfig:
    return ModelConfig(
        name=name,
        type="openai",
        base_url="http://example.test/v1",
        model_id=name,
        pinned_revision="test",
        price_per_1k_tokens=TokenPrices(input=1.0, output=2.0),
        concurrency=concurrency,
        max_tokens=max_tokens,
        inferencer=inferencer,
    )


def task(task_id: str = "t1") -> BenchmarkTask:
    return BenchmarkTask(
        task_id=task_id,
        suite="humaneval",
        prompt="make add",
        test_code="assert add(1, 2) == 3",
        entry_point="add",
        version="test",
    )


def test_select_models_include_and_skip() -> None:
    models = {"a": model("a"), "b": model("b")}

    assert [item.name for item in select_models(models, include="a,b", skip="b")] == ["a"]


def test_select_models_reports_unknown_include() -> None:
    models = {"a": model("a")}

    try:
        select_models(models, include="missing")
    except ValueError as exc:
        assert "unknown model(s): missing" in str(exc)
    else:
        raise AssertionError("expected unknown model error")


def test_run_endpoint_suite_writes_records_and_resumes(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("local_code_bench.runner.provider_for_model", lambda _model: FakeProvider())
    path = tmp_path / "run.jsonl"
    messages: list[str] = []

    summary = run_endpoint_suite(
        models=[model("a")],
        tasks=[task()],
        result_path=path,
        progress=messages.append,
    )
    resumed = run_endpoint_suite(
        models=[model("a")],
        tasks=[task()],
        result_path=path,
        resume=True,
        progress=messages.append,
    )

    assert summary["passed"] == 1
    assert resumed["skipped"] == 1
    assert completed_pairs(path) == {("a", "t1")}
    assert messages == ["[1/1] a t1: passed", "[1/1] a t1: skipped"]


def test_run_endpoint_suite_records_provider_init_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "local_code_bench.runner.provider_for_model",
        lambda _model: (_ for _ in ()).throw(ProviderError("server down")),
    )
    path = tmp_path / "run.jsonl"
    messages: list[str] = []

    summary = run_endpoint_suite(
        models=[model("a")],
        tasks=[task()],
        result_path=path,
        progress=messages.append,
    )

    assert summary["infra_failed"] == 1
    assert "server down" in path.read_text(encoding="utf-8")
    assert messages == ["[1/1] a t1: infra-failed"]


def test_run_endpoint_suite_records_provider_stream_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("local_code_bench.runner.provider_for_model", lambda _model: FailingProvider())
    path = tmp_path / "run.jsonl"
    messages: list[str] = []

    summary = run_endpoint_suite(
        models=[model("a")],
        tasks=[task()],
        result_path=path,
        progress=messages.append,
    )

    assert summary["infra_failed"] == 1
    assert "stream down" in path.read_text(encoding="utf-8")
    assert messages == ["[1/1] a t1: infra-failed"]


def test_run_endpoint_suite_records_scoring_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("local_code_bench.runner.provider_for_model", lambda _model: FakeProvider())
    monkeypatch.setattr(
        "local_code_bench.runner.score_completion",
        lambda _task, _completion: (_ for _ in ()).throw(RuntimeError("bad tests")),
    )
    path = tmp_path / "run.jsonl"

    summary = run_endpoint_suite(models=[model("a")], tasks=[task()], result_path=path)

    assert summary["failed"] == 1
    assert "scoring failed: bad tests" in path.read_text(encoding="utf-8")


def test_run_endpoint_suite_concurrency_writes_one_record_per_task(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("local_code_bench.runner.provider_for_model", lambda _model: FakeProvider())
    path = tmp_path / "run.jsonl"
    tasks = [task(f"t{n}") for n in range(5)]

    summary = run_endpoint_suite(
        models=[model("a", concurrency=4)],
        tasks=tasks,
        result_path=path,
    )

    assert summary["passed"] == 5
    assert completed_pairs(path) == {("a", f"t{n}") for n in range(5)}


def test_run_endpoint_suite_passes_max_tokens_to_request(tmp_path, monkeypatch) -> None:
    provider = RecordingProvider()
    monkeypatch.setattr("local_code_bench.runner.provider_for_model", lambda _model: provider)
    path = tmp_path / "run.jsonl"
    tasks = [task(f"t{n}") for n in range(3)]

    run_endpoint_suite(
        models=[model("a", concurrency=3)],
        tasks=tasks,
        result_path=path,
        max_tokens=512,
    )

    assert provider.max_tokens == [512, 512, 512]


def test_run_endpoint_suite_defaults_max_tokens_cap(tmp_path, monkeypatch) -> None:
    provider = RecordingProvider()
    monkeypatch.setattr("local_code_bench.runner.provider_for_model", lambda _model: provider)
    path = tmp_path / "run.jsonl"

    run_endpoint_suite(models=[model("a")], tasks=[task()], result_path=path)

    # No model or CLI cap set, so the runner applies its default endpoint cap.
    assert provider.max_tokens == [1024]


def test_run_endpoint_suite_warmup_sends_discarded_request(tmp_path, monkeypatch) -> None:
    provider = RecordingProvider()
    monkeypatch.setattr("local_code_bench.runner.provider_for_model", lambda _model: provider)
    local_model = model("local", inferencer="ollama")
    provenance = EngineProvenance(
        name="ollama",
        versions={"ollama": "0.32.0"},
        capture_method="live-api",
    )

    summary = run_endpoint_suite(
        models=[local_model],
        tasks=[task()],
        result_path=tmp_path / "run.jsonl",
        warmup=True,
        engine_provenance={"local": provenance},
    )

    # First call is the discarded warmup (max_tokens=1), then the real task.
    assert provider.max_tokens == [1, 1024]
    # The warmup is not scored: still exactly one task recorded.
    assert summary["passed"] == 1


def test_run_endpoint_suite_does_not_warm_up_cloud_model(tmp_path, monkeypatch) -> None:
    provider = RecordingProvider()
    monkeypatch.setattr("local_code_bench.runner.provider_for_model", lambda _model: provider)

    run_endpoint_suite(
        models=[model("cloud")],
        tasks=[task()],
        result_path=tmp_path / "run.jsonl",
        warmup=True,
    )

    assert provider.max_tokens == [1024]


def test_run_endpoint_suite_warms_only_local_model_in_mixed_run(tmp_path, monkeypatch) -> None:
    providers = {"cloud": RecordingProvider(), "local": RecordingProvider()}
    monkeypatch.setattr(
        "local_code_bench.runner.provider_for_model",
        lambda selected: providers[selected.name],
    )
    provenance = EngineProvenance(
        name="ollama",
        versions={"ollama": "0.32.0"},
        capture_method="live-api",
    )

    run_endpoint_suite(
        models=[model("cloud"), model("local", inferencer="ollama")],
        tasks=[task()],
        result_path=tmp_path / "run.jsonl",
        warmup=True,
        engine_provenance={"local": provenance},
    )

    assert providers["cloud"].max_tokens == [1024]
    assert providers["local"].max_tokens == [1, 1024]


def test_run_endpoint_suite_no_warmup_skips_local_warmup(tmp_path, monkeypatch) -> None:
    provider = RecordingProvider()
    monkeypatch.setattr("local_code_bench.runner.provider_for_model", lambda _model: provider)
    local_model = model("local", inferencer="ollama")
    provenance = EngineProvenance(
        name="ollama",
        versions={"ollama": "0.32.0"},
        capture_method="live-api",
    )

    run_endpoint_suite(
        models=[local_model],
        tasks=[task()],
        result_path=tmp_path / "run.jsonl",
        warmup=False,
        engine_provenance={"local": provenance},
    )

    assert provider.max_tokens == [1024]


def test_run_endpoint_suite_warmup_errors_do_not_abort(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("local_code_bench.runner.provider_for_model", lambda _model: FailingProvider())
    local_model = model("local", inferencer="ollama")
    provenance = EngineProvenance(
        name="ollama",
        versions={"ollama": "0.32.0"},
        capture_method="live-api",
    )

    summary = run_endpoint_suite(
        models=[local_model],
        tasks=[task()],
        result_path=tmp_path / "run.jsonl",
        warmup=True,
        engine_provenance={"local": provenance},
    )

    # Warmup failure is swallowed; the real task still runs and is recorded.
    assert summary["infra_failed"] == 1


def test_run_endpoint_suite_passes_timeout_to_scorer(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("local_code_bench.runner.provider_for_model", lambda _model: FakeProvider())
    captured: dict[str, float | None] = {}

    class _Score:
        passed = True
        reason = "passed"

    def fake_score(_task, _completion, *, timeout_seconds=5.0):
        captured["timeout"] = timeout_seconds
        return _Score()

    monkeypatch.setattr("local_code_bench.runner.score_completion", fake_score)
    run_endpoint_suite(
        models=[model("a")],
        tasks=[task()],
        result_path=tmp_path / "run.jsonl",
        timeout_seconds=30.0,
    )

    assert captured["timeout"] == 30.0


def test_local_endpoint_suite_records_engine_on_metadata_and_task(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("local_code_bench.runner.provider_for_model", lambda _model: FakeProvider())
    local_model = model("local", inferencer="ollama")
    provenance = EngineProvenance(
        name="ollama",
        versions={"ollama": "0.32.0"},
        capture_method="live-api",
    )
    path = tmp_path / "run.jsonl"

    run_endpoint_suite(
        models=[local_model],
        tasks=[task()],
        result_path=path,
        engine_provenance={"local": provenance},
    )

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["models"]["local"]["engine"] == provenance.as_dict()
    assert records[1]["engine"] == provenance.as_dict()


def test_cloud_endpoint_suite_records_provider_on_metadata_and_task(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("local_code_bench.runner.provider_for_model", lambda _model: FakeProvider())
    cloud_model = ModelConfig(
        name="qwen",
        type="openai",
        base_url="https://openrouter.ai/api/v1",
        model_id="qwen/qwen3.6-27b",
        pinned_revision="test",
        price_per_1k_tokens=TokenPrices(input=1.0, output=2.0),
    )
    path = tmp_path / "run.jsonl"

    run_endpoint_suite(models=[cloud_model], tasks=[task()], result_path=path)

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["models"]["qwen"]["endpoint_provider"] == "openrouter.ai"
    assert records[1]["endpoint_provider"] == "openrouter.ai"


def test_cloud_endpoint_failure_records_provider(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("local_code_bench.runner.provider_for_model", lambda _model: FailingProvider())
    cloud_model = ModelConfig(
        name="qwen",
        type="openai",
        base_url="https://openrouter.ai/api/v1",
        model_id="qwen/qwen3.6-27b",
        pinned_revision="test",
        price_per_1k_tokens=TokenPrices(input=1.0, output=2.0),
    )
    path = tmp_path / "run.jsonl"

    run_endpoint_suite(models=[cloud_model], tasks=[task()], result_path=path)

    assert read_jsonl(path)[1]["endpoint_provider"] == "openrouter.ai"


def test_resume_rejects_changed_cloud_endpoint_provider(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("local_code_bench.runner.provider_for_model", lambda _model: FakeProvider())
    openrouter_model = ModelConfig(
        name="qwen",
        type="openai",
        base_url="https://openrouter.ai/api/v1",
        model_id="qwen/qwen3.6-27b",
        pinned_revision="test",
        price_per_1k_tokens=TokenPrices(input=1.0, output=2.0),
    )
    gateway_model = ModelConfig(
        name="qwen",
        type="openai",
        base_url="https://gateway.example.test/v1",
        model_id="qwen/qwen3.6-27b",
        pinned_revision="test",
        price_per_1k_tokens=TokenPrices(input=1.0, output=2.0),
    )
    path = tmp_path / "run.jsonl"
    run_endpoint_suite(models=[openrouter_model], tasks=[task()], result_path=path)
    original = path.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="endpoint provider does not match"):
        run_endpoint_suite(
            models=[gateway_model],
            tasks=[task()],
            result_path=path,
            resume=True,
        )

    assert path.read_text(encoding="utf-8") == original


def test_local_endpoint_suite_requires_engine_before_writing(tmp_path) -> None:
    path = tmp_path / "run.jsonl"

    with pytest.raises(ValueError, match="requires exact engine provenance"):
        run_endpoint_suite(
            models=[model("local", inferencer="ollama")],
            tasks=[task()],
            result_path=path,
        )

    assert not path.exists()


def test_resume_rejects_changed_engine_version_before_appending(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("local_code_bench.runner.provider_for_model", lambda _model: FakeProvider())
    local_model = model("local", inferencer="ollama")
    old = EngineProvenance(
        name="ollama",
        versions={"ollama": "0.31.0"},
        capture_method="live-api",
    )
    new = EngineProvenance(
        name="ollama",
        versions={"ollama": "0.32.0"},
        capture_method="live-api",
    )
    path = tmp_path / "run.jsonl"
    run_endpoint_suite(
        models=[local_model],
        tasks=[task()],
        result_path=path,
        engine_provenance={"local": old},
    )
    original = path.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="engine provenance does not match"):
        run_endpoint_suite(
            models=[local_model],
            tasks=[task()],
            result_path=path,
            resume=True,
            engine_provenance={"local": new},
        )

    assert path.read_text(encoding="utf-8") == original
