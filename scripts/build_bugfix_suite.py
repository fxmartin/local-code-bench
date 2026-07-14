"""Generate the bugfix-py suite dataset (configs/datasets/bugfix-py.jsonl).

Rung 4 of the home-grown ladder, on a different axis from the mini-apps: each
task is a *debugging* problem — a small buggy Python module plus a bug report,
and the model must return the complete fixed module. The discriminating skill
is fault localization and a behaviour-preserving fix, not greenfield
generation; every test_code asserts both the fixed behaviour and regression
behaviour the fix must not break, so a from-scratch rewrite still has to meet
the full contract.

The five bugs are classic Python failure modes: a shared mutable default
argument, shallow-copy pollution of module defaults, an off-by-one that drops
the final window, a double-applied sort reversal that flips the tie-break, and
generator exhaustion. Unlike the mini-app suites there is one record per bug
(binary pass each); partial credit comes from the five tasks together.

Validation is self-proving: the shipped buggy source must FAIL its own
test_code (so the bug report is real and the tests detect it) and the
reference fix must PASS (tests/test_bugfix_suite.py runs both through the real
sandbox). Same conventions as the other generators: the checked-in dataset is
a generated artifact kept in sync by a drift test, references are never shown
to the model, and the tasks are frozen once benchmarked — change them only by
cutting a new versioned suite id.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

SUITE_ID = "bugfix-py"
VERSION = "bugfix-py-v1"

DATASET_PATH = Path(__file__).resolve().parents[1] / "configs" / "datasets" / "bugfix-py.jsonl"

_PROMPT_TEMPLATE = '''# Task — fix the bug in this Python module

The module below has a bug. The observed failure:

{report}

Fix the bug and return the COMPLETE corrected module inside a single fenced
```python code block. Keep the module's public interface unchanged (names,
signatures, and return shapes), keep it standard-library only, and do not add
printing or other I/O. Behaviour not mentioned in the bug report must keep
working exactly as before.

## Current source

```python
{buggy}
```
'''


@dataclass(frozen=True)
class BugCase:
    """One debugging task: buggy module, true bug report, tests, reference fix."""

    name: str
    entry_point: str
    report: str
    buggy: str
    fixed: str
    test_code: str


CASES: tuple[BugCase, ...] = (
    BugCase(
        name="mutable-default",
        entry_point="record_event",
        report=(
            "`record_event(\"boot\")` followed by `record_event(\"shutdown\")` returns\n"
            "`[\"boot\", \"shutdown\"]` instead of `[\"shutdown\"]` — independent calls\n"
            "share history."
        ),
        buggy='''def record_event(name, log=[]):
    """Append name to log and return it; callers omitting log get a fresh one."""
    log.append(name)
    return log
''',
        fixed='''def record_event(name, log=None):
    """Append name to log and return it; callers omitting log get a fresh one."""
    if log is None:
        log = []
    log.append(name)
    return log
''',
        test_code='''assert record_event("boot") == ["boot"]
assert record_event("shutdown") == ["shutdown"], "default log must be fresh per call"

shared = ["x"]
assert record_event("y", shared) == ["x", "y"]
assert shared == ["x", "y"], "a caller-provided log must still be mutated in place"
''',
    ),
    BugCase(
        name="shallow-copy",
        entry_point="build_config",
        report=(
            "`build_config({\"limits\": {\"cpu\": 4.0}})` followed by `build_config({})`\n"
            "returns cpu 4.0 from the second call too — the module-level DEFAULTS\n"
            "are being mutated."
        ),
        buggy='''DEFAULTS = {
    "retries": 3,
    "limits": {"cpu": 1.0, "memory_mb": 512},
}


def build_config(overrides):
    """Merge overrides into a copy of DEFAULTS without changing DEFAULTS."""
    config = dict(DEFAULTS)
    for key, value in overrides.items():
        if key != "limits":
            config[key] = value
    config["limits"].update(overrides.get("limits", {}))
    return config
''',
        fixed='''DEFAULTS = {
    "retries": 3,
    "limits": {"cpu": 1.0, "memory_mb": 512},
}


def build_config(overrides):
    """Merge overrides into a copy of DEFAULTS without changing DEFAULTS."""
    config = dict(DEFAULTS)
    config["limits"] = dict(DEFAULTS["limits"])
    for key, value in overrides.items():
        if key != "limits":
            config[key] = value
    config["limits"].update(overrides.get("limits", {}))
    return config
''',
        test_code='''first = build_config({"limits": {"cpu": 4.0}, "retries": 5})
assert first["retries"] == 5
assert first["limits"] == {"cpu": 4.0, "memory_mb": 512}

second = build_config({})
assert second["retries"] == 3
assert second["limits"] == {"cpu": 1.0, "memory_mb": 512}, "DEFAULTS must not be mutated"
assert DEFAULTS["limits"] == {"cpu": 1.0, "memory_mb": 512}
''',
    ),
    BugCase(
        name="off-by-one-window",
        entry_point="sliding_windows",
        report=(
            "`sliding_windows([1, 2, 3, 4], 2)` returns `[[1, 2], [2, 3]]` — the final\n"
            "window `[3, 4]` is missing — and `sliding_windows([7], 1)` returns `[]`\n"
            "instead of `[[7]]`."
        ),
        buggy='''def sliding_windows(values, size):
    """Every consecutive run of exactly `size` items, in order."""
    if size <= 0:
        raise ValueError("size must be positive")
    return [values[i : i + size] for i in range(len(values) - size)]
''',
        fixed='''def sliding_windows(values, size):
    """Every consecutive run of exactly `size` items, in order."""
    if size <= 0:
        raise ValueError("size must be positive")
    return [values[i : i + size] for i in range(len(values) - size + 1)]
''',
        test_code='''assert sliding_windows([1, 2, 3, 4], 2) == [[1, 2], [2, 3], [3, 4]]
assert sliding_windows([7], 1) == [[7]]
assert sliding_windows([1, 2], 3) == [], "windows larger than the input yield nothing"
assert sliding_windows([], 2) == []
try:
    sliding_windows([1], 0)
    raise AssertionError("size=0 must raise ValueError")
except ValueError:
    pass
''',
    ),
    BugCase(
        name="tie-break-sort",
        entry_point="rank_players",
        report=(
            "Tied scores come out in reverse alphabetical order: with ana and bob both\n"
            "on 10 points, `rank_players` puts bob before ana. Expected: score\n"
            "descending, ties broken by name ascending."
        ),
        buggy='''def rank_players(entries):
    """Sort by score descending; ties broken by name ascending."""
    return sorted(entries, key=lambda entry: (entry["score"], entry["name"]), reverse=True)
''',
        fixed='''def rank_players(entries):
    """Sort by score descending; ties broken by name ascending."""
    return sorted(entries, key=lambda entry: (-entry["score"], entry["name"]))
''',
        test_code='''entries = [
    {"name": "bob", "score": 10},
    {"name": "ana", "score": 10},
    {"name": "zoe", "score": 12},
    {"name": "kim", "score": 9},
]
ranked = rank_players(entries)
assert [entry["name"] for entry in ranked] == ["zoe", "ana", "bob", "kim"], (
    f"wrong order: {[entry['name'] for entry in ranked]}"
)
assert rank_players([]) == []
assert entries[0]["name"] == "bob", "the input list must not be reordered in place"
''',
    ),
    BugCase(
        name="iterator-exhaustion",
        entry_point="summarize",
        report=(
            "`summarize([1, 2, 3, -1])` returns `{\"total\": 6, \"count\": 0, \"mean\": 0.0}`\n"
            "— the count and mean ignore every value the total just counted."
        ),
        buggy='''def summarize(numbers):
    """Total, count, and mean of the non-negative values."""
    valid = (n for n in numbers if n >= 0)
    total = sum(valid)
    count = len(list(valid))
    return {"total": total, "count": count, "mean": total / count if count else 0.0}
''',
        fixed='''def summarize(numbers):
    """Total, count, and mean of the non-negative values."""
    valid = [n for n in numbers if n >= 0]
    total = sum(valid)
    count = len(valid)
    return {"total": total, "count": count, "mean": total / count if count else 0.0}
''',
        test_code='''assert summarize([1, 2, 3, -1]) == {"total": 6, "count": 3, "mean": 2.0}
assert summarize([]) == {"total": 0, "count": 0, "mean": 0.0}
assert summarize([-5, -1]) == {"total": 0, "count": 0, "mean": 0.0}
assert summarize([2]) == {"total": 2, "count": 1, "mean": 2.0}
''',
    ),
)


def build_prompt(case: BugCase) -> str:
    return _PROMPT_TEMPLATE.format(report=case.report, buggy=case.buggy.rstrip("\n"))


def build_records() -> list[dict[str, str]]:
    """The suite's records in canonical order: one record per bug."""

    return [
        {
            "task_id": f"{SUITE_ID}/{case.name}",
            "prompt": build_prompt(case),
            "test_code": case.test_code,
            "entry_point": case.entry_point,
            "version": VERSION,
        }
        for case in CASES
    ]


def render_jsonl(records: list[dict[str, str]]) -> str:
    return "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)


def main() -> None:
    DATASET_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATASET_PATH.write_text(render_jsonl(build_records()), encoding="utf-8")
    print(f"wrote {DATASET_PATH}")


if __name__ == "__main__":
    main()
