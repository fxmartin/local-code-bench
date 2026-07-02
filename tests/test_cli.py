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

    def fail_run_agent_task(**_kwargs):
        raise AssertionError("resume should skip completed agent task")

    monkeypatch.setattr("local_code_bench.cli.run_agent_task", fail_run_agent_task)

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


def _server_cfg(name: str = "dflash", port: int = 8000, url: str | None = None) -> InferencerConfig:
    return InferencerConfig(
        name=name,
        lifecycle="server",
        detect_kind="binary",
        detect_target=name,
        port=port,
        health_url="http://127.0.0.1:{port}/v1/models",
        start=(name, "serve"),
        url=url,
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


def test_inferencer_list_shows_url_and_manual_install_note(monkeypatch, capsys) -> None:
    configs = {
        "mtplx": _server_cfg("mtplx", 8003, url="https://github.com/youssofal/mtplx"),
    }
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _path: configs)
    monkeypatch.setattr(
        "local_code_bench.inferencers.detect.is_installed",
        lambda cfg: False,
    )

    exit_code = main(["inferencer", "list"])

    out = capsys.readouterr().out
    assert exit_code == 0
    # The reference URL is shown so an uninstalled engine points to its install page.
    assert "https://github.com/youssofal/mtplx" in out
    # The output makes clear the harness never installs engines.
    assert "manual" in out.lower() and "install" in out.lower()


