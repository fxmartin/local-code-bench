"""Go-aware source extraction for Task A scoring.

A model's Task A response is prose plus (hopefully) one fenced code block holding
a Go program. This mirrors ``scoring.extract_code`` but recognises Go fences and a
Go program shape, tolerating minor preamble before the fence.
"""

from __future__ import annotations

_GO_FENCE_TAGS = {"go", "golang"}


def extract_go_code(text: str) -> str:
    """Extract a Go program from ``text``.

    Prefers a ```go fenced block; falls back to any fenced block that looks like a
    Go program. Returns an empty string when no plausible Go source is found.
    """
    if "```" not in text:
        stripped = text.strip()
        return stripped if "package main" in stripped else ""

    parts = text.split("```")
    candidates: list[str] = []
    for index in range(1, len(parts), 2):
        block = parts[index]
        lines = block.splitlines()
        if lines and lines[0].strip().lower() in _GO_FENCE_TAGS:
            candidates.append("\n".join(lines[1:]).strip())
        else:
            candidates.append(block.strip())

    for candidate in candidates:
        if "package main" in candidate:
            return candidate
    for candidate in candidates:
        if "func " in candidate:
            return candidate
    return ""
