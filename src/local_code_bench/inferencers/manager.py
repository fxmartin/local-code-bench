"""Lifecycle management for headless inference servers.

Models the subprocess pattern in `power.py` (Popen → terminate → kill →
communicate-with-timeout, errors swallowed) but adds persistence: one JSON state
file per started server under the state dir plus a captured `<name>.log`, so a
server started in one CLI invocation can be inspected or stopped from another.

The timing-integrity invariant of the project — exactly one engine holds the GPU —
is enforced one layer up (Story 08.3); this module only knows how to start, stop,
and report a single engine. GUI (`app`-lifecycle) engines are detect-only: start
and stop refuse rather than spawning or killing anything.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..config import InferencerConfig, resolve_health_url
from ..engine_provenance import (
    EngineProvenance,
    EngineProvenanceError,
    capture_mlx_provenance,
)
from . import detect

_LOG_TAIL_LINES = 20
_GUI_REFUSAL = "{name} is a GUI app — start and stop it from its own UI, not the harness."


class InferencerError(RuntimeError):
    """Raised when starting or stopping an inference server fails."""


@dataclass(frozen=True)
class InferencerStatus:
    name: str
    installed: bool
    lifecycle: str
    running: bool
    pid: int | None
    port: int
    healthy: bool
    detail: str


def health_check(url: str, timeout: float = 1.0) -> bool:
    """Return True if `url` answers with a 2xx, swallowing connection errors."""

    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 300
    except urllib.error.URLError:
        return False
    except OSError:
        return False


def status(cfg: InferencerConfig, state_dir: str | Path) -> InferencerStatus:
    """Report installed / running / healthy state for a single engine.

    For `server` engines, liveness is the persisted PID plus a health probe; a dead
    PID is reported not-running and its stale state file is removed. `app` engines
    are detect-only: running tracks the health probe with no PID or state file.
    """

    installed = detect.is_installed(cfg)
    health_url = resolve_health_url(cfg)

    if cfg.lifecycle == "app":
        healthy = health_check(health_url)
        detail = "GUI app — managed from its own UI"
        return InferencerStatus(
            cfg.name, installed, cfg.lifecycle, healthy, None, cfg.port, healthy, detail
        )

    state = _read_state(state_dir, cfg.name)
    if state is None:
        return InferencerStatus(
            cfg.name, installed, cfg.lifecycle, False, None, cfg.port, False, "not running"
        )

    pid = state.get("pid")
    if not isinstance(pid, int) or not _pid_alive(pid):
        _remove_state(state_dir, cfg.name)
        return InferencerStatus(
            cfg.name, installed, cfg.lifecycle, False, None, cfg.port, False, "stale state removed"
        )

    healthy = health_check(health_url)
    detail = "running and healthy" if healthy else "process alive, not yet healthy"
    return InferencerStatus(
        cfg.name, installed, cfg.lifecycle, True, pid, cfg.port, healthy, detail
    )


def status_all(
    configs: dict[str, InferencerConfig], state_dir: str | Path
) -> dict[str, InferencerStatus]:
    """Map each engine name to its current status."""

    return {name: status(cfg, state_dir) for name, cfg in configs.items()}


def start(
    cfg: InferencerConfig,
    state_dir: str | Path,
    *,
    timeout: float = 30.0,
    poll_interval: float = 0.5,
    health_timeout: float = 1.0,
    grace_period: float = 5.0,
    progress: Callable[[str], None] | None = None,
) -> InferencerStatus:
    """Spawn a headless server, poll its health endpoint, and report status.

    Raises `InferencerError` for GUI engines (which must be managed from their own
    UI) and if the server does not become healthy within `timeout` — in which case
    the spawned process group is killed, the captured log tail is attached, and no
    stale state file is left behind.
    """

    if cfg.lifecycle == "app":
        raise InferencerError(_GUI_REFUSAL.format(name=cfg.name))

    current = status(cfg, state_dir)
    if current.running and current.healthy:
        return current

    state_path = Path(state_dir)
    state_path.mkdir(parents=True, exist_ok=True)
    log_path = _log_path(state_path, cfg.name)
    command = list(cfg.start or ())
    health_url = resolve_health_url(cfg)
    engine_provenance: EngineProvenance | None = None
    if cfg.name == "mlx-lm":
        try:
            engine_provenance = capture_mlx_provenance(command)
        except EngineProvenanceError as exc:
            raise InferencerError(f"{cfg.name}: {exc}") from exc

    if progress is not None:
        progress(f"starting {cfg.name}: {' '.join(command)}")

    log_file = log_path.open("w", encoding="utf-8")
    try:
        proc = subprocess.Popen(  # noqa: S603 - command comes from a trusted local config
            command,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise InferencerError(f"{cfg.name}: executable not found: {command[0]}") from exc
    finally:
        log_file.close()

    _write_state(
        state_path,
        cfg.name,
        pid=proc.pid,
        port=cfg.port,
        command=command,
        health_url=health_url,
        engine_provenance=engine_provenance,
    )

    if _await_health(proc, health_url, timeout, poll_interval, health_timeout):
        if progress is not None:
            progress(f"{cfg.name} is healthy on port {cfg.port}")
        return InferencerStatus(
            cfg.name, detect.is_installed(cfg), cfg.lifecycle, True, proc.pid, cfg.port, True,
            "started and healthy",
        )

    _terminate_group(proc.pid, grace_period)
    tail = _log_tail(log_path)
    _remove_state(state_path, cfg.name)
    raise InferencerError(
        f"{cfg.name} did not become healthy within {timeout:g}s; log tail:\n{tail}"
    )


def stop(
    cfg: InferencerConfig,
    state_dir: str | Path,
    *,
    grace_period: float = 5.0,
    progress: Callable[[str], None] | None = None,
) -> None:
    """Stop a running server gracefully (SIGTERM, then SIGKILL); a no-op if down.

    Raises `InferencerError` for GUI engines, which the harness never force-quits.
    """

    if cfg.lifecycle == "app":
        raise InferencerError(_GUI_REFUSAL.format(name=cfg.name))

    state = _read_state(state_dir, cfg.name)
    if state is None:
        return

    pid = state.get("pid")
    if isinstance(pid, int):
        if progress is not None:
            progress(f"stopping {cfg.name} (pid {pid})")
        _terminate_group(pid, grace_period)
    _remove_state(state_dir, cfg.name)


def managed_engine_provenance(
    cfg: InferencerConfig,
    state_dir: str | Path,
) -> EngineProvenance:
    """Return provenance persisted for the healthy managed process.

    A legacy or manually started MLX-LM process is deliberately insufficient: its
    installed package may not be the package serving the benchmark endpoint.
    """

    current = status(cfg, state_dir)
    if not current.running or not current.healthy:
        raise InferencerError(
            f"{cfg.name}: no healthy managed process; restart it with --manage-inferencers"
        )
    state = _read_state(state_dir, cfg.name)
    value = state.get("engine") if state is not None else None
    try:
        provenance = EngineProvenance.from_dict(value)
    except EngineProvenanceError as exc:
        raise InferencerError(
            f"{cfg.name}: managed state has no exact engine provenance; "
            "restart it with --manage-inferencers"
        ) from exc
    if provenance.name != cfg.name:
        raise InferencerError(
            f"{cfg.name}: managed provenance identifies {provenance.name!r}; "
            "restart it with --manage-inferencers"
        )
    return provenance


def running_others(
    target: str, configs: dict[str, InferencerConfig], state_dir: str | Path
) -> list[InferencerStatus]:
    """Return the currently-running engines other than `target`.

    Both `server` and `app` engines count: a running GUI app holds the GPU just as
    a headless server does, so the mutual-exclusion rule must see it.
    """

    return [
        st
        for name, cfg in configs.items()
        if name != target and (st := status(cfg, state_dir)).running
    ]


def start_exclusive(
    target_cfg: InferencerConfig,
    configs: dict[str, InferencerConfig],
    state_dir: str | Path,
    *,
    confirm: Callable[[list[InferencerStatus]], bool],
    force: bool = False,
    progress: Callable[[str], None] | None = None,
) -> InferencerStatus:
    """Start `target_cfg` as the only running engine, enforcing one-active.

    This is the single place the timing-integrity invariant lives, regardless of
    entry surface (CLI, web): the `confirm` callback is injected so each surface
    supplies its own yes/no prompt. A running GUI app blocks the start (the harness
    never force-quits one) unless `force` is set; the headless servers that would be
    stopped are passed to `confirm`, and only on acceptance are they stopped before
    the target starts. Raises `InferencerError` for a GUI target, a blocking GUI, or
    a declined confirmation — in every case nothing is started.
    """

    if target_cfg.lifecycle == "app":
        raise InferencerError(_GUI_REFUSAL.format(name=target_cfg.name))

    others = running_others(target_cfg.name, configs, state_dir)
    gui_blockers = [st for st in others if configs[st.name].lifecycle == "app"]
    if gui_blockers and not force:
        names = ", ".join(st.name for st in gui_blockers)
        raise InferencerError(
            f"{names} is running — quit it from its own UI before starting "
            f"{target_cfg.name}, or pass --force to start past it."
        )

    to_stop = [st for st in others if configs[st.name].lifecycle == "server"]
    if to_stop:
        if not confirm(to_stop):
            stopping = ", ".join(st.name for st in to_stop)
            raise InferencerError(
                f"aborted: {target_cfg.name} not started ({stopping} left running)"
            )
        for st in to_stop:
            stop(configs[st.name], state_dir, progress=progress)

    return start(target_cfg, state_dir, progress=progress)


def _await_health(
    proc: subprocess.Popen,
    health_url: str,
    timeout: float,
    poll_interval: float,
    health_timeout: float,
) -> bool:
    """Poll the health endpoint until it answers, the process dies, or time runs out."""

    deadline = time.monotonic() + timeout
    while True:
        if health_check(health_url, timeout=health_timeout):
            return True
        if proc.poll() is not None:
            return False
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll_interval)


def _terminate_group(pid: int, grace_period: float) -> None:
    """SIGTERM the process group, then SIGKILL it if still alive after the grace period."""

    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError:
        return
    deadline = time.monotonic() + grace_period
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return
        time.sleep(0.05)
    if _pid_alive(pid):
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            pass


def _pid_alive(pid: int) -> bool:
    """Return True if a process with `pid` exists, via the signal-0 liveness probe."""

    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _state_path(state_dir: str | Path, name: str) -> Path:
    return Path(state_dir) / f"{name}.json"


def _log_path(state_dir: str | Path, name: str) -> Path:
    return Path(state_dir) / f"{name}.log"


def _write_state(
    state_dir: str | Path,
    name: str,
    *,
    pid: int,
    port: int,
    command: list[str],
    health_url: str,
    engine_provenance: EngineProvenance | None = None,
) -> None:
    state = {
        "name": name,
        "pid": pid,
        "port": port,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "command": command,
        "health_url": health_url,
    }
    if engine_provenance is not None:
        state["engine"] = engine_provenance.as_dict()
    _state_path(state_dir, name).write_text(json.dumps(state, indent=2), encoding="utf-8")


def _read_state(state_dir: str | Path, name: str) -> dict | None:
    path = _state_path(state_dir, name)
    if not path.exists():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _remove_state(state_dir: str | Path, name: str) -> None:
    _state_path(state_dir, name).unlink(missing_ok=True)


def _log_tail(path: Path, lines: int = _LOG_TAIL_LINES) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])
