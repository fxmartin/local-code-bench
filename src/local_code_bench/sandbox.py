"""Sandboxed execution for generated benchmark code."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path
from shutil import which


@dataclass(frozen=True)
class SandboxResult:
    passed: bool
    reason: str
    stdout: str
    stderr: str
    returncode: int | None
    timed_out: bool = False


def run_in_sandbox(code: str, test_code: str, *, timeout_seconds: float = 5.0) -> SandboxResult:
    with tempfile.TemporaryDirectory(prefix="local-code-bench-") as tmp:
        root = Path(tmp)
        runner = root / "runner.py"
        runner.write_text(_runner_source(root, code, test_code), encoding="utf-8")
        command = _sandbox_command(root, runner)
        try:
            completed = subprocess.run(
                command,
                cwd=root,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env={"PATH": "/usr/bin:/bin", "PYTHONNOUSERSITE": "1"},
                preexec_fn=_limit_resources if sys.platform != "win32" else None,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return SandboxResult(
                passed=False,
                reason="timeout",
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                returncode=None,
                timed_out=True,
            )
    if completed.returncode == 0:
        return SandboxResult(True, "passed", completed.stdout, completed.stderr, completed.returncode)
    reason = _reason_from_stderr(completed.stderr)
    return SandboxResult(False, reason, completed.stdout, completed.stderr, completed.returncode)


def _runner_source(root: Path, code: str, test_code: str) -> str:
    return textwrap.dedent(
        f"""
        import builtins
        import os
        import pathlib
        import socket
        import subprocess
        import sys

        ROOT = {str(root)!r}
        _real_open = builtins.open
        _real_path_open = pathlib.Path.open

        def _inside_root(path):
            resolved = os.path.realpath(os.path.join(os.getcwd(), os.fspath(path)))
            return os.path.commonpath([os.path.realpath(ROOT), resolved]) == os.path.realpath(ROOT)

        def _assert_safe_write(path, mode):
            if any(flag in mode for flag in ("w", "a", "x", "+")):
                if not _inside_root(path):
                    raise PermissionError("write outside sandbox denied")

        def guarded_open(file, mode="r", *args, **kwargs):
            _assert_safe_write(file, mode)
            return _real_open(file, mode, *args, **kwargs)

        def guarded_path_open(self, mode="r", *args, **kwargs):
            _assert_safe_write(self, mode)
            return _real_path_open(self, mode, *args, **kwargs)

        def blocked_socket(*args, **kwargs):
            raise PermissionError("network disabled in sandbox")

        def blocked_popen(*args, **kwargs):
            raise PermissionError("subprocess disabled in sandboxed code")

        def audit(event, args):
            if event.startswith("socket."):
                raise PermissionError("network disabled in sandbox")
            if event in {{"subprocess.Popen", "os.system", "os.posix_spawn", "os.fork", "os.forkpty"}}:
                raise PermissionError("process spawning disabled in sandbox")
            if event == "open" and len(args) >= 2:
                path, mode = args[0], args[1]
                if isinstance(path, (str, bytes, os.PathLike)):
                    _assert_safe_write(path, mode or "r")

        sys.addaudithook(audit)
        builtins.open = guarded_open
        pathlib.Path.open = guarded_path_open
        socket.socket = blocked_socket
        subprocess.Popen = blocked_popen

        # Program-shaped candidates guard their CLI entry with __main__; give the
        # namespace a name so the guard is skipped instead of raising NameError.
        namespace = {{"__name__": "__bench__"}}
        exec({code!r}, namespace)
        exec({test_code!r}, namespace)
        """
    )


def _reason_from_stderr(stderr: str) -> str:
    last = stderr.strip().splitlines()[-1:] or ["failed"]
    return last[0]


def _sandbox_command(root: Path, runner: Path) -> list[str]:
    base = [sys.executable, "-I", str(runner)]
    if sys.platform != "darwin" or which("sandbox-exec") is None:
        return base
    profile = root / "sandbox.sb"
    root_literal = str(root).replace("\\", "\\\\").replace('"', '\\"')
    profile.write_text(
        textwrap.dedent(
            f"""
            (version 1)
            (allow default)
            (deny network*)
            (deny file-write*)
            (allow file-write* (subpath "{root_literal}"))
            """
        ).strip(),
        encoding="utf-8",
    )
    return ["sandbox-exec", "-f", str(profile), *base]


def _limit_resources() -> None:
    try:
        import resource

        memory_bytes = 512 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
        resource.setrlimit(resource.RLIMIT_FSIZE, (10 * 1024 * 1024, 10 * 1024 * 1024))
    except Exception:
        return