def test_inferencer_list_shows_dash_when_url_missing(monkeypatch, capsys) -> None:
    # An engine without a reference URL renders "-" in the URL column rather than
    # an empty cell, while the manual-install note is still printed.
    configs = {"dflash": _server_cfg("dflash", 8000, url=None)}
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _path: configs)
    monkeypatch.setattr(
        "local_code_bench.inferencers.detect.is_installed",
        lambda cfg: False,
    )

    exit_code = main(["inferencer", "list"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "dflash" in out and "-" in out
    assert "manual" in out.lower() and "install" in out.lower()


def test_inferencer_status_shows_url(monkeypatch, capsys) -> None:
    configs = {"mtplx": _server_cfg("mtplx", 8003, url="https://github.com/youssofal/mtplx")}
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _path: configs)
    monkeypatch.setattr(
        "local_code_bench.cli.manager.status_all",
        lambda cfgs, sd: {"mtplx": _status("mtplx", port=8003)},
    )

    exit_code = main(["inferencer", "status"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "https://github.com/youssofal/mtplx" in out


def test_inferencer_status_shows_dash_when_url_missing(monkeypatch, capsys) -> None:
    # An engine whose config carries no URL shows "-" in the status URL column.
    configs = {"dflash": _server_cfg("dflash", 8000, url=None)}
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _path: configs)
    monkeypatch.setattr(
        "local_code_bench.cli.manager.status_all",
        lambda cfgs, sd: {"dflash": _status("dflash", port=8000)},
    )

    exit_code = main(["inferencer", "status"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "dflash" in out and "-" in out


def test_print_status_table_without_configs_renders_dash() -> None:
    # The configs argument is optional (backward-compatible default): when omitted,
    # every URL cell falls back to "-" and no lookup error is raised.
    from local_code_bench.cli import _print_status_table

    _print_status_table({"dflash": _status("dflash", port=8000)})


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


# --- bench inferencer models (Story 11.4-001) -------------------------------


def _stored(inferencer, name, fmt, path, size):
    from local_code_bench.inferencers.inventory import StoredModel

    return StoredModel(
        inferencer=inferencer, store_format=fmt, name=name, path=path, size_bytes=size
    )


def test_inferencer_models_lists_per_inferencer_with_format_quant_size(
    monkeypatch, capsys
) -> None:
    stored = [
        _stored(
            "dflash",
            "mlx-community/Llama-3.2-Q4_K_M",
            "hf-safetensors",
            "/cache/llama",
            1_500_000_000,
        ),
        _stored("ollama", "llama3.1:8b", "ollama", "/ollama/manifest", 4_000_000_000),
    ]
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _p: {})
    monkeypatch.setattr("local_code_bench.cli.scan_inferencers", lambda configs: stored)

    exit_code = main(["inferencer", "models"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "dflash" in out and "mlx-community/Llama-3.2-Q4_K_M" in out
    assert "hf-safetensors" in out
    assert "Q4_K_M" in out  # quant parsed from the name
    assert "ollama" in out and "llama3.1:8b" in out
    # Sizes are human-readable, not raw byte counts.
    assert "1500000000" not in out


def test_inferencer_models_empty_inventory_prints_notice(monkeypatch, capsys) -> None:
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _p: {})
    monkeypatch.setattr("local_code_bench.cli.scan_inferencers", lambda configs: [])

    exit_code = main(["inferencer", "models"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "no downloaded models" in out.lower()


def test_inferencer_models_shared_groups_engines(monkeypatch, capsys) -> None:
    # Two engines pointing at the same on-disk artifact share one logical model;
    # an engine owning a model alone is not listed in the shared view.
    stored = [
        _stored("dflash", "org/Model", "hf-safetensors", "/cache/repo", 1000),
        _stored("turboquant", "org/Model", "hf-safetensors", "/cache/repo", 1000),
        _stored("ollama", "solo:8b", "ollama", "/ollama/solo", 2000),
    ]
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _p: {})
    monkeypatch.setattr("local_code_bench.cli.scan_inferencers", lambda configs: stored)

    exit_code = main(["inferencer", "models", "--shared"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "org/Model" in out
    assert "dflash" in out and "turboquant" in out
    assert "solo:8b" not in out  # single-owner model is not shared


def test_inferencer_models_shared_none_prints_notice(monkeypatch, capsys) -> None:
    stored = [_stored("dflash", "org/Model", "hf-safetensors", "/cache/repo", 1000)]
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _p: {})
    monkeypatch.setattr("local_code_bench.cli.scan_inferencers", lambda configs: stored)

    exit_code = main(["inferencer", "models", "--shared"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "no shared models" in out.lower()


def test_inferencer_models_json_emits_inventory(monkeypatch, capsys) -> None:
    import json

    stored = [
        _stored("dflash", "org/Model-Q4_K_M", "hf-safetensors", "/cache/repo", 1234)
    ]
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _p: {})
    monkeypatch.setattr("local_code_bench.cli.scan_inferencers", lambda configs: stored)

    exit_code = main(["inferencer", "models", "--json"])

    out = capsys.readouterr().out
    assert exit_code == 0
    data = json.loads(out)
    assert isinstance(data, list)
    assert data[0]["inferencer"] == "dflash"
    assert data[0]["name"] == "org/Model-Q4_K_M"
    assert data[0]["store_format"] == "hf-safetensors"
    assert data[0]["quant"] == "Q4_K_M"
    assert data[0]["size_bytes"] == 1234


def test_inferencer_models_json_shared_emits_sharing_sets(monkeypatch, capsys) -> None:
    import json

    stored = [
        _stored("dflash", "org/Model", "hf-safetensors", "/cache/repo", 1000),
        _stored("turboquant", "org/Model", "hf-safetensors", "/cache/repo", 1000),
        _stored("ollama", "solo:8b", "ollama", "/ollama/solo", 2000),
    ]
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _p: {})
    monkeypatch.setattr("local_code_bench.cli.scan_inferencers", lambda configs: stored)

    exit_code = main(["inferencer", "models", "--shared", "--json"])

    out = capsys.readouterr().out
    assert exit_code == 0
    data = json.loads(out)
    assert isinstance(data, list)
    assert len(data) == 1  # only the shared model
    assert data[0]["name"] == "org/Model"
    assert data[0]["inferencers"] == ["dflash", "turboquant"]  # sorted, deduped
    assert data[0]["is_shared"] is True


def test_inferencer_models_scan_failure_exits_2(monkeypatch, capsys) -> None:
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _p: {})

    def boom(configs):
        raise OSError("model store unreadable")

    monkeypatch.setattr("local_code_bench.cli.scan_inferencers", boom)

    exit_code = main(["inferencer", "models"])

    assert exit_code == 2
    assert "bench: error: model store unreadable" in capsys.readouterr().err


def test_inferencer_models_config_error_exits_2(monkeypatch, capsys) -> None:
    def boom(_path):
        raise ConfigError("inferencer config not found: missing.yaml")

    monkeypatch.setattr("local_code_bench.cli.load_inferencers", boom)

    exit_code = main(["inferencer", "models", "--config", "missing.yaml"])

    assert exit_code == 2
    assert "bench: error: inferencer config not found" in capsys.readouterr().err


def test_format_size_renders_human_readable_units() -> None:
    from local_code_bench.cli import _format_size

    assert _format_size(0) == "0 B"
    assert _format_size(512) == "512 B"
    assert _format_size(1024).endswith("KiB")
    assert _format_size(5_000_000).endswith("MiB")  # mid-range unit, not B/KiB/GiB
    assert _format_size(1_500_000_000).endswith("GiB")
    # Sub-byte-boundary values still round to one decimal in their unit.
    assert _format_size(1536) == "1.5 KiB"


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


def test_parser_accepts_opencode_subcommand() -> None:
    args = build_parser().parse_args(
        [
            "opencode",
            "--model",
            "local",
            "--mode",
            "thinking",
            "--endpoint",
            "http://127.0.0.1:1234/v1",
            "--engine",
            "ollama",
            "--quant",
            "IQ3_XXS",
            "--provider",
            "unsloth",
            "--seed",
            "7",
            "--max-tokens",
            "512",
        ]
    )

    assert args.command == "opencode"
    assert args.model == "local"
    assert args.opencode_mode == "thinking"
    assert args.endpoint == "http://127.0.0.1:1234/v1"
    assert args.engine == "ollama"
    assert args.quant == "IQ3_XXS"
    assert args.provider == "unsloth"
    assert args.seed == 7
    assert args.max_tokens == 512


def test_opencode_mode_defaults_to_default() -> None:
    args = build_parser().parse_args(["opencode", "--model", "local"])

    assert args.opencode_mode == "default"
    assert args.temperature == 0.0


def test_opencode_rejects_unknown_mode() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["opencode", "--model", "local", "--mode", "nope"])


def test_opencode_requires_model(capsys) -> None:
    exit_code = main(["opencode"])

    assert exit_code == 2
    assert "opencode requires --model" in capsys.readouterr().err


def test_opencode_unknown_model_errors(monkeypatch, capsys) -> None:
    model = ModelConfig(
        name="m",
        type="openai",
        base_url="http://localhost:9000/v1",
        model_id="qwen",
        pinned_revision="abc",
        price_per_1k_tokens=TokenPrices(input=0, output=0),
    )
    monkeypatch.setattr("local_code_bench.cli.load_models", lambda _path: {"m": model})

    exit_code = main(["opencode", "--model", "ghost"])

    assert exit_code == 2
    assert "unknown model 'ghost'" in capsys.readouterr().err


def test_opencode_dispatches_to_run_opencode(monkeypatch, tmp_path, capsys) -> None:
    model = ModelConfig(
        name="local",
        type="openai",
        base_url="http://localhost:9000/v1",
        model_id="qwen",
        pinned_revision="abc",
        price_per_1k_tokens=TokenPrices(input=0, output=0),
    )
    monkeypatch.setattr("local_code_bench.cli.load_models", lambda _path: {"local": model})

    captured: dict = {}

    def fake_run_opencode(**kwargs):
        captured.update(kwargs)
        return tmp_path / "opencode-run.jsonl", [("task-a", None), ("task-b", None)]

    monkeypatch.setattr("local_code_bench.cli.run_opencode", fake_run_opencode)

    exit_code = main(
        [
            "opencode",
            "--model",
            "local",
            "--mode",
            "thinking",
            "--engine",
            "ollama",
            "--seed",
            "9",
        ]
    )

    assert exit_code == 0
    assert captured["model"] is model
    assert captured["mode"] == "thinking"
    assert captured["overrides"].engine == "ollama"
    assert captured["seed"] == 9
    assert "opencode" in capsys.readouterr().out


def test_opencode_records_scorecard_after_run(monkeypatch, tmp_path, capsys) -> None:
    from local_code_bench.metrics import CompletionMeasurement

    model = ModelConfig(
        name="local",
        type="openai",
        base_url="http://localhost:9000/v1",
        model_id="qwen",
        pinned_revision="abc",
        price_per_1k_tokens=TokenPrices(input=0, output=0),
        quant="IQ3_XXS",
        provider="unsloth",
    )
    monkeypatch.setattr("local_code_bench.cli.load_models", lambda _path: {"local": model})

    def measurement(text: str) -> CompletionMeasurement:
        return CompletionMeasurement(
            response=text,
            ttft_seconds=0.1,
            latency_seconds=1.0,
            prompt_tokens=10,
            completion_tokens=20,
            prefill_tokens_per_second=100.0,
            decode_tokens_per_second=20.0,
            token_counts_estimated=False,
        )

    # Empty responses keep Task A scoring off the Go toolchain (no code -> BUILD_FAIL)
    # and Task B unparseable (-> PARSE_FAIL), so the path is deterministic and offline.
    def fake_run_opencode(**kwargs):
        return tmp_path / "opencode-run.jsonl", [
            ("task-a", measurement("")),
            ("task-b", measurement("")),
        ]

    monkeypatch.setattr("local_code_bench.cli.run_opencode", fake_run_opencode)

    exit_code = main(
        [
            "--results-dir",
            str(tmp_path),
            "opencode",
            "--model",
            "local",
        ]
    )

    assert exit_code == 0
    csv_path = tmp_path / "scorecard.csv"
    md_path = tmp_path / "scorecard.md"
    assert csv_path.exists()
    assert md_path.exists()
    csv_text = csv_path.read_text(encoding="utf-8")
    assert "local" in csv_text
    assert "IQ3_XXS" in csv_text and "unsloth" in csv_text
    assert "BUILD_FAIL" in csv_text and "PARSE_FAIL" in csv_text
    assert "Provenance note" in md_path.read_text(encoding="utf-8")
    assert "scorecard=" in capsys.readouterr().out


# --- Story 10.5-001: sweep, repeat/variance, engine version ----------------


def _opencode_model(name: str, **overrides: object) -> ModelConfig:
    base = {
        "name": name,
        "type": "openai",
        "base_url": "http://localhost:9000/v1",
        "model_id": "qwen",
        "pinned_revision": "abc",
        "price_per_1k_tokens": TokenPrices(input=0, output=0),
    }
    base.update(overrides)
    return ModelConfig(**base)  # type: ignore[arg-type]


def _scoreable_run(monkeypatch, tmp_path):
    """Stub run_opencode + engine version so scoring is deterministic and offline.

    Empty task responses score BUILD_FAIL / PARSE_FAIL, exercising the full
    score-and-append path without the Go toolchain or a live model.
    """

    from local_code_bench.metrics import CompletionMeasurement

    def measurement() -> CompletionMeasurement:
        return CompletionMeasurement(
            response="",
            ttft_seconds=0.1,
            latency_seconds=1.0,
            prompt_tokens=10,
            completion_tokens=20,
            prefill_tokens_per_second=100.0,
            decode_tokens_per_second=20.0,
            token_counts_estimated=False,
        )

    def fake_run_opencode(**kwargs):
        return tmp_path / "opencode-run.jsonl", [
            ("task-a", measurement()),
            ("task-b", measurement()),
        ]

    monkeypatch.setattr("local_code_bench.cli.run_opencode", fake_run_opencode)
    monkeypatch.setattr(
        "local_code_bench.cli.capture_engine_version", lambda _engine, _url: "ollama 0.5.7"
    )


def test_parser_accepts_opencode_sweep_and_repeat() -> None:
    args = build_parser().parse_args(
        ["opencode", "--sweep", "models.txt", "--repeat", "3"]
    )

    assert args.sweep == "models.txt"
    assert args.repeat == 3


def test_opencode_repeat_default_is_one() -> None:
    args = build_parser().parse_args(["opencode", "--model", "local"])

    assert args.repeat == 1
    assert args.sweep is None


def test_opencode_rejects_non_positive_repeat(monkeypatch, capsys) -> None:
    monkeypatch.setattr("local_code_bench.cli.load_models", lambda _path: {})

    exit_code = main(["opencode", "--model", "local", "--repeat", "0"])

    assert exit_code == 2
    assert "--repeat must be a positive integer" in capsys.readouterr().err


def test_opencode_sweep_consolidates_models_into_one_scorecard(
    monkeypatch, tmp_path, capsys
) -> None:
    models = {"alpha": _opencode_model("alpha"), "beta": _opencode_model("beta")}
    monkeypatch.setattr("local_code_bench.cli.load_models", lambda _path: models)
    _scoreable_run(monkeypatch, tmp_path)

    sweep_file = tmp_path / "models.txt"
    sweep_file.write_text("alpha\nbeta\n", encoding="utf-8")

    exit_code = main(
        ["--results-dir", str(tmp_path), "opencode", "--sweep", str(sweep_file)]
    )

    assert exit_code == 0
    from local_code_bench.opencode.scorecard import read_runs

    rows = read_runs(tmp_path / "scorecard.csv")
    assert [row.model for row in rows] == ["alpha", "beta"]
    # Engine version is captured per row.
    assert all(row.engine_version == "ollama 0.5.7" for row in rows)
    assert "scorecard=" in capsys.readouterr().out


def test_opencode_sweep_unknown_model_errors(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setattr(
        "local_code_bench.cli.load_models", lambda _path: {"alpha": _opencode_model("alpha")}
    )
    _scoreable_run(monkeypatch, tmp_path)
    sweep_file = tmp_path / "models.txt"
    sweep_file.write_text("alpha\nghost\n", encoding="utf-8")

    exit_code = main(
        ["--results-dir", str(tmp_path), "opencode", "--sweep", str(sweep_file)]
    )

    assert exit_code == 2
    assert "unknown model 'ghost'" in capsys.readouterr().err


def test_opencode_repeat_runs_n_times_and_reports_variance(
    monkeypatch, tmp_path, capsys
) -> None:
    monkeypatch.setattr(
        "local_code_bench.cli.load_models", lambda _path: {"local": _opencode_model("local")}
    )
    _scoreable_run(monkeypatch, tmp_path)

    exit_code = main(
        ["--results-dir", str(tmp_path), "opencode", "--model", "local", "--repeat", "3"]
    )

    assert exit_code == 0
    from local_code_bench.opencode.scorecard import read_runs

    rows = read_runs(tmp_path / "scorecard.csv")
    assert len(rows) == 3
    md = (tmp_path / "scorecard.md").read_text(encoding="utf-8")
    assert "## Variance" in md
    assert "3 runs" in md
    out = capsys.readouterr().out
    assert "run=1/3" in out and "run=3/3" in out


# --- bench inferencer tier inventory + move commands (Story 12.6-001) --------


def _local_model(inferencer, name, fmt, path, size, *, identity=None):
    from local_code_bench.inferencers.inventory import LocalModel

    return LocalModel(
        inferencer=inferencer,
        store_format=fmt,
        name=name,
        path=path,
        size_bytes=size,
        quant=None,
        provider=None,
        identity=identity or path,
        tier="local",
    )


def _ext_model(inferencer, name, fmt, path, size, *, identity=None):
    from local_code_bench.inferencers.inventory import LocalModel

    return LocalModel(
        inferencer=inferencer,
        store_format=fmt,
        name=name,
        path=path,
        size_bytes=size,
        quant=None,
        provider=None,
        identity=identity or path,
        tier="external",
    )


def _ext_cfg(root="/Volumes/ext"):
    from local_code_bench.config import ExternalRepoConfig

    return ExternalRepoConfig(root=root)


def _mounted(is_mounted):
    from local_code_bench.inferencers.external import (
        ExternalRepoStatus,
        TierAvailability,
    )

    return ExternalRepoStatus(
        availability=TierAvailability.MOUNTED if is_mounted else TierAvailability.OFFLINE,
        root=Path("/Volumes/ext"),
        marker=Path("/Volumes/ext/.marker"),
    )


def test_models_listing_gains_tier_column_local_and_external(monkeypatch, capsys) -> None:
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _p: {})
    monkeypatch.setattr(
        "local_code_bench.cli.scan_inferencers",
        lambda configs: [],
    )
    monkeypatch.setattr(
        "local_code_bench.cli.normalize_all",
        lambda stored: [_local_model("dflash", "llama-local", "gguf", "/cache/llama", 1000)],
    )
    monkeypatch.setattr("local_code_bench.cli.load_external_repo", lambda _p: _ext_cfg())
    monkeypatch.setattr("local_code_bench.cli.check_availability", lambda cfg, **k: _mounted(True))
    monkeypatch.setattr(
        "local_code_bench.cli.scan_external_tier",
        lambda cfg, infs, **k: [_ext_model("dflash", "qwen-ext", "gguf", "/ext/qwen", 2000)],
    )

    exit_code = main(["inferencer", "models"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "TIER" in out
    assert "llama-local" in out and "local" in out
    assert "qwen-ext" in out and "external" in out


def test_models_external_offline_renders_external_offline(monkeypatch, capsys) -> None:
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _p: {})
    monkeypatch.setattr("local_code_bench.cli.scan_inferencers", lambda configs: [])
    monkeypatch.setattr("local_code_bench.cli.normalize_all", lambda stored: [])
    monkeypatch.setattr("local_code_bench.cli.load_external_repo", lambda _p: _ext_cfg())
    monkeypatch.setattr("local_code_bench.cli.check_availability", lambda cfg, **k: _mounted(False))
    monkeypatch.setattr(
        "local_code_bench.cli.read_external_catalog",
        lambda state_dir: [_ext_model("dflash", "qwen-cached", "gguf", "/ext/qwen", 2000)],
    )

    exit_code = main(["inferencer", "models"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "qwen-cached" in out
    assert "external-offline" in out


def test_models_tier_filter_keeps_only_requested_tier(monkeypatch, capsys) -> None:
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _p: {})
    monkeypatch.setattr("local_code_bench.cli.scan_inferencers", lambda configs: [])
    monkeypatch.setattr(
        "local_code_bench.cli.normalize_all",
        lambda stored: [_local_model("dflash", "llama-local", "gguf", "/cache/llama", 1000)],
    )
    monkeypatch.setattr("local_code_bench.cli.load_external_repo", lambda _p: _ext_cfg())
    monkeypatch.setattr("local_code_bench.cli.check_availability", lambda cfg, **k: _mounted(True))
    monkeypatch.setattr(
        "local_code_bench.cli.scan_external_tier",
        lambda cfg, infs, **k: [_ext_model("dflash", "qwen-ext", "gguf", "/ext/qwen", 2000)],
    )

    exit_code = main(["inferencer", "models", "--tier", "external"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "qwen-ext" in out
    assert "llama-local" not in out


def test_models_json_includes_tier_field(monkeypatch, capsys) -> None:
    import json

    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _p: {})
    monkeypatch.setattr("local_code_bench.cli.scan_inferencers", lambda configs: [])
    monkeypatch.setattr(
        "local_code_bench.cli.normalize_all",
        lambda stored: [_local_model("dflash", "llama-local", "gguf", "/cache/llama", 1000)],
    )
    monkeypatch.setattr("local_code_bench.cli.load_external_repo", lambda _p: None)

    exit_code = main(["inferencer", "models", "--json"])

    out = capsys.readouterr().out
    assert exit_code == 0
    data = json.loads(out)
    assert data[0]["tier"] == "local"


def test_promote_success_prints_summary(monkeypatch, capsys) -> None:
    from local_code_bench.inferencers.tiering import PromotePlan, PromoteResult

    cfg = InferencerConfig(
        name="dflash",
        lifecycle="server",
        detect_kind="binary",
        detect_target="dflash",
        port=1,
        health_url="http://127.0.0.1:{port}/h",
        model_store=("~/models",),
        store_format="gguf",
    )
    ext = _ext_model("dflash", "qwen", "gguf", "/ext/qwen.gguf", 2000)
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _p: {"dflash": cfg})
    monkeypatch.setattr("local_code_bench.cli.load_external_repo", lambda _p: _ext_cfg())
    monkeypatch.setattr("local_code_bench.cli.check_availability", lambda c, **k: _mounted(True))
    monkeypatch.setattr("local_code_bench.cli.scan_external_tier", lambda c, infs, **k: [ext])

    plan = PromotePlan(
        name="qwen",
        store_format="gguf",
        source=Path("/ext/qwen.gguf"),
        destination=Path("/local/qwen.gguf"),
        size_bytes=2000,
    )

    def fake_promote(model, inferencer, external_cfg, configs, state_dir, **k):
        return PromoteResult(plan=plan, destination=Path("/local/qwen.gguf"), bytes_copied=2000, verified=True)

    monkeypatch.setattr("local_code_bench.cli.promote_model", fake_promote)

    exit_code = main(["inferencer", "promote", "qwen"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "promoted" in out.lower()
    assert "qwen" in out
    assert "local" in out


def test_promote_offline_exits_2(monkeypatch, capsys) -> None:
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _p: {})
    monkeypatch.setattr("local_code_bench.cli.load_external_repo", lambda _p: _ext_cfg())
    monkeypatch.setattr("local_code_bench.cli.check_availability", lambda c, **k: _mounted(False))

    exit_code = main(["inferencer", "promote", "qwen"])

    err = capsys.readouterr().err
    assert exit_code == 2
    assert err.startswith("bench: error:")
    assert "offline" in err


def test_promote_no_external_repo_exits_2(monkeypatch, capsys) -> None:
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _p: {})
    monkeypatch.setattr("local_code_bench.cli.load_external_repo", lambda _p: None)

    exit_code = main(["inferencer", "promote", "qwen"])

    err = capsys.readouterr().err
    assert exit_code == 2
    assert err.startswith("bench: error:")


def test_promote_missing_name_exits_2(monkeypatch, capsys) -> None:
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _p: {})
    monkeypatch.setattr("local_code_bench.cli.load_external_repo", lambda _p: _ext_cfg())

    exit_code = main(["inferencer", "promote"])

    err = capsys.readouterr().err
    assert exit_code == 2
    assert err.startswith("bench: error:")


def test_demote_success_prints_summary(monkeypatch, capsys) -> None:
    from local_code_bench.inferencers.tiering import DemotePlan, DemoteResult

    local = _local_model("dflash", "qwen", "gguf", "/local/qwen.gguf", 2000)
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _p: {})
    monkeypatch.setattr("local_code_bench.cli.load_external_repo", lambda _p: _ext_cfg())
    monkeypatch.setattr("local_code_bench.cli.scan_inferencers", lambda configs: [])
    monkeypatch.setattr("local_code_bench.cli.normalize_all", lambda stored: [local])

    plan = DemotePlan(
        name="qwen",
        store_format="gguf",
        source=Path("/local/qwen.gguf"),
        destination=Path("/ext/qwen.gguf"),
        size_bytes=2000,
    )

    def fake_demote(model, external_cfg, configs, state_dir, **k):
        return DemoteResult(
            plan=plan,
            destination=Path("/ext/qwen.gguf"),
            bytes_reclaimed=2000,
            verified=True,
            reused_existing=False,
        )

    monkeypatch.setattr("local_code_bench.cli.demote_model", fake_demote)

    exit_code = main(["inferencer", "demote", "qwen"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "demoted" in out.lower()
    assert "external" in out


def test_demote_not_found_exits_2(monkeypatch, capsys) -> None:
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _p: {})
    monkeypatch.setattr("local_code_bench.cli.load_external_repo", lambda _p: _ext_cfg())
    monkeypatch.setattr("local_code_bench.cli.scan_inferencers", lambda configs: [])
    monkeypatch.setattr("local_code_bench.cli.normalize_all", lambda stored: [])

    exit_code = main(["inferencer", "demote", "ghost"])

    err = capsys.readouterr().err
    assert exit_code == 2
    assert err.startswith("bench: error:")


def test_demote_move_error_exits_2(monkeypatch, capsys) -> None:
    from local_code_bench.inferencers.tiering import DemoteError

    local = _local_model("dflash", "qwen", "gguf", "/local/qwen.gguf", 2000)
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _p: {})
    monkeypatch.setattr("local_code_bench.cli.load_external_repo", lambda _p: _ext_cfg())
    monkeypatch.setattr("local_code_bench.cli.scan_inferencers", lambda configs: [])
    monkeypatch.setattr("local_code_bench.cli.normalize_all", lambda stored: [local])

    def boom(*a, **k):
        raise DemoteError("dflash is running and could be serving qwen")

    monkeypatch.setattr("local_code_bench.cli.demote_model", boom)

    exit_code = main(["inferencer", "demote", "qwen"])

    err = capsys.readouterr().err
    assert exit_code == 2
    assert "bench: error: dflash is running" in err


def _autotier_cfg(max_local_gb=1.0, pins=()):
    from local_code_bench.config import AutoTierConfig

    return AutoTierConfig(max_local_gb=max_local_gb, pins=pins)


def test_tier_dry_run_prints_plan_without_applying(monkeypatch, capsys) -> None:
    # Two local models, ~3 GiB total, budget 1 GiB → evict the LRU one.
    big = 2 * 1024**3
    small = 1 * 1024**3
    a = _local_model("dflash", "old", "gguf", "/local/old.gguf", big, identity="id-old")
    b = _local_model("dflash", "new", "gguf", "/local/new.gguf", small, identity="id-new")
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _p: {})
    monkeypatch.setattr("local_code_bench.cli.load_external_repo", lambda _p: _ext_cfg())
    monkeypatch.setattr("local_code_bench.cli.load_autotier", lambda _p: _autotier_cfg(max_local_gb=1.0))
    monkeypatch.setattr("local_code_bench.cli.scan_inferencers", lambda configs: [])
    monkeypatch.setattr("local_code_bench.cli.normalize_all", lambda stored: [a, b])
    monkeypatch.setattr("local_code_bench.cli.check_availability", lambda c, **k: _mounted(True))
    # Deterministic LRU: "old" is least-recently used.
    monkeypatch.setattr(
        "local_code_bench.cli._local_free_bytes", lambda configs: None
    )
    monkeypatch.setattr(
        "local_code_bench.inferencers.autotier.mtime_last_used",
        lambda m: 1.0 if m.name == "old" else 2.0,
    )

    applied = {"called": False}

    def fake_apply(*a, **k):
        applied["called"] = True
        return []

    monkeypatch.setattr("local_code_bench.inferencers.autotier.apply_plan", fake_apply)

    exit_code = main(["inferencer", "tier"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "old" in out  # the eviction candidate is shown
    assert applied["called"] is False  # dry-run moves nothing


def test_tier_apply_executes_plan(monkeypatch, capsys) -> None:
    big = 2 * 1024**3
    a = _local_model("dflash", "old", "gguf", "/local/old.gguf", big, identity="id-old")
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _p: {})
    monkeypatch.setattr("local_code_bench.cli.load_external_repo", lambda _p: _ext_cfg())
    monkeypatch.setattr("local_code_bench.cli.load_autotier", lambda _p: _autotier_cfg(max_local_gb=1.0))
    monkeypatch.setattr("local_code_bench.cli.scan_inferencers", lambda configs: [])
    monkeypatch.setattr("local_code_bench.cli.normalize_all", lambda stored: [a])
    monkeypatch.setattr("local_code_bench.cli.check_availability", lambda c, **k: _mounted(True))
    monkeypatch.setattr("local_code_bench.cli._local_free_bytes", lambda configs: None)

    from local_code_bench.inferencers.tiering import DemotePlan, DemoteResult

    calls = {"n": 0}

    def fake_apply(plan, external_cfg, configs, state_dir, **k):
        calls["n"] = len(plan.evictions)
        return [
            DemoteResult(
                plan=DemotePlan(
                    name=ev.name,
                    store_format=ev.store_format,
                    source=Path(ev.model.path),
                    destination=Path("/ext") / ev.name,
                    size_bytes=ev.size_bytes,
                ),
                destination=Path("/ext") / ev.name,
                bytes_reclaimed=ev.size_bytes,
                verified=True,
                reused_existing=False,
            )
            for ev in plan.evictions
        ]

    monkeypatch.setattr("local_code_bench.inferencers.autotier.apply_plan", fake_apply)

    exit_code = main(["inferencer", "tier", "--apply"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert calls["n"] == 1
    assert "applied" in out.lower()


def test_tier_paused_when_external_offline(monkeypatch, capsys) -> None:
    big = 2 * 1024**3
    a = _local_model("dflash", "old", "gguf", "/local/old.gguf", big, identity="id-old")
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _p: {})
    monkeypatch.setattr("local_code_bench.cli.load_external_repo", lambda _p: _ext_cfg())
    monkeypatch.setattr("local_code_bench.cli.load_autotier", lambda _p: _autotier_cfg(max_local_gb=1.0))
    monkeypatch.setattr("local_code_bench.cli.scan_inferencers", lambda configs: [])
    monkeypatch.setattr("local_code_bench.cli.normalize_all", lambda stored: [a])
    monkeypatch.setattr("local_code_bench.cli.check_availability", lambda c, **k: _mounted(False))
    monkeypatch.setattr("local_code_bench.cli._local_free_bytes", lambda configs: None)

    exit_code = main(["inferencer", "tier"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "paused" in out.lower()


def test_tier_apply_offline_exits_2(monkeypatch, capsys) -> None:
    big = 2 * 1024**3
    a = _local_model("dflash", "old", "gguf", "/local/old.gguf", big, identity="id-old")
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _p: {})
    monkeypatch.setattr("local_code_bench.cli.load_external_repo", lambda _p: _ext_cfg())
    monkeypatch.setattr("local_code_bench.cli.load_autotier", lambda _p: _autotier_cfg(max_local_gb=1.0))
    monkeypatch.setattr("local_code_bench.cli.scan_inferencers", lambda configs: [])
    monkeypatch.setattr("local_code_bench.cli.normalize_all", lambda stored: [a])
    monkeypatch.setattr("local_code_bench.cli.check_availability", lambda c, **k: _mounted(False))
    monkeypatch.setattr("local_code_bench.cli._local_free_bytes", lambda configs: None)

    exit_code = main(["inferencer", "tier", "--apply"])

    err = capsys.readouterr().err
    assert exit_code == 2
    assert err.startswith("bench: error:")


def test_tier_no_policy_configured_exits_2(monkeypatch, capsys) -> None:
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _p: {})
    monkeypatch.setattr("local_code_bench.cli.load_external_repo", lambda _p: _ext_cfg())
    monkeypatch.setattr("local_code_bench.cli.load_autotier", lambda _p: None)

    exit_code = main(["inferencer", "tier"])

    err = capsys.readouterr().err
    assert exit_code == 2
    assert err.startswith("bench: error:")


def test_parser_accepts_tier_and_move_actions() -> None:
    for action in ("promote", "demote", "tier"):
        args = build_parser().parse_args(["inferencer", action, "m"])
        assert args.action == action


def test_promote_model_not_found_on_external_exits_2(monkeypatch, capsys) -> None:
    # A non-matching model on the external tier forces _find_named to iterate
    # and still come up empty, surfacing the "model not found" refusal.
    other = _ext_model("dflash", "other", "gguf", "/ext/other.gguf", 2000)
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _p: {})
    monkeypatch.setattr("local_code_bench.cli.load_external_repo", lambda _p: _ext_cfg())
    monkeypatch.setattr("local_code_bench.cli.check_availability", lambda c, **k: _mounted(True))
    monkeypatch.setattr("local_code_bench.cli.scan_external_tier", lambda c, infs, **k: [other])

    exit_code = main(["inferencer", "promote", "qwen"])

    err = capsys.readouterr().err
    assert exit_code == 2
    assert "qwen: model not found on the external tier" in err


def test_promote_no_inferencer_configured_exits_2(monkeypatch, capsys) -> None:
    # The model exists externally but its declaring engine has no config to land in.
    ext = _ext_model("ghost-engine", "qwen", "gguf", "/ext/qwen.gguf", 2000)
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _p: {})
    monkeypatch.setattr("local_code_bench.cli.load_external_repo", lambda _p: _ext_cfg())
    monkeypatch.setattr("local_code_bench.cli.check_availability", lambda c, **k: _mounted(True))
    monkeypatch.setattr("local_code_bench.cli.scan_external_tier", lambda c, infs, **k: [ext])

    exit_code = main(["inferencer", "promote", "qwen"])

    err = capsys.readouterr().err
    assert exit_code == 2
    assert "no inferencer configured to promote it into" in err


def test_demote_no_external_repo_exits_2(monkeypatch, capsys) -> None:
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _p: {})
    monkeypatch.setattr("local_code_bench.cli.load_external_repo", lambda _p: None)

    exit_code = main(["inferencer", "demote", "qwen"])

    err = capsys.readouterr().err
    assert exit_code == 2
    assert err.startswith("bench: error:")
    assert "no external_repo configured" in err


def test_demote_missing_name_exits_2(monkeypatch, capsys) -> None:
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _p: {})
    monkeypatch.setattr("local_code_bench.cli.load_external_repo", lambda _p: _ext_cfg())

    exit_code = main(["inferencer", "demote"])

    err = capsys.readouterr().err
    assert exit_code == 2
    assert "demote requires a model name" in err


def test_demote_scan_failure_exits_2(monkeypatch, capsys) -> None:
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _p: {})
    monkeypatch.setattr("local_code_bench.cli.load_external_repo", lambda _p: _ext_cfg())

    def boom(_configs):
        raise OSError("local store unreadable")

    monkeypatch.setattr("local_code_bench.cli.scan_inferencers", boom)

    exit_code = main(["inferencer", "demote", "qwen"])

    err = capsys.readouterr().err
    assert exit_code == 2
    assert "bench: error: local store unreadable" in err


