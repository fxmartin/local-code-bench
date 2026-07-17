"""Tests for the `bench optimizer` CLI subcommand (Epic-13, Story 13.4-001)."""

from __future__ import annotations

from local_code_bench.cli import main
from local_code_bench.config import AgentConfig, ConfigError, OptimizerConfig
from local_code_bench.optimizers.manager import OptimizerError, OptimizerStatus
from local_code_bench.tasks import BenchmarkTask


def _proxy_cfg(
    name: str = "headroom",
    port: int = 8787,
    url: str | None = "https://headroom-docs.vercel.app/docs",
) -> OptimizerConfig:
    return OptimizerConfig(
        name=name,
        detect_kind="binary",
        detect_target=name,
        port=port,
        health_url="http://127.0.0.1:{port}/v1/models",
        start=(name, "proxy", "--port", "{port}", "{upstream}"),
        url=url,
    )


def _proxy_status(
    cfg: OptimizerConfig,
    *,
    installed: bool = True,
    running: bool = True,
    healthy: bool = True,
    upstream: str | None = "http://127.0.0.1:8080/v1",
    detail: str = "running and healthy",
) -> OptimizerStatus:
    return OptimizerStatus(
        name=cfg.name,
        installed=installed,
        running=running,
        pid=4321 if running else None,
        port=cfg.port,
        upstream=upstream,
        healthy=healthy,
        detail=detail,
    )


def _qwen_agent(*, inferencer: str | None = None) -> AgentConfig:
    return AgentConfig(
        "qwen-local",
        "qwen-code",
        "qwen",
        "none",
        10,
        model="qwen3-coder",
        base_url="http://127.0.0.1:8080/v1",
        inferencer=inferencer,
    )


def _task(task_id: str = "suite/1") -> BenchmarkTask:
    return BenchmarkTask(task_id, "humaneval", "prompt", "assert True", "solution", "v")


def _record(condition: str) -> dict:
    record: dict = {
        "task_id": "suite/1",
        "passed": True,
        "wall_time_seconds": 10.0,
        "usage": {"prompt_tokens": 1000},
        "optimization": {"condition": condition, "proxy_in_path": condition == "proxied"},
    }
    if condition == "proxied":
        record["optimization"]["proxy"] = {
            "name": "headroom",
            "port": 8787,
            "upstream": "http://127.0.0.1:8080/v1",
            "command": ["headroom", "proxy"],
        }
    return record


# --- list / status -----------------------------------------------------------


