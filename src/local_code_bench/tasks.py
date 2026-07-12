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

BUILTIN_SUITES: tuple[str, ...] = ("humaneval", "mbpp", "canary", "humaneval-plus", "mbpp-plus")

# Custom-suite registry consulted by load_suite for non-builtin names. Kept in
# sync with suite_catalog.DEFAULT_SUITES_PATH (which imports from this module,
# so the constant cannot live in one place without a cycle).
DEFAULT_SUITES_PATH = "configs/suites.yaml"

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
    suite: str
    prompt: str
    test_code: str
    entry_point: str
    version: str


def load_suite(
    name: str,
    *,
    cache_dir: str | Path = ".cache/benchmarks",
    suites_path: str | Path = DEFAULT_SUITES_PATH,
) -> list[BenchmarkTask]:
    if name == "humaneval":
        return load_humaneval(cache_dir=cache_dir)
    if name == "mbpp":
        return load_mbpp(cache_dir=cache_dir)
    if name == "canary":
        return load_canary(cache_dir=cache_dir)
    if name in EVALPLUS_FILENAMES:
        return load_evalplus(name, cache_dir=cache_dir)
    definitions = _custom_suite_definitions(suites_path)
    for definition in definitions:
        if definition.id == name:
            return _load_custom_suite(definition, Path(suites_path).resolve().parent)
    available = [*BUILTIN_SUITES, *(definition.id for definition in definitions)]
    raise TaskLoadError(f"unknown suite '{name}'. Available suites: {', '.join(available)}")


def _custom_suite_definitions(suites_path: str | Path) -> list[Any]:
    """Read the custom-suite registry, mapping config errors to TaskLoadError."""

    # Imported lazily: suite_catalog imports this module at top level.
    from local_code_bench.config import ConfigError
    from local_code_bench.suite_catalog import load_custom_suites

    try:
        return load_custom_suites(suites_path)
    except ConfigError as exc:
        raise TaskLoadError(f"invalid custom-suite registry {suites_path}: {exc}") from exc


def _load_custom_suite(definition: Any, base: Path) -> list[BenchmarkTask]:
    """Load a config-registered dataset into sandbox-scorable BenchmarkTasks."""

    source = (base / definition.source).resolve()
    if not source.exists():
        raise TaskLoadError(f"custom suite '{definition.id}' source not found: {source}")
    fmt = (definition.format or _dataset_format(source)).lower()
    if fmt == "jsonl":
        rows: Any = _read_jsonl_any(source)
    elif fmt == "json":
        raw = json.loads(source.read_text(encoding="utf-8"))
        rows = raw if isinstance(raw, list) else raw.get("data") if isinstance(raw, dict) else None
        if not isinstance(rows, list):
            raise TaskLoadError(
                f"custom suite '{definition.id}' json dataset must be a list "
                "or contain a 'data' list"
            )
    else:
        raise TaskLoadError(f"custom suite '{definition.id}' has unsupported format '{fmt}'")
    tasks = [_parse_custom_row(row, definition.id, index) for index, row in enumerate(rows)]
    if not tasks:
        raise TaskLoadError(f"custom suite '{definition.id}' dataset {source} contained no tasks")
    return tasks


def _dataset_format(path: Path) -> str:
    suffixes = [suffix.lower() for suffix in path.suffixes]
    if ".jsonl" in suffixes:
        return "jsonl"
    if ".json" in suffixes:
        return "json"
    raise TaskLoadError(f"cannot infer dataset format from '{path.name}'")


def _parse_custom_row(row: Any, suite_id: str, index: int) -> BenchmarkTask:
    if not isinstance(row, dict):
        raise TaskLoadError(f"custom suite '{suite_id}' record [{index}] must be a mapping")
    task_id = str(row.get("task_id") or f"{suite_id}/{index}")
    prompt = row.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise TaskLoadError(f"custom suite '{suite_id}' task {task_id} missing prompt")
    test_code = row.get("test_code")
    if not isinstance(test_code, str) or not test_code.strip():
        raise TaskLoadError(f"custom suite '{suite_id}' task {task_id} missing test_code")
    entry_point = row.get("entry_point", "solution")
    if not isinstance(entry_point, str) or not entry_point:
        raise TaskLoadError(f"custom suite '{suite_id}' task {task_id} has invalid entry_point")
    return BenchmarkTask(
        task_id=task_id,
        suite=suite_id,
        prompt=prompt,
        test_code=test_code,
        entry_point=entry_point,
        version=str(row.get("version") or f"custom:{suite_id}"),
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
