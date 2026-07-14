"""Generate the logclass-cli suite dataset (configs/datasets/logclass-cli.jsonl).

Rung 1 of the home-grown ladder: a Python port of the OpenCode Task A
log-line classifier (prompts/task-a.md), so the full ladder runs through one
pipeline (`bench --suite <id>`) with comparable pass@1 rows. The Go original
stays untouched in the opencode flow — it keeps the cross-language axis and
its scorecard history; this port exists so rung 1 lands on the same
leaderboard as rungs 2-4 without a Go toolchain.

The severity rules are identical to Task A (first match wins, case-sensitive
substring matching), and the validation suite proves it: the reference
solution's classifier is diffed against the authoritative
local_code_bench.opencode.fixtures.classify_line over the shipped sample log,
so the two rungs can never disagree on what "correct" means. Where task-a.md
left output formats loose (the Go scorer matches by regex), this spec pins
them exactly, which is what exact-assertion scoring needs.

Same conventions as the other generators: the checked-in dataset is generated
and kept in sync by a drift test, four behavioural slices share one prompt for
graded partial credit, and REFERENCE_SOLUTION exists only for offline
validation. Spec and tests are frozen once benchmarked; change them only by
cutting a new versioned suite id.
"""

from __future__ import annotations

import json
from pathlib import Path

SUITE_ID = "logclass-cli"
VERSION = "logclass-cli-v1"

DATASET_PATH = Path(__file__).resolve().parents[1] / "configs" / "datasets" / "logclass-cli.jsonl"

PROMPT = '''# Task — classify: a log-line severity classifier CLI in Python

Write a single self-contained Python 3 program (standard library only) that
classifies log lines by severity. The program must be fully deterministic and
must not use the network.

## Entry point

Define a function `main(argv: list[str]) -> int` where `argv` is the argument
list *excluding* the program name (like `sys.argv[1:]`). The grader imports
your code and calls `main` directly, so `main` must `return` its exit code
rather than calling `sys.exit`.

## Severity rules

Classify each line by the FIRST matching rule, evaluated in this order.
Matching is case-sensitive substring matching — only the exact upper-case
tokens count, anywhere in the line:

1. The line contains `ERROR` or `FATAL` -> `error`
2. Otherwise, the line contains `WARN` -> `warn`
3. Otherwise, the line contains `INFO` -> `info`
4. Otherwise -> `unknown`

The lines of a file are its contents split on newlines, with no extra empty
line for a trailing final newline (Python's `str.splitlines`). Blank lines
classify as `unknown`.

## Modes

- `main([path])` — read the log file and print a count per level: exactly
  four lines, always all four levels even when a count is zero, in the fixed
  order `error`, `warn`, `info`, `unknown`, each formatted as
  `<level> <count>` (one space).
- `main(["--json", path])` — print the counts as a single line containing one
  JSON object that maps each of the four level names to its integer count.
  All four keys are always present.
- `main(["--filter", level, path])` — print only the lines whose severity is
  `level`, verbatim and in input order. No matches prints nothing and still
  exits 0.

Nothing may be written to standard output on any non-zero exit (error
messages may go to standard error).

## Exit codes

- `0` — success
- `1` — the input file does not exist or cannot be read
- `2` — bad arguments: no arguments, an unknown flag, a missing file
  argument, extra arguments, or a `--filter` level that is not one of
  `error`, `warn`, `info`, `unknown`

Return only the complete Python source inside a single fenced ```python code
block.
'''

# Shared by every slice: fixture writing plus a lenient runner that captures
# stdout and tolerates sys.exit-style returns. `main` comes from the candidate,
# exec'd into the same sandbox namespace before this code runs.
_PRELUDE = '''import contextlib
import io
import json


def _write(name, text):
    with open(name, "w", encoding="utf-8") as handle:
        handle.write(text)


def _run(argv):
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            code = main(list(argv))
    except SystemExit as exc:
        raw = exc.code
        code = raw if isinstance(raw, int) else (0 if raw is None else 1)
    return code, buffer.getvalue()


'''

_COUNTS = '''_write(
    "app.log",
    "2026-01-01 INFO boot\\n"
    "2026-01-02 ERROR disk full\\n"
    "2026-01-03 WARN low memory\\n"
    "2026-01-04 FATAL kernel panic\\n"
    "plain line\\n"
    "\\n"
    "2026-01-05 INFO done\\n",
)
code, out = _run(["app.log"])
assert code == 0, f"counts mode must exit 0, got {code}"
assert out == "error 2\\nwarn 1\\ninfo 2\\nunknown 2\\n", f"count table mismatch: {out!r}"

_write("one.log", "INFO a\\n")
code, out = _run(["one.log"])
assert code == 0, f"single-line file must exit 0, got {code}"
assert out == "error 0\\nwarn 0\\ninfo 1\\nunknown 0\\n", (
    f"zero counts must still be printed: {out!r}"
)

_write("empty.log", "")
code, out = _run(["empty.log"])
assert code == 0, f"empty file must exit 0, got {code}"
assert out == "error 0\\nwarn 0\\ninfo 0\\nunknown 0\\n", f"empty file mismatch: {out!r}"
'''

