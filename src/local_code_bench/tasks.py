"""Benchmark task loading for HumanEval and MBPP."""

from __future__ import annotations

import ast
import gzip
import json
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

SuiteName = Literal["humaneval", "mbpp", "humaneval-plus", "mbpp-plus"]

HUMANEVAL_URL = (
    "https://raw.githubusercontent.com/openai/human-eval/master/data/HumanEval.jsonl.gz"
)
MBPP_URL = (
    "https://raw.githubusercontent.com/google-research/google-research/master/mbpp/sanitized-mbpp.json"
)

# EvalPlus releases are not auto-downloaded (the URL/version moves and the file is
# large). Drop the release jsonl(.gz) into the cache dir under these names, or
# export it from the `evalplus` package. See docs/EVALUATION-METHODOLOGY.md.
EVALPLUS_FILENAMES: dict[str, tuple[str, ...]] = {
    "humaneval-plus": ("HumanEvalPlus.jsonl.gz", "HumanEvalPlus.jsonl"),
    "mbpp-plus": ("MbppPlus.jsonl.gz", "MbppPlus.jsonl"),
}

_MBPP_TUPLE_EACH_ARGUMENT = frozenset(
    {
        2,
        116,
        132,
        143,
        222,
        261,
        273,
        394,
        399,
        421,
        424,
        429,
        470,
        560,
        579,
        596,
        616,
        630,
        726,
        740,
        744,
        809,
    }
)
_MBPP_NESTED_TUPLES = frozenset(
    {63, 64, 70, 94, 120, 237, 272, 299, 400, 409, 417, 438, 473, 614, 780}
)
_MBPP_TUPLE_FIRST_ARGUMENT = frozenset({250, 405, 446, 617, 720, 763, 808})
_MBPP_DEEPLY_NESTED_TUPLES = frozenset({259, 401, 445})
_MBPP_RECURSIVE_TUPLES = frozenset({580, 615, 791})


# Hand-curated anchor subset: a fixed, deterministic spread of HumanEval tasks
# used by the `canary` suite for a fast "still usable?" quality signal instead of
# the full 164-task pass. It is a pragmatic stand-in for a formal tinyBenchmarks /
# IRT anchor set; regenerate it with item-response selection when that lands and
# keep the IDs stable so historical canary runs stay comparable.
CANARY_HUMANEVAL_IDS: tuple[str, ...] = (
    "HumanEval/0",
    "HumanEval/2",
    "HumanEval/7",
    "HumanEval/11",
    "HumanEval/16",
    "HumanEval/23",
    "HumanEval/28",
    "HumanEval/35",
    "HumanEval/40",
    "HumanEval/51",
    "HumanEval/63",
    "HumanEval/72",
    "HumanEval/83",
    "HumanEval/92",
    "HumanEval/101",
    "HumanEval/113",
    "HumanEval/126",
    "HumanEval/138",
    "HumanEval/150",
    "HumanEval/161",
)


class TaskLoadError(RuntimeError):
    """Raised when a benchmark suite cannot be loaded."""


@dataclass(frozen=True)
class BenchmarkTask:
    task_id: str
    suite: SuiteName
    prompt: str
    test_code: str
    entry_point: str
    version: str


def load_suite(name: str, *, cache_dir: str | Path = ".cache/benchmarks") -> list[BenchmarkTask]:
    if name == "humaneval":
        return load_humaneval(cache_dir=cache_dir)
    if name == "mbpp":
        return load_mbpp(cache_dir=cache_dir)
    if name == "canary":
        return load_canary(cache_dir=cache_dir)
    if name in EVALPLUS_FILENAMES:
        return load_evalplus(name, cache_dir=cache_dir)
    raise TaskLoadError(
        f"unknown suite '{name}'. Available suites: "
        "humaneval, mbpp, canary, humaneval-plus, mbpp-plus"
    )


