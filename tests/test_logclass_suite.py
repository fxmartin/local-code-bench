"""Offline validation of the logclass-cli suite (Python port of Task A).

Same approach as the other ladder suites — reference passes every slice in the
real sandbox, targeted buggy variants fail exactly their slice, drift test —
plus this suite's signature check: the reference classifier must agree with
the authoritative opencode Task A ground truth
(local_code_bench.opencode.fixtures.classify_line) on the shipped sample log,
so the Python rung and the Go original can never disagree on severity.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from local_code_bench.opencode import fixtures
from local_code_bench.sandbox import run_in_sandbox
from local_code_bench.tasks import load_suite

REPO_ROOT = Path(__file__).resolve().parents[1]
SUITES_PATH = REPO_ROOT / "configs" / "suites.yaml"
GENERATOR_PATH = REPO_ROOT / "scripts" / "build_logclass_suite.py"

EXPECTED_TASK_IDS = (
    "logclass-cli/counts",
    "logclass-cli/json-filter",
    "logclass-cli/edge-rules",
    "logclass-cli/exit-codes",
)


def _generator():
    spec = importlib.util.spec_from_file_location("build_logclass_suite", GENERATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generator():
    return _generator()


@pytest.fixture(scope="module")
def suite_tasks():
    return load_suite("logclass-cli", suites_path=SUITES_PATH)


def _tests_for(suite_tasks, slice_name: str) -> str:
    by_id = {task.task_id: task for task in suite_tasks}
    return by_id[f"logclass-cli/{slice_name}"].test_code


def _buggy(reference: str, target: str, replacement: str) -> str:
    mutated = reference.replace(target, replacement)
    assert mutated != reference, f"bug injection did not apply: {target!r}"
    return mutated


def test_suite_loads_with_stable_task_ids(suite_tasks) -> None:
    assert tuple(task.task_id for task in suite_tasks) == EXPECTED_TASK_IDS
    assert all(task.suite == "logclass-cli" for task in suite_tasks)
    assert all(task.entry_point == "main" for task in suite_tasks)
    assert all(task.version == "logclass-cli-v1" for task in suite_tasks)
    assert len({task.prompt for task in suite_tasks}) == 1


def test_checked_in_dataset_matches_generator(generator) -> None:
    dataset = REPO_ROOT / "configs" / "datasets" / "logclass-cli.jsonl"
    expected = generator.render_jsonl(generator.build_records())
    assert dataset.read_text(encoding="utf-8") == expected, (
        "configs/datasets/logclass-cli.jsonl is out of sync with its generator; "
        "rerun scripts/build_logclass_suite.py"
    )


def test_reference_matches_task_a_ground_truth(generator) -> None:
    # The port's severity semantics must equal the Go Task A single source of
    # truth on the shipped sample log — the two rungs can never disagree.
    namespace: dict = {"__name__": "__bench__"}
    exec(generator.REFERENCE_SOLUTION, namespace)  # noqa: S102 - our own reference source
    sample = fixtures.load_fixture()

    mine = {i: namespace["classify_line"](line) for i, line in enumerate(sample, start=1)}

    assert mine == fixtures.ground_truth(sample)


@pytest.mark.parametrize("slice_name", [task_id.split("/")[1] for task_id in EXPECTED_TASK_IDS])
def test_reference_solution_passes_every_slice(generator, suite_tasks, slice_name) -> None:
    result = run_in_sandbox(generator.REFERENCE_SOLUTION, _tests_for(suite_tasks, slice_name))

    assert result.passed, f"reference failed {slice_name}: {result.reason}"


def test_case_insensitive_matching_fails_edge_rules(generator, suite_tasks) -> None:
    buggy = _buggy(
        generator.REFERENCE_SOLUTION,
        'if "ERROR" in line or "FATAL" in line:',
        'if "ERROR" in line.upper() or "FATAL" in line.upper():',
    )

    assert run_in_sandbox(buggy, _tests_for(suite_tasks, "counts")).passed
    assert not run_in_sandbox(buggy, _tests_for(suite_tasks, "edge-rules")).passed


def test_wrong_missing_file_exit_fails_exit_codes(generator, suite_tasks) -> None:
    buggy = _buggy(
        generator.REFERENCE_SOLUTION,
        "    except OSError:\n        return 1\n",
        "    except OSError:\n        return 2\n",
    )

    assert run_in_sandbox(buggy, _tests_for(suite_tasks, "counts")).passed
    assert not run_in_sandbox(buggy, _tests_for(suite_tasks, "exit-codes")).passed


def test_json_dropping_zero_counts_fails_json_filter(generator, suite_tasks) -> None:
    buggy = _buggy(
        generator.REFERENCE_SOLUTION,
        "print(json.dumps(counts))",
        "print(json.dumps({k: v for k, v in counts.items() if v}))",
    )

    assert run_in_sandbox(buggy, _tests_for(suite_tasks, "counts")).passed
    assert not run_in_sandbox(buggy, _tests_for(suite_tasks, "json-filter")).passed


def test_do_nothing_program_fails_every_slice(suite_tasks) -> None:
    lazy = "def main(argv):\n    return 0\n"
    for task in suite_tasks:
        assert not run_in_sandbox(lazy, task.test_code).passed, task.task_id
