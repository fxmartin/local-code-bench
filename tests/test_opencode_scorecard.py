"""Tests for Story 10.4-001: comparable scorecard with provenance note."""

from __future__ import annotations

from pathlib import Path

from local_code_bench.opencode.blackbox import BUILD_FAIL, TaskAResult
from local_code_bench.opencode.scorecard import (
    SCORECARD_COLUMNS,
    ScorecardRow,
    append_run,
    build_row,
    parse_bit_width,
    provenance_note,
    read_runs,
    render_markdown,
    row_passed,
)
from local_code_bench.opencode.taskb import PARSE_FAIL, TaskBScore


def _task_a(*, compiled: bool, passed: int, total: int = 5) -> TaskAResult:
    return TaskAResult(
        compiled=compiled,
        score=passed / total if total else 0.0,
        tests_passed=passed,
        tests_total=total,
        checks=(),
        flag=None if compiled else BUILD_FAIL,
        extracted_code="package main",
        build_output="",
    )


def _task_b(*, error_rate: float, coverage: float = 1.0, collisions: int = 0, flag=None) -> TaskBScore:
    expected = 4
    return TaskBScore(
        error_rate=error_rate,
        coverage=coverage,
        collisions=collisions,
        expected=expected,
        present=round(coverage * expected),
        mismatches=round(error_rate * expected),
        flag=flag,
    )


def _row(
    *,
    model: str = "local",
    quant: str | None = "IQ3_XXS",
    provider: str | None = "unsloth",
    mode: str = "default",
    compiled: bool = True,
    passed: int = 5,
    total: int = 5,
    error_rate: float = 0.0,
    coverage: float = 1.0,
    collisions: int = 0,
    task_b_flag=None,
    tokens_per_second: float | None = 42.0,
    wall_clock_seconds: float | None = 3.2,
) -> ScorecardRow:
    return build_row(
        model_name=model,
        quant=quant,
        provider=provider,
        mode=mode,
        task_a=_task_a(compiled=compiled, passed=passed, total=total),
        task_b=_task_b(error_rate=error_rate, coverage=coverage, collisions=collisions, flag=task_b_flag),
        tokens_per_second=tokens_per_second,
        wall_clock_seconds=wall_clock_seconds,
    )


# --- build_row -------------------------------------------------------------


def test_build_row_carries_provenance_and_both_task_scores() -> None:
    row = build_row(
        model_name="qwen3-30b",
        quant="IQ3_XXS",
        provider="unsloth",
        mode="thinking",
        task_a=_task_a(compiled=True, passed=4, total=5),
        task_b=_task_b(error_rate=0.05, coverage=0.9, collisions=1),
        tokens_per_second=37.5,
        wall_clock_seconds=12.0,
    )
    assert row.model == "qwen3-30b"
    assert row.quant == "IQ3_XXS"
    assert row.provider == "unsloth"
    assert row.mode == "thinking"
    assert row.compiled is True
    assert row.tests_passed == 4
    assert row.tests_total == 5
    assert row.error_rate == 0.05
    assert row.coverage == 0.9
    assert row.collisions == 1
    assert row.tokens_per_second == 37.5
    assert row.wall_clock_seconds == 12.0


def test_build_row_propagates_task_flags() -> None:
    row = build_row(
        model_name="m",
        quant=None,
        provider=None,
        mode="default",
        task_a=_task_a(compiled=False, passed=0, total=5),
        task_b=_task_b(error_rate=1.0, coverage=0.0, flag=PARSE_FAIL),
        tokens_per_second=None,
        wall_clock_seconds=None,
    )
    assert row.task_a_flag == BUILD_FAIL
    assert row.task_b_flag == PARSE_FAIL


# --- row_passed ------------------------------------------------------------


def test_row_passed_requires_compile_and_full_suite() -> None:
    assert row_passed(_row(compiled=True, passed=5, total=5)) is True
    assert row_passed(_row(compiled=True, passed=4, total=5)) is False
    assert row_passed(_row(compiled=False, passed=0, total=5)) is False


# --- parse_bit_width -------------------------------------------------------


def test_parse_bit_width_extracts_leading_integer() -> None:
    assert parse_bit_width("IQ3_XXS") == "3"
    assert parse_bit_width("Q4_K_M") == "4"
    assert parse_bit_width("Q8_0") == "8"
    assert parse_bit_width("MLX-4bit") == "4"
    assert parse_bit_width("BF16") == "16"


def test_parse_bit_width_none_when_no_digits_or_missing() -> None:
    assert parse_bit_width(None) is None
    assert parse_bit_width("") is None
    assert parse_bit_width("full") is None


# --- append_run / read_runs ------------------------------------------------


def test_append_run_writes_header_then_rows(tmp_path: Path) -> None:
    csv_path = tmp_path / "scorecard.csv"
    append_run(csv_path, _row(model="a"))
    append_run(csv_path, _row(model="b"))

    text = csv_path.read_text(encoding="utf-8")
    # Header written exactly once, both rows present.
    assert text.count(",".join(SCORECARD_COLUMNS)) == 1
    rows = read_runs(csv_path)
    assert [r.model for r in rows] == ["a", "b"]