def load_canary(*, cache_dir: str | Path = ".cache/benchmarks") -> list[BenchmarkTask]:
    """Return the curated HumanEval anchor subset, in its fixed canonical order."""

    by_id = {task.task_id: task for task in load_humaneval(cache_dir=cache_dir)}
    missing = [task_id for task_id in CANARY_HUMANEVAL_IDS if task_id not in by_id]
    if missing:
        raise TaskLoadError(f"canary references unknown HumanEval tasks: {', '.join(missing)}")
    return [by_id[task_id] for task_id in CANARY_HUMANEVAL_IDS]


def load_evalplus(
    name: str,
    *,
    cache_dir: str | Path = ".cache/benchmarks",
    max_inputs: int | None = None,
) -> list[BenchmarkTask]:
    """Load an EvalPlus suite (HumanEval+ / MBPP+) from a cached release jsonl.

    Each task is scored by differential testing: the candidate is compared against
    the EvalPlus canonical solution across the union of base and plus inputs. The
    file is not auto-downloaded; place it in ``cache_dir`` (see EVALPLUS_FILENAMES).
    """

    if name not in EVALPLUS_FILENAMES:
        raise TaskLoadError(f"'{name}' is not an EvalPlus suite")
    path = _find_cached(Path(cache_dir), EVALPLUS_FILENAMES[name])
    if path is None:
        wanted = " or ".join(EVALPLUS_FILENAMES[name])
        raise TaskLoadError(
            f"{name} requires a cached EvalPlus release file ({wanted}) in {cache_dir}. "
            "Download it from the evalplus releases or export it with the evalplus package."
        )
    rows = _read_jsonl_any(path)
    tasks = [_parse_evalplus(row, name, index, max_inputs) for index, row in enumerate(rows)]
    if not tasks:
        raise TaskLoadError(f"{name} cache {path} contained no tasks")
    return tasks


def _parse_evalplus(row: Any, name: str, index: int, max_inputs: int | None) -> BenchmarkTask:
    if not isinstance(row, dict):
        raise TaskLoadError(f"{name}[{index}] must be a mapping")
    task_id = str(row.get("task_id") or f"{name}/{index}")
    entry_point = row.get("entry_point")
    if not isinstance(entry_point, str) or not entry_point:
        raise TaskLoadError(f"{name} task {task_id} missing entry_point")
    prompt = str(row.get("prompt", ""))
    canonical = row.get("canonical_solution")
    if not isinstance(canonical, str) or not canonical.strip():
        raise TaskLoadError(f"{name} task {task_id} missing canonical_solution")
    base_inputs = row.get("base_input") or []
    plus_inputs = row.get("plus_input") or []
    if not isinstance(base_inputs, list) or not isinstance(plus_inputs, list):
        raise TaskLoadError(f"{name} task {task_id} base_input/plus_input must be lists")
    inputs = [*base_inputs, *plus_inputs]
    if name == "mbpp-plus":
        inputs = _deserialize_mbpp_inputs(task_id, inputs)
    if max_inputs is not None:
        inputs = inputs[:max_inputs]
    if not inputs:
        raise TaskLoadError(f"{name} task {task_id} has no test inputs")
    atol = row.get("atol", 0)
    atol = float(atol) if isinstance(atol, int | float) and not isinstance(atol, bool) else 0.0
    return build_evalplus_task(
        task_id=task_id,
        suite=name,  # type: ignore[arg-type]
        prompt=prompt,
        canonical_solution=canonical,
        entry_point=entry_point,
        inputs=inputs,
        atol=atol,
    )


