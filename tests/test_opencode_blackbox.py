"""Tests for Task A scoring: extract, compile, and behaviourally test the Go (10.2-001).

The compile/run tests skip-guard when no ``go`` toolchain is installed; the
extraction and fixture-logic tests run everywhere.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from local_code_bench.opencode import blackbox, fixtures
from local_code_bench.opencode.blackbox import BUILD_FAIL, score_task_a
from local_code_bench.opencode.extract import extract_go_code

requires_go = pytest.mark.skipif(not blackbox.go_available(), reason="go toolchain not installed")

REFERENCE_GO = (
    Path(__file__).resolve().parents[1] / "src/local_code_bench/opencode/reference/classifier.go"
)

# A tiny self-contained fixture used by the behavioural tests (independent of the
# shipped sample log so the assertions stay obvious).
SAMPLE_FIXTURE = (
    "boot INFO started\n"
    "disk WARN almost full\n"
    "db ERROR connection refused\n"
    "trace DEBUG noise\n"
    "kernel FATAL panic\n"
)


def _wrap(go_source: str, *, tag: str = "go", preamble: str = "") -> str:
    return f"{preamble}```{tag}\n{go_source}\n```\n"


# --------------------------------------------------------------------------- #
# extract_go_code — no toolchain needed
# --------------------------------------------------------------------------- #


def test_extract_go_from_tagged_fence() -> None:
    code = extract_go_code(_wrap("package main\n\nfunc main() {}"))
    assert code == "package main\n\nfunc main() {}"


def test_extract_go_tolerates_preamble() -> None:
    response = _wrap(
        "package main\n\nfunc main() {}",
        preamble="Sure! Here is the program you asked for:\n\n",
    )
    assert extract_go_code(response) == "package main\n\nfunc main() {}"


def test_extract_go_from_untagged_fence_with_package_main() -> None:
    response = "```\npackage main\nfunc main() {}\n```"
    assert extract_go_code(response) == "package main\nfunc main() {}"


def test_extract_go_picks_the_go_block_over_prose_block() -> None:
    response = "```\njust some shell output\n```\nand the code:\n```go\npackage main\n```"
    assert extract_go_code(response) == "package main"


def test_extract_go_returns_empty_when_no_code() -> None:
    assert extract_go_code("I cannot help with that.") == ""
    assert extract_go_code("```\nrandom text, no program\n```") == ""


def test_extract_go_falls_back_to_func_block_without_package_main() -> None:
    # No ``package main`` anywhere, but a fenced block that still looks like Go.
    response = "```\nfunc helper() int { return 1 }\n```"
    assert extract_go_code(response) == "func helper() int { return 1 }"


# --------------------------------------------------------------------------- #
# fixtures — severity rules as the single source of truth
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("plain ERROR here", "error"),
        ("a FATAL crash", "error"),
        ("ERROR wins over WARN and INFO", "error"),  # first match wins
        ("only WARN", "warn"),
        ("WARN before INFO", "warn"),
        ("just INFO", "info"),
        ("nothing notable", "unknown"),
        ("lowercase error is not a keyword", "unknown"),  # case-sensitive
    ],
)
def test_classify_line_rules(line: str, expected: str) -> None:
    assert fixtures.classify_line(line) == expected


def test_expected_counts_and_levels_for_sample() -> None:
    assert fixtures.expected_counts(SAMPLE_FIXTURE) == {
        "error": 2,
        "warn": 1,
        "info": 1,
        "unknown": 1,
    }
    assert fixtures.expected_line_levels(SAMPLE_FIXTURE) == {
        1: "info",
        2: "warn",
        3: "error",
        4: "unknown",
        5: "error",
    }


def test_expected_filter_returns_matching_lines_in_order() -> None:
    assert fixtures.expected_filter(SAMPLE_FIXTURE, "error") == [
        "db ERROR connection refused",
        "kernel FATAL panic",
    ]


def test_shipped_fixture_is_version_controlled_and_classifiable() -> None:
    text = fixtures.DEFAULT_FIXTURE_PATH.read_text(encoding="utf-8")
    assert text.strip(), "shipped fixture must not be empty"
    counts = fixtures.expected_counts(text)
    assert sum(counts.values()) == len(fixtures.fixture_lines(text))


# --------------------------------------------------------------------------- #
# score_task_a — non-compiling / no-code paths need no toolchain
# --------------------------------------------------------------------------- #


def test_no_code_scores_zero_and_flags_build_fail() -> None:
    result = score_task_a("I won't write any Go today.")
    assert result.compiled is False
    assert result.score == 0.0
    assert result.tests_passed == 0
    assert result.tests_total == blackbox.SUITE_SIZE
    assert result.flag == BUILD_FAIL
    assert result.extracted_code == ""


# --------------------------------------------------------------------------- #
# Compile + behavioural suite — requires a go toolchain
# --------------------------------------------------------------------------- #


@requires_go
def test_reference_compiles_and_passes_full_suite() -> None:
    response = _wrap(REFERENCE_GO.read_text(encoding="utf-8"))
    result = score_task_a(response)
    assert result.compiled is True
    assert result.flag is None
    assert result.score == 1.0
    assert result.tests_passed == result.tests_total == blackbox.SUITE_SIZE
    assert all(check.passed for check in result.checks), [(c.name, c.detail) for c in result.checks]


@requires_go
def test_reference_passes_against_custom_fixture(tmp_path: Path) -> None:
    fixture = tmp_path / "sample.log"
    fixture.write_text(SAMPLE_FIXTURE, encoding="utf-8")
    response = _wrap(REFERENCE_GO.read_text(encoding="utf-8"))
    result = score_task_a(response, fixture_path=fixture)
    assert result.score == 1.0


@requires_go
def test_non_compiling_go_scores_zero_and_flags_build_fail() -> None:
    response = _wrap("package main\n\nfunc main() { this is not valid go }")
    result = score_task_a(response)
    assert result.compiled is False
    assert result.score == 0.0
    assert result.flag == BUILD_FAIL
    assert result.checks == ()
    assert result.build_output  # build diagnostics are captured


# Compiles and gets the exit codes right, but never prints output: the two
# exit-code checks pass while counts/json/filter fail — a true partial score.
PARTIAL_GO = (
    "package main\n\n"
    'import "os"\n\n'
    "func main() {\n"
    "\targs := os.Args[1:]\n"
    "\tif len(args) == 0 {\n"
    "\t\tos.Exit(2)\n"
    "\t}\n"
    "\tfile := args[len(args)-1]\n"
    "\tif _, err := os.Stat(file); err != nil {\n"
    "\t\tos.Exit(1)\n"
    "\t}\n"
    "}\n"
)


@requires_go
def test_compiles_but_misbehaves_scores_partial(tmp_path: Path) -> None:
    fixture = tmp_path / "sample.log"
    fixture.write_text(SAMPLE_FIXTURE, encoding="utf-8")
    result = score_task_a(_wrap(PARTIAL_GO), fixture_path=fixture)
    assert result.compiled is True
    assert result.flag is None  # compiled, so NOT a BUILD_FAIL despite a low score
    assert 0.0 < result.score < 1.0
    assert result.tests_passed == 2  # exit_missing_file + exit_bad_args
    assert result.tests_total == blackbox.SUITE_SIZE
    passed_names = {check.name for check in result.checks if check.passed}
    assert passed_names == {"exit_missing_file", "exit_bad_args"}


# --------------------------------------------------------------------------- #
# Toolchain-independent build / run / score coverage (fakes + mocks)
#
# A real ``go`` toolchain is not assumed in CI, so the build/run/score code
# paths are exercised with a *fake* classifier binary — a tiny executable that
# faithfully mirrors the reference classifier's observable behaviour — plus
# targeted mocks of ``subprocess`` and the module's run helper. This keeps the
# harness logic under test independent of any live backend, per the project's
# testing strategy.
# --------------------------------------------------------------------------- #


# Each fake-binary mode produces specific observable behaviour, letting one
# executable drive every black-box check (and its failure branches).
_FAKE_BINARY_TEMPLATE = '''#!{python}
import json
import sys
from pathlib import Path

MODE = {mode!r}
LEVELS = ("error", "warn", "info", "unknown")


def classify(line):
    if "ERROR" in line or "FATAL" in line:
        return "error"
    if "WARN" in line:
        return "warn"
    if "INFO" in line:
        return "info"
    return "unknown"


args = sys.argv[1:]
json_mode = False
filter_level = ""
target = None
i = 0
while i < len(args):
    a = args[i]
    if a == "--json":
        json_mode = True
    elif a == "--filter":
        i += 1
        filter_level = args[i]
    elif a.startswith("-"):
        sys.exit(2)
    else:
        target = a
    i += 1

if MODE == "silent":
    # Compiles and parses args but never opens the file or prints anything.
    sys.exit(0)

if target is None:
    sys.exit(2)
try:
    text = Path(target).read_text(encoding="utf-8")
except OSError:
    sys.exit(1)

lines = text.splitlines()
levels = [classify(line) for line in lines]
counts = {{lvl: 0 for lvl in LEVELS}}
for lvl in levels:
    counts[lvl] += 1

if json_mode:
    if MODE == "json_linemap":
        print(json.dumps({{str(n): lv for n, lv in enumerate(levels, 1)}}))
    elif MODE == "json_linemap_wrong":
        print(json.dumps({{"1": "unknown", "2": "unknown"}}))
    elif MODE == "json_list":
        print("[]")
    elif MODE == "json_badkeys":
        print(json.dumps({{"nope": 1}}))
    elif MODE == "json_count_wrong":
        print(json.dumps({{"error": 999, "warn": 0, "info": 0, "unknown": 0}}))
    else:
        print(json.dumps(counts))
elif filter_level:
    for line, lvl in zip(lines, levels):
        if lvl == filter_level:
            print(line)
else:
    for lvl in LEVELS:
        print(f"{{lvl}}: {{counts[lvl]}}")
'''


def _make_fake_binary(tmp_path: Path, mode: str = "faithful") -> Path:
    """Write an executable fake classifier with the given behaviour ``mode``."""
    binary = tmp_path / f"fake-classify-{mode}"
    binary.write_text(_FAKE_BINARY_TEMPLATE.format(python=sys.executable, mode=mode))
    binary.chmod(0o755)
    return binary


@pytest.fixture
def sample_fixture(tmp_path: Path) -> Path:
    fixture = tmp_path / "sample.log"
    fixture.write_text(SAMPLE_FIXTURE, encoding="utf-8")
    return fixture


# --- _build_env -------------------------------------------------------------


def test_build_env_is_offline_and_deterministic() -> None:
    env = blackbox._build_env()
    assert env["GO111MODULE"] == "on"
    assert env["GOFLAGS"] == "-mod=mod"
    assert env["GOTOOLCHAIN"] == "local"
    assert env["CGO_ENABLED"] == "0"
    # Inherits the ambient environment rather than replacing it.
    assert set(os.environ).issubset(env)


# --- build_go (subprocess mocked; no go toolchain needed) -------------------


def test_build_go_success_writes_module_and_returns_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(cmd, **kwargs):
        out = Path(cmd[cmd.index("-o") + 1])
        out.write_text("#!fake binary\n")  # stand in for the compiled artifact
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(blackbox.subprocess, "run", fake_run)
    ok, binary, output = blackbox.build_go(
        "package main\nfunc main() {}", tmp_path, timeout_seconds=5
    )
    assert ok is True
    assert binary is not None and binary.exists()
    assert (tmp_path / "go.mod").read_text(encoding="utf-8").startswith("module classifier")
    assert (tmp_path / "main.go").read_text(encoding="utf-8") == "package main\nfunc main() {}"
    assert isinstance(output, str)


def test_build_go_compile_failure_returns_no_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="syntax error near }")

    monkeypatch.setattr(blackbox.subprocess, "run", fake_run)
    ok, binary, output = blackbox.build_go("package main", tmp_path, timeout_seconds=5)
    assert ok is False
    assert binary is None
    assert "syntax error" in output


def test_build_go_timeout_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 1, output="partial-out", stderr="partial-err")

    monkeypatch.setattr(blackbox.subprocess, "run", fake_run)
    ok, binary, output = blackbox.build_go("package main", tmp_path, timeout_seconds=1)
    assert ok is False
    assert binary is None
    assert "build timed out" in output
    assert "partial-out" in output and "partial-err" in output


# --- run_blackbox_suite against a fake binary -------------------------------


def test_faithful_fake_binary_passes_full_suite(
    tmp_path: Path, sample_fixture: Path
) -> None:
    binary = _make_fake_binary(tmp_path, "faithful")
    checks = blackbox.run_blackbox_suite(binary, sample_fixture, timeout_seconds=10)
    assert len(checks) == blackbox.SUITE_SIZE
    assert all(check.passed for check in checks), [(c.name, c.detail) for c in checks]
    assert {c.name for c in checks} == {
        "counts",
        "json",
        "filter",
        "exit_missing_file",
        "exit_bad_args",
    }


def test_silent_fake_binary_fails_every_check(
    tmp_path: Path, sample_fixture: Path
) -> None:
    # Exits 0 and prints nothing: counts/json/filter mismatch and the two
    # exit-code checks get the wrong code — every check fails, none error out.
    binary = _make_fake_binary(tmp_path, "silent")
    checks = blackbox.run_blackbox_suite(binary, sample_fixture, timeout_seconds=10)
    assert not any(check.passed for check in checks)


def test_suite_checks_report_timeout(
    tmp_path: Path, sample_fixture: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args, **kwargs):
        raise subprocess.TimeoutExpired("fake", 1)

    monkeypatch.setattr(blackbox, "_run_binary", boom)
    binary = _make_fake_binary(tmp_path, "faithful")
    checks = blackbox.run_blackbox_suite(binary, sample_fixture, timeout_seconds=1)
    assert not any(check.passed for check in checks)
    assert all(check.detail == "timed out" for check in checks)


# --- _check_json shape matrix ----------------------------------------------


@pytest.mark.parametrize(
    ("mode", "passed", "detail_contains"),
    [
        ("faithful", True, "count table"),
        ("json_linemap", True, "line map"),
        ("json_count_wrong", False, "count table mismatches"),
        ("json_linemap_wrong", False, "line map mismatches"),
        ("json_list", False, "non-empty object"),
        ("json_badkeys", False, "neither levels nor line numbers"),
        ("silent", False, "not valid JSON"),
    ],
)
def test_check_json_accepts_or_rejects_each_shape(
    tmp_path: Path, sample_fixture: Path, mode: str, passed: bool, detail_contains: str
) -> None:
    binary = _make_fake_binary(tmp_path, mode)
    check = blackbox._check_json(binary, sample_fixture, SAMPLE_FIXTURE, timeout=10)
    assert check.name == "json"
    assert check.passed is passed
    assert detail_contains in check.detail


def test_check_json_reports_nonzero_exit(
    tmp_path: Path, sample_fixture: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(binary, args, *, timeout_seconds):
        return subprocess.CompletedProcess(args, 3, stdout="", stderr="boom")

    monkeypatch.setattr(blackbox, "_run_binary", fake_run)
    binary = _make_fake_binary(tmp_path, "faithful")
    check = blackbox._check_json(binary, sample_fixture, SAMPLE_FIXTURE, timeout=10)
    assert check.passed is False
    assert check.detail == "exit 3"


def test_check_counts_and_filter_report_nonzero_exit(
    tmp_path: Path, sample_fixture: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(binary, args, *, timeout_seconds):
        return subprocess.CompletedProcess(args, 5, stdout="", stderr="boom")

    monkeypatch.setattr(blackbox, "_run_binary", fake_run)
    binary = _make_fake_binary(tmp_path, "faithful")
    counts = blackbox._check_counts(binary, sample_fixture, SAMPLE_FIXTURE, timeout=10)
    filt = blackbox._check_filter(binary, sample_fixture, SAMPLE_FIXTURE, timeout=10)
    assert counts.passed is False and counts.detail == "exit 5"
    assert filt.passed is False and filt.detail == "exit 5"


# --- score_task_a build/score paths (build_go mocked) -----------------------


def test_score_task_a_full_success_with_fake_binary(
    tmp_path: Path, sample_fixture: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = _make_fake_binary(tmp_path, "faithful")

    def fake_build(code, workdir, *, timeout_seconds):
        return True, binary, "compiled"

    monkeypatch.setattr(blackbox, "build_go", fake_build)
    response = _wrap("package main\nfunc main() {}")
    result = score_task_a(response, fixture_path=sample_fixture)
    assert result.compiled is True
    assert result.flag is None
    assert result.score == 1.0
    assert result.tests_passed == result.tests_total == blackbox.SUITE_SIZE
    assert result.extracted_code == "package main\nfunc main() {}"
    assert result.build_output == "compiled"


def test_score_task_a_build_failure_flags_build_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_build(code, workdir, *, timeout_seconds):
        return False, None, "boom: compile error"

    monkeypatch.setattr(blackbox, "build_go", fake_build)
    result = score_task_a(_wrap("package main\nfunc main() {}"))
    assert result.compiled is False
    assert result.score == 0.0
    assert result.flag == BUILD_FAIL
    assert result.tests_passed == 0
    assert result.tests_total == blackbox.SUITE_SIZE
    assert result.checks == ()
    assert result.build_output == "boom: compile error"
    assert result.extracted_code == "package main\nfunc main() {}"
