"""PID-safe lifecycle control for the unified dashboard process."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from types import FrameType

IdentityForPid = Callable[[int], str | None]


class DashboardLifecycleError(RuntimeError):
    """Raised when dashboard lifecycle state cannot be used safely."""


class DashboardTermination(Exception):
    """Internal graceful-exit signal raised when SIGTERM is received."""


@dataclass(frozen=True)
class DashboardStatus:
    running: bool
    pid: int | None
    host: str | None
    port: int | None
    detail: str

    @property
    def url(self) -> str | None:
        if self.host is None or self.port is None:
            return None
        return f"http://{self.host}:{self.port}"


@dataclass(frozen=True)
class _DashboardState:
    pid: int
    identity: str
    host: str
    port: int


def dashboard_status(
    state_file: str | Path,
    *,
    identity_for_pid: IdentityForPid = lambda pid: _process_identity(pid),
) -> DashboardStatus:
    """Report a running dashboard or clean stale/PID-reused state safely."""

    status, _state = _inspect_state(Path(state_file), identity_for_pid)
    return status


def stop_dashboard(
    state_file: str | Path,
    *,
    timeout: float = 5.0,
    identity_for_pid: IdentityForPid = lambda pid: _process_identity(pid),
    send_signal: Callable[[int, int], None] = os.kill,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> DashboardStatus:
    """Gracefully stop only the exact dashboard process recorded in state."""

    path = Path(state_file)
    status, state = _inspect_state(path, identity_for_pid)
    if not status.running or state is None:
        return status
    try:
        send_signal(state.pid, signal.SIGTERM)
    except (OSError, PermissionError) as exc:
        raise DashboardLifecycleError(
            f"could not stop dashboard pid {state.pid}: {exc}"
        ) from exc

    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if identity_for_pid(state.pid) != state.identity:
            _remove_state(path)
            return DashboardStatus(
                running=False,
                pid=state.pid,
                host=state.host,
                port=state.port,
                detail="dashboard stopped",
            )
        sleep(0.05)
    raise DashboardLifecycleError(
        f"dashboard pid {state.pid} did not stop within {timeout:g}s"
    )


@contextmanager
def dashboard_process(
    state_file: str | Path,
    *,
    host: str,
    port: int,
    identity_for_pid: IdentityForPid = lambda pid: _process_identity(pid),
) -> Iterator[None]:
    """Claim dashboard state for this process and clean it on every normal exit."""

    path = Path(state_file)
    existing, _state = _inspect_state(path, identity_for_pid)
    if existing.running:
        raise DashboardLifecycleError(
            f"dashboard already running pid={existing.pid} url={existing.url}"
        )

    pid = os.getpid()
    identity = identity_for_pid(pid)
    if identity is None or not _is_dashboard_identity(identity):
        raise DashboardLifecycleError("could not verify current dashboard process identity")
    state = _DashboardState(pid=pid, identity=identity, host=host, port=port)
    _write_state(path, state)

    previous_handler: signal.Handlers | None = None
    if threading.current_thread() is threading.main_thread():
        previous_handler = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, _handle_sigterm)
    try:
        yield
    finally:
        if previous_handler is not None:
            signal.signal(signal.SIGTERM, previous_handler)
        _remove_if_owned(path, state)


def _inspect_state(
    path: Path,
    identity_for_pid: IdentityForPid,
) -> tuple[DashboardStatus, _DashboardState | None]:
    if not path.exists():
        return DashboardStatus(False, None, None, None, "dashboard is not running"), None
    try:
        state = _read_state(path)
    except DashboardLifecycleError:
        _remove_state(path)
        return DashboardStatus(False, None, None, None, "stale dashboard state removed"), None

    current_identity = identity_for_pid(state.pid)
    if (
        current_identity is None
        or current_identity != state.identity
        or not _is_dashboard_identity(current_identity)
    ):
        _remove_state(path)
        return (
            DashboardStatus(
                False,
                state.pid,
                state.host,
                state.port,
                "stale dashboard state removed",
            ),
            None,
        )
    return DashboardStatus(True, state.pid, state.host, state.port, "running"), state


def _read_state(path: Path) -> _DashboardState:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DashboardLifecycleError(f"invalid dashboard state: {path}") from exc
    if not isinstance(payload, dict):
        raise DashboardLifecycleError(f"invalid dashboard state: {path}")
    pid = payload.get("pid")
    identity = payload.get("identity")
    host = payload.get("host")
    port = payload.get("port")
    if (
        not isinstance(pid, int)
        or isinstance(pid, bool)
        or pid <= 0
        or not isinstance(identity, str)
        or not identity
        or not isinstance(host, str)
        or not host
        or not isinstance(port, int)
        or isinstance(port, bool)
        or not 1 <= port <= 65535
    ):
        raise DashboardLifecycleError(f"invalid dashboard state: {path}")
    return _DashboardState(pid=pid, identity=identity, host=host, port=port)


def _write_state(path: Path, state: _DashboardState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(asdict(state), handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def _remove_if_owned(path: Path, expected: _DashboardState) -> None:
    try:
        current = _read_state(path)
    except (DashboardLifecycleError, FileNotFoundError):
        return
    if current == expected:
        _remove_state(path)


def _remove_state(path: Path) -> None:
    path.unlink(missing_ok=True)


def _process_identity(pid: int) -> str | None:
    try:
        completed = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart=", "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
            timeout=1.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    identity = completed.stdout.strip()
    return identity if completed.returncode == 0 and identity else None


def _is_dashboard_identity(identity: str) -> bool:
    lowered = identity.lower()
    return " dashboard" in lowered and ("bench" in lowered or "local_code_bench" in lowered)


def _handle_sigterm(_signum: int, _frame: FrameType | None) -> None:
    raise DashboardTermination