def test_read_runs_round_trips_types(tmp_path: Path) -> None:
    csv_path = tmp_path / "scorecard.csv"
    original = _row(
        model="m",
        quant=None,
        provider=None,
        compiled=False,
        passed=0,
        error_rate=1.0,
        coverage=0.0,
        task_b_flag=PARSE_FAIL,
        tokens_per_second=None,
        wall_clock_seconds=None,
    )
    append_run(csv_path, original)
    (restored,) = read_runs(csv_path)
    assert restored == original


def test_append_run_also_writes_jsonl_provenance(tmp_path: Path) -> None:
    csv_path = tmp_path / "scorecard.csv"
    jsonl_path = tmp_path / "scorecard.jsonl"
    append_run(csv_path, _row(model="a"), jsonl_path=jsonl_path)
    assert jsonl_path.exists()
    assert '"model":"a"' in jsonl_path.read_text(encoding="utf-8")


def test_read_runs_missing_file_is_empty(tmp_path: Path) -> None:
    assert read_runs(tmp_path / "nope.csv") == []


# --- render_markdown -------------------------------------------------------


def test_render_markdown_has_all_required_columns() -> None:
    table = render_markdown([_row()])
    for header in ("Model", "Quant", "Provider", "Mode", "Task A", "err", "Coverage", "Collisions"):
        assert header in table
    assert "tok/s" in table
    assert "Wall" in table


def test_render_markdown_shows_task_a_build_and_tests() -> None:
    table = render_markdown([_row(compiled=True, passed=5, total=5)])
    assert "5/5" in table
    failing = render_markdown([_row(compiled=False, passed=0, total=5)])
    assert "0/5" in failing
    assert BUILD_FAIL in failing


def test_render_markdown_sorts_passing_first_then_error_rate() -> None:
    rows = [
        _row(model="fail-low-err", compiled=False, passed=0, error_rate=0.10),
        _row(model="pass-high-err", compiled=True, passed=5, error_rate=0.40),
        _row(model="pass-low-err", compiled=True, passed=5, error_rate=0.05),
    ]
    table = render_markdown(rows)
    order = [line for line in table.splitlines() if line.startswith("| ") and "Model" not in line]
    # Passing rows first (ascending error rate among them), then failing rows.
    assert order[0].split("|")[1].strip() == "pass-low-err"
    assert order[1].split("|")[1].strip() == "pass-high-err"
    assert order[2].split("|")[1].strip() == "fail-low-err"


def test_render_markdown_marks_parse_fail_and_missing_metrics() -> None:
    table = render_markdown(
        [_row(error_rate=1.0, coverage=0.0, task_b_flag=PARSE_FAIL, tokens_per_second=None, wall_clock_seconds=None)]
    )
    assert PARSE_FAIL in table


# --- provenance_note -------------------------------------------------------


def test_provenance_note_surfaces_same_model_bitwidth_different_provider() -> None:
    rows = [
        _row(model="qwen3", quant="IQ3_XXS", provider="unsloth", error_rate=0.05),
        _row(model="qwen3", quant="IQ3_XXS", provider="bartowski", error_rate=1.0),
    ]
    note = provenance_note(rows)
    assert "qwen3" in note
    assert "unsloth" in note and "bartowski" in note
    # Reports the delta between the two providers (5% vs 100% => ~95 points).
    assert "95" in note


def test_provenance_note_ignores_single_provider_groups() -> None:
    rows = [
        _row(model="qwen3", quant="IQ3_XXS", provider="unsloth"),
        _row(model="other", quant="Q4_K_M", provider="bartowski"),
    ]
    note = provenance_note(rows)
    assert "unsloth" not in note or "bartowski" not in note


def test_provenance_note_groups_by_bit_width_not_full_quant_string() -> None:
    # Same model, same numeric bit-width (3), different providers and quant suffix.
    rows = [
        _row(model="qwen3", quant="IQ3_XXS", provider="unsloth", error_rate=0.05),
        _row(model="qwen3", quant="Q3_K_S", provider="bartowski", error_rate=0.80),
    ]
    note = provenance_note(rows)
    assert "unsloth" in note and "bartowski" in note


def test_provenance_note_keeps_best_error_rate_per_provider() -> None:
    # A provider with two rows: the worse (higher error) row must not displace
    # the already-recorded best, and the surfaced delta uses each provider's best.
    rows = [
        _row(model="qwen3", quant="IQ3_XXS", provider="unsloth", error_rate=0.05),
        _row(model="qwen3", quant="IQ3_XXS", provider="unsloth", error_rate=0.90),
        _row(model="qwen3", quant="IQ3_XXS", provider="bartowski", error_rate=1.0),
    ]
    note = provenance_note(rows)
    # unsloth best (5%) vs bartowski (100%) => ~95 point delta, not 0.9-based.
    assert "95" in note
    assert "90.0% error" not in note


def test_provenance_note_skips_rows_without_parseable_quant() -> None:
    rows = [
        _row(model="qwen3", quant=None, provider="unsloth"),
        _row(model="qwen3", quant="full", provider="bartowski"),
    ]
    note = provenance_note(rows)
    # No comparable bit-width, so no pair is surfaced.
    assert "unsloth" not in note
