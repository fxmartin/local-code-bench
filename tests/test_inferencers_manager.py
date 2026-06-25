from __future__ import annotations

import json
import signal
from pathlib import Path

import pytest

from local_code_bench.config import InferencerConfig
from local_code_bench.inferencers import manager
from local_code_bench.inferencers.manager import InferencerError, InferencerStatus


def _server_cfg(name: str = "dflash", port: int = 8000) -> InferencerConfig:
    return InferencerConfig(
        name=name,
        lifecycle="server",
        detect_kind="binary",
        detect_target="dflash",
        port=port,
        health_url="http://127.0.0.1:{port}/v1/models",
        start=("dflash", "serve"),
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


def _make_popen(created: list, *, write: str = ""):
    """Build a fake `subprocess.Popen` that records instances and seeds the log."""

    class _FakeProc:
        def __init__(self, command, **kwargs) -> None:
            self.command = command
            self.kwargs = kwargs
            self.pid = 4321
            self.terminated = False
            self.killed = False
            stdout = kwargs.get("stdout")
            if stdout is not None and write:
                stdout.write(write)
                stdout.flush()
            created.append(self)

        def poll(self):
            return None

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

        def wait(self, timeout=None):
            return 0

    return _FakeProc


def _write_state_file(state_dir: Path, name: str, *, pid: int, port: int) -> None:
    payload = {
        "name": name,
        "pid": pid,
        "port": port,
        "started_at": "2026-06-25T00:00:00+00:00",
        "command": ["dflash", "serve"],
        "health_url": f"http://127.0.0.1:{port}/v1/models",
    }
    (Path(state_dir) / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")


# --- health_check -----------------------------------------------------------


def test_health_check_true_on_200(monkeypatch) -> None:
    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(manager.urllib.request, "urlopen", lambda url, timeout=1.0: _Resp())

    assert manager.health_check("http://127.0.0.1:8000/v1/models") is True


def test_health_check_false_on_urlerror(monkeypatch) -> None:
    def boom(url, timeout=1.0):
        raise manager.urllib.error.URLError("connection refused")

    monkeypatch.setattr(manager.urllib.request, "urlopen", boom)

    assert manager.health_check("http://127.0.0.1:8000/v1/models") is False


def test_health_check_false_on_os_error(monkeypatch) -> None:
    def boom(url, timeout=1.0):
        raise TimeoutError("timed out")

    monkeypatch.setattr(manager.urllib.request, "urlopen", boom)

    assert manager.health_check("http://127.0.0.1:8000/v1/models") is False


# --- start: success ---------------------------------------------------------


def test_start_spawns_polls_health_and_reports_running(tmp_path, monkeypatch) -> None:
    cfg = _server_cfg()
    created: list = []
    monkeypatch.setattr(manager.subprocess, "Popen", _make_popen(created))
    monkeypatch.setattr(manager, "health_check", lambda url, timeout=1.0: True)
    monkeypatch.setattr(manager.detect, "is_installed", lambda c: True)

    status = manager.start(cfg, tmp_path, timeout=5.0, poll_interval=0.0)

    assert status.running is True
    assert status.healthy is True
    assert status.pid == 4321
    assert status.port == 8000
    assert created and created[0].kwargs["start_new_session"] is True
    state = json.loads((tmp_path / "dflash.json").read_text(encoding="utf-8"))
    assert state["pid"] == 4321
    assert state["port"] == 8000
    assert state["command"] == ["dflash", "serve"]
    assert state["health_url"] == "http://127.0.0.1:8000/v1/models"


# --- start: failure ---------------------------------------------------------


def test_start_failure_kills_process_cleans_state_and_raises(tmp_path, monkeypatch) -> None:
    cfg = _server_cfg()
    created: list = []
    monkeypatch.setattr(
        manager.subprocess, "Popen", _make_popen(created, write="boot\nfatal: port already in use\n")
    )
    monkeypatch.setattr(manager, "health_check", lambda url, timeout=1.0: False)
    monkeypatch.setattr(manager.detect, "is_installed", lambda c: True)
    monkeypatch.setattr(manager.time, "sleep", lambda s: None)
    killpg_calls: list = []
    monkeypatch.setattr(manager.os, "killpg", lambda pid, sig: killpg_calls.append((pid, sig)))
    monkeypatch.setattr(
        manager.os, "kill", lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError())
    )

    with pytest.raises(InferencerError) as exc:
        manager.start(cfg, tmp_path, timeout=0.0, poll_interval=0.0)

    assert "fatal: port already in use" in str(exc.value)
    assert killpg_calls == [(4321, signal.SIGTERM)]
    assert not (tmp_path / "dflash.json").exists()


def test_start_missing_executable_raises(tmp_path, monkeypatch) -> None:
    cfg = _server_cfg()

    def boom(command, **kwargs):
        raise FileNotFoundError(command[0])

    monkeypatch.setattr(manager.subprocess, "Popen", boom)
    monkeypatch.setattr(manager.detect, "is_installed", lambda c: True)

    with pytest.raises(InferencerError) as exc:
        manager.start(cfg, tmp_path)

    assert "dflash" in str(exc.value)
    assert not (tmp_path / "dflash.json").exists()


# --- start/stop: GUI apps refused -------------------------------------------


def test_start_app_refuses_without_spawning(tmp_path, monkeypatch) -> None:
    cfg = _app_cfg()
    spawned: list = []
    monkeypatch.setattr(manager.subprocess, "Popen", lambda *a, **k: spawned.append(1))

    with pytest.raises(InferencerError) as exc:
        manager.start(cfg, tmp_path)

    assert "UI" in str(exc.value)
    assert spawned == []


def test_stop_app_refuses(tmp_path, monkeypatch) -> None:
    cfg = _app_cfg()
    killpg_calls: list = []
    monkeypatch.setattr(manager.os, "killpg", lambda pid, sig: killpg_calls.append((pid, sig)))

    with pytest.raises(InferencerError) as exc:
        manager.stop(cfg, tmp_path)

    assert "UI" in str(exc.value)
    assert killpg_calls == []


# --- status -----------------------------------------------------------------


def test_status_no_state_is_not_running(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(manager.detect, "is_installed", lambda c: True)

    status = manager.status(_server_cfg(), tmp_path)

    assert status.running is False
    assert status.pid is None
    assert status.installed is True


def test_status_live_pid_uses_persisted_pid_and_health(tmp_path, monkeypatch) -> None:
    cfg = _server_cfg()
    _write_state_file(tmp_path, "dflash", pid=4321, port=8000)
    monkeypatch.setattr(manager.detect, "is_installed", lambda c: True)
    monkeypatch.setattr(manager, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(manager, "health_check", lambda url, timeout=1.0: True)

    status = manager.status(cfg, tmp_path)

    assert status.running is True
    assert status.healthy is True
    assert status.pid == 4321


def test_status_dead_pid_is_cleaned_up(tmp_path, monkeypatch) -> None:
    cfg = _server_cfg()
    _write_state_file(tmp_path, "dflash", pid=9999, port=8000)
    monkeypatch.setattr(manager.detect, "is_installed", lambda c: True)
    monkeypatch.setattr(manager, "_pid_alive", lambda pid: False)

    status = manager.status(cfg, tmp_path)

    assert status.running is False
    assert status.pid is None
    assert not (tmp_path / "dflash.json").exists()


def test_status_app_reports_detect_and_health(tmp_path, monkeypatch) -> None:
    cfg = _app_cfg()
    monkeypatch.setattr(manager.detect, "is_installed", lambda c: True)
    monkeypatch.setattr(manager, "health_check", lambda url, timeout=1.0: True)

    status = manager.status(cfg, tmp_path)

    assert status.installed is True
    assert status.healthy is True
    assert status.lifecycle == "app"
    assert status.pid is None


def test_status_all_maps_each_name(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(manager.detect, "is_installed", lambda c: True)
    configs = {
        "dflash": _server_cfg("dflash", 8000),
        "turboquant": _server_cfg("turboquant", 8002),
    }

    result = manager.status_all(configs, tmp_path)

    assert set(result) == {"dflash", "turboquant"}
    assert all(isinstance(s, InferencerStatus) for s in result.values())


# --- stop -------------------------------------------------------------------


def test_stop_terminates_group_removes_state_and_is_idempotent(tmp_path, monkeypatch) -> None:
    cfg = _server_cfg()
    _write_state_file(tmp_path, "dflash", pid=4321, port=8000)
    killpg_calls: list = []
    monkeypatch.setattr(manager.os, "killpg", lambda pid, sig: killpg_calls.append((pid, sig)))
    monkeypatch.setattr(manager, "_pid_alive", lambda pid: False)

    manager.stop(cfg, tmp_path)

    assert killpg_calls == [(4321, signal.SIGTERM)]
    assert not (tmp_path / "dflash.json").exists()

    # A second stop with no state file is a no-op (no further signals).
    manager.stop(cfg, tmp_path)
    assert killpg_calls == [(4321, signal.SIGTERM)]


def test_stop_escalates_to_sigkill_after_grace(tmp_path, monkeypatch) -> None:
    cfg = _server_cfg()
    _write_state_file(tmp_path, "dflash", pid=4321, port=8000)
    signals: list = []
    monkeypatch.setattr(manager.os, "killpg", lambda pid, sig: signals.append(sig))
    monkeypatch.setattr(manager, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(manager.time, "sleep", lambda s: None)

    manager.stop(cfg, tmp_path, grace_period=0.0)

    assert signal.SIGTERM in signals
    assert signal.SIGKILL in signals
    assert not (tmp_path / "dflash.json").exists()
