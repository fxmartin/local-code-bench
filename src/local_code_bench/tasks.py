"""Benchmark task loading for HumanEval and MBPP."""

from __future__ import annotations

import gzip
import json
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

SuiteName = Literal["humaneval", "mbpp"]

HUMANEVAL_URL = (
    "https://raw.githubusercontent.com/openai/human-eval/master/data/HumanEval.jsonl.gz"
)
MBPP_URL = (
    "https://raw.githubusercontent.com/google-research/google-research/master/mbpp/sanitized-mbpp.json"
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
    raise TaskLoadError(f"unknown suite '{name}'. Available suites: humaneval, mbpp")


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
    return BenchmarkTask(
        task_id=f"mbpp/{row.get('task_id', row.get('id'))}",
        suite="mbpp",
        prompt=str(row.get("prompt", row.get("text", ""))),
        test_code="\n".join(str(test) for test in tests) + "\n",
        entry_point=entry_point,
        version="sanitized-mbpp-json",
    )


def _entry_point_from_tests(tests: list[Any]) -> str:
    first = str(tests[0])
    marker = "assert "
    if marker in first:
        candidate = first.split(marker, 1)[1].split("(", 1)[0].strip()
        if candidate.isidentifier():
            return candidate
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


def _read_jsonl_gz(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]
