from __future__ import annotations

import subprocess
import sys
from importlib.metadata import version

import pytest

from local_code_bench.cli import _parse_context_sizes, build_parser, main
from local_code_bench.config import AgentConfig, ModelConfig, TokenPrices
from local_code_bench.metrics import StreamEvent
from local_code_bench.results import append_jsonl, read_jsonl
from local_code_bench.tasks import BenchmarkTask


def test_main_help_prints_usage(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])

    assert exc_info.value.code == 0

    output = capsys.readouterr().out
    assert "usage: bench" in output


def test_parser_accepts_canary_suite() -> None:
    args = build_parser().parse_args(["--suite", "canary"])

    assert args.suite == "canary"


def test_parser_rejects_unknown_suite() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--suite", "nope"])


def test_parser_accepts_evalplus_suites_and_timeout() -> None:
    args = build_parser().parse_args(["--suite", "humaneval-plus", "--timeout", "30"])

    assert args.suite == "humaneval-plus"
    assert args.timeout == 30.0


def test_parser_warmup_defaults_on_and_can_disable() -> None:
    assert build_parser().parse_args([]).warmup is True
    assert build_parser().parse_args(["--no-warmup"]).warmup is False


def test_parse_context_sizes_valid() -> None:
    assert _parse_context_sizes("2000, 8000,16000") == (2000, 8000, 16000)


def test_parse_context_sizes_rejects_non_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        _parse_context_sizes("2000,0")


def test_parse_context_sizes_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="invalid --context-sizes"):
        _parse_context_sizes("2000,abc")


def test_sweep_prompt_print_honors_context_sizes(capsys) -> None:
    code = main(["--mode", "sweep", "--prompt", "do x", "--context-sizes", "100,200"])

    assert code == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert [line.split("\t")[0] for line in lines] == ["100", "200"]


def test_sweep_invalid_context_sizes_errors(capsys) -> None:
    code = main(["--mode", "sweep", "--prompt", "do x", "--context-sizes", "nope"])

    assert code == 2
    assert "context-sizes" in capsys.readouterr().err


def test_bench_help_entrypoint_exits_successfully() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "local_code_bench.cli", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "usage: bench" in result.stdout


def test_main_version_matches_package_metadata(capsys) -> None:
    assert main(["--version"]) == 0

    assert capsys.readouterr().out.strip() == version("local-code-bench")


def test_agent_mode_resume_skips_completed_task(tmp_path, monkeypatch, capsys) -> None:
    result_path = tmp_path / "agent.jsonl"
    append_jsonl(result_path, {"run_mode": "agent", "agent": "codex", "task_id": "suite/1"})
    task = BenchmarkTask("suite/1", "humaneval", "prompt", "assert True", "solution", "v")
    agent = AgentConfig("codex", "codex", "codex", "workspace-write", 10)

    monkeypatch.setattr("local_code_bench.cli.load_agents", lambda _path: {"codex": agent})
    monkeypatch.setattr("local_code_bench.cli.load_suite", lambda _suite, cache_dir: [task])

    def fail_run_codex_task(**_kwargs):
        raise AssertionError("resume should skip completed agent task")

    monkeypatch.setattr("local_code_bench.cli.run_codex_task", fail_run_codex_task)

    exit_code = main(
        [
            "--mode",
            "agent",
            "--agent",
            "codex",
            "--suite",
            "humaneval",
            "--run-file",
            str(result_path),
            "--resume",
        ]
    )

    assert exit_code == 0
    assert "[1/1] codex suite/1: skipped" in capsys.readouterr().out


