from __future__ import annotations

from local_code_bench.leaderboard import generate_leaderboard
from local_code_bench.results import append_jsonl
from local_code_bench.config import ModelConfig, TokenPrices
from local_code_bench.metrics import StreamEvent
from local_code_bench.results import read_jsonl
from local_code_bench.sweep import padded_prompt, run_sweep, summarize_sweep


def test_generate_leaderboard_from_endpoint_and_agent_records(tmp_path) -> None:
    path = tmp_path / "run.jsonl"
    append_jsonl(
        path,
        {
            "run_mode": "endpoint",
            "model": "m1",
            "passed": True,
            "cost_usd": 0.01,
            "metrics": {"latency_seconds": 1.0, "prefill_tokens_per_second": 2.0, "decode_tokens_per_second": 3.0},
        },
    )
    append_jsonl(
        path,
        {
            "run_mode": "agent",
            "agent": "codex",
            "task_id": "HumanEval/0",
            "passed": False,
            "wall_time_seconds": 5.0,
            "sandbox_mode": "workspace-write",
        },
    )
    append_jsonl(
        path,
        {
            "run_mode": "agent",
            "agent": "codex",
            "task_id": "HumanEval/0",
            "passed": True,
            "wall_time_seconds": 7.0,
            "sandbox_mode": "workspace-write",
            "tokens": {"total": 1234, "estimated": False},
            "cost_status": "tokens_available",
        },
    )

    content = generate_leaderboard([path], tmp_path / "LEADERBOARD.md")

    assert "| m1 | 1/1 |" in content
    assert "| codex | 1/1 |" in content
    assert "1,234 tok" in content


def test_sweep_prompt_and_summary() -> None:
    prompt = padded_prompt("question", 20)
    summary = summarize_sweep(
        [
            {
                "model": "m",
                "context_tokens": 2000,
                "metrics": {"ttft_seconds": 1.0, "prefill_tokens_per_second": 100.0},
            }
        ]
    )

    assert len(prompt.split()) >= 20
    assert "| m | 2000 | 1.000 | 100.000 |" in summary


def test_run_sweep_executes_provider_and_writes_records(tmp_path, monkeypatch) -> None:
    class FakeProvider:
        def stream_chat(self, request):
            yield StreamEvent(content="ok", prompt_tokens=10, completion_tokens=1)

    monkeypatch.setattr("local_code_bench.sweep.provider_for_model", lambda _model: FakeProvider())
    model = ModelConfig(
        name="m",
        type="openai",
        base_url="http://example.test/v1",
        model_id="m",
        pinned_revision="test",
        price_per_1k_tokens=TokenPrices(input=0, output=0),
    )
    path = tmp_path / "sweep.jsonl"

    summary = run_sweep(models=[model], question="q", result_path=path, sizes=(20,))

    records = read_jsonl(path)
    assert summary == {"sweeps": 1}
    assert records[0]["run_mode"] == "sweep"
    assert records[0]["context_tokens"] == 20
