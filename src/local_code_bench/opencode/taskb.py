"""Task B scoring — structured classification map diffed against ground truth.

The model is asked to emit a JSON object mapping each fixture line number to its
severity level. This module renders that prompt, parses the model's reply, and
diffs it against the authoritative ground truth, reporting three independent
signals:

* **error rate** — mismatches / expected lines (lower is better).
* **coverage** — lines present / lines expected (catches dropped rows).
* **collisions** — count of distinct inputs that collapsed onto a colliding key
  (the article's "8 pages -> 1 URL" failure).

Malformed or unparseable output scores 100% error and is flagged ``PARSE_FAIL``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

#: Placeholder in ``prompts/task-b.md`` replaced by the rendered fixture.
FIXTURE_PLACEHOLDER = "{{FIXTURE}}"

#: Flag recorded when the model output cannot be parsed into a JSON object.
PARSE_FAIL = "PARSE_FAIL"


@dataclass(frozen=True)
class TaskBScore:
    """Outcome of diffing one model response against ground truth."""

    error_rate: float
    coverage: float
    collisions: int
    expected: int
    present: int
    mismatches: int
    flag: str | None = None


def render_task_b_prompt(template: str, lines: list[str]) -> str:
    """Substitute ``{{FIXTURE}}`` with the 1-based numbered fixture lines."""
    numbered = "\n".join(f"{number}: {line}" for number, line in enumerate(lines, start=1))
    return template.replace(FIXTURE_PLACEHOLDER, numbered)


def _extract_json_object(text: str) -> str | None:
    """Return the outermost ``{...}`` substring, tolerating fences/preamble."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    return text[start : end + 1]


def parse_classification(text: str) -> tuple[dict[int, str], int] | None:
    """Parse a model reply into ``{line_number: level}`` and a collision count.

    Returns ``None`` when no JSON object can be parsed (a ``PARSE_FAIL``).
    Duplicate keys are counted as collisions; the last value wins, matching
    ``json`` semantics.
    """
    blob = _extract_json_object(text)
    if blob is None:
        return None

    collisions = 0

    def count_collisions(pairs: list[tuple[str, object]]) -> dict[str, object]:
        nonlocal collisions
        keys = [key for key, _ in pairs]
        collisions += len(keys) - len(set(keys))
        return dict(pairs)

    try:
        raw = json.loads(blob, object_pairs_hook=count_collisions)
    except (ValueError, TypeError):
        return None
    if not isinstance(raw, dict):  # pragma: no cover - defensive: an extracted blob always starts with '{', so a successful parse is an object
        return None

    mapping: dict[int, str] = {}
    for key, value in raw.items():
        try:
            number = int(key)
        except (TypeError, ValueError):
            continue
        mapping[number] = value if isinstance(value, str) else str(value)
    return mapping, collisions


def score_task_b(response: str, truth: dict[int, str]) -> TaskBScore:
    """Diff a model response against ground truth and compute Task B metrics."""
    expected = len(truth)
    parsed = parse_classification(response)
    if parsed is None:
        return TaskBScore(
            error_rate=1.0,
            coverage=0.0,
            collisions=0,
            expected=expected,
            present=0,
            mismatches=expected,
            flag=PARSE_FAIL,
        )

    mapping, collisions = parsed
    present = sum(1 for number in truth if number in mapping)
    mismatches = sum(1 for number, level in truth.items() if mapping.get(number) != level)
    error_rate = mismatches / expected if expected else 0.0
    coverage = present / expected if expected else 1.0
    return TaskBScore(
        error_rate=error_rate,
        coverage=coverage,
        collisions=collisions,
        expected=expected,
        present=present,
        mismatches=mismatches,
        flag=None,
    )
