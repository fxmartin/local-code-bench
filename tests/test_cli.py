from __future__ import annotations

import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import pytest

from local_code_bench.cli import (
    _emit_power,
    _format_optional_seconds,
    _make_confirm,
    _parse_context_sizes,
    build_parser,
    main,
    run_single_prompt,
)
from local_code_bench.config import AgentConfig, ConfigError, InferencerConfig, ModelConfig, TokenPrices
from local_code_bench.power import PowerSummary
from local_code_bench.inferencers.manager import InferencerError, InferencerStatus
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


# --- unified dashboard command ----------------------------------------------


def test_unified_dashboard_command_invokes_server(monkeypatch) -> None:
    captured: dict = {}

    def fake_serve(
        config,
        state_dir,
        result_paths,
        *,
        models_path,
        results_dir,
        suites_path,
        host,
        port,
        progress,
    ) -> None:
        captured.update(
            config=config,
            state_dir=state_dir,
            result_paths=result_paths,
            models_path=models_path,
            results_dir=results_dir,
            suites_path=suites_path,
            host=host,
            port=port,
        )

    monkeypatch.setattr("local_code_bench.unified_dashboard.serve_dashboard", fake_serve)

    exit_code = main(
        [
            "dashboard", "--port", "9001", "--config", "x.yaml",
            "--models", "custom/models.yaml", "--input", "results/a.jsonl",
        ]
    )

    assert exit_code == 0
    assert captured["port"] == 9001
    assert captured["config"] == "x.yaml"
    assert captured["models_path"] == "custom/models.yaml"
    assert captured["host"] == "127.0.0.1"
    assert captured["result_paths"] == [Path("results/a.jsonl")]
    # the Run launcher's suite catalog falls back to the default registry; the live
    # monitor + auto-refresh read the results dir
    assert captured["suites_path"] == "configs/suites.yaml"
    assert captured["results_dir"] == "results"


def test_unified_dashboard_discovers_results_dir(monkeypatch, tmp_path) -> None:
    (tmp_path / "run-a.jsonl").write_text("{}\n")
    (tmp_path / "run-b.jsonl").write_text("{}\n")
    (tmp_path / "notes.txt").write_text("ignore me")
    captured: dict = {}

    def fake_serve(
        config, state_dir, result_paths, *, models_path, results_dir, suites_path, host, port, progress
    ) -> None:
        captured["result_paths"] = result_paths

    monkeypatch.setattr("local_code_bench.unified_dashboard.serve_dashboard", fake_serve)

    exit_code = main(["dashboard", "--results-dir", str(tmp_path)])

    assert exit_code == 0
    assert captured["result_paths"] == [tmp_path / "run-a.jsonl", tmp_path / "run-b.jsonl"]


def test_unified_dashboard_missing_results_dir_yields_no_inputs(monkeypatch, tmp_path) -> None:
    captured: dict = {}

    def fake_serve(
        config, state_dir, result_paths, *, models_path, results_dir, suites_path, host, port, progress
    ) -> None:
        captured["result_paths"] = result_paths

    monkeypatch.setattr("local_code_bench.unified_dashboard.serve_dashboard", fake_serve)

    exit_code = main(["dashboard", "--results-dir", str(tmp_path / "nope")])

    assert exit_code == 0
    assert captured["result_paths"] == []


def test_unified_dashboard_config_error_exits_2(monkeypatch, capsys) -> None:
    from local_code_bench.config import ConfigError

    def boom(*_args, **_kwargs) -> None:
        raise ConfigError("inferencer config not found: missing.yaml")

    monkeypatch.setattr("local_code_bench.unified_dashboard.serve_dashboard", boom)

    exit_code = main(["dashboard"])

    assert exit_code == 2
    assert "bench: error:" in capsys.readouterr().err


# --- dashboard mode ----------------------------------------------------------


def test_parser_accepts_dashboard_mode() -> None:
    args = build_parser().parse_args(["--mode", "dashboard"])

    assert args.mode == "dashboard"
    assert args.serve is False


def test_dashboard_mode_generates_static_html(tmp_path, capsys) -> None:
    input_path = tmp_path / "endpoint.jsonl"
    append_jsonl(
        input_path,
        {
            "run_mode": "endpoint",
            "model": "m",
            "suite": "humaneval",
            "task_id": "suite/1",
            "passed": True,
            "metrics": {"latency_seconds": 1.0},
        },
    )
    output_path = tmp_path / "dashboard.html"

    exit_code = main(
        ["--mode", "dashboard", "--input", str(input_path), "--output", str(output_path)]
    )

    assert exit_code == 0
    assert output_path.exists()
    assert "<!DOCTYPE html>" in output_path.read_text(encoding="utf-8")
    assert f"wrote {output_path}" in capsys.readouterr().out