def test_optimizer_list_prints_installed_port_url_and_manual_note(monkeypatch, capsys) -> None:
    monkeypatch.setattr("local_code_bench.cli.load_optimizers", lambda _path: {"headroom": _proxy_cfg()})
    monkeypatch.setattr("local_code_bench.inferencers.detect.is_installed", lambda cfg: True)

    exit_code = main(["optimizer", "list"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "headroom" in out
    assert "yes" in out
    assert "8787" in out
    assert "https://headroom-docs.vercel.app/docs" in out
    assert "installation is manual" in out


def test_optimizer_list_marks_uninstalled_proxy(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "local_code_bench.cli.load_optimizers", lambda _path: {"headroom": _proxy_cfg(url=None)}
    )
    monkeypatch.setattr("local_code_bench.inferencers.detect.is_installed", lambda cfg: False)

    exit_code = main(["optimizer", "list"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "no" in out
    assert "-" in out


def test_optimizer_status_shows_installed_running_healthy_upstream(monkeypatch, capsys) -> None:
    cfg = _proxy_cfg()
    monkeypatch.setattr("local_code_bench.cli.load_optimizers", lambda _path: {"headroom": cfg})
    monkeypatch.setattr(
        "local_code_bench.optimizers.manager.status",
        lambda config, state_dir: _proxy_status(config),
    )

    exit_code = main(["optimizer", "status"])

    assert exit_code == 0
    out = capsys.readouterr().out
    for column in ("INSTALLED", "RUNNING", "HEALTHY", "UPSTREAM"):
        assert column in out
    assert "http://127.0.0.1:8080/v1" in out
    assert "running and healthy" in out


def test_optimizer_status_uses_state_dir_flag(monkeypatch, capsys) -> None:
    cfg = _proxy_cfg()
    seen: list[str] = []

    def fake_status(config, state_dir):
        seen.append(str(state_dir))
        return _proxy_status(config, running=False, healthy=False, upstream=None, detail="not running")

    monkeypatch.setattr("local_code_bench.cli.load_optimizers", lambda _path: {"headroom": cfg})
    monkeypatch.setattr("local_code_bench.optimizers.manager.status", fake_status)

    exit_code = main(["optimizer", "status", "--state-dir", "/tmp/opt-state"])

    assert exit_code == 0
    assert seen == ["/tmp/opt-state"]
    assert "not running" in capsys.readouterr().out


# --- start / stop ------------------------------------------------------------


def test_optimizer_start_with_named_inferencer_chains_upstream(monkeypatch, capsys) -> None:
    cfg = _proxy_cfg()
    seen: dict = {}

    def fake_named_upstream(name, configs, state_dir):
        seen["engine"] = name
        return "http://127.0.0.1:8080/v1"

    def fake_start(config, upstream, state_dir, **kwargs):
        seen["upstream"] = upstream
        return _proxy_status(config, detail="started and healthy")

    monkeypatch.setattr("local_code_bench.cli.load_optimizers", lambda _path: {"headroom": cfg})
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _path: {})
    monkeypatch.setattr(
        "local_code_bench.optimizers.manager.named_inferencer_base_url", fake_named_upstream
    )
    monkeypatch.setattr("local_code_bench.optimizers.manager.start", fake_start)

    exit_code = main(["optimizer", "start", "headroom", "--inferencer", "mlx-lm"])

    assert exit_code == 0
    assert seen == {"engine": "mlx-lm", "upstream": "http://127.0.0.1:8080/v1"}
    assert "started headroom" in capsys.readouterr().out


def test_optimizer_start_defaults_to_single_active_engine(monkeypatch, capsys) -> None:
    cfg = _proxy_cfg()
    seen: dict = {}

    def fake_start_chained(config, inferencer_configs, inferencer_state_dir, state_dir, **kwargs):
        seen["proxy"] = config.name
        return _proxy_status(config, detail="started and healthy")

    monkeypatch.setattr("local_code_bench.cli.load_optimizers", lambda _path: {"headroom": cfg})
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _path: {})
    monkeypatch.setattr("local_code_bench.optimizers.manager.start_chained", fake_start_chained)

    exit_code = main(["optimizer", "start", "headroom"])

    assert exit_code == 0
    assert seen == {"proxy": "headroom"}
    assert "started headroom" in capsys.readouterr().out


def test_optimizer_start_without_name_errors_exit_2(monkeypatch, capsys) -> None:
    monkeypatch.setattr("local_code_bench.cli.load_optimizers", lambda _path: {"headroom": _proxy_cfg()})

    exit_code = main(["optimizer", "start"])

    assert exit_code == 2
    assert "bench: error:" in capsys.readouterr().err


def test_optimizer_start_unknown_proxy_errors_exit_2(monkeypatch, capsys) -> None:
    monkeypatch.setattr("local_code_bench.cli.load_optimizers", lambda _path: {})

    exit_code = main(["optimizer", "start", "ghost"])

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "bench: error:" in err
    assert "unknown optimizer 'ghost'" in err


def test_optimizer_start_lifecycle_error_exits_2(monkeypatch, capsys) -> None:
    cfg = _proxy_cfg()

    def refuse(config, inferencer_configs, inferencer_state_dir, state_dir, **kwargs):
        raise OptimizerError("no active inferencer — a proxy must front a real engine; start one first")

    monkeypatch.setattr("local_code_bench.cli.load_optimizers", lambda _path: {"headroom": cfg})
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _path: {})
    monkeypatch.setattr("local_code_bench.optimizers.manager.start_chained", refuse)

    exit_code = main(["optimizer", "start", "headroom"])

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "bench: error:" in err
    assert "no active inferencer" in err


def test_optimizer_config_error_exits_2(monkeypatch, capsys) -> None:
    def boom(_path):
        raise ConfigError("optimizer config not found: missing.yaml")

    monkeypatch.setattr("local_code_bench.cli.load_optimizers", boom)

    exit_code = main(["optimizer", "list"])

    assert exit_code == 2
    assert "optimizer config not found" in capsys.readouterr().err


def test_optimizer_stop_stops_named_proxy(monkeypatch, capsys) -> None:
    cfg = _proxy_cfg()
    seen: list[str] = []

    def fake_stop(config, state_dir, **kwargs):
        seen.append(config.name)

    monkeypatch.setattr("local_code_bench.cli.load_optimizers", lambda _path: {"headroom": cfg})
    monkeypatch.setattr("local_code_bench.optimizers.manager.stop", fake_stop)

    exit_code = main(["optimizer", "stop", "headroom"])

    assert exit_code == 0
    assert seen == ["headroom"]
    assert "stopped headroom" in capsys.readouterr().out


def test_optimizer_stop_is_idempotent_when_not_running(monkeypatch, capsys) -> None:
    cfg = _proxy_cfg()
    # The manager's stop is a documented no-op when the proxy is down; the CLI
    # must surface that as success, not an error.
    monkeypatch.setattr("local_code_bench.cli.load_optimizers", lambda _path: {"headroom": cfg})
    monkeypatch.setattr("local_code_bench.optimizers.manager.stop", lambda config, state_dir, **kwargs: None)

    first = main(["optimizer", "stop", "headroom"])
    second = main(["optimizer", "stop", "headroom"])

    assert first == 0
    assert second == 0


# --- ab ----------------------------------------------------------------------


def _patch_ab_happy_path(monkeypatch, agent: AgentConfig, cfg: OptimizerConfig, seen: dict) -> None:
    def fake_run_ab_comparison(**kwargs):
        seen.update(kwargs)
        return [_record("bare"), _record("proxied")]

    monkeypatch.setattr("local_code_bench.cli.load_agents", lambda _path: {"qwen-local": agent})
    monkeypatch.setattr("local_code_bench.cli.load_suite", lambda _suite, cache_dir: [_task()])
    monkeypatch.setattr("local_code_bench.cli.load_optimizers", lambda _path: {"headroom": cfg})
    monkeypatch.setattr("local_code_bench.cli.run_ab_comparison", fake_run_ab_comparison)


def test_optimizer_ab_runs_comparison_and_prints_report(tmp_path, monkeypatch, capsys) -> None:
    agent = _qwen_agent()
    cfg = _proxy_cfg()
    seen: dict = {}
    _patch_ab_happy_path(monkeypatch, agent, cfg, seen)

    exit_code = main(
        [
            "optimizer",
            "ab",
            "--task",
            "humaneval",
            "--proxy",
            "headroom",
            "--agent",
            "qwen-local",
            "--run-file",
            str(tmp_path / "ab.jsonl"),
            "--state-dir",
            str(tmp_path / "state"),
        ]
    )

    assert exit_code == 0
    assert seen["agent"] is agent
    assert seen["proxy"] is cfg
    assert seen["state_dir"] == str(tmp_path / "state")
    out = capsys.readouterr().out
    assert "task success" in out
    assert "tokens prefilled" in out
    assert "proxy=headroom" in out


def test_optimizer_ab_requires_proxy_task_and_agent(monkeypatch, capsys) -> None:
    for argv, needle in (
        (["optimizer", "ab", "--task", "humaneval", "--agent", "qwen-local"], "--proxy"),
        (["optimizer", "ab", "--proxy", "headroom", "--agent", "qwen-local"], "--task"),
        (["optimizer", "ab", "--task", "humaneval", "--proxy", "headroom"], "--agent"),
    ):
        exit_code = main(argv)
        assert exit_code == 2
        err = capsys.readouterr().err
        assert "bench: error:" in err
        assert needle in err


def test_optimizer_ab_unknown_proxy_errors_exit_2(tmp_path, monkeypatch, capsys) -> None:
    agent = _qwen_agent()
    monkeypatch.setattr("local_code_bench.cli.load_agents", lambda _path: {"qwen-local": agent})
    monkeypatch.setattr("local_code_bench.cli.load_suite", lambda _suite, cache_dir: [_task()])
    monkeypatch.setattr("local_code_bench.cli.load_optimizers", lambda _path: {})

    exit_code = main(
        [
            "optimizer",
            "ab",
            "--task",
            "humaneval",
            "--proxy",
            "ghost",
            "--agent",
            "qwen-local",
            "--run-file",
            str(tmp_path / "ab.jsonl"),
        ]
    )

    assert exit_code == 2
    assert "unknown optimizer 'ghost'" in capsys.readouterr().err


def test_optimizer_ab_unknown_agent_errors_exit_2(monkeypatch, capsys) -> None:
    monkeypatch.setattr("local_code_bench.cli.load_agents", lambda _path: {})
    monkeypatch.setattr("local_code_bench.cli.load_optimizers", lambda _path: {"headroom": _proxy_cfg()})

    exit_code = main(
        ["optimizer", "ab", "--task", "humaneval", "--proxy", "headroom", "--agent", "ghost"]
    )

    assert exit_code == 2
    assert "unknown agent 'ghost'" in capsys.readouterr().err


def test_optimizer_ab_inferencer_mismatch_errors_exit_2(monkeypatch, capsys) -> None:
    agent = _qwen_agent(inferencer="mlx-lm")
    monkeypatch.setattr("local_code_bench.cli.load_agents", lambda _path: {"qwen-local": agent})
    monkeypatch.setattr("local_code_bench.cli.load_optimizers", lambda _path: {"headroom": _proxy_cfg()})

    exit_code = main(
        [
            "optimizer",
            "ab",
            "--task",
            "humaneval",
            "--proxy",
            "headroom",
            "--agent",
            "qwen-local",
            "--inferencer",
            "dflash",
        ]
    )

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "dflash" in err
    assert "mlx-lm" in err


def test_optimizer_ab_optimizer_error_exits_2(tmp_path, monkeypatch, capsys) -> None:
    agent = _qwen_agent()
    cfg = _proxy_cfg()
    monkeypatch.setattr("local_code_bench.cli.load_agents", lambda _path: {"qwen-local": agent})
    monkeypatch.setattr("local_code_bench.cli.load_suite", lambda _suite, cache_dir: [_task()])
    monkeypatch.setattr("local_code_bench.cli.load_optimizers", lambda _path: {"headroom": cfg})

    def refuse(**_kwargs):
        raise OptimizerError("proxy 'headroom' is not running — start it first (13.2 lifecycle)")

    monkeypatch.setattr("local_code_bench.cli.run_ab_comparison", refuse)

    exit_code = main(
        [
            "optimizer",
            "ab",
            "--task",
            "humaneval",
            "--proxy",
            "headroom",
            "--agent",
            "qwen-local",
            "--run-file",
            str(tmp_path / "ab.jsonl"),
        ]
    )

    assert exit_code == 2
    assert "not running" in capsys.readouterr().err
