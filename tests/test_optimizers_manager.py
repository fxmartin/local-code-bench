from __future__ import annotations

import json
import signal
from pathlib import Path

import pytest

from local_code_bench.config import OptimizerConfig
from local_code_bench.inferencers.manager import InferencerStatus
from local_code_bench.optimizers import manager
from local_code_bench.optimizers.manager import OptimizerError, OptimizerStatus


def _proxy_cfg(
    name: str = "headroom",
    port: int = 8787,
    start: tuple[str, ...] = ("headroom", "proxy", "--port", "{port}", "{upstream}"),
) -> OptimizerConfig:
    return OptimizerConfig(
        name=name,
        detect_kind="binary",
        detect_target="headroom",
        port=port,
        health_url="http://127.0.0.1:{port}/v1/models",
        start=start,
    )


def _engine_status(name: str, *, running: bool, port: int = 8080) -> InferencerStatus:
    return InferencerStatus(
        name=name,
        installed=True,
        lifecycle="server",
        running=running,
        pid=111 if running else None,
        port=port,
        healthy=running,
        detail="running" if running else "not running",
    )


def _make_popen(created: list, *, write: str = ""):
    """Build a fake `subprocess.Popen` that records instances and seeds the log."""

    class _FakeProc:
        def __init__(self, command, **kwargs) -> None:
            self.command = command
            self.kwargs = kwargs
            self.pid = 5678
            stdout = kwargs.get("stdout")
            if stdout is not None and write:
                stdout.write(write)
                stdout.flush()
            created.append(self)

        def poll(self):
            return None

    return _FakeProc