_JSON_FILTER = '''_write("app.log", "INFO a\\nERROR b\\nFATAL c\\nnoise\\n")

code, out = _run(["--json", "app.log"])
assert code == 0, f"--json must exit 0, got {code}"
assert out.endswith("\\n") and out.count("\\n") == 1, f"--json must print one line: {out!r}"
parsed = json.loads(out)
assert parsed == {"error": 2, "warn": 0, "info": 1, "unknown": 1}, (
    f"--json counts mismatch (all four keys required): {parsed!r}"
)

code, out = _run(["--filter", "error", "app.log"])
assert code == 0, f"--filter must exit 0, got {code}"
assert out == "ERROR b\\nFATAL c\\n", f"filtered lines must be verbatim, in order: {out!r}"

code, out = _run(["--filter", "unknown", "app.log"])
assert code == 0 and out == "noise\\n", f"--filter unknown mismatch: {code} {out!r}"

code, out = _run(["--filter", "warn", "app.log"])
assert code == 0 and out == "", f"zero matches must print nothing, exit 0: {code} {out!r}"
'''

_EDGE_RULES = '''_write(
    "edge.log",
    "error lowercase not matched\\n"
    "WARNING contains warn token\\n"
    "FATALITY strikes\\n"
    "INFO and ERROR both appear\\n"
    "Warn mixed case\\n",
)
code, out = _run(["edge.log"])
assert code == 0, f"edge fixture must exit 0, got {code}"
assert out == "error 2\\nwarn 1\\ninfo 0\\nunknown 2\\n", (
    "case-sensitive substring rules mismatch (lowercase 'error' is unknown, "
    f"'WARNING' is warn, 'FATALITY' is error, ERROR beats INFO): {out!r}"
)

code, out = _run(["--filter", "error", "edge.log"])
assert code == 0, f"--filter on edge fixture must exit 0, got {code}"
assert out == "FATALITY strikes\\nINFO and ERROR both appear\\n", (
    f"rule-precedence filter mismatch: {out!r}"
)
'''

_EXIT_CODES = '''_write("ok.log", "INFO fine\\n")

code, out = _run(["missing.log"])
assert code == 1 and out == "", f"missing file must exit 1 with no stdout: {code} {out!r}"

code, out = _run(["--json", "missing.log"])
assert code == 1 and out == "", f"--json on missing file must exit 1: {code} {out!r}"

code, out = _run([])
assert code == 2 and out == "", f"no args must exit 2: {code} {out!r}"

code, out = _run(["--xml", "ok.log"])
assert code == 2 and out == "", f"unknown flag must exit 2: {code} {out!r}"

code, out = _run(["--filter", "bogus", "ok.log"])
assert code == 2 and out == "", f"unknown filter level must exit 2: {code} {out!r}"

code, out = _run(["--filter", "error"])
assert code == 2 and out == "", f"missing file argument must exit 2: {code} {out!r}"

code, out = _run(["ok.log", "extra"])
assert code == 2 and out == "", f"extra arguments must exit 2: {code} {out!r}"

code, out = _run(["ok.log"])
assert code == 0, f"valid invocation must exit 0, got {code}"
'''

#: Slice name -> acceptance-test body, in canonical record order.
SLICES: tuple[tuple[str, str], ...] = (
    ("counts", _COUNTS),
    ("json-filter", _JSON_FILTER),
    ("edge-rules", _EDGE_RULES),
    ("exit-codes", _EXIT_CODES),
)

REFERENCE_SOLUTION = '''import json

LEVELS = ("error", "warn", "info", "unknown")


def classify_line(line):
    if "ERROR" in line or "FATAL" in line:
        return "error"
    if "WARN" in line:
        return "warn"
    if "INFO" in line:
        return "info"
    return "unknown"


def main(argv):
    if len(argv) == 1 and not argv[0].startswith("--"):
        mode, level, path = "counts", None, argv[0]
    elif len(argv) == 2 and argv[0] == "--json":
        mode, level, path = "json", None, argv[1]
    elif len(argv) == 3 and argv[0] == "--filter":
        if argv[1] not in LEVELS:
            return 2
        mode, level, path = "filter", argv[1], argv[2]
    else:
        return 2
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return 1
    if mode == "filter":
        for line in lines:
            if classify_line(line) == level:
                print(line)
        return 0
    counts = {name: 0 for name in LEVELS}
    for line in lines:
        counts[classify_line(line)] += 1
    if mode == "json":
        print(json.dumps(counts))
        return 0
    for name in LEVELS:
        print(f"{name} {counts[name]}")
    return 0
'''


def build_records() -> list[dict[str, str]]:
    """The suite's records in canonical order: one prompt, four test slices."""

    return [
        {
            "task_id": f"{SUITE_ID}/{name}",
            "prompt": PROMPT,
            "test_code": _PRELUDE + body,
            "entry_point": "main",
            "version": VERSION,
        }
        for name, body in SLICES
    ]


def render_jsonl(records: list[dict[str, str]]) -> str:
    return "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)


def main() -> None:
    DATASET_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATASET_PATH.write_text(render_jsonl(build_records()), encoding="utf-8")
    print(f"wrote {DATASET_PATH}")


if __name__ == "__main__":
    main()