def test_tier_no_external_repo_exits_2(monkeypatch, capsys) -> None:
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _p: {})
    monkeypatch.setattr("local_code_bench.cli.load_autotier", lambda _p: _autotier_cfg(max_local_gb=1.0))
    monkeypatch.setattr("local_code_bench.cli.load_external_repo", lambda _p: None)

    exit_code = main(["inferencer", "tier"])

    err = capsys.readouterr().err
    assert exit_code == 2
    assert "no external_repo configured" in err


def test_tier_scan_failure_exits_2(monkeypatch, capsys) -> None:
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _p: {})
    monkeypatch.setattr("local_code_bench.cli.load_autotier", lambda _p: _autotier_cfg(max_local_gb=1.0))
    monkeypatch.setattr("local_code_bench.cli.load_external_repo", lambda _p: _ext_cfg())

    def boom(_configs):
        raise OSError("local store unreadable")

    monkeypatch.setattr("local_code_bench.cli.scan_inferencers", boom)

    exit_code = main(["inferencer", "tier"])

    err = capsys.readouterr().err
    assert exit_code == 2
    assert "bench: error: local store unreadable" in err


def test_tier_within_budget_reports_nothing_to_evict(monkeypatch, capsys) -> None:
    # One small local model under a generous budget → an empty plan.
    a = _local_model("dflash", "tiny", "gguf", "/local/tiny.gguf", 1024, identity="id-tiny")
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _p: {})
    monkeypatch.setattr("local_code_bench.cli.load_external_repo", lambda _p: _ext_cfg())
    monkeypatch.setattr("local_code_bench.cli.load_autotier", lambda _p: _autotier_cfg(max_local_gb=100.0))
    monkeypatch.setattr("local_code_bench.cli.scan_inferencers", lambda configs: [])
    monkeypatch.setattr("local_code_bench.cli.normalize_all", lambda stored: [a])
    monkeypatch.setattr("local_code_bench.cli.check_availability", lambda c, **k: _mounted(True))
    monkeypatch.setattr("local_code_bench.cli._local_free_bytes", lambda configs: None)

    exit_code = main(["inferencer", "tier"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "nothing to evict" in out


def test_local_free_bytes_probes_first_existing_store(tmp_path) -> None:
    from local_code_bench.cli import _local_free_bytes

    cfg = InferencerConfig(
        name="dflash",
        lifecycle="server",
        detect_kind="binary",
        detect_target="dflash",
        port=1,
        health_url="http://127.0.0.1:{port}/h",
        model_store=("/does/not/exist", str(tmp_path)),
        store_format="gguf",
    )

    free = _local_free_bytes({"dflash": cfg})

    assert isinstance(free, int)
    assert free > 0


def test_local_free_bytes_returns_none_when_no_store_exists() -> None:
    from local_code_bench.cli import _local_free_bytes

    cfg = InferencerConfig(
        name="dflash",
        lifecycle="server",
        detect_kind="binary",
        detect_target="dflash",
        port=1,
        health_url="http://127.0.0.1:{port}/h",
        model_store=("/does/not/exist",),
        store_format="gguf",
    )

    assert _local_free_bytes({"dflash": cfg}) is None
