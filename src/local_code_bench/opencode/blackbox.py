"""Task A scoring: extract, compile, and behaviourally test the generated Go.

Coding ability is judged by a compiler and a test binary, never by eye. The
model's Go is extracted from its response, ``go build`` decides the *compiles*
pass/fail first, and a fixed black-box suite then exercises the compiled binary
against a known fixture — asserting only observable behaviour (stdout, exit
codes), never internal structure. A non-compiling submission scores 0 and is
flagged ``BUILD_FAIL``, mirroring the article's hard-fail rows.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from shutil import which

from local_code_bench.opencode import fixtures
from local_code_bench.opencode.extract import extract_go_code

BUILD_FAIL = "BUILD_FAIL"

# Module name used for the throwaway build (kept offline; stdlib-only).
_GO_MODULE = "classifier"
_BINARY_NAME = "classify"
_MISSING_FILE = "does-not-exist.log"


@dataclass(frozen=True)
class Check:
    """One observable-behaviour assertion against the compiled binary."""

    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class TaskAResult:
    """Outcome of scoring a single Task A submission."""

    compiled: bool
    score: float
    tests_passed: int
    tests_total: int
    checks: tuple[Check, ...]
    flag: str | None
    extracted_code: str
    build_output: str


def go_available() -> bool:
    """True when a ``go`` toolchain is on PATH."""
    return which("go") is not None


def _build_env() -> dict[str, str]:
    env = dict(os.environ)
    # Offline, deterministic, self-contained build (stdlib only, no toolchain fetch).
    env["GO111MODULE"] = "on"
    env["GOFLAGS"] = "-mod=mod"
    env["GOTOOLCHAIN"] = "local"
    env["CGO_ENABLED"] = "0"
    return env


def build_go(
    source: str, workdir: Path, *, timeout_seconds: float
) -> tuple[bool, Path | None, str]:
    """Compile ``source`` in ``workdir``. Returns (ok, binary_path, combined_output)."""
    (workdir / "go.mod").write_text(f"module {_GO_MODULE}\n\ngo 1.21\n", encoding="utf-8")
    (workdir / "main.go").write_text(source, encoding="utf-8")
    cache = workdir / ".gocache"
    gopath = workdir / ".gopath"
    cache.mkdir(exist_ok=True)
    gopath.mkdir(exist_ok=True)
    env = _build_env()
    env["GOCACHE"] = str(cache)
    env["GOPATH"] = str(gopath)
    binary = workdir / _BINARY_NAME
    try:
        completed = subprocess.run(
            ["go", "build", "-o", str(binary), "."],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return False, None, f"build timed out\n{exc.stdout or ''}{exc.stderr or ''}"
    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode == 0 and binary.exists():
        return True, binary, output
    return False, None, output


def _run_binary(
    binary: Path, args: list[str], *, timeout_seconds: float
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(binary), *args],
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )


def _check_counts(binary: Path, fixture: Path, text: str, *, timeout: float) -> Check:
    expected = fixtures.expected_counts(text)
    try:
        result = _run_binary(binary, [str(fixture)], timeout_seconds=timeout)
    except subprocess.TimeoutExpired:
        return Check("counts", False, "timed out")
    if result.returncode != 0:
        return Check("counts", False, f"exit {result.returncode}")
    found: dict[str, int] = {}
    for level in fixtures.LEVELS:
        match = re.search(rf"\b{level}\b[^0-9]*(\d+)", result.stdout, re.IGNORECASE)
        if match:
            found[level] = int(match.group(1))
    if found == expected:
        return Check("counts", True, "counts match")
    return Check("counts", False, f"expected {expected}, got {found}")


def _check_json(binary: Path, fixture: Path, text: str, *, timeout: float) -> Check:
    """Validate ``--json`` as schema-correct structured output.

    The Task A prompt is agnostic about the exact JSON shape, so accept either of
    the two faithful structurings: a level -> count map (the JSON count table) or a
    1-based line-number -> level map (which bridges into Task B). Either must match
    the fixture's ground truth.
    """
    expected_counts = fixtures.expected_counts(text)
    expected_levels = fixtures.expected_line_levels(text)
    try:
        result = _run_binary(binary, ["--json", str(fixture)], timeout_seconds=timeout)
    except subprocess.TimeoutExpired:
        return Check("json", False, "timed out")
    if result.returncode != 0:
        return Check("json", False, f"exit {result.returncode}")
    try:
        parsed = json.loads(result.stdout)
    except (ValueError, TypeError):
        return Check("json", False, "output is not valid JSON")
    if not isinstance(parsed, dict) or not parsed:
        return Check("json", False, "JSON is not a non-empty object")

    keys = list(parsed.keys())
    if all(str(key).lower() in fixtures.LEVELS for key in keys):
        counts = {str(key).lower(): value for key, value in parsed.items()}
        if counts == expected_counts:
            return Check("json", True, "schema-correct count table matches ground truth")
        return Check("json", False, "JSON count table mismatches ground truth")

    try:
        line_levels = {int(key): str(value).lower() for key, value in parsed.items()}
    except (ValueError, TypeError):
        return Check("json", False, "JSON keys are neither levels nor line numbers")
    if line_levels == expected_levels:
        return Check("json", True, "schema-correct line map matches ground truth")
    return Check("json", False, "JSON line map mismatches ground truth")


def _check_filter(binary: Path, fixture: Path, text: str, *, timeout: float) -> Check:
    expected = fixtures.expected_filter(text, "error")
    try:
        result = _run_binary(binary, ["--filter", "error", str(fixture)], timeout_seconds=timeout)
    except subprocess.TimeoutExpired:
        return Check("filter", False, "timed out")
    if result.returncode != 0:
        return Check("filter", False, f"exit {result.returncode}")
    got = [line for line in result.stdout.splitlines() if line.strip()]
    if [line.strip() for line in got] == [line.strip() for line in expected]:
        return Check("filter", True, "only error lines returned")
    return Check("filter", False, "filtered output mismatch")


def _check_exit_missing(binary: Path, *, timeout: float) -> Check:
    try:
        result = _run_binary(binary, [_MISSING_FILE], timeout_seconds=timeout)
    except subprocess.TimeoutExpired:
        return Check("exit_missing_file", False, "timed out")
    if result.returncode == 1:
        return Check("exit_missing_file", True, "exit 1 on missing file")
    return Check("exit_missing_file", False, f"expected exit 1, got {result.returncode}")


def _check_exit_bad_args(binary: Path, *, timeout: float) -> Check:
    try:
        result = _run_binary(binary, [], timeout_seconds=timeout)
    except subprocess.TimeoutExpired:
        return Check("exit_bad_args", False, "timed out")
    if result.returncode == 2:
        return Check("exit_bad_args", True, "exit 2 on bad args")
    return Check("exit_bad_args", False, f"expected exit 2, got {result.returncode}")


def run_blackbox_suite(binary: Path, fixture: Path, *, timeout_seconds: float) -> tuple[Check, ...]:
    """Run the fixed black-box suite against a compiled binary. Observable behaviour only."""
    text = fixture.read_text(encoding="utf-8")
    return (
        _check_counts(binary, fixture, text, timeout=timeout_seconds),
        _check_json(binary, fixture, text, timeout=timeout_seconds),
        _check_filter(binary, fixture, text, timeout=timeout_seconds),
        _check_exit_missing(binary, timeout=timeout_seconds),
        _check_exit_bad_args(binary, timeout=timeout_seconds),
    )


# Total number of behavioural checks; a build failure scores 0 / this denominator.
SUITE_SIZE = 5


def score_task_a(
    response: str,
    *,
    fixture_path: Path | None = None,
    build_timeout_seconds: float = 60.0,
    run_timeout_seconds: float = 10.0,
) -> TaskAResult:
    """Extract, compile, and behaviourally test the Go in ``response``.

    Score = ``tests_passed / tests_total``. A submission that yields no Go source
    or fails to compile scores 0 and is flagged ``BUILD_FAIL``.
    """
    fixture = fixture_path or fixtures.DEFAULT_FIXTURE_PATH
    code = extract_go_code(response)
    if not code:
        return TaskAResult(
            compiled=False,
            score=0.0,
            tests_passed=0,
            tests_total=SUITE_SIZE,
            checks=(),
            flag=BUILD_FAIL,
            extracted_code="",
            build_output="no Go source extracted",
        )

    with tempfile.TemporaryDirectory(prefix="opencode-taska-") as tmp:
        workdir = Path(tmp)
        compiled, binary, build_output = build_go(
            code, workdir, timeout_seconds=build_timeout_seconds
        )
        if not compiled or binary is None:
            return TaskAResult(
                compiled=False,
                score=0.0,
                tests_passed=0,
                tests_total=SUITE_SIZE,
                checks=(),
                flag=BUILD_FAIL,
                extracted_code=code,
                build_output=build_output,
            )
        checks = run_blackbox_suite(binary, fixture, timeout_seconds=run_timeout_seconds)

    passed = sum(1 for check in checks if check.passed)
    total = len(checks)
    return TaskAResult(
        compiled=True,
        score=passed / total if total else 0.0,
        tests_passed=passed,
        tests_total=total,
        checks=checks,
        flag=None,
        extracted_code=code,
        build_output=build_output,
    )