def _write_state_file(
    state_dir: Path,
    name: str,
    *,
    pid: int,
    port: int = 8787,
    upstream: str = "http://127.0.0.1:8080/v1",
) -> None:
    payload = {
        "name": name,
        "pid": pid,
        "port": port,
        "upstream": upstream,
        "started_at": "2026-07-17T00:00:00+00:00",
        "command": ["headroom", "proxy", "--port", str(port), upstream],
        "health_url": f"http://127.0.0.1:{port}/v1/models",
    }
    (Path(state_dir) / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")


# --- active_inferencer_base_url ---------------------------------------------


def test_active_upstream_resolved_from_single_running_engine(monkeypatch) -> None:
    statuses = {
        "dflash": _engine_status("dflash", running=False, port=8000),
        "mlx-lm": _engine_status("mlx-lm", running=True, port=8080),
    }
    monkeypatch.setattr(manager, "status_all", lambda configs, sd: statuses)

    upstream = manager.active_inferencer_base_url({}, "/state")

    assert upstream == "http://127.0.0.1:8080/v1"


def test_active_upstream_refuses_when_no_engine_running(monkeypatch) -> None:
    statuses = {"dflash": _engine_status("dflash", running=False)}
    monkeypatch.setattr(manager, "status_all", lambda configs, sd: statuses)

    with pytest.raises(OptimizerError) as exc:
        manager.active_inferencer_base_url({}, "/state")

    assert "real engine" in str(exc.value)


def test_active_upstream_refuses_when_multiple_engines_running(monkeypatch) -> None:
    statuses = {
        "dflash": _engine_status("dflash", running=True, port=8000),
        "mlx-lm": _engine_status("mlx-lm", running=True, port=8080),
    }
    monkeypatch.setattr(manager, "status_all", lambda configs, sd: statuses)

    with pytest.raises(OptimizerError) as exc:
        manager.active_inferencer_base_url({}, "/state")

    assert "dflash" in str(exc.value)
    assert "mlx-lm" in str(exc.value)


# --- start: success ----------------------------------------------------------


def test_start_substitutes_upstream_polls_health_and_records_state(
    tmp_path, monkeypatch
) -> None:
    cfg = _proxy_cfg()
    created: list = []
    monkeypatch.setattr(manager.subprocess, "Popen", _make_popen(created))
    monkeypatch.setattr(manager, "health_check", lambda url, timeout=1.0: True)
    monkeypatch.setattr(manager.detect, "is_installed", lambda c: True)

    status = manager.start(
        cfg, "http://127.0.0.1:8080/v1", tmp_path, timeout=5.0, poll_interval=0.0
    )

    assert status.running is True
    assert status.healthy is True
    assert status.pid == 5678
    assert status.port == 8787
    assert status.upstream == "http://127.0.0.1:8080/v1"
    assert created and created[0].kwargs["start_new_session"] is True
    assert created[0].command == [
        "headroom", "proxy", "--port", "8787", "http://127.0.0.1:8080/v1",
    ]
    state = json.loads((tmp_path / "headroom.json").read_text(encoding="utf-8"))
    assert state["pid"] == 5678
    assert state["port"] == 8787
    assert state["upstream"] == "http://127.0.0.1:8080/v1"
    assert state["health_url"] == "http://127.0.0.1:8787/v1/models"


def test_start_returns_current_when_already_running_and_healthy(tmp_path, monkeypatch) -> None:
    cfg = _proxy_cfg()
    _write_state_file(tmp_path, "headroom", pid=5678)
    spawned: list = []
    monkeypatch.setattr(manager.subprocess, "Popen", lambda *a, **k: spawned.append(1))
    monkeypatch.setattr(manager.detect, "is_installed", lambda c: True)
    monkeypatch.setattr(manager, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(manager, "health_check", lambda url, timeout=1.0: True)

    status = manager.start(cfg, "http://127.0.0.1:8080/v1", tmp_path)

    assert status.running is True
    assert status.healthy is True
    assert status.pid == 5678
    assert spawned == []  # already up: no new process spawned


def test_start_reports_progress_on_success(tmp_path, monkeypatch) -> None:
    cfg = _proxy_cfg()
    monkeypatch.setattr(manager.subprocess, "Popen", _make_popen([]))
    monkeypatch.setattr(manager, "health_check", lambda url, timeout=1.0: True)
    monkeypatch.setattr(manager.detect, "is_installed", lambda c: True)
    messages: list = []

    manager.start(
        cfg,
        "http://127.0.0.1:8080/v1",
        tmp_path,
        timeout=5.0,
        poll_interval=0.0,
        progress=messages.append,
    )

    assert any("starting headroom" in m for m in messages)
    assert any("healthy on port 8787" in m for m in messages)


# --- start_chained -----------------------------------------------------------


def test_start_chained_resolves_upstream_then_starts(tmp_path, monkeypatch) -> None:
    cfg = _proxy_cfg()
    statuses = {"mlx-lm": _engine_status("mlx-lm", running=True, port=8080)}
    monkeypatch.setattr(manager, "status_all", lambda configs, sd: statuses)
    calls: list = []
    monkeypatch.setattr(
        manager,
        "start",
        lambda c, upstream, sd, **k: calls.append((c.name, upstream)) or "started",
    )

    result = manager.start_chained(cfg, {}, "/engine-state", tmp_path)

    assert calls == [("headroom", "http://127.0.0.1:8080/v1")]
    assert result == "started"


def test_start_chained_refuses_without_engine_and_spawns_nothing(tmp_path, monkeypatch) -> None:
    cfg = _proxy_cfg()
    monkeypatch.setattr(manager, "status_all", lambda configs, sd: {})
    spawned: list = []
    monkeypatch.setattr(manager.subprocess, "Popen", lambda *a, **k: spawned.append(1))

    with pytest.raises(OptimizerError) as exc:
        manager.start_chained(cfg, {}, "/engine-state", tmp_path)

    assert "real engine" in str(exc.value)
    assert spawned == []
    assert not (tmp_path / "headroom.json").exists()


# --- start: failure ----------------------------------------------------------


def test_start_failure_kills_process_cleans_state_and_raises(tmp_path, monkeypatch) -> None:
    cfg = _proxy_cfg()
    created: list = []
    monkeypatch.setattr(
        manager.subprocess,
        "Popen",
        _make_popen(created, write="boot\nfatal: upstream unreachable\n"),
    )
    monkeypatch.setattr(manager, "health_check", lambda url, timeout=1.0: False)
    monkeypatch.setattr(manager.detect, "is_installed", lambda c: True)
    monkeypatch.setattr(manager.time, "sleep", lambda s: None)
    killpg_calls: list = []
    monkeypatch.setattr(manager.os, "killpg", lambda pid, sig: killpg_calls.append((pid, sig)))
    monkeypatch.setattr(
        manager.os, "kill", lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError())
    )

    with pytest.raises(OptimizerError) as exc:
        manager.start(
            cfg, "http://127.0.0.1:8080/v1", tmp_path, timeout=0.0, poll_interval=0.0
        )

    assert "fatal: upstream unreachable" in str(exc.value)
    assert killpg_calls == [(5678, signal.SIGTERM)]
    assert not (tmp_path / "headroom.json").exists()


def test_start_failure_when_process_dies_during_poll(tmp_path, monkeypatch) -> None:
    cfg = _proxy_cfg()

    class _DeadProc:
        pid = 5678

        def __init__(self, command, **kwargs) -> None:
            stdout = kwargs.get("stdout")
            if stdout is not None:
                stdout.write("crashed on startup\n")
                stdout.flush()

        def poll(self):
            return 1  # already exited

    monkeypatch.setattr(manager.subprocess, "Popen", _DeadProc)
    monkeypatch.setattr(manager, "health_check", lambda url, timeout=1.0: False)
    monkeypatch.setattr(manager.detect, "is_installed", lambda c: True)
    monkeypatch.setattr(manager, "_terminate_group", lambda pid, grace: None)

    with pytest.raises(OptimizerError) as exc:
        manager.start(
            cfg, "http://127.0.0.1:8080/v1", tmp_path, timeout=30.0, poll_interval=0.0
        )

    assert "crashed on startup" in str(exc.value)
    assert not (tmp_path / "headroom.json").exists()


def test_start_missing_executable_raises(tmp_path, monkeypatch) -> None:
    cfg = _proxy_cfg()

    def boom(command, **kwargs):
        raise FileNotFoundError(command[0])

    monkeypatch.setattr(manager.subprocess, "Popen", boom)
    monkeypatch.setattr(manager.detect, "is_installed", lambda c: True)

    with pytest.raises(OptimizerError) as exc:
        manager.start(cfg, "http://127.0.0.1:8080/v1", tmp_path)

    assert "headroom" in str(exc.value)
    assert not (tmp_path / "headroom.json").exists()


# --- status ------------------------------------------------------------------


def test_status_no_state_is_not_running(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(manager.detect, "is_installed", lambda c: True)

    status = manager.status(_proxy_cfg(), tmp_path)

    assert status.running is False
    assert status.pid is None
    assert status.upstream is None
    assert status.installed is True


def test_status_live_pid_uses_persisted_pid_health_and_upstream(tmp_path, monkeypatch) -> None:
    cfg = _proxy_cfg()
    _write_state_file(tmp_path, "headroom", pid=5678)
    monkeypatch.setattr(manager.detect, "is_installed", lambda c: True)
    monkeypatch.setattr(manager, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(manager, "health_check", lambda url, timeout=1.0: True)

    status = manager.status(cfg, tmp_path)

    assert status.running is True
    assert status.healthy is True
    assert status.pid == 5678
    assert status.upstream == "http://127.0.0.1:8080/v1"


def test_status_dead_pid_is_reported_not_running_and_cleaned_up(tmp_path, monkeypatch) -> None:
    cfg = _proxy_cfg()
    _write_state_file(tmp_path, "headroom", pid=9999)
    monkeypatch.setattr(manager.detect, "is_installed", lambda c: True)
    monkeypatch.setattr(manager, "_pid_alive", lambda pid: False)

    status = manager.status(cfg, tmp_path)

    assert status.running is False
    assert status.pid is None
    assert not (tmp_path / "headroom.json").exists()


def test_status_returns_optimizer_status_dataclass(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(manager.detect, "is_installed", lambda c: False)

    assert isinstance(manager.status(_proxy_cfg(), tmp_path), OptimizerStatus)


# --- stop --------------------------------------------------------------------


def test_stop_terminates_group_removes_state_and_is_idempotent(tmp_path, monkeypatch) -> None:
    cfg = _proxy_cfg()
    _write_state_file(tmp_path, "headroom", pid=5678)
    killpg_calls: list = []
    monkeypatch.setattr(manager.os, "killpg", lambda pid, sig: killpg_calls.append((pid, sig)))
    monkeypatch.setattr(manager.os, "kill", lambda pid, sig: (_ for _ in ()).throw(OSError()))

    manager.stop(cfg, tmp_path)

    assert killpg_calls == [(5678, signal.SIGTERM)]
    assert not (tmp_path / "headroom.json").exists()

    # A second stop with no state file is a no-op (no further signals) — and the
    # upstream inferencer is never signalled by either call.
    manager.stop(cfg, tmp_path)
    assert killpg_calls == [(5678, signal.SIGTERM)]


def test_stop_escalates_to_sigkill_after_grace(tmp_path, monkeypatch) -> None:
    cfg = _proxy_cfg()
    _write_state_file(tmp_path, "headroom", pid=5678)
    signals: list = []
    monkeypatch.setattr(manager.os, "killpg", lambda pid, sig: signals.append(sig))
    monkeypatch.setattr(manager.os, "kill", lambda pid, sig: None)  # pid stays alive
    monkeypatch.setattr(manager.time, "sleep", lambda s: None)

    manager.stop(cfg, tmp_path, grace_period=0.0)

    assert signal.SIGTERM in signals
    assert signal.SIGKILL in signals
    assert not (tmp_path / "headroom.json").exists()


def test_stop_reports_progress(tmp_path, monkeypatch) -> None:
    cfg = _proxy_cfg()
    _write_state_file(tmp_path, "headroom", pid=5678)
    monkeypatch.setattr(manager, "_terminate_group", lambda pid, grace: None)
    messages: list = []

    manager.stop(cfg, tmp_path, progress=messages.append)

    assert any("stopping headroom (pid 5678)" in m for m in messages)


def test_stop_with_non_int_pid_skips_signal_and_removes_state(tmp_path, monkeypatch) -> None:
    payload = {"name": "headroom", "pid": "not-an-int", "port": 8787}
    (tmp_path / "headroom.json").write_text(json.dumps(payload), encoding="utf-8")
    killpg_calls: list = []
    monkeypatch.setattr(manager.os, "killpg", lambda pid, sig: killpg_calls.append((pid, sig)))

    manager.stop(_proxy_cfg(), tmp_path)

    assert killpg_calls == []
    assert not (tmp_path / "headroom.json").exists()


# --- small private helpers ---------------------------------------------------


def test_read_state_returns_none_for_malformed_json(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(manager.detect, "is_installed", lambda c: True)
    (tmp_path / "headroom.json").write_text("{not json", encoding="utf-8")

    status = manager.status(_proxy_cfg(), tmp_path)

    assert status.running is False
    assert manager._read_state(tmp_path, "headroom") is None


def test_await_health_sleeps_then_succeeds(tmp_path, monkeypatch) -> None:
    cfg = _proxy_cfg()
    monkeypatch.setattr(manager.subprocess, "Popen", _make_popen([]))
    monkeypatch.setattr(manager.detect, "is_installed", lambda c: True)
    monkeypatch.setattr(manager.time, "sleep", lambda s: None)
    health_results = iter([False, True])
    monkeypatch.setattr(manager, "health_check", lambda url, timeout=1.0: next(health_results))

    status = manager.start(
        cfg, "http://127.0.0.1:8080/v1", tmp_path, timeout=5.0, poll_interval=0.5
    )

    assert status.healthy is True


# --- named_inferencer_base_url ------------------------------------------------


def test_named_upstream_resolved_from_running_engine(monkeypatch) -> None:
    statuses = {
        "dflash": _engine_status("dflash", running=True, port=8000),
        "mlx-lm": _engine_status("mlx-lm", running=True, port=8080),
    }
    monkeypatch.setattr(manager, "status_all", lambda configs, sd: statuses)

    upstream = manager.named_inferencer_base_url("mlx-lm", {}, "/state")

    assert upstream == "http://127.0.0.1:8080/v1"


def test_named_upstream_refuses_unknown_engine(monkeypatch) -> None:
    statuses = {"mlx-lm": _engine_status("mlx-lm", running=True)}
    monkeypatch.setattr(manager, "status_all", lambda configs, sd: statuses)

    with pytest.raises(OptimizerError) as exc:
        manager.named_inferencer_base_url("ghost", {}, "/state")

    assert "ghost" in str(exc.value)
    assert "mlx-lm" in str(exc.value)


def test_named_upstream_refuses_stopped_engine(monkeypatch) -> None:
    statuses = {"mlx-lm": _engine_status("mlx-lm", running=False)}
    monkeypatch.setattr(manager, "status_all", lambda configs, sd: statuses)

    with pytest.raises(OptimizerError) as exc:
        manager.named_inferencer_base_url("mlx-lm", {}, "/state")

    assert "not running" in str(exc.value)