def _deserialize_mbpp_inputs(task_id: str, inputs: list[Any]) -> list[Any]:
    """Restore the non-JSON input types encoded by the official MBPP+ release."""

    try:
        number = int(task_id.rsplit("/", 1)[-1])
    except ValueError as exc:
        raise TaskLoadError(f"mbpp-plus task has invalid task_id {task_id!r}") from exc

    if number in _MBPP_TUPLE_EACH_ARGUMENT:
        return [[tuple(value) for value in arguments] for arguments in inputs]
    if number in _MBPP_NESTED_TUPLES:
        return [
            [[tuple(value) for value in nested] for nested in arguments]
            for arguments in inputs
        ]
    if number in {75, 413, 444, 753}:
        return [[[tuple(value) for value in arguments[0]], arguments[1]] for arguments in inputs]
    if number in {106, 750}:
        return [[arguments[0], tuple(arguments[1])] for arguments in inputs]
    if number == 115:
        return [
            [[set(value) if isinstance(value, list) and value else {} for value in arguments[0]]]
            for arguments in inputs
        ]
    if number == 124:
        return [(float(arguments[0]), complex(arguments[1])) for arguments in inputs]
    if number in _MBPP_TUPLE_FIRST_ARGUMENT:
        return [[tuple(arguments[0]), arguments[1]] for arguments in inputs]
    if number in _MBPP_DEEPLY_NESTED_TUPLES:
        nested = [
            [[tuple(value) for value in group] for group in arguments]
            for arguments in inputs
        ]
        return [[tuple(value) for value in arguments] for arguments in nested]
    if number == 278:
        nested = [
            [[tuple(value) if isinstance(value, list) else value for value in arguments[0]]]
            for arguments in inputs
        ]
        return [[tuple(value) for value in arguments] for arguments in nested]
    if number == 307:
        return [[tuple(arguments[0]), arguments[1], arguments[2]] for arguments in inputs]
    if number == 722:
        return [
            [{key: tuple(value) for key, value in arguments[0].items()}, *arguments[1:]]
            for arguments in inputs
        ]
    if number == 252:
        return [[complex(arguments[0])] for arguments in inputs]
    if number in _MBPP_RECURSIVE_TUPLES:
        return [_lists_to_tuples(arguments) for arguments in inputs]
    return inputs


def _lists_to_tuples(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_lists_to_tuples(item) for item in value)
    return value


def build_evalplus_task(
    *,
    task_id: str,
    suite: SuiteName,
    prompt: str,
    canonical_solution: str,
    entry_point: str,
    inputs: list[Any],
    atol: float = 0.0,
) -> BenchmarkTask:
    """Build a differential-testing BenchmarkTask from EvalPlus fields.

    The generated test_code rebuilds the canonical reference in an isolated
    namespace (so it cannot shadow the candidate), then asserts the candidate
    matches the reference on every input, with float tolerance and deep-copied
    arguments so in-place mutation cannot leak between the two calls.
    """

    test_code = (
        "import copy\n"
        "import math\n"
        f"ENTRY = {entry_point!r}\n"
        f"ATOL = {atol!r}\n"
        f"PROMPT = {prompt!r}\n"
        f"CANON = {canonical_solution!r}\n"
        f"INPUTS = {inputs!r}\n"
        "_refns = {}\n"
        "try:\n"
        "    exec(PROMPT + '\\n' + CANON, _refns)\n"
        "    if ENTRY not in _refns:\n"
        "        raise KeyError(ENTRY)\n"
        "except Exception:\n"
        "    _refns = {}\n"
        "    exec(CANON, _refns)\n"
        "_ref = _refns[ENTRY]\n"
        "_cand = globals()[ENTRY]\n"
        "def _eq(a, b):\n"
        "    if isinstance(a, float) or isinstance(b, float):\n"
        "        try:\n"
        "            return math.isclose(a, b, rel_tol=1e-9, abs_tol=ATOL or 1e-9)\n"
        "        except TypeError:\n"
        "            return a == b\n"
        "    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):\n"
        "        return len(a) == len(b) and all(_eq(x, y) for x, y in zip(a, b))\n"
        "    return a == b\n"
        "for _args in INPUTS:\n"
        "    _expected = _ref(*copy.deepcopy(_args))\n"
        "    _actual = _cand(*copy.deepcopy(_args))\n"
        "    assert _eq(_actual, _expected), (\n"
        "        'evalplus mismatch on %r: got %r expected %r' % (_args, _actual, _expected)\n"
        "    )\n"
    )
    return BenchmarkTask(
        task_id=task_id,
        suite=suite,
        prompt=prompt,
        test_code=test_code,
        entry_point=entry_point,
        version=f"{suite}-differential",
    )