def test_dashboard_mode_defaults_output_path(tmp_path, monkeypatch, capsys) -> None:
    input_path = tmp_path / "endpoint.jsonl"
    append_jsonl(input_path, {"run_mode": "endpoint", "model": "m", "task_id": "t", "passed": True})
    captured: dict = {}

    def fake_generate(paths, output):
        captured.update(paths=paths, output=output)
        return "<html></html>"

    monkeypatch.setattr("local_code_bench.dashboard.generate_dashboard", fake_generate)

    exit_code = main(["--mode", "dashboard", "--input", str(input_path)])

    assert exit_code == 0
    assert captured["output"] == Path("results/dashboard.html")
    assert captured["paths"] == [input_path]


def test_dashboard_mode_serve_starts_server(tmp_path, monkeypatch, capsys) -> None:
    input_path = tmp_path / "endpoint.jsonl"
    captured: dict = {}

    def fake_serve(paths, *, host, port, progress):
        captured.update(paths=paths, host=host, port=port)
        progress(f"results dashboard on http://{host}:{port}")

    monkeypatch.setattr("local_code_bench.dashboard_server.serve_dashboard", fake_serve)

    exit_code = main(
        ["--mode", "dashboard", "--input", str(input_path), "--serve", "--port", "9123"]
    )

    assert exit_code == 0
    assert captured["paths"] == [input_path]
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 9123
    assert "http://127.0.0.1:9123" in capsys.readouterr().out


