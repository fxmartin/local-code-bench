"""Tests for the bare-vs-proxied A/B orchestration (Epic-13, Story 13.3-001)."""

from __future__ import annotations

import pytest

from local_code_bench.cli import main
from local_code_bench.config import AgentConfig, OptimizerConfig
from local_code_bench.optimizers import manager
from local_code_bench.optimizers.abrun import (
    proxied_agent,
    render_ab_report,
    run_ab_comparison,
)
from local_code_bench.optimizers.manager import OptimizerError, OptimizerStatus
from local_code_bench.tasks import BenchmarkTask


def _proxy_cfg(name: str = "headroom", port: int = 8787) -> OptimizerConfig:
    return OptimizerConfig(
        name=name,
        detect_kind="binary",
        detect_target=name,
        port=port,
        health_url="http://127.0.0.1:{port}/v1/models",
        start=(name, "proxy", "--port", "{port}", "{upstream}"),
    )


def _proxy_status(
    cfg: OptimizerConfig,
    *,
    running: bool = True,
    healthy: bool = True,
    upstream: str | None = "http://127.0.0.1:8080/v1",
) -> OptimizerStatus:
    return OptimizerStatus(
        name=cfg.name,
        installed=True,
        running=running,
        pid=4321 if running else None,
        port=cfg.port,
        upstream=upstream,
        healthy=healthy,
        detail="running and healthy" if healthy else "not running",
    )


def _qwen_agent(base_url: str = "http://127.0.0.1:8080/v1") -> AgentConfig:
    return AgentConfig(
        "qwen-local",
        "qwen-code",
        "qwen",
        "none",
        10,
        model="qwen3-coder",
        base_url=base_url,
    )


def _task(task_id: str = "suite/1") -> BenchmarkTask:
    return BenchmarkTask(task_id, "humaneval", "prompt", "assert True", "solution", "v")


# --- proxied agent derivation -----------------------------------------------


def test_proxied_agent_swaps_openai_base_url_to_proxy_listen_port() -> None:
    agent = _qwen_agent()

    proxied = proxied_agent(agent, _proxy_cfg(port=9999))

    assert proxied.base_url == "http://127.0.0.1:9999/v1"
    assert proxied.name == agent.name
    assert proxied.model == agent.model
    # The bare config is untouched — both conditions share everything else.
    assert agent.base_url == "http://127.0.0.1:8080/v1"


def test_proxied_agent_swaps_anthropic_base_url_without_v1_suffix() -> None:
    agent = AgentConfig(
        "claude-gateway",
        "claude-code",
        "claude",
        "none",
        10,
        model="claude-sonnet-5",
        anthropic_base_url="http://127.0.0.1:8080",
    )

    proxied = proxied_agent(agent, _proxy_cfg(port=8787))

    assert proxied.anthropic_base_url == "http://127.0.0.1:8787"
    assert proxied.base_url is None


def test_proxied_agent_without_configurable_base_url_refuses() -> None:
    agent = AgentConfig("codex", "codex", "codex", "workspace-write", 10)

    with pytest.raises(OptimizerError, match="no configurable base URL"):
        proxied_agent(agent, _proxy_cfg())


# --- orchestration ----------------------------------------------------------


def test_run_ab_comparison_refuses_when_proxy_not_running(tmp_path, monkeypatch) -> None:
    cfg = _proxy_cfg()
    monkeypatch.setattr(
        manager, "status", lambda _cfg, _state_dir: _proxy_status(cfg, running=False, healthy=False)
    )

    with pytest.raises(OptimizerError, match="not running"):
        run_ab_comparison(
            agent=_qwen_agent(),
            tasks=[_task()],
            proxy=cfg,
            state_dir=tmp_path,
            result_path=tmp_path / "ab.jsonl",
            runner=lambda **_kwargs: {},
        )


def test_run_ab_comparison_refuses_when_proxy_unhealthy(tmp_path, monkeypatch) -> None:
    cfg = _proxy_cfg()
    monkeypatch.setattr(
        manager, "status", lambda _cfg, _state_dir: _proxy_status(cfg, healthy=False)
    )

    with pytest.raises(OptimizerError, match="not healthy"):
        run_ab_comparison(
            agent=_qwen_agent(),
            tasks=[_task()],
            proxy=cfg,
            state_dir=tmp_path,
            result_path=tmp_path / "ab.jsonl",
            runner=lambda **_kwargs: {},
        )