def load_humaneval(*, cache_dir: str | Path = ".cache/benchmarks") -> list[BenchmarkTask]:
    path = _ensure_cached(Path(cache_dir), "HumanEval.jsonl.gz", HUMANEVAL_URL)
    rows = _read_jsonl_gz(path)
    tasks = [
        BenchmarkTask(
            task_id=str(row["task_id"]),
            suite="humaneval",
            prompt=str(row["prompt"]),
            test_code=f"{row['test']}\ncheck({row['entry_point']})\n",
            entry_point=str(row["entry_point"]),
            version="humaneval-jsonl-gz",
        )
        for row in rows
    ]
    if len(tasks) != 164:
        raise TaskLoadError(f"HumanEval cache has {len(tasks)} tasks, expected 164")
    return tasks


def load_mbpp(*, cache_dir: str | Path = ".cache/benchmarks") -> list[BenchmarkTask]:
    path = _ensure_cached(Path(cache_dir), "sanitized-mbpp.json", MBPP_URL)
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw if isinstance(raw, list) else raw.get("data")
    if not isinstance(rows, list):
        raise TaskLoadError("MBPP cache must be a list or contain a 'data' list")
    return [_parse_mbpp(row) for row in rows]


def limit_tasks(tasks: Iterable[BenchmarkTask], limit: int | None) -> list[BenchmarkTask]:
    selected = list(tasks)
    if limit is None:
        return selected
    if limit < 0:
        raise TaskLoadError("limit must be non-negative")
    return selected[:limit]


def _parse_mbpp(row: Any) -> BenchmarkTask:
    if not isinstance(row, dict):
        raise TaskLoadError("MBPP task rows must be mappings")
    tests = row.get("test_list") or row.get("tests")
    if not isinstance(tests, list) or not tests:
        raise TaskLoadError("MBPP task missing test_list")
    entry_point = _entry_point_from_tests(tests)
    prompt = str(row.get("prompt", row.get("text", ""))).strip()
    tests_text = "\n".join(str(test) for test in tests)
    return BenchmarkTask(
        task_id=f"mbpp/{row.get('task_id', row.get('id'))}",
        suite="mbpp",
        prompt=(
            f"{prompt}\n\n"
            f"Define a Python function named `{entry_point}` that satisfies these tests:\n"
            f"{tests_text}\n"
            "Return only the function implementation."
        ),
        test_code=tests_text + "\n",
        entry_point=entry_point,
        version="sanitized-mbpp-json",
    )


def _entry_point_from_tests(tests: list[Any]) -> str:
    wrapper_calls = {
        "abs",
        "all",
        "any",
        "bool",
        "dict",
        "float",
        "frozenset",
        "int",
        "len",
        "list",
        "max",
        "min",
        "round",
        "set",
        "sorted",
        "str",
        "sum",
        "tuple",
    }
    for test in tests:
        try:
            tree = ast.parse(str(test))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id not in wrapper_calls:
                    return node.func.id
    return "solution"


def _ensure_cached(cache_dir: Path, filename: str, url: str) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / filename
    if path.exists():
        return path
    try:
        urllib.request.urlretrieve(url, path)
    except Exception as exc:  # pragma: no cover - exercised without network in integration use
        raise TaskLoadError(f"{filename} is not cached and download failed: {exc}") from exc
    return path


def _find_cached(cache_dir: Path, filenames: tuple[str, ...]) -> Path | None:
    for filename in filenames:
        path = cache_dir / filename
        if path.exists():
            return path
    return None


def _read_jsonl_gz(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]


def _read_jsonl_any(path: Path) -> list[dict[str, Any]]:
    if path.suffix == ".gz":
        return _read_jsonl_gz(path)
    with path.open("rt", encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]