def test_dashboard_mode_without_input_errors(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--mode", "dashboard"])

    assert exc.value.code == 2
    assert "requires --input" in capsys.readouterr().err


def test_dashboard_mode_serve_honors_custom_host(tmp_path, monkeypatch, capsys) -> None:
    input_path = tmp_path / "endpoint.jsonl"
    captured: dict = {}

    def fake_serve(paths, *, host, port, progress):
        captured.update(host=host, port=port)
        progress(f"results dashboard on http://{host}:{port}")

    monkeypatch.setattr("local_code_bench.dashboard_server.serve_dashboard", fake_serve)

    exit_code = main(
        ["--mode", "dashboard", "--input", str(input_path), "--serve", "--host", "0.0.0.0"]
    )

    assert exit_code == 0
    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 8770
    assert "http://0.0.0.0:8770" in capsys.readouterr().out


def test_dashboard_mode_passes_multiple_inputs(tmp_path, monkeypatch) -> None:
    first = tmp_path / "endpoint.jsonl"
    second = tmp_path / "agent.jsonl"
    captured: dict = {}

    def fake_generate(paths, output):
        captured.update(paths=paths, output=output)
        return "<html></html>"

    monkeypatch.setattr("local_code_bench.dashboard.generate_dashboard", fake_generate)

    exit_code = main(
        ["--mode", "dashboard", "--input", str(first), str(second)]
    )

    assert exit_code == 0
    assert captured["paths"] == [first, second]


# --- argument validation -----------------------------------------------------


def test_no_args_prints_help_and_returns_zero(capsys) -> None:
    exit_code = main([])

    assert exit_code == 0
    assert "usage" in capsys.readouterr().out.lower()


def test_leaderboard_mode_without_input_errors(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--mode", "leaderboard"])

    assert exc.value.code == 2
    assert "requires --input" in capsys.readouterr().err


def test_rescore_mode_without_input_errors(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--mode", "rescore"])

    assert exc.value.code == 2
    assert "requires --input and --suite" in capsys.readouterr().err


def test_agent_mode_without_agent_errors(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--mode", "agent"])

    assert exc.value.code == 2
    assert "requires --agent and --suite" in capsys.readouterr().err


def test_model_without_prompt_errors(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--model", "m"])

    assert exc.value.code == 2
    assert "must be provided together" in capsys.readouterr().err


def test_agent_mode_unknown_agent_errors_exit_2(monkeypatch, capsys) -> None:
    agent = AgentConfig("codex", "codex", "codex", "workspace-write", 10)
    monkeypatch.setattr("local_code_bench.cli.load_agents", lambda _path: {"codex": agent})

    exit_code = main(["--mode", "agent", "--agent", "ghost", "--suite", "canary"])

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "unknown agent 'ghost'" in err
    assert "codex" in err


# --- bench inferencer subcommands -------------------------------------------


def _server_cfg(name: str = "dflash", port: int = 8000) -> InferencerConfig:
    return InferencerConfig(
        name=name,
        lifecycle="server",
        detect_kind="binary",
        detect_target=name,
        port=port,
        health_url="http://127.0.0.1:{port}/v1/models",
        start=(name, "serve"),
    )


def _app_cfg(name: str = "lm-studio", port: int = 1234) -> InferencerConfig:
    return InferencerConfig(
        name=name,
        lifecycle="app",
        detect_kind="app",
        detect_target="LM Studio.app",
        port=port,
        health_url="http://127.0.0.1:{port}/v1/models",
    )


def _status(name: str, **over) -> InferencerStatus:
    base = dict(
        name=name,
        installed=True,
        lifecycle="server",
        running=False,
        pid=None,
        port=8000,
        healthy=False,
        detail="not running",
    )
    base.update(over)
    return InferencerStatus(**base)  # type: ignore[arg-type]


def test_inferencer_list_prints_install_lifecycle_and_port(monkeypatch, capsys) -> None:
    configs = {"dflash": _server_cfg("dflash", 8000), "lm-studio": _app_cfg("lm-studio", 1234)}
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _path: configs)
    monkeypatch.setattr(
        "local_code_bench.inferencers.detect.is_installed",
        lambda cfg: cfg.name == "dflash",
    )

    exit_code = main(["inferencer", "list"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "dflash" in out and "server" in out and "8000" in out
    assert "lm-studio" in out and "app" in out and "1234" in out


def test_inferencer_status_prints_table(monkeypatch, capsys) -> None:
    configs = {"dflash": _server_cfg("dflash", 8000)}
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _path: configs)
    monkeypatch.setattr(
        "local_code_bench.cli.manager.status_all",
        lambda cfgs, sd: {"dflash": _status("dflash", running=True, healthy=True, pid=4321)},
    )

    exit_code = main(["inferencer", "status"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "dflash" in out
    assert "4321" in out  # pid shown


def test_inferencer_start_yes_auto_confirms_and_starts(monkeypatch, capsys) -> None:
    configs = {"dflash": _server_cfg("dflash", 8000)}
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _path: configs)
    captured: dict = {}

    def fake_start_exclusive(cfg, cfgs, state_dir, *, confirm, force, progress=None):
        captured["name"] = cfg.name
        captured["force"] = force
        captured["confirm"] = confirm([_status("turboquant", running=True)])
        return _status("dflash", running=True, healthy=True, pid=4321)

    monkeypatch.setattr("local_code_bench.cli.manager.start_exclusive", fake_start_exclusive)

    exit_code = main(["inferencer", "start", "dflash", "--yes"])

    assert exit_code == 0
    assert captured["name"] == "dflash"
    assert captured["confirm"] is True  # --yes auto-confirms
    assert captured["force"] is False
    assert "started dflash" in capsys.readouterr().out


def test_inferencer_start_non_tty_defaults_to_no(monkeypatch) -> None:
    confirm = _make_confirm(assume_yes=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    assert confirm([_status("turboquant", running=True)]) is False


def test_inferencer_start_force_flag_passed_through(monkeypatch) -> None:
    configs = {"dflash": _server_cfg("dflash", 8000)}
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _path: configs)
    captured: dict = {}

    def fake_start_exclusive(cfg, cfgs, state_dir, *, confirm, force, progress=None):
        captured["force"] = force
        return _status("dflash", running=True, healthy=True)

    monkeypatch.setattr("local_code_bench.cli.manager.start_exclusive", fake_start_exclusive)

    exit_code = main(["inferencer", "start", "dflash", "--yes", "--force"])

    assert exit_code == 0
    assert captured["force"] is True


def test_inferencer_start_unknown_name_errors_exit_2(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "local_code_bench.cli.load_inferencers", lambda _path: {"dflash": _server_cfg()}
    )

    exit_code = main(["inferencer", "start", "nope", "--yes"])

    assert exit_code == 2
    assert "bench: error:" in capsys.readouterr().err


def test_inferencer_start_lifecycle_failure_exit_2(monkeypatch, capsys) -> None:
    configs = {"dflash": _server_cfg("dflash", 8000)}
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _path: configs)

    def boom(*_a, **_k):
        raise InferencerError("dflash did not become healthy")

    monkeypatch.setattr("local_code_bench.cli.manager.start_exclusive", boom)

    exit_code = main(["inferencer", "start", "dflash", "--yes"])

    assert exit_code == 2
    assert "bench: error: dflash did not become healthy" in capsys.readouterr().err


def test_inferencer_stop_is_idempotent(monkeypatch, capsys) -> None:
    configs = {"dflash": _server_cfg("dflash", 8000)}
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _path: configs)
    calls: list = []
    monkeypatch.setattr(
        "local_code_bench.cli.manager.stop", lambda cfg, sd, **k: calls.append(cfg.name)
    )

    exit_code = main(["inferencer", "stop", "dflash"])

    assert exit_code == 0
    assert calls == ["dflash"]
    assert "stopped dflash" in capsys.readouterr().out


def test_inferencer_status_watch_clears_and_rerenders(monkeypatch, capsys) -> None:
    configs = {"dflash": _server_cfg("dflash", 8000)}
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _path: configs)
    monkeypatch.setattr(
        "local_code_bench.cli.manager.status_all",
        lambda cfgs, sd: {"dflash": _status("dflash", running=True, healthy=True, pid=4321)},
    )

    def stop_after_first(_interval):
        raise KeyboardInterrupt

    monkeypatch.setattr("local_code_bench.cli.time.sleep", stop_after_first)

    exit_code = main(["inferencer", "status", "--watch", "--interval", "0"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "\033[2J" in out  # ANSI clear-screen
    assert "dflash" in out


def test_inferencer_start_interactive_confirm_reads_stdin(monkeypatch) -> None:
    confirm = _make_confirm(assume_yes=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    prompts: list[str] = []

    def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        return " Yes "

    monkeypatch.setattr("builtins.input", fake_input)

    assert confirm([_status("turboquant", running=True)]) is True
    assert "turboquant" in prompts[0]


def test_inferencer_start_interactive_confirm_declines_on_blank(monkeypatch) -> None:
    confirm = _make_confirm(assume_yes=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "")

    assert confirm([_status("turboquant", running=True)]) is False


def test_inferencer_start_emits_manager_progress(monkeypatch, capsys) -> None:
    configs = {"dflash": _server_cfg("dflash", 8000)}
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _path: configs)

    def fake_start_exclusive(cfg, cfgs, state_dir, *, confirm, force, progress):
        progress("waiting for dflash to become healthy")
        return _status("dflash", running=True, healthy=True, pid=4321)

    monkeypatch.setattr("local_code_bench.cli.manager.start_exclusive", fake_start_exclusive)

    exit_code = main(["inferencer", "start", "dflash", "--yes"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "waiting for dflash to become healthy" in out
    assert "started dflash" in out


def test_inferencer_start_without_name_errors_exit_2(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "local_code_bench.cli.load_inferencers", lambda _path: {"dflash": _server_cfg()}
    )

    exit_code = main(["inferencer", "start"])

    assert exit_code == 2
    assert "bench: error: inferencer start requires an engine name" in capsys.readouterr().err


def test_inferencer_stop_without_name_errors_exit_2(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "local_code_bench.cli.load_inferencers", lambda _path: {"dflash": _server_cfg()}
    )

    exit_code = main(["inferencer", "stop"])

    assert exit_code == 2
    assert "bench: error: inferencer stop requires an engine name" in capsys.readouterr().err


def test_inferencer_config_error_exit_2(monkeypatch, capsys) -> None:
    from local_code_bench.config import ConfigError

    def boom(_path):
        raise ConfigError("inferencer config not found")

    monkeypatch.setattr("local_code_bench.cli.load_inferencers", boom)

    exit_code = main(["inferencer", "list"])

    assert exit_code == 2
    assert "bench: error: inferencer config not found" in capsys.readouterr().err


# --- helper coverage ---------------------------------------------------------


def test_format_optional_seconds_handles_none_and_value() -> None:
    assert _format_optional_seconds(None) == "n/a"
    assert _format_optional_seconds(1.5) == "1.500s"


def test_run_single_prompt_unknown_model_raises_config_error(tmp_path, monkeypatch) -> None:
    model = ModelConfig(
        name="m",
        type="openai",
        base_url="http://example.test/v1",
        model_id="m",
        pinned_revision="test",
        price_per_1k_tokens=TokenPrices(input=0, output=0),
    )
    monkeypatch.setattr("local_code_bench.cli.load_models", lambda _path: {"m": model})

    with pytest.raises(ConfigError, match="unknown model 'ghost'. Available models: m"):
        run_single_prompt(
            config_path=tmp_path / "models.yaml",
            model_name="ghost",
            prompt="hi",
            results_dir=tmp_path,
        )


def test_single_prompt_mode_config_error_exits_2(monkeypatch, capsys) -> None:
    def boom(**_kwargs) -> None:
        raise ConfigError("unknown model 'ghost'. Available models: m")

    monkeypatch.setattr("local_code_bench.cli.run_single_prompt", boom)

    exit_code = main(["--model", "ghost", "--prompt", "hi"])

    assert exit_code == 2
    assert "bench: error: unknown model 'ghost'" in capsys.readouterr().err


def test_emit_power_warns_when_requested_but_no_samples(tmp_path, capsys) -> None:
    class FakeSampler:
        def result(self) -> PowerSummary:
            return PowerSummary.unavailable()

    _emit_power(
        FakeSampler(),
        tmp_path / "run.jsonl",
        models=[],
        requested=True,
    )

    err = capsys.readouterr().err
    assert "power: powermetrics produced no samples" in err
    assert not (tmp_path / "run.jsonl").exists()
