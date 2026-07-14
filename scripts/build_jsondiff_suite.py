"""Generate the jsondiff-cli mini-app suite dataset (configs/datasets/jsondiff-cli.jsonl).

The suite is one small-but-sharp CLI app — a deterministic JSON diff tool —
specified precisely enough to have a single observable correct behaviour. The
hidden acceptance tests are split into four behavioural slices (core,
format-order, type-edges, exit-codes) shipped as four records sharing one
prompt, so a run yields graded partial credit while riding the unchanged
pass@1 machinery.

The checked-in dataset is a generated artifact: rerun this script after any
edit here, and keep the two in sync (tests/test_jsondiff_suite.py enforces it).
The spec and tests are frozen once benchmarked — change them only by cutting a
new versioned suite id, or historical runs stop being comparable.

REFERENCE_SOLUTION is the contract's canonical implementation. It exists so the
acceptance tests can be validated offline (it must pass every slice, and known
buggy variants must fail their targeted slice); the benchmarked model never
sees it.
"""

from __future__ import annotations

import json
from pathlib import Path

SUITE_ID = "jsondiff-cli"
VERSION = "jsondiff-cli-v1"

DATASET_PATH = Path(__file__).resolve().parents[1] / "configs" / "datasets" / "jsondiff-cli.jsonl"

PROMPT = '''# Task — jsondiff: a JSON comparison CLI in Python

Write a single self-contained Python 3 program (standard library only) that
compares two JSON documents and reports their differences. The program must be
fully deterministic and must not use the network.

## Entry point

Define a function `main(argv: list[str]) -> int` where `argv` is the argument
list *excluding* the program name (like `sys.argv[1:]`). The grader imports
your code and calls `main` directly, so `main` must `return` its exit code
rather than calling `sys.exit`.

## Behaviour

`main([left_path, right_path])` reads two JSON files and prints one line per
difference to standard output, in the deterministic order defined below.
Nothing else may be written to standard output (error messages may go to
standard error).

## Difference rules

Compare the two documents recursively. Each reported line names a *path*:

- the root is `$`
- an object member is `<path>.<key>` (keys only ever match `[A-Za-z0-9_-]+`)
- an array element is `<path>[<index>]` with 0-based indices

At each node:

1. If both values are objects, visit the union of their keys in ascending
   lexicographic order. A key only in the left document prints
   `removed <path>.<key>`; a key only in the right document prints
   `added <path>.<key>`; a key present in both recurses. Do **not** descend
   into an added or removed subtree — report only its root path.
2. Otherwise, if both values are arrays, recurse index by index over the
   shared prefix (ascending), then print `removed <path>[<i>]` for each extra
   left index (ascending), then `added <path>[<i>]` for each extra right index
   (ascending). The same no-descend rule applies to the extras.
3. Otherwise the two values are compared as leaves. Equal values print
   nothing. Unequal values print `changed <path>: <left> -> <right>` where
   each value is rendered as compact JSON, exactly
   `json.dumps(value, sort_keys=True, separators=(",", ":"))`.

Leaf equality is JSON-type-strict, with one numeric exception:

- an integer and a float that are numerically equal are equal (`1` == `1.0`)
- booleans are their own type: `true` is never equal to `1`, `false` never
  equals `0`
- `null` only equals `null`
- values of different JSON types are unequal (and print a `changed` line, even
  for object-vs-array or container-vs-scalar mismatches)

Differences are printed depth-first in visit order — children of a node are
printed before the node's later siblings.

## Exit codes

- `0` — the documents are identical (nothing is printed)
- `1` — at least one difference was printed
- `2` — usage error: `argv` does not contain exactly two paths, a file cannot
  be read, or a file is not valid JSON (nothing is printed to standard output)

Return only the complete Python source inside a single fenced ```python code
block.
'''

# Shared by every slice: fixture writing plus a lenient runner that captures
# stdout and tolerates sys.exit-style returns. `main` comes from the candidate,
# exec'd into the same sandbox namespace before this code runs.
_PRELUDE = '''import contextlib
import io
import json


def _write(name, value):
    # value: raw JSON text when str (allows scalar/invalid docs), else serialized.
    text = value if isinstance(value, str) else json.dumps(value)
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

_CORE = '''_write("a.json", {"name": "bench", "tags": ["x", "y"], "size": 3})
_write("b.json", {"name": "bench", "tags": ["x", "y"], "size": 3})
code, out = _run(["a.json", "b.json"])
assert code == 0, f"identical documents must exit 0, got {code}"
assert out == "", f"identical documents must print nothing, got {out!r}"

_write("c.json", {"name": "bench", "size": 4})
code, out = _run(["a.json", "c.json"])
assert code == 1, f"differing documents must exit 1, got {code}"
lines = out.splitlines()
assert "changed $.size: 3 -> 4" in lines, f"missing changed line in {lines!r}"
assert "removed $.tags" in lines, f"missing removed line in {lines!r}"
assert len(lines) == 2, f"expected exactly 2 diff lines, got {lines!r}"

_write("n1.json", {"a": {"b": {"c": 1}}})
_write("n2.json", {"a": {"b": {"c": 2}}})
code, out = _run(["n1.json", "n2.json"])
assert code == 1, f"nested change must exit 1, got {code}"
assert out == "changed $.a.b.c: 1 -> 2\\n", f"nested change mismatch: {out!r}"

_write("l1.json", [1, 2, 3])
_write("l2.json", [1, 5, 3])
code, out = _run(["l1.json", "l2.json"])
assert code == 1, f"array change must exit 1, got {code}"
assert out == "changed $[1]: 2 -> 5\\n", f"array change mismatch: {out!r}"

