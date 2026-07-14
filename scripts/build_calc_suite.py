"""Generate the calc-cli mini-app suite dataset (configs/datasets/calc-cli.jsonl).

Rung 3 of the mini-app ladder: a harder app than jsondiff-cli — a full
expression evaluator that needs a real tokenizer and recursive-descent parser.
The grammar is deliberately NOT Python syntax (`^` is exponentiation, `**` is a
syntax error), so an eval()-based shortcut fails the suite; the offline
validation below proves that with an explicit eval-cheat variant.

Discriminating edges the spec pins: right-associative `^`, unary minus binding
between `^` and `*`/`/` (`-2^2` is -4 but `(-2)^2` is 4), IEEE-double
semantics with an exact formatting rule (integral results print without a
decimal point, everything else prints as Python's repr), and an all-or-nothing
output contract in file mode that forces buffering (a late error must leave
stdout empty).

Same conventions as build_jsondiff_suite.py: the checked-in dataset is a
generated artifact kept in sync by a drift test, four behavioural slices share
one prompt for graded partial credit, and REFERENCE_SOLUTION exists only for
offline validation — the benchmarked model never sees it. Spec and tests are
frozen once benchmarked; change them only by cutting a new versioned suite id.
"""

from __future__ import annotations

import json
from pathlib import Path

SUITE_ID = "calc-cli"
VERSION = "calc-cli-v1"

DATASET_PATH = Path(__file__).resolve().parents[1] / "configs" / "datasets" / "calc-cli.jsonl"

PROMPT = '''# Task — calc: an arithmetic expression evaluator CLI in Python

Write a single self-contained Python 3 program (standard library only) that
evaluates arithmetic expressions. The program must be fully deterministic and
must not use the network. The expression grammar is NOT Python syntax — do not
evaluate input with `eval`.

## Entry point

Define a function `main(argv: list[str]) -> int` where `argv` is the argument
list *excluding* the program name (like `sys.argv[1:]`). The grader imports
your code and calls `main` directly, so `main` must `return` its exit code
rather than calling `sys.exit`.

## Modes

- `main([expression])` — evaluate one expression and print its result.
- `main(["-f", path])` — read a UTF-8 text file with one expression per line
  (blank and whitespace-only lines are ignored) and print one result per line,
  in input order.
- Any other argument shape is a usage error (exit 2).

If *any* expression fails, nothing at all may be written to standard output —
even results of earlier valid lines in file mode. Error messages may go to
standard error.

## Grammar

- A number is one or more digits, optionally followed by `.` and one or more
  digits (`12`, `3.5` — not `.5`, not `3.`).
- Operators: binary `+  -  *  /  ^`, unary `-`, and parentheses.
- Spaces and tabs may appear between tokens.
- Precedence, tightest first:
  1. `^` (exponentiation) — **right-associative**: `2^3^2` is `2^(3^2)` = 512.
  2. unary minus — it applies to the power expression that follows, so
     `-2^2` is `-(2^2)` = -4 while `(-2)^2` is 4. The exponent may itself be
     signed: `2^-3` is 0.125. Unary minus may also follow a binary operator:
     `5--3` is 8, `2*-3` is -6.
  3. `*` and `/` — left-associative.
  4. binary `+` and `-` — left-associative: `10-3-4` is 3.
- `**` is NOT an operator (two `*` tokens in a row are a syntax error), and any
  character outside the grammar is a syntax error.

## Semantics and output format

All arithmetic is IEEE-double (Python `float`) arithmetic; `/` is always true
division. Format each result on its own line:

- if the value is integral, print it as an integer with no decimal point
  (`8/2` prints `4`, `2^3` prints `8`)
- otherwise print Python's `repr` of the float (`7/2` prints `3.5`, `0.1+0.2`
  prints `0.30000000000000004`)

## Exit codes

- `0` — every expression evaluated; results were printed
- `1` — evaluation error: division by zero, or raising zero to a negative
  power (nothing is printed to standard output)
- `2` — usage error, unreadable file, or syntax error (nothing is printed to
  standard output)

Return only the complete Python source inside a single fenced ```python code
block.
'''