def test_run_ab_comparison_runs_each_task_bare_then_proxied_with_tags(
    tmp_path, monkeypatch
) -> None:
    cfg = _proxy_cfg()
    monkeypatch.setattr(manager, "status", lambda _cfg, _state_dir: _proxy_status(cfg))
    agent = _qwen_agent()
    tasks = [_task("suite/1"), _task("suite/2")]
    calls: list[dict] = []

    def fake_runner(**kwargs):
        calls.append(kwargs)
        return {"task_id": kwargs["task"].task_id, **dict(kwargs["record_extra"])}

    records = run_ab_comparison(
        agent=agent,
        tasks=tasks,
        proxy=cfg,
        state_dir=tmp_path,
        result_path=tmp_path / "ab.jsonl",
        runner=fake_runner,
    )

    # Two conditions per task, bare first, under the same result file.
    assert len(calls) == 4
    assert [call["task"].task_id for call in calls] == ["suite/1", "suite/1", "suite/2", "suite/2"]
    assert all(call["result_path"] == tmp_path / "ab.jsonl" for call in calls)
    bare_calls = calls[0::2]
    proxied_calls = calls[1::2]
    assert all(call["agent"].base_url == "http://127.0.0.1:8080/v1" for call in bare_calls)
    assert all(call["agent"].base_url == "http://127.0.0.1:8787/v1" for call in proxied_calls)

    # Every record is tagged with its condition; proxied records name the proxy.
    conditions = [record["optimization"]["condition"] for record in records]
    assert conditions == ["bare", "proxied", "bare", "proxied"]
    bare_tag = records[0]["optimization"]
    assert bare_tag["proxy_in_path"] is False
    proxied_tag = records[1]["optimization"]
    assert proxied_tag["proxy_in_path"] is True
    assert proxied_tag["proxy"]["name"] == "headroom"
    assert proxied_tag["proxy"]["port"] == 8787
    assert proxied_tag["proxy"]["upstream"] == "http://127.0.0.1:8080/v1"
    assert proxied_tag["proxy"]["command"] == list(cfg.start)


def test_run_ab_comparison_forwards_progress_and_provenance(tmp_path, monkeypatch) -> None:
    cfg = _proxy_cfg()
    monkeypatch.setattr(manager, "status", lambda _cfg, _state_dir: _proxy_status(cfg))
    seen: list[dict] = []
    messages: list[str] = []

    def fake_runner(**kwargs):
        seen.append(kwargs)
        kwargs["progress"]("qwen-local suite/1: passed")
        return {"task_id": "suite/1", **dict(kwargs["record_extra"])}

    run_ab_comparison(
        agent=_qwen_agent(),
        tasks=[_task()],
        proxy=cfg,
        state_dir=tmp_path,
        result_path=tmp_path / "ab.jsonl",
        runner=fake_runner,
        progress=messages.append,
        engine_provenance="provenance-sentinel",
    )

    assert all(call["engine_provenance"] == "provenance-sentinel" for call in seen)
    assert messages == [
        "[1/1] bare: qwen-local suite/1: passed",
        "[1/1] proxied: qwen-local suite/1: passed",
    ]


# --- report rendering -------------------------------------------------------


def _record(
    condition: str,
    *,
    task_id: str = "suite/1",
    passed: bool | None = True,
    prompt_tokens: int | None = 1000,
    wall_time: float = 10.0,
) -> dict:
    record: dict = {
        "task_id": task_id,
        "wall_time_seconds": wall_time,
        "optimization": {
            "condition": condition,
            "proxy_in_path": condition == "proxied",
        },
    }
    if condition == "proxied":
        record["optimization"]["proxy"] = {
            "name": "headroom",
            "port": 8787,
            "upstream": "http://127.0.0.1:8080/v1",
            "command": ["headroom", "proxy"],
        }
    if passed is not None:
        record["passed"] = passed
    if prompt_tokens is not None:
        record["usage"] = {"prompt_tokens": prompt_tokens, "completion_tokens": 50}
    return record


def test_render_ab_report_pairs_token_saving_with_correctness() -> None:
    records = [
        _record("bare", prompt_tokens=1000, passed=True, wall_time=20.0),
        _record("proxied", prompt_tokens=600, passed=False, wall_time=12.0),
    ]

    report = render_ab_report(records, agent_name="qwen-local")

    assert "tokens prefilled" in report
    assert "1000" in report and "600" in report
    assert "-40.0%" in report
    # A saving is never shown in isolation — task success sits alongside it.
    assert "task success" in report
    assert "1/1 passed" in report
    assert "0/1 passed" in report
    assert "-1 task(s)" in report
    assert "headroom" in report
    assert "qwen-local" in report


def test_render_ab_report_shows_latency_delta() -> None:
    records = [
        _record("bare", wall_time=20.0),
        _record("proxied", wall_time=12.0),
    ]

    report = render_ab_report(records)

    assert "latency" in report
    assert "20.00" in report and "12.00" in report
    assert "-8.00s" in report


