"""Sandboxed execution for generated benchmark code."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path


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
        try:
            completed = subprocess.run(
                [sys.executable, "-I", str(runner)],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
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

        ROOT = {str(root)!r}
        _real_open = builtins.open
        _real_path_open = pathlib.Path.open

        def _assert_safe_write(path, mode):
            if any(flag in mode for flag in ("w", "a", "x", "+")):
                resolved = os.path.realpath(os.path.join(os.getcwd(), os.fspath(path)))
                if not resolved.startswith(os.path.realpath(ROOT) + os.sep):
                    raise PermissionError("write outside sandbox denied")

        def guarded_open(file, mode="r", *args, **kwargs):
            _assert_safe_write(file, mode)
            return _real_open(file, mode, *args, **kwargs)

        def guarded_path_open(self, mode="r", *args, **kwargs):
            _assert_safe_write(self, mode)
            return _real_path_open(self, mode, *args, **kwargs)

        def blocked_socket(*args, **kwargs):
            raise PermissionError("network disabled in sandbox")

        builtins.open = guarded_open
        pathlib.Path.open = guarded_path_open
        socket.socket = blocked_socket

        namespace = {{}}
        exec({code!r}, namespace)
        exec({test_code!r}, namespace)
        """
    )


def _reason_from_stderr(stderr: str) -> str:
    last = stderr.strip().splitlines()[-1:] or ["failed"]
    return last[0]
