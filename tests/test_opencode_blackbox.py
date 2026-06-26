"""Tests for Task A scoring: extract, compile, and behaviourally test the Go (10.2-001).

The compile/run tests skip-guard when no ``go`` toolchain is installed; the
extraction and fixture-logic tests run everywhere.
"""

from __future__ import annotations

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
    text = fixtures.load_fixture()
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
