"""Tests for the exit-with-parent watchdog (Story 18.1-002).

The macOS app supervises `bench dashboard` as a child process. If the app is
force-quit it cannot clean up, so the dashboard watches its parent pid and
terminates itself the moment it is orphaned — no live process, no orphan.
"""

from __future__ import annotations

import signal
import threading

import pytest

from local_code_bench.parent_watch import start_parent_watch, watch_parent


def _ppid_sequence(values: list[int]):
    """A fake ``getppid`` returning each value in turn, then the last forever."""

    state = {"index": 0}

    def getppid() -> int:
        value = values[min(state["index"], len(values) - 1)]
        state["index"] += 1
        return value

    return getppid


def test_watch_parent_triggers_when_ppid_changes() -> None:
    triggered = []
    sleeps: list[float] = []

    watch_parent(
        lambda: triggered.append(True),
        getppid=_ppid_sequence([500, 500, 500, 1]),
        poll_interval=0.25,
        sleep=sleeps.append,
    )

    assert triggered == [True]
    # two unchanged polls after the initial read, each separated by a sleep
    assert sleeps == [0.25, 0.25]


def test_watch_parent_triggers_on_any_reparent_not_just_launchd() -> None:
    triggered = []

    watch_parent(
        lambda: triggered.append(True),
        getppid=_ppid_sequence([500, 777]),
        poll_interval=0.1,
        sleep=lambda _: None,
    )

    assert triggered == [True]


def test_watch_parent_triggers_immediately_when_already_orphaned() -> None:
    """A parent pid of 1 (launchd) at startup means the parent is already gone."""

    triggered = []
    sleeps: list[float] = []

    watch_parent(
        lambda: triggered.append(True),
        getppid=_ppid_sequence([1]),
        poll_interval=0.1,
        sleep=sleeps.append,
    )

    assert triggered == [True]
    assert sleeps == []


def test_watch_parent_rejects_nonpositive_interval() -> None:
    with pytest.raises(ValueError):
        watch_parent(lambda: None, poll_interval=0)


def test_start_parent_watch_runs_in_daemon_thread() -> None:
    orphaned = threading.Event()

    thread = start_parent_watch(
        on_orphaned=orphaned.set,
        getppid=_ppid_sequence([500, 1]),
        poll_interval=0.01,
    )

    assert thread.daemon
    assert orphaned.wait(timeout=5.0)
    thread.join(timeout=5.0)
    assert not thread.is_alive()


def test_start_parent_watch_default_action_sends_sigterm_to_self(monkeypatch) -> None:
    """SIGTERM to self reuses dashboard_lifecycle's graceful-shutdown path."""

    sent = threading.Event()
    calls: dict = {}

    def fake_kill(pid: int, signum: int) -> None:
        calls.update(pid=pid, signum=signum)
        sent.set()

    monkeypatch.setattr("local_code_bench.parent_watch.os.kill", fake_kill)
    monkeypatch.setattr("local_code_bench.parent_watch.os.getpid", lambda: 4242)

    thread = start_parent_watch(getppid=_ppid_sequence([500, 1]), poll_interval=0.01)

    assert sent.wait(timeout=5.0)
    thread.join(timeout=5.0)
    assert calls == {"pid": 4242, "signum": signal.SIGTERM}
