"""Log-line severity rules and ground truth for the OpenCode benchmark.

`classify_line` implements the severity rules verbatim (first-match-wins,
case-sensitive). It is the single source of truth for both Task A's behavioural
expectations and Task B's ground truth, so the two tasks can never disagree on
what "correct" means.
"""

from __future__ import annotations

from pathlib import Path

#: The four severity levels, in rule-evaluation order.
LEVELS: tuple[str, ...] = ("error", "warn", "info", "unknown")


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


def load_fixture(path: str | Path) -> list[str]:
    """Read a fixture log into a list of lines, dropping the trailing newline."""
    return Path(path).read_text(encoding="utf-8").splitlines()


def ground_truth(lines: list[str]) -> dict[int, str]:
    """Map each 1-based line number to its expected severity level."""
    return {number: classify_line(line) for number, line in enumerate(lines, start=1)}
