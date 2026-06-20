from __future__ import annotations

from local_code_bench.leaderboard import generate_leaderboard
from local_code_bench.results import append_jsonl
from local_code_bench.sweep import padded_prompt, summarize_sweep


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
            "passed": False,
            "wall_time_seconds": 5.0,
            "sandbox_mode": "workspace-write",
        },
    )

    content = generate_leaderboard([path], tmp_path / "LEADERBOARD.md")

    assert "| m1 | 1/1 |" in content
    assert "| codex | 0/1 |" in content


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