def test_leaderboard_mode_writes_requested_output(tmp_path, capsys) -> None:
    result_path = tmp_path / "run.jsonl"
    output_path = tmp_path / "LEADERBOARD.md"
    append_jsonl(
        result_path,
        {
            "run_mode": "endpoint",
            "model": "m",
            "task_id": "suite/1",
            "passed": True,
            "metrics": {"latency_seconds": 1.0},
        },
    )

    exit_code = main(
        [
            "--mode",
            "leaderboard",
            "--input",
            str(result_path),
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    assert f"wrote {output_path}" in capsys.readouterr().out
    assert "| m | 1/1 |" in output_path.read_text(encoding="utf-8")


def test_sweep_summary_mode_reads_stored_records(tmp_path, capsys) -> None:
    result_path = tmp_path / "sweep.jsonl"
    append_jsonl(
        result_path,
        {
            "run_mode": "sweep",
            "model": "m",
            "context_tokens": 2000,
            "metrics": {"ttft_seconds": 1.25, "prefill_tokens_per_second": 50.0},
        },
    )

    exit_code = main(["--mode", "sweep", "--input", str(result_path)])

    assert exit_code == 0
    assert "| m | 2000 | 1.250 | 50.000 |" in capsys.readouterr().out


def test_rescore_mode_scores_stored_endpoint_record(tmp_path, monkeypatch, capsys) -> None:
    input_path = tmp_path / "endpoint.jsonl"
    output_path = tmp_path / "rescored.jsonl"
    append_jsonl(
        input_path,
        {
            "run_mode": "endpoint",
            "model": "m",
            "task_id": "suite/1",
            "raw_response": "def add(a, b):\n    return a + b\n",
        },
    )
    task = BenchmarkTask("suite/1", "humaneval", "prompt", "assert add(1, 2) == 3", "add", "v")
    monkeypatch.setattr("local_code_bench.cli.load_suite", lambda _suite, cache_dir: [task])

    exit_code = main(
        [
            "--mode",
            "rescore",
            "--suite",
            "humaneval",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    assert "rescored={'rescored': 1, 'missing_task': 0}" in capsys.readouterr().out
    assert read_jsonl(output_path)[0]["passed"] is True


def test_endpoint_suite_mode_passes_selection_and_resume_to_runner(
    tmp_path, monkeypatch, capsys
) -> None:
    model = ModelConfig(
        name="m",
        type="openai",
        base_url="http://example.test/v1",
        model_id="m",
        pinned_revision="test",
        price_per_1k_tokens=TokenPrices(input=0, output=0),
    )
    task = BenchmarkTask("suite/1", "humaneval", "prompt", "assert True", "solution", "v")
    run_file = tmp_path / "run.jsonl"
    captured: dict[str, object] = {}
    monkeypatch.setattr("local_code_bench.cli.load_models", lambda _path: {"m": model})
    monkeypatch.setattr("local_code_bench.cli.load_suite", lambda _suite, cache_dir: [task])

    def fake_run_endpoint_suite(**kwargs):
        captured.update(kwargs)
        return {"passed": 1, "failed": 0, "infra_failed": 0, "skipped": 0}

    monkeypatch.setattr("local_code_bench.cli.run_endpoint_suite", fake_run_endpoint_suite)

    exit_code = main(
        [
            "--suite",
            "humaneval",
            "--model",
            "m",
            "--run-file",
            str(run_file),
            "--resume",
        ]
    )

    assert exit_code == 0
    assert captured["models"] == [model]
    assert captured["tasks"] == [task]
    assert captured["result_path"] == run_file
    assert captured["resume"] is True
    assert f"suite=humaneval results={run_file}" in capsys.readouterr().out


def test_single_prompt_mode_streams_and_writes_result(tmp_path, monkeypatch, capsys) -> None:
    model = ModelConfig(
        name="m",
        type="openai",
        base_url="http://example.test/v1",
        model_id="m",
        pinned_revision="test",
        price_per_1k_tokens=TokenPrices(input=0, output=0),
    )

    class FakeProvider:
        def stream_chat(self, request):
            assert request.prompt == "hello"
            yield StreamEvent(content="world", prompt_tokens=1, completion_tokens=1)

    monkeypatch.setattr("local_code_bench.cli.load_models", lambda _path: {"m": model})
    monkeypatch.setattr("local_code_bench.cli.provider_for_model", lambda _model: FakeProvider())

    exit_code = main(["--model", "m", "--prompt", "hello", "--results-dir", str(tmp_path)])

    records = read_jsonl(next(tmp_path.glob("m-*.jsonl")))
    assert exit_code == 0
    assert records[0]["raw_response"] == "world"
    assert records[0]["tokens"] == {"prompt": 1, "completion": 1, "estimated": False}
    assert "model=m prompt_tokens=1 completion_tokens=1" in capsys.readouterr().out


def test_inferencer_dashboard_command_invokes_server(monkeypatch) -> None:
    captured: dict = {}

    def fake_serve(config, state_dir, *, host, port, progress) -> None:
        captured.update(config=config, state_dir=state_dir, host=host, port=port)

    monkeypatch.setattr(
        "local_code_bench.inferencers.dashboard.serve_dashboard", fake_serve
    )

    exit_code = main(["inferencer", "dashboard", "--port", "9000", "--config", "x.yaml"])

    assert exit_code == 0
    assert captured["port"] == 9000
    assert captured["config"] == "x.yaml"
    assert captured["host"] == "127.0.0.1"


def test_inferencer_dashboard_config_error_exits_2(monkeypatch, capsys) -> None:
    from local_code_bench.config import ConfigError

    def boom(*_args, **_kwargs) -> None:
        raise ConfigError("inferencer config not found: missing.yaml")

    monkeypatch.setattr(
        "local_code_bench.inferencers.dashboard.serve_dashboard", boom
    )

    exit_code = main(["inferencer", "dashboard"])

    assert exit_code == 2
    assert "bench: error:" in capsys.readouterr().err
