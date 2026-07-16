from __future__ import annotations

import json
import os
import signal

import pytest

from local_code_bench.dashboard_lifecycle import (
    DashboardLifecycleError,
    dashboard_process,
    dashboard_status,
    stop_dashboard,
)


def test_dashboard_process_records_identity_and_cleans_state(tmp_path) -> None:
    state_file = tmp_path / "dashboard.json"
    identity = "Mon Jul 20 10:00:00 2026 python /venv/bin/bench dashboard"

    with dashboard_process(
        state_file,
        host="127.0.0.1",
        port=8765,
        identity_for_pid=lambda _pid: identity,
    ):
        payload = json.loads(state_file.read_text())
        assert payload == {
            "host": "127.0.0.1",
            "identity": identity,
            "pid": os.getpid(),
            "port": 8765,
        }
        status = dashboard_status(
            state_file,
            identity_for_pid=lambda _pid: identity,
        )
        assert status.running is True
        assert status.pid == os.getpid()
        assert status.url == "http://127.0.0.1:8765"

    assert not state_file.exists()


def test_status_removes_stale_dead_pid_state(tmp_path) -> None:
    state_file = tmp_path / "dashboard.json"
    state_file.write_text(
        json.dumps(
            {
                "pid": 999999,
                "identity": "python bench dashboard",
                "host": "127.0.0.1",
                "port": 8765,
            }
        )
    )

    status = dashboard_status(state_file, identity_for_pid=lambda _pid: None)

    assert status.running is False
    assert status.detail == "stale dashboard state removed"
    assert not state_file.exists()


def test_stop_refuses_pid_reuse_without_signaling_foreign_process(tmp_path) -> None:
    state_file = tmp_path / "dashboard.json"
    state_file.write_text(
        json.dumps(
            {
                "pid": 42,
                "identity": "Mon Jul 20 python /venv/bin/bench dashboard",
                "host": "127.0.0.1",
                "port": 8765,
            }
        )
    )
    signals: list[tuple[int, int]] = []

    status = stop_dashboard(
        state_file,
        identity_for_pid=lambda _pid: "Mon Jul 21 python unrelated-service",
        send_signal=lambda pid, sig: signals.append((pid, sig)),
    )

    assert status.running is False
    assert status.detail == "stale dashboard state removed"
    assert signals == []
    assert not state_file.exists()


def test_stop_sends_sigterm_to_owned_dashboard_and_waits_for_exit(tmp_path) -> None:
    state_file = tmp_path / "dashboard.json"
    identity = "Mon Jul 20 python /venv/bin/bench dashboard"
    state_file.write_text(
        json.dumps(
            {
                "pid": 42,
                "identity": identity,
                "host": "127.0.0.1",
                "port": 8765,
            }
        )
    )
    running = True
    signals: list[tuple[int, int]] = []

    def send(pid: int, sig: int) -> None:
        nonlocal running
        signals.append((pid, sig))
        running = False

    status = stop_dashboard(
        state_file,
        identity_for_pid=lambda _pid: identity if running else None,
        send_signal=send,
        sleep=lambda _seconds: None,
    )

    assert signals == [(42, signal.SIGTERM)]
    assert status.running is False
    assert status.pid == 42
    assert status.detail == "dashboard stopped"
    assert not state_file.exists()


def test_dashboard_process_refuses_second_owned_instance(tmp_path) -> None:
    state_file = tmp_path / "dashboard.json"
    identity = "Mon Jul 20 python /venv/bin/bench dashboard"
    state_file.write_text(
        json.dumps(
            {
                "pid": 42,
                "identity": identity,
                "host": "127.0.0.1",
                "port": 8765,
            }
        )
    )

    with pytest.raises(DashboardLifecycleError, match="already running pid=42"):
        with dashboard_process(
            state_file,
            host="127.0.0.1",
            port=9000,
            identity_for_pid=lambda _pid: identity,
        ):
            pytest.fail("second dashboard must not start")
