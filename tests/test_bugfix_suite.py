"""Offline validation of the bugfix-py debugging suite.

The suite is self-proving: for every record the shipped buggy source must FAIL
its own test_code in the real sandbox (so the bug report is real and the tests
detect it) and the reference fix must PASS (so a correct fix is achievable and
the regression assertions are satisfiable). A drift test keeps the checked-in
dataset in sync with its generator.
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
GENERATOR_PATH = REPO_ROOT / "scripts" / "build_bugfix_suite.py"

EXPECTED_TASK_IDS = (
    "bugfix-py/mutable-default",
    "bugfix-py/shallow-copy",
    "bugfix-py/off-by-one-window",
    "bugfix-py/tie-break-sort",
    "bugfix-py/iterator-exhaustion",
)


def _generator():
    spec = importlib.util.spec_from_file_location("build_bugfix_suite", GENERATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generator():
    return _generator()


@pytest.fixture(scope="module")
def suite_tasks():
    return load_suite("bugfix-py", suites_path=SUITES_PATH)


def test_suite_loads_with_stable_task_ids(suite_tasks) -> None:
    assert tuple(task.task_id for task in suite_tasks) == EXPECTED_TASK_IDS
    assert all(task.suite == "bugfix-py" for task in suite_tasks)
    assert all(task.version == "bugfix-py-v1" for task in suite_tasks)


def test_every_prompt_embeds_its_buggy_source_and_report(generator, suite_tasks) -> None:
    by_id = {task.task_id: task for task in suite_tasks}
    for case in generator.CASES:
        prompt = by_id[f"bugfix-py/{case.name}"].prompt
        assert case.buggy.rstrip("\n") in prompt, f"{case.name}: buggy source not in prompt"
        assert case.report in prompt, f"{case.name}: bug report not in prompt"
        assert by_id[f"bugfix-py/{case.name}"].entry_point == case.entry_point


def test_checked_in_dataset_matches_generator(generator) -> None:
    dataset = REPO_ROOT / "configs" / "datasets" / "bugfix-py.jsonl"
    expected = generator.render_jsonl(generator.build_records())
    assert dataset.read_text(encoding="utf-8") == expected, (
        "configs/datasets/bugfix-py.jsonl is out of sync with its generator; "
        "rerun scripts/build_bugfix_suite.py"
    )


@pytest.mark.parametrize(
    "case_name", [task_id.split("/")[1] for task_id in EXPECTED_TASK_IDS]
)
def test_buggy_source_fails_and_reference_fix_passes(generator, case_name) -> None:
    case = next(c for c in generator.CASES if c.name == case_name)

    buggy = run_in_sandbox(case.buggy, case.test_code)
    assert not buggy.passed, f"{case.name}: the shipped buggy source must fail its tests"

    fixed = run_in_sandbox(case.fixed, case.test_code)
    assert fixed.passed, f"{case.name}: reference fix failed: {fixed.reason}"