# Shared by every slice: a file writer plus a lenient runner that captures
# stdout and tolerates sys.exit-style returns. `main` comes from the candidate,
# exec'd into the same sandbox namespace before this code runs.
_PRELUDE = '''import contextlib
import io


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

_ARITHMETIC = '''for expression, expected in [
    ("2+3*4", "14"),
    ("(2+3)*4", "20"),
    ("10-3-4", "3"),
    ("100/4/5", "5"),
    ("7/2", "3.5"),
    ("8/2", "4"),
    ("6 * 7", "42"),
    ("0.1+0.2", "0.30000000000000004"),
    ("2.5+2.5", "5"),
]:
    code, out = _run([expression])
    assert code == 0, f"{expression}: expected exit 0, got {code}"
    assert out == expected + "\\n", f"{expression}: expected {expected!r}, got {out!r}"
'''

_POWER_UNARY = '''for expression, expected in [
    ("2^3", "8"),
    ("2^3^2", "512"),
    ("-2^2", "-4"),
    ("(-2)^2", "4"),
    ("2^-3", "0.125"),
    ("5--3", "8"),
    ("2*-3", "-6"),
    ("-3+5", "2"),
    ("2^0.5", "1.4142135623730951"),
]:
    code, out = _run([expression])
    assert code == 0, f"{expression}: expected exit 0, got {code}"
    assert out == expected + "\\n", f"{expression}: expected {expected!r}, got {out!r}"
'''

_FORMAT_FILE = '''_write("exprs.txt", "2+3*4\\n\\n7/2\\n2^3^2\\n")
code, out = _run(["-f", "exprs.txt"])
assert code == 0, f"file mode must exit 0, got {code}"
assert out == "14\\n3.5\\n512\\n", f"file mode output mismatch: {out!r}"

_write("empty.txt", "\\n   \\n")
code, out = _run(["-f", "empty.txt"])
assert code == 0 and out == "", f"blank-only file must print nothing, exit 0: {code} {out!r}"

_write("late-error.txt", "1+1\\n1/0\\n")
code, out = _run(["-f", "late-error.txt"])
assert code == 1, f"evaluation error in file mode must exit 1, got {code}"
assert out == "", f"nothing may reach stdout when a later line fails: {out!r}"

_write("late-syntax.txt", "1+1\\n2+\\n")
code, out = _run(["-f", "late-syntax.txt"])
assert code == 2, f"syntax error in file mode must exit 2, got {code}"
assert out == "", f"nothing may reach stdout on a syntax error: {out!r}"
'''

_ERRORS = '''code, out = _run([])
assert code == 2 and out == "", f"no args must exit 2: {code} {out!r}"

code, out = _run(["1+1", "2+2"])
assert code == 2 and out == "", f"two expressions must exit 2: {code} {out!r}"

code, out = _run(["-f", "missing.txt"])
assert code == 2 and out == "", f"missing file must exit 2: {code} {out!r}"

for bad in ["2+", "2 3", "(4", "4)", "2**3", "2$3", "abc", ""]:
    code, out = _run([bad])
    assert code == 2 and out == "", f"{bad!r} must be a syntax error: {code} {out!r}"

for evaluation_error in ["1/0", "0^-1", "(2-2)^-2"]:
    code, out = _run([evaluation_error])
    assert code == 1 and out == "", f"{evaluation_error!r} must exit 1: {code} {out!r}"
'''

#: Slice name -> acceptance-test body, in canonical record order.
SLICES: tuple[tuple[str, str], ...] = (
    ("arithmetic", _ARITHMETIC),
    ("power-unary", _POWER_UNARY),
    ("format-file", _FORMAT_FILE),
    ("errors", _ERRORS),
)

REFERENCE_SOLUTION = '''class _CalcSyntaxError(Exception):
    pass


