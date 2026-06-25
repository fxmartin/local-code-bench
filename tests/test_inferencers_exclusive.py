"""Mutual-exclusion start used by the benchmark auto-start integration (08.5).

`start_exclusive` is the single place the timing-integrity invariant — exactly one
engine holds the GPU — is enforced. It is exercised here through patched
`status`/`start`/`stop`, so no subprocess or server is launched.
"""

from __future__ import annotations

import pytest

from local_code_bench.config import InferencerConfig
from local_code_bench.inferencers import manager
from local_code_bench.inferencers.manager import InferencerError, InferencerStatus


def _server_cfg(name: str, port: int) -> InferencerConfig:
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


def _status(cfg: InferencerConfig, *, running: bool) -> InferencerStatus:
    return InferencerStatus(
        name=cfg.name,
        installed=True,
        lifecycle=cfg.lifecycle,
        running=running,
        pid=4321 if running else None,
        port=cfg.port,
        healthy=running,
        detail="running" if running else "not running",
    )


def _patch_manager(monkeypatch, configs, running_names):
    """Stub status/start/stop and record the start/stop call order."""

    calls: list[str] = []

    def fake_status(cfg, _state_dir):
        return _status(cfg, running=cfg.name in running_names)

    def fake_start(cfg, _state_dir, **_kwargs):
        calls.append(f"start:{cfg.name}")
        return _status(cfg, running=True)

    def fake_stop(cfg, _state_dir, **_kwargs):
        calls.append(f"stop:{cfg.name}")

    monkeypatch.setattr(manager, "status", fake_status)
    monkeypatch.setattr(manager, "start", fake_start)
    monkeypatch.setattr(manager, "stop", fake_stop)
    return calls


def test_running_others_excludes_target(monkeypatch) -> None:
    target = _server_cfg("dflash", 8000)
    other = _server_cfg("turboquant", 8002)
    configs = {target.name: target, other.name: other}
    _patch_manager(monkeypatch, configs, running_names={"turboquant"})

    others = manager.running_others("dflash", configs, ".runtime")

    assert [s.name for s in others] == ["turboquant"]


def test_start_exclusive_with_no_others_starts_without_confirm(monkeypatch) -> None:
    target = _server_cfg("dflash", 8000)
    configs = {target.name: target}
    calls = _patch_manager(monkeypatch, configs, running_names=set())
    confirm_calls: list[object] = []

    result = manager.start_exclusive(
        target,
        configs,
        ".runtime",
        confirm=lambda others: confirm_calls.append(others) or True,
    )

    assert calls == ["start:dflash"]
    assert confirm_calls == []  # nothing to stop → no confirmation prompt
    assert result.name == "dflash"


def test_start_exclusive_declined_aborts_without_starting(monkeypatch) -> None:
    target = _server_cfg("dflash", 8000)
    other = _server_cfg("turboquant", 8002)
    configs = {target.name: target, other.name: other}
    calls = _patch_manager(monkeypatch, configs, running_names={"turboquant"})

    with pytest.raises(InferencerError, match="aborted"):
        manager.start_exclusive(target, configs, ".runtime", confirm=lambda _others: False)

    assert calls == []  # neither stopped nor started


def test_start_exclusive_accepted_stops_others_then_starts(monkeypatch) -> None:
    target = _server_cfg("dflash", 8000)
    other = _server_cfg("turboquant", 8002)
    configs = {target.name: target, other.name: other}
    calls = _patch_manager(monkeypatch, configs, running_names={"turboquant"})
    seen: list[list[str]] = []

    manager.start_exclusive(
        target,
        configs,
        ".runtime",
        confirm=lambda others: seen.append([s.name for s in others]) or True,
    )

    assert calls == ["stop:turboquant", "start:dflash"]
    assert seen == [["turboquant"]]  # confirm shown the engines it would stop


def test_start_exclusive_gui_blocks_without_force(monkeypatch) -> None:
    target = _server_cfg("dflash", 8000)
    gui = _app_cfg()
    configs = {target.name: target, gui.name: gui}
    calls = _patch_manager(monkeypatch, configs, running_names={"lm-studio"})

    with pytest.raises(InferencerError, match="GUI"):
        manager.start_exclusive(target, configs, ".runtime", confirm=lambda _o: True)

    assert calls == []


def test_start_exclusive_gui_allowed_with_force(monkeypatch) -> None:
    target = _server_cfg("dflash", 8000)
    gui = _app_cfg()
    configs = {target.name: target, gui.name: gui}
    calls = _patch_manager(monkeypatch, configs, running_names={"lm-studio"})

    manager.start_exclusive(
        target, configs, ".runtime", confirm=lambda _o: True, force=True
    )

    # GUI app is never stopped; only the target starts.
    assert calls == ["start:dflash"]