def test_render_ab_report_marks_missing_prefill_tokens_unavailable() -> None:
    records = [
        _record("bare", prompt_tokens=None),
        _record("proxied", prompt_tokens=None),
    ]

    report = render_ab_report(records)

    assert "unavailable" in report
    assert "task success" in report


def test_render_ab_report_states_unverified_correctness_explicitly() -> None:
    records = [
        _record("bare", passed=None),
        _record("proxied", passed=None),
    ]

    report = render_ab_report(records)

    assert "unverified" in report
    # No implied parity: an unverified condition never renders a passed count.
    assert "1/1 passed" not in report
    assert "0/1 passed" not in report


def test_render_ab_report_counts_partially_unverified_tasks() -> None:
    records = [
        _record("bare", task_id="suite/1", passed=True),
        _record("proxied", task_id="suite/1", passed=True),
        _record("bare", task_id="suite/2", passed=None),
        _record("proxied", task_id="suite/2", passed=None),
    ]

    report = render_ab_report(records)

    assert "correctness unverified for 1 of 2 task(s)" in report


def test_render_ab_report_rejects_untagged_records() -> None:
    with pytest.raises(ValueError, match="condition"):
        render_ab_report([{"task_id": "suite/1", "passed": True}])


# --- CLI wiring -------------------------------------------------------------


def test_cli_ab_proxy_runs_comparison_and_prints_report(tmp_path, monkeypatch, capsys) -> None:
    agent = _qwen_agent()
    cfg = _proxy_cfg()
    task = _task()
    seen: dict = {}

    def fake_run_ab_comparison(**kwargs):
        seen.update(kwargs)
        return [
            _record("bare"),
            _record("proxied"),
        ]

    monkeypatch.setattr("local_code_bench.cli.load_agents", lambda _path: {"qwen-local": agent})
    monkeypatch.setattr("local_code_bench.cli.load_suite", lambda _suite, cache_dir: [task])
    monkeypatch.setattr("local_code_bench.cli.load_optimizers", lambda _path: {"headroom": cfg})
    monkeypatch.setattr("local_code_bench.cli.run_ab_comparison", fake_run_ab_comparison)

    exit_code = main(
        [
            "--mode",
            "agent",
            "--agent",
            "qwen-local",
            "--suite",
            "humaneval",
            "--ab-proxy",
            "headroom",
            "--run-file",
            str(tmp_path / "ab.jsonl"),
            "--optimizer-state-dir",
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
    assert "ab_proxy=headroom" in out


def test_cli_ab_proxy_unknown_optimizer_errors_exit_2(tmp_path, monkeypatch, capsys) -> None:
    agent = _qwen_agent()
    monkeypatch.setattr("local_code_bench.cli.load_agents", lambda _path: {"qwen-local": agent})
    monkeypatch.setattr("local_code_bench.cli.load_suite", lambda _suite, cache_dir: [_task()])
    monkeypatch.setattr("local_code_bench.cli.load_optimizers", lambda _path: {})

    exit_code = main(
        [
            "--mode",
            "agent",
            "--agent",
            "qwen-local",
            "--suite",
            "humaneval",
            "--ab-proxy",
            "ghost",
            "--run-file",
            str(tmp_path / "ab.jsonl"),
        ]
    )

    assert exit_code == 2
    assert "unknown optimizer 'ghost'" in capsys.readouterr().err


def test_cli_ab_proxy_rejects_resume(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "--mode",
                "agent",
                "--agent",
                "qwen-local",
                "--suite",
                "humaneval",
                "--ab-proxy",
                "headroom",
                "--resume",
            ]
        )

    assert exc.value.code == 2
    assert "--resume" in capsys.readouterr().err


def test_cli_ab_proxy_optimizer_error_exit_2(tmp_path, monkeypatch, capsys) -> None:
    agent = _qwen_agent()
    cfg = _proxy_cfg()
    monkeypatch.setattr("local_code_bench.cli.load_agents", lambda _path: {"qwen-local": agent})
    monkeypatch.setattr("local_code_bench.cli.load_suite", lambda _suite, cache_dir: [_task()])
    monkeypatch.setattr("local_code_bench.cli.load_optimizers", lambda _path: {"headroom": cfg})

    def refuse(**_kwargs):
        raise OptimizerError("headroom is not running")

    monkeypatch.setattr("local_code_bench.cli.run_ab_comparison", refuse)

    exit_code = main(
        [
            "--mode",
            "agent",
            "--agent",
            "qwen-local",
            "--suite",
            "humaneval",
            "--ab-proxy",
            "headroom",
            "--run-file",
            str(tmp_path / "ab.jsonl"),
        ]
    )

    assert exit_code == 2
    assert "headroom is not running" in capsys.readouterr().err