def _tokenize(text):
    tokens = []
    index = 0
    while index < len(text):
        char = text[index]
        if char in " \\t":
            index += 1
            continue
        if char.isdigit():
            start = index
            while index < len(text) and text[index].isdigit():
                index += 1
            if index < len(text) and text[index] == ".":
                index += 1
                if index >= len(text) or not text[index].isdigit():
                    raise _CalcSyntaxError("malformed number")
                while index < len(text) and text[index].isdigit():
                    index += 1
            tokens.append(("number", float(text[start:index])))
            continue
        if char in "+-*/^()":
            tokens.append((char, None))
            index += 1
            continue
        raise _CalcSyntaxError(f"unexpected character {char!r}")
    return tokens


def _parse_expression(tokens, pos):
    value, pos = _parse_term(tokens, pos)
    while pos < len(tokens) and tokens[pos][0] in "+-":
        operator = tokens[pos][0]
        right, pos = _parse_term(tokens, pos + 1)
        value = value + right if operator == "+" else value - right
    return value, pos


def _parse_term(tokens, pos):
    value, pos = _parse_factor(tokens, pos)
    while pos < len(tokens) and tokens[pos][0] in "*/":
        operator = tokens[pos][0]
        right, pos = _parse_factor(tokens, pos + 1)
        value = value * right if operator == "*" else value / right
    return value, pos


def _parse_factor(tokens, pos):
    if pos < len(tokens) and tokens[pos][0] == "-":
        value, pos = _parse_factor(tokens, pos + 1)
        return -value, pos
    return _parse_power(tokens, pos)


def _parse_power(tokens, pos):
    value, pos = _parse_atom(tokens, pos)
    if pos < len(tokens) and tokens[pos][0] == "^":
        exponent, pos = _parse_factor(tokens, pos + 1)
        value = value**exponent
    return value, pos


def _parse_atom(tokens, pos):
    if pos >= len(tokens):
        raise _CalcSyntaxError("unexpected end of expression")
    kind, number = tokens[pos]
    if kind == "number":
        return number, pos + 1
    if kind == "(":
        value, pos = _parse_expression(tokens, pos + 1)
        if pos >= len(tokens) or tokens[pos][0] != ")":
            raise _CalcSyntaxError("missing closing parenthesis")
        return value, pos + 1
    raise _CalcSyntaxError(f"unexpected token {kind!r}")


def _evaluate(text):
    tokens = _tokenize(text)
    if not tokens:
        raise _CalcSyntaxError("empty expression")
    value, pos = _parse_expression(tokens, 0)
    if pos != len(tokens):
        raise _CalcSyntaxError("unexpected trailing tokens")
    return value


def _format(value):
    if value.is_integer():
        return str(int(value))
    return repr(value)


def main(argv):
    if len(argv) == 1 and argv[0] != "-f":
        sources = [argv[0]]
    elif len(argv) == 2 and argv[0] == "-f":
        try:
            with open(argv[1], "r", encoding="utf-8") as handle:
                lines = handle.read().splitlines()
        except OSError:
            return 2
        sources = [line for line in lines if line.strip()]
    else:
        return 2
    results = []
    for source in sources:
        try:
            value = _evaluate(source)
        except _CalcSyntaxError:
            return 2
        except (ZeroDivisionError, OverflowError):
            return 1
        results.append(_format(value))
    for line in results:
        print(line)
    return 0
'''

# An eval()-based shortcut with otherwise-correct argv/format/exit handling.
# Exists to prove the grammar is eval-proof: `^` is XOR in Python and `**`
# parses fine, so this variant must fail the power-unary and errors slices.
EVAL_CHEAT = '''def _format(value):
    if value.is_integer():
        return str(int(value))
    return repr(value)


def main(argv):
    if len(argv) == 1 and argv[0] != "-f":
        sources = [argv[0]]
    elif len(argv) == 2 and argv[0] == "-f":
        try:
            with open(argv[1], "r", encoding="utf-8") as handle:
                lines = handle.read().splitlines()
        except OSError:
            return 2
        sources = [line for line in lines if line.strip()]
    else:
        return 2
    results = []
    for source in sources:
        try:
            value = float(eval(source, {"__builtins__": {}}, {}))
        except ZeroDivisionError:
            return 1
        except Exception:
            return 2
        results.append(_format(value))
    for line in results:
        print(line)
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
