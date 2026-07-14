"""Offline validation of the calc-cli mini-app suite.

Same approach as test_jsondiff_suite: the reference solution must pass every
slice in the real sandbox, targeted buggy variants must fail exactly their
slice, and a drift test keeps the checked-in dataset in sync with its
generator. The eval-cheat check is this suite's signature: the grammar is
deliberately not Python, so an eval()-based shortcut must be caught.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from local_code_bench.sandbox import run_in_sandbox
from local_code_bench.tasks import load_suite

REPO_ROOT = Path(__file__).resolve().parents[1]
SUITES_PATH = REPO_ROOT / "configs" / "suites.yaml"
GENERATOR_PATH = REPO_ROOT / "scripts" / "build_calc_suite.py"

EXPECTED_TASK_IDS = (
    "calc-cli/arithmetic",
    "calc-cli/power-unary",
    "calc-cli/format-file",
    "calc-cli/errors",
)


def _generator():
    spec = importlib.util.spec_from_file_location("build_calc_suite", GENERATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generator():
    return _generator()


@pytest.fixture(scope="module")
def suite_tasks():
    return load_suite("calc-cli", suites_path=SUITES_PATH)


def _tests_for(suite_tasks, slice_name: str) -> str:
    by_id = {task.task_id: task for task in suite_tasks}
    return by_id[f"calc-cli/{slice_name}"].test_code


def _buggy(reference: str, target: str, replacement: str) -> str:
    mutated = reference.replace(target, replacement)
    assert mutated != reference, f"bug injection did not apply: {target!r}"
    return mutated


def test_suite_loads_with_stable_task_ids(suite_tasks) -> None:
    assert tuple(task.task_id for task in suite_tasks) == EXPECTED_TASK_IDS
    assert all(task.suite == "calc-cli" for task in suite_tasks)
    assert all(task.entry_point == "main" for task in suite_tasks)
    assert all(task.version == "calc-cli-v1" for task in suite_tasks)
    assert len({task.prompt for task in suite_tasks}) == 1


def test_checked_in_dataset_matches_generator(generator) -> None:
    dataset = REPO_ROOT / "configs" / "datasets" / "calc-cli.jsonl"
    expected = generator.render_jsonl(generator.build_records())
    assert dataset.read_text(encoding="utf-8") == expected, (
        "configs/datasets/calc-cli.jsonl is out of sync with its generator; "
        "rerun scripts/build_calc_suite.py"
    )


@pytest.mark.parametrize("slice_name", [task_id.split("/")[1] for task_id in EXPECTED_TASK_IDS])
def test_reference_solution_passes_every_slice(generator, suite_tasks, slice_name) -> None:
    result = run_in_sandbox(generator.REFERENCE_SOLUTION, _tests_for(suite_tasks, slice_name))

    assert result.passed, f"reference failed {slice_name}: {result.reason}"


def test_eval_cheat_is_caught(generator, suite_tasks) -> None:
    # `^` is XOR in Python and `**` parses fine, so an eval()-based shortcut
    # must fail the grammar-sensitive slices even with perfect argv handling.
    cheat = generator.EVAL_CHEAT

    assert run_in_sandbox(cheat, _tests_for(suite_tasks, "arithmetic")).passed
    assert not run_in_sandbox(cheat, _tests_for(suite_tasks, "power-unary")).passed
    assert not run_in_sandbox(cheat, _tests_for(suite_tasks, "errors")).passed


def test_always_repr_formatting_fails_arithmetic(generator, suite_tasks) -> None:
    buggy = _buggy(
        generator.REFERENCE_SOLUTION,
        "    if value.is_integer():\n        return str(int(value))\n    return repr(value)",
        "    return repr(value)",
    )

    assert not run_in_sandbox(buggy, _tests_for(suite_tasks, "arithmetic")).passed
    assert run_in_sandbox(buggy, _tests_for(suite_tasks, "errors")).passed


def test_unbuffered_file_output_fails_format_file(generator, suite_tasks) -> None:
    # Printing results as they are computed leaks earlier lines to stdout when
    # a later line fails — the all-or-nothing contract requires buffering.
    buggy = _buggy(
        generator.REFERENCE_SOLUTION,
        "results.append(_format(value))",
        "print(_format(value))",
    )

    assert run_in_sandbox(buggy, _tests_for(suite_tasks, "arithmetic")).passed
    assert run_in_sandbox(buggy, _tests_for(suite_tasks, "power-unary")).passed
    assert not run_in_sandbox(buggy, _tests_for(suite_tasks, "format-file")).passed


def test_do_nothing_program_fails_every_slice(suite_tasks) -> None:
    lazy = "def main(argv):\n    return 0\n"
    for task in suite_tasks:
        assert not run_in_sandbox(lazy, task.test_code).passed, task.task_id
