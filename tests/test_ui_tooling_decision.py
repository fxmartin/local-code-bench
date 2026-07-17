"""Story 16.3-001 — CSS/chart tooling evaluation decision note.

Contract under test:

* ``docs/UI-TOOLING-DECISION.md`` records an explicit adopt/reject outcome for
  each shortlisted candidate (Open Props, Pico.css, uPlot).
* Every candidate section covers the required evaluation dimensions: control
  sharpness, vendored size, licence, offline serving, no-build compatibility,
  and composition with the 16.1 token layer.
* Each candidate is pinned — evaluated version and measured file size recorded.
* A rejected candidate leaves no vendored asset behind in the package, so the
  hand-rolled token approach proceeds unchanged (a valid outcome per the AC).
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_NOTE = _REPO_ROOT / "docs" / "UI-TOOLING-DECISION.md"

_CANDIDATES = ("Open Props", "Pico.css", "uPlot")

# Evaluation dimensions the story's AC requires per candidate, as lowercase
# grep targets against that candidate's section.
_DIMENSIONS = (
    "control sharpness",
    "vendored size",
    "licence",
    "offline",
    "no-build",
    "token layer",
)

_OUTCOME_RE = re.compile(r"\*\*Outcome\*\*:\s*(Adopt|Reject)")
_VERSION_RE = re.compile(r"\b\d+\.\d+\.\d+\b")
_SIZE_RE = re.compile(r"\b[\d,]+\s*(?:bytes|B\b|KB\b)", re.IGNORECASE)

# Filename stems a vendored copy of a rejected candidate would use.
_VENDOR_STEMS = {
    "Open Props": "open-props",
    "Pico.css": "pico",
    "uPlot": "uplot",
}


def _note_text() -> str:
    return _NOTE.read_text(encoding="utf-8")


def _candidate_sections(text: str) -> dict[str, str]:
    """Split the note into per-candidate sections keyed by candidate name."""

    sections: dict[str, str] = {}
    headings = [
        (match.start(), candidate)
        for candidate in _CANDIDATES
        for match in re.finditer(rf"^##+ .*{re.escape(candidate)}", text, re.MULTILINE)
    ]
    headings.sort()
    for index, (start, candidate) in enumerate(headings):
        end = headings[index + 1][0] if index + 1 < len(headings) else len(text)
        sections[candidate] = text[start:end]
    return sections


def test_decision_note_exists() -> None:
    assert _NOTE.is_file(), f"missing decision note {_NOTE}"


def test_every_candidate_has_a_section() -> None:
    sections = _candidate_sections(_note_text())
    for candidate in _CANDIDATES:
        assert candidate in sections, f"no section for candidate {candidate}"


def test_every_candidate_has_explicit_outcome() -> None:
    sections = _candidate_sections(_note_text())
    for candidate in _CANDIDATES:
        match = _OUTCOME_RE.search(sections[candidate])
        assert match, f"{candidate} section lacks an explicit **Outcome**: Adopt|Reject line"


def test_every_candidate_covers_required_dimensions() -> None:
    sections = _candidate_sections(_note_text())
    for candidate in _CANDIDATES:
        section = sections[candidate].lower()
        for dimension in _DIMENSIONS:
            assert dimension in section, f"{candidate} section does not cover '{dimension}'"


def test_every_candidate_is_pinned_with_measured_size() -> None:
    sections = _candidate_sections(_note_text())
    for candidate in _CANDIDATES:
        section = sections[candidate]
        assert _VERSION_RE.search(section), f"{candidate} section lacks a pinned version"
        assert _SIZE_RE.search(section), f"{candidate} section lacks a measured file size"


def test_rejected_candidates_are_not_vendored() -> None:
    sections = _candidate_sections(_note_text())
    package_root = _REPO_ROOT / "src"
    for candidate in _CANDIDATES:
        outcome = _OUTCOME_RE.search(sections[candidate])
        assert outcome is not None
        if outcome.group(1) != "Reject":
            continue
        stem = _VENDOR_STEMS[candidate]
        vendored = [
            path for path in package_root.rglob("*") if path.is_file() and stem in path.name.lower()
        ]
        assert vendored == [], f"rejected candidate {candidate} has vendored files: {vendored}"
