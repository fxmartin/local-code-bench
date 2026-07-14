"""Offline validation of the jsondiff-cli mini-app suite.

Mirrors the EvalPlus validation approach: the acceptance tests are exercised in
the real sandbox against the reference solution (which must pass every slice)
and against known-buggy variants (each of which must fail exactly its targeted
slice). A drift test keeps the checked-in dataset in sync with its generator.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from local_code_bench.sandbox import run_in_sandbox
from local_code_bench.tasks import load_suite

REPO_ROOT = Path(__file__).resolve().parents[1]
SUITES_PATH = REPO_ROOT / "configs" / "suites.yaml"
GENERATOR_PATH = REPO_ROOT / "scripts" / "build_jsondiff_suite.py"

EXPECTED_TASK_IDS = (
    "jsondiff-cli/core",
    "jsondiff-cli/format-order",
    "jsondiff-cli/type-edges",
    "jsondiff-cli/exit-codes",
)


def _generator():
    spec = importlib.util.spec_from_file_location("build_jsondiff_suite", GENERATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generator():
    return _generator()


@pytest.fixture(scope="module")
def suite_tasks():
    return load_suite("jsondiff-cli", suites_path=SUITES_PATH)


def _tests_for(suite_tasks, slice_name: str) -> str:
    by_id = {task.task_id: task for task in suite_tasks}
    return by_id[f"jsondiff-cli/{slice_name}"].test_code


def _buggy(reference: str, target: str, replacement: str) -> str:
    mutated = reference.replace(target, replacement)
    assert mutated != reference, f"bug injection did not apply: {target!r}"
    return mutated


def test_suite_loads_with_stable_task_ids(suite_tasks) -> None:
    assert tuple(task.task_id for task in suite_tasks) == EXPECTED_TASK_IDS
    assert all(task.suite == "jsondiff-cli" for task in suite_tasks)
    assert all(task.entry_point == "main" for task in suite_tasks)
    assert all(task.version == "jsondiff-cli-v1" for task in suite_tasks)
    # One spec, four behavioural slices: every record shares the same prompt.
    assert len({task.prompt for task in suite_tasks}) == 1


def test_checked_in_dataset_matches_generator(generator) -> None:
    dataset = REPO_ROOT / "configs" / "datasets" / "jsondiff-cli.jsonl"
    expected = generator.render_jsonl(generator.build_records())
    assert dataset.read_text(encoding="utf-8") == expected, (
        "configs/datasets/jsondiff-cli.jsonl is out of sync with its generator; "
        "rerun scripts/build_jsondiff_suite.py"
    )


@pytest.mark.parametrize("slice_name", [task_id.split("/")[1] for task_id in EXPECTED_TASK_IDS])
def test_reference_solution_passes_every_slice(generator, suite_tasks, slice_name) -> None:
    result = run_in_sandbox(generator.REFERENCE_SOLUTION, _tests_for(suite_tasks, slice_name))

    assert result.passed, f"reference failed {slice_name}: {result.reason}"


def test_bool_int_confusion_fails_type_edges(generator, suite_tasks) -> None:
    # Dropping the bool guard makes `true` equal `1` (Python's bool-is-int trap).
    buggy = _buggy(
        generator.REFERENCE_SOLUTION,
        "    if isinstance(left, bool) or isinstance(right, bool):\n"
        "        return isinstance(left, bool) and isinstance(right, bool) and left == right\n",
        "",
    )

    assert run_in_sandbox(buggy, _tests_for(suite_tasks, "core")).passed
    assert not run_in_sandbox(buggy, _tests_for(suite_tasks, "type-edges")).passed


def test_unsorted_key_order_fails_format_order(generator, suite_tasks) -> None:
    buggy = _buggy(
        generator.REFERENCE_SOLUTION,
        "sorted(set(left) | set(right))",
        "list(dict.fromkeys([*left, *right]))",
    )

    assert run_in_sandbox(buggy, _tests_for(suite_tasks, "core")).passed
    assert not run_in_sandbox(buggy, _tests_for(suite_tasks, "format-order")).passed


def test_wrong_error_exit_code_fails_exit_codes(generator, suite_tasks) -> None:
    buggy = _buggy(
        generator.REFERENCE_SOLUTION,
        "        except (OSError, ValueError):\n            return 2\n",
        "        except (OSError, ValueError):\n            return 1\n",
    )

    assert run_in_sandbox(buggy, _tests_for(suite_tasks, "core")).passed
    assert not run_in_sandbox(buggy, _tests_for(suite_tasks, "exit-codes")).passed


def test_do_nothing_program_fails_every_slice(suite_tasks) -> None:
    lazy = "def main(argv):\n    return 0\n"
    for task in suite_tasks:
        assert not run_in_sandbox(lazy, task.test_code).passed, task.task_id