_write("e1.json", {})
_write("e2.json", {"k": [1, 2]})
code, out = _run(["e1.json", "e2.json"])
assert code == 1, f"added key must exit 1, got {code}"
assert out == "added $.k\\n", f"added key mismatch: {out!r}"
'''

_FORMAT_ORDER = '''_write("left.json", {"beta": 1, "alpha": {"y": [1, 2], "x": True}, "gamma": [{"k": 1}]})
_write(
    "right.json",
    {"beta": 2, "alpha": {"y": [1, 3, 4], "x": True}, "delta": None, "gamma": [{"k": 2}]},
)
code, out = _run(["left.json", "right.json"])
assert code == 1, f"differing documents must exit 1, got {code}"
expected = (
    "changed $.alpha.y[1]: 2 -> 3\\n"
    "added $.alpha.y[2]\\n"
    "changed $.beta: 1 -> 2\\n"
    "added $.delta\\n"
    "changed $.gamma[0].k: 1 -> 2\\n"
)
assert out == expected, f"expected {expected!r}, got {out!r}"

_write("r1.json", {"obj": {"b": 1, "a": 2}})
_write("r2.json", {"obj": 7})
code, out = _run(["r1.json", "r2.json"])
assert code == 1, f"kind mismatch must exit 1, got {code}"
assert out == 'changed $.obj: {"a":2,"b":1} -> 7\\n', f"compact rendering mismatch: {out!r}"
'''

_TYPE_EDGES = '''def _case(left_text, right_text):
    _write("t1.json", left_text)
    _write("t2.json", right_text)
    return _run(["t1.json", "t2.json"])


code, out = _case("true", "1")
assert code == 1 and out == "changed $: true -> 1\\n", f"true vs 1: {code} {out!r}"

code, out = _case("1", "1.0")
assert code == 0 and out == "", f"1 vs 1.0 must be equal: {code} {out!r}"

code, out = _case("0", "false")
assert code == 1 and out == "changed $: 0 -> false\\n", f"0 vs false: {code} {out!r}"

code, out = _case("null", "false")
assert code == 1 and out == "changed $: null -> false\\n", f"null vs false: {code} {out!r}"

code, out = _case("{}", "[]")
assert code == 1 and out == "changed $: {} -> []\\n", f"object vs array: {code} {out!r}"

code, out = _case('"1"', "1")
assert code == 1 and out == 'changed $: "1" -> 1\\n', f"string vs number: {code} {out!r}"

_write("u1.json", {"a": 1})
_write("u2.json", {"a": 1, "cfg": {"inner": {"deep": 1}}})
code, out = _run(["u1.json", "u2.json"])
assert code == 1 and out == "added $.cfg\\n", f"added subtree must not be descended: {out!r}"
code, out = _run(["u2.json", "u1.json"])
assert code == 1 and out == "removed $.cfg\\n", f"removed subtree must not be descended: {out!r}"
'''

_EXIT_CODES = '''_write("ok.json", {"a": 1})
_write("same.json", {"a": 1})
_write("diff.json", {"a": 2})
_write("bad.json", "{not json")

code, out = _run([])
assert code == 2 and out == "", f"no args must exit 2 with no stdout: {code} {out!r}"

code, out = _run(["ok.json"])
assert code == 2 and out == "", f"one arg must exit 2: {code} {out!r}"

code, out = _run(["ok.json", "same.json", "diff.json"])
assert code == 2 and out == "", f"three args must exit 2: {code} {out!r}"

code, out = _run(["ok.json", "missing-file.json"])
assert code == 2 and out == "", f"missing file must exit 2: {code} {out!r}"

code, out = _run(["ok.json", "bad.json"])
assert code == 2 and out == "", f"invalid JSON must exit 2: {code} {out!r}"

code, out = _run(["ok.json", "same.json"])
assert code == 0 and out == "", f"identical must exit 0: {code} {out!r}"

code, out = _run(["ok.json", "diff.json"])
assert code == 1 and out == "changed $.a: 1 -> 2\\n", f"difference must exit 1: {code} {out!r}"
'''

#: Slice name -> acceptance-test body, in canonical record order.
SLICES: tuple[tuple[str, str], ...] = (
    ("core", _CORE),
    ("format-order", _FORMAT_ORDER),
    ("type-edges", _TYPE_EDGES),
    ("exit-codes", _EXIT_CODES),
)

REFERENCE_SOLUTION = '''import json


def _render(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _equal(left, right):
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if type(left) is not type(right):
        return False
    return left == right


def _diff(left, right, path, out):
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            child = f"{path}.{key}"
            if key not in right:
                out.append(f"removed {child}")
            elif key not in left:
                out.append(f"added {child}")
            else:
                _diff(left[key], right[key], child, out)
        return
    if isinstance(left, list) and isinstance(right, list):
        common = min(len(left), len(right))
        for index in range(common):
            _diff(left[index], right[index], f"{path}[{index}]", out)
        for index in range(common, len(left)):
            out.append(f"removed {path}[{index}]")
        for index in range(common, len(right)):
            out.append(f"added {path}[{index}]")
        return
    if not _equal(left, right):
        out.append(f"changed {path}: {_render(left)} -> {_render(right)}")


def main(argv):
    if len(argv) != 2:
        return 2
    documents = []
    for name in argv:
        try:
            with open(name, "r", encoding="utf-8") as handle:
                documents.append(json.load(handle))
        except (OSError, ValueError):
            return 2
    differences = []
    _diff(documents[0], documents[1], "$", differences)
    for line in differences:
        print(line)
    return 1 if differences else 0
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
