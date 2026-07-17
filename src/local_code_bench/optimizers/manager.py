"""Lifecycle management for context-optimization proxies (Epic-13, 13.2-001).

Mirrors the Epic-08 inferencer manager pattern — `Popen(start_new_session=True)`,
one JSON state file plus a captured `<name>.log` per proxy under the state dir,
`urllib` health polling, and SIGTERM→SIGKILL process-group teardown — so a proxy
started in one CLI invocation can be inspected or stopped from another.

The one thing a proxy adds over an engine is chaining: a proxy must front a real
engine, so `start` takes the resolved `{upstream}` base URL and `start_chained`
derives it from the single active Epic-08 inferencer, refusing when none (or
more than one) is running. Stopping a proxy never touches its upstream engine.
"""

from __future__ import annotations

import json
import os  # noqa: F401 - re-exported for tests patching os.killpg/os.kill (shared module object)
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..config import (
    InferencerConfig,
    OptimizerConfig,
    resolve_health_url,
    resolve_optimizer_start,
)
from ..inferencers import detect
from ..inferencers.manager import (
    _log_tail,
    _pid_alive,
    _terminate_group,
    health_check,
    status_all,
)

# The active engine's OpenAI-compatible base URL, derived from its listen port
# (same shape the unified dashboard uses to talk to a managed inferencer).
_UPSTREAM_TEMPLATE = "http://127.0.0.1:{port}/v1"


class OptimizerError(RuntimeError):
    """Raised when starting, stopping, or chaining a proxy fails."""


@dataclass(frozen=True)
class OptimizerStatus:
    name: str
    installed: bool
    running: bool
    pid: int | None
    port: int
    upstream: str | None
    healthy: bool
    detail: str


def active_inferencer_base_url(
    configs: dict[str, InferencerConfig], state_dir: str | Path
) -> str:
    """Resolve the base URL of the single active inferencer.

    A proxy must front a real engine, so zero running engines is a refusal; more
    than one violates the Epic-08 one-active invariant and is refused too rather
    than guessing which engine to chain.
    """

    running = [st for st in status_all(configs, state_dir).values() if st.running]
    if not running:
        raise OptimizerError(
            "no active inferencer — a proxy must front a real engine; start one first"
        )
    if len(running) > 1:
        names = ", ".join(sorted(st.name for st in running))
        raise OptimizerError(
            f"multiple inferencers running ({names}) — exactly one engine must be "
            "active to chain a proxy"
        )
    return _UPSTREAM_TEMPLATE.format(port=running[0].port)


def status(cfg: OptimizerConfig, state_dir: str | Path) -> OptimizerStatus:
    """Report installed / running / healthy state for a single proxy.

    Liveness is the persisted PID plus a health probe; a dead PID is reported
    not-running and its stale state file is removed.
    """

    installed = detect.is_installed(cfg)
    health_url = resolve_health_url(cfg)

    state = _read_state(state_dir, cfg.name)
    if state is None:
        return OptimizerStatus(
            cfg.name, installed, False, None, cfg.port, None, False, "not running"
        )

    pid = state.get("pid")
    if not isinstance(pid, int) or not _pid_alive(pid):
        _remove_state(state_dir, cfg.name)
        return OptimizerStatus(
            cfg.name, installed, False, None, cfg.port, None, False, "stale state removed"
        )

    healthy = health_check(health_url)
    upstream = state.get("upstream") if isinstance(state.get("upstream"), str) else None
    detail = "running and healthy" if healthy else "process alive, not yet healthy"
    return OptimizerStatus(cfg.name, installed, True, pid, cfg.port, upstream, healthy, detail)


def start(
    cfg: OptimizerConfig,
    upstream: str,
    state_dir: str | Path,
    *,
    timeout: float = 30.0,
    poll_interval: float = 0.5,
    health_timeout: float = 1.0,
    grace_period: float = 5.0,
    progress: Callable[[str], None] | None = None,
) -> OptimizerStatus:
    """Spawn a proxy wired to `upstream`, poll its health, and report status.

    Raises `OptimizerError` if the proxy does not become healthy within
    `timeout` — in which case the spawned process group is killed, the captured
    log tail is attached, and no stale state file is left behind.
    """

    current = status(cfg, state_dir)
    if current.running and current.healthy:
        return current

    state_path = Path(state_dir)
    state_path.mkdir(parents=True, exist_ok=True)
    log_path = _log_path(state_path, cfg.name)
    command = list(resolve_optimizer_start(cfg, upstream))
    health_url = resolve_health_url(cfg)

    if progress is not None:
        progress(f"starting {cfg.name} -> {upstream}: {' '.join(command)}")

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
        raise OptimizerError(f"{cfg.name}: executable not found: {command[0]}") from exc
    finally:
        log_file.close()

    _write_state(
        state_path,
        cfg.name,
        pid=proc.pid,
        port=cfg.port,
        upstream=upstream,
        command=command,
        health_url=health_url,
    )

    if _await_health(proc, health_url, timeout, poll_interval, health_timeout):
        if progress is not None:
            progress(f"{cfg.name} is healthy on port {cfg.port} (upstream {upstream})")
        return OptimizerStatus(
            cfg.name, detect.is_installed(cfg), True, proc.pid, cfg.port, upstream, True,
            "started and healthy",
        )

    _terminate_group(proc.pid, grace_period)
    tail = _log_tail(log_path)
    _remove_state(state_path, cfg.name)
    raise OptimizerError(
        f"{cfg.name} did not become healthy within {timeout:g}s; log tail:\n{tail}"
    )


def start_chained(
    cfg: OptimizerConfig,
    inferencer_configs: dict[str, InferencerConfig],
    inferencer_state_dir: str | Path,
    state_dir: str | Path,
    **kwargs,
) -> OptimizerStatus:
    """Start `cfg` chained in front of the single active inferencer.

    Resolves `{upstream}` from the running engine's base URL before spawning;
    refuses (starting nothing) when no engine is active.
    """

    upstream = active_inferencer_base_url(inferencer_configs, inferencer_state_dir)
    return start(cfg, upstream, state_dir, **kwargs)


def stop(
    cfg: OptimizerConfig,
    state_dir: str | Path,
    *,
    grace_period: float = 5.0,
    progress: Callable[[str], None] | None = None,
) -> None:
    """Stop a running proxy gracefully (SIGTERM, then SIGKILL); a no-op if down.

    Only the proxy's own process group is signalled — the upstream inferencer is
    left untouched.
    """

    state = _read_state(state_dir, cfg.name)
    if state is None:
        return

    pid = state.get("pid")
    if isinstance(pid, int):
        if progress is not None:
            progress(f"stopping {cfg.name} (pid {pid})")
        _terminate_group(pid, grace_period)
    _remove_state(state_dir, cfg.name)


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
    upstream: str,
    command: list[str],
    health_url: str,
) -> None:
    state = {
        "name": name,
        "pid": pid,
        "port": port,
        "upstream": upstream,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "command": command,
        "health_url": health_url,
    }
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
