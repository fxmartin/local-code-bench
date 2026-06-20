from __future__ import annotations

from local_code_bench.config import ModelConfig, TokenPrices
from local_code_bench.metrics import StreamEvent
from local_code_bench.runner import completed_pairs, run_endpoint_suite, select_models
from local_code_bench.tasks import BenchmarkTask


class FakeProvider:
    def stream_chat(self, request):
        yield StreamEvent(content="def add(a, b):\n    return a + b", prompt_tokens=3, completion_tokens=8)


def model(name: str) -> ModelConfig:
    return ModelConfig(
        name=name,
        type="openai",
        base_url="http://example.test/v1",
        model_id=name,
        pinned_revision="test",
        price_per_1k_tokens=TokenPrices(input=1.0, output=2.0),
    )


def task() -> BenchmarkTask:
    return BenchmarkTask(
        task_id="t1",
        suite="humaneval",
        prompt="make add",
        test_code="assert add(1, 2) == 3",
        entry_point="add",
        version="test",
    )


def test_select_models_include_and_skip() -> None:
    models = {"a": model("a"), "b": model("b")}

    assert [item.name for item in select_models(models, include="a,b", skip="b")] == ["a"]


def test_run_endpoint_suite_writes_records_and_resumes(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("local_code_bench.runner.provider_for_model", lambda _model: FakeProvider())
    path = tmp_path / "run.jsonl"

    summary = run_endpoint_suite(models=[model("a")], tasks=[task()], result_path=path)
    resumed = run_endpoint_suite(models=[model("a")], tasks=[task()], result_path=path, resume=True)

    assert summary["passed"] == 1
    assert resumed["skipped"] == 1
    assert completed_pairs(path) == {("a", "t1")}
