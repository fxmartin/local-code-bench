"""Log-line severity rules and ground truth for the OpenCode benchmark.

`classify_line` implements the severity rules verbatim (first-match-wins,
case-sensitive). It is the single source of truth for both Task A's behavioural
expectations and Task B's ground truth, so the two tasks can never disagree on
what "correct" means. The shipped reference Go implementation in
`reference/classifier.go` mirrors these rules exactly.
"""

from __future__ import annotations

from pathlib import Path

#: The four severity levels, in rule-evaluation / canonical reporting order.
LEVELS: tuple[str, ...] = ("error", "warn", "info", "unknown")

#: Repo-root fixture, version-controlled and authoritative for both tasks.
DEFAULT_FIXTURE_PATH = Path(__file__).resolve().parents[3] / "fixtures" / "opencode-sample.log"


def classify_line(line: str) -> str:
    """Return the severity level for one log line (first matching rule wins).

    Matching is case-sensitive — only the exact upper-case tokens count.
    """
    if "ERROR" in line or "FATAL" in line:
        return "error"
    if "WARN" in line:
        return "warn"
    if "INFO" in line:
        return "info"
    return "unknown"


def fixture_lines(text: str) -> list[str]:
    """Split fixture text into lines using the same semantics as the Go scanner."""
    return text.splitlines()


def load_fixture(path: str | Path | None = None) -> list[str]:
    """Read a fixture log into a list of lines, dropping the trailing newline.

    Defaults to the repo-root sample log when ``path`` is omitted.
    """
    source = Path(path) if path is not None else DEFAULT_FIXTURE_PATH
    return source.read_text(encoding="utf-8").splitlines()


def ground_truth(lines: list[str]) -> dict[int, str]:
    """Map each 1-based line number to its expected severity level."""
    return {number: classify_line(line) for number, line in enumerate(lines, start=1)}


def expected_counts(text: str) -> dict[str, int]:
    """Expected per-level counts for the fixture text."""
    counts = {level: 0 for level in LEVELS}
    for line in fixture_lines(text):
        counts[classify_line(line)] += 1
    return counts


def expected_line_levels(text: str) -> dict[int, str]:
    """Expected 1-based line-number -> level map for the fixture text (Task B ground truth)."""
    return {index: classify_line(line) for index, line in enumerate(fixture_lines(text), start=1)}


def expected_filter(text: str, level: str) -> list[str]:
    """Original fixture lines that classify to ``level``, in order."""
    return [line for line in fixture_lines(text) if classify_line(line) == level]
