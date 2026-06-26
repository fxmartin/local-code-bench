"""Tests for Story 10.3-001: Task B structured classification scoring."""

from __future__ import annotations

from pathlib import Path

import pytest

from local_code_bench.opencode.fixtures import (
    classify_line,
    ground_truth,
    load_fixture,
)
from local_code_bench.opencode.taskb import (
    FIXTURE_PLACEHOLDER,
    PARSE_FAIL,
    parse_classification,
    render_task_b_prompt,
    score_task_b,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = REPO_ROOT / "fixtures" / "opencode-sample.log"
PROMPT_PATH = REPO_ROOT / "prompts" / "task-b.md"


# --- Severity rules (single source of truth) -------------------------------


def test_classify_error_on_error_token() -> None:
    assert classify_line("2026 ERROR failed to open file") == "error"


def test_classify_error_on_fatal_token() -> None:
    assert classify_line("2026 FATAL out of memory") == "error"


def test_classify_warn() -> None:
    assert classify_line("2026 WARN disk almost full") == "warn"


def test_classify_info() -> None:
    assert classify_line("2026 INFO server started") == "info"


def test_classify_unknown_when_no_token() -> None:
    assert classify_line("2026 connection closed cleanly") == "unknown"


def test_classify_is_case_sensitive() -> None:
    assert classify_line("2026 info lowercase ignored") == "unknown"


def test_classify_first_matching_rule_wins() -> None:
    # ERROR is evaluated before WARN, so a line with both is an error.
    assert classify_line("2026 ERROR WARN both present") == "error"


# --- Fixture + ground truth ------------------------------------------------


def test_load_fixture_drops_trailing_newline() -> None:
    lines = load_fixture(FIXTURE_PATH)
    assert lines
    assert all(not line.endswith("\n") for line in lines)


def test_ground_truth_is_one_based_and_covers_every_line() -> None:
    lines = load_fixture(FIXTURE_PATH)
    truth = ground_truth(lines)
    assert set(truth) == set(range(1, len(lines) + 1))
    assert set(truth.values()) == {"error", "warn", "info", "unknown"}


# --- Prompt rendering ------------------------------------------------------


def test_prompt_file_carries_placeholder_and_rules() -> None:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    assert FIXTURE_PLACEHOLDER in template
    assert "ERROR" in template and "FATAL" in template
    assert "WARN" in template and "INFO" in template
    assert "JSON" in template


def test_render_substitutes_numbered_fixture() -> None:
    template = "RULES\n\n{{FIXTURE}}\n\nEND"
    rendered = render_task_b_prompt(template, ["alpha", "beta"])
    assert FIXTURE_PLACEHOLDER not in rendered
    assert "1: alpha" in rendered
    assert "2: beta" in rendered


def test_render_real_prompt_against_real_fixture() -> None:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    lines = load_fixture(FIXTURE_PATH)
    rendered = render_task_b_prompt(template, lines)
    assert FIXTURE_PLACEHOLDER not in rendered
    assert f"{len(lines)}: {lines[-1]}" in rendered


# --- Parsing ---------------------------------------------------------------


def test_parse_plain_json() -> None:
    parsed = parse_classification('{"1": "info", "2": "warn"}')
    assert parsed is not None
    mapping, collisions = parsed
    assert mapping == {1: "info", 2: "warn"}
    assert collisions == 0


def test_parse_tolerates_fence_and_preamble() -> None:
    text = 'Here is the map:\n```json\n{"1": "error"}\n```\nDone.'
    parsed = parse_classification(text)
    assert parsed is not None
    mapping, _ = parsed
    assert mapping == {1: "error"}


def test_parse_detects_colliding_keys() -> None:
    # Distinct inputs collapsing to one key (the "8 pages -> 1 URL" failure).
    parsed = parse_classification('{"1": "info", "1": "warn", "2": "info"}')
    assert parsed is not None
    mapping, collisions = parsed
    assert collisions == 1
    assert mapping[1] == "warn"  # last value wins after collision


def test_parse_returns_none_on_malformed_json() -> None:
    assert parse_classification("{not json at all") is None


def test_parse_returns_none_on_braced_but_invalid_json() -> None:
    # Has both braces, so extraction succeeds, but the body is not valid JSON.
    assert parse_classification('{"1": }') is None


def test_parse_skips_non_integer_keys() -> None:
    parsed = parse_classification('{"1": "info", "note": "warn"}')
    assert parsed is not None
    mapping, _ = parsed
    assert mapping == {1: "info"}


def test_parse_coerces_non_string_values() -> None:
    parsed = parse_classification('{"1": 7}')
    assert parsed is not None
    mapping, _ = parsed
    assert mapping == {1: "7"}


def test_parse_returns_none_when_no_object() -> None:
    assert parse_classification("the answer is 42") is None


def test_parse_returns_none_on_non_object_json() -> None:
    assert parse_classification("[1, 2, 3]") is None


# --- Scoring ---------------------------------------------------------------


def _truth() -> dict[int, str]:
    return {1: "info", 2: "warn", 3: "error", 4: "unknown"}


def test_score_perfect() -> None:
    response = '{"1": "info", "2": "warn", "3": "error", "4": "unknown"}'
    score = score_task_b(response, _truth())
    assert score.error_rate == 0.0
    assert score.coverage == 1.0
    assert score.collisions == 0
    assert score.flag is None
    assert score.mismatches == 0
    assert score.present == 4


def test_score_counts_wrong_levels_as_errors() -> None:
    response = '{"1": "warn", "2": "warn", "3": "error", "4": "unknown"}'
    score = score_task_b(response, _truth())
    assert score.mismatches == 1
    assert score.error_rate == pytest.approx(0.25)
    assert score.coverage == 1.0


def test_score_dropped_rows_lower_coverage_and_count_as_errors() -> None:
    response = '{"1": "info", "2": "warn"}'
    score = score_task_b(response, _truth())
    assert score.present == 2
    assert score.coverage == pytest.approx(0.5)
    assert score.mismatches == 2
    assert score.error_rate == pytest.approx(0.5)


def test_score_flags_collisions() -> None:
    response = '{"1": "info", "1": "info", "2": "warn", "3": "error", "4": "unknown"}'
    score = score_task_b(response, _truth())
    assert score.collisions == 1


def test_score_parse_fail_is_full_error() -> None:
    score = score_task_b("sorry, I cannot help with that", _truth())
    assert score.flag == PARSE_FAIL
    assert score.error_rate == 1.0
    assert score.coverage == 0.0
    assert score.mismatches == 4
    assert score.present == 0


def test_score_empty_object_is_full_error_but_not_parse_fail() -> None:
    score = score_task_b("{}", _truth())
    assert score.flag is None
    assert score.error_rate == 1.0
    assert score.coverage == 0.0


def test_score_real_fixture_round_trips_to_perfect() -> None:
    import json

    lines = load_fixture(FIXTURE_PATH)
    truth = ground_truth(lines)
    response = json.dumps({str(k): v for k, v in truth.items()})
    score = score_task_b(response, truth)
    assert score.error_rate == 0.0
    assert score.coverage == 1.0
    assert score.flag is None
