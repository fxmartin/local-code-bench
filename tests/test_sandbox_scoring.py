from __future__ import annotations

from local_code_bench.sandbox import run_in_sandbox
from local_code_bench.scoring import extract_code, score_completion
from local_code_bench.tasks import BenchmarkTask


def test_run_in_sandbox_passes_code_and_tests() -> None:
    result = run_in_sandbox("def add(a, b):\n    return a + b", "assert add(1, 2) == 3")

    assert result.passed is True


def test_run_in_sandbox_blocks_outside_write(tmp_path) -> None:
    result = run_in_sandbox(
        f"open({str(tmp_path / 'escape.txt')!r}, 'w').write('bad')",
        "assert True",
    )

    assert result.passed is False
    assert "PermissionError" in result.reason
    assert not (tmp_path / "escape.txt").exists()


def test_score_completion_extracts_python_fence() -> None:
    task = BenchmarkTask(
        task_id="x",
        suite="humaneval",
        prompt="",
        test_code="assert add(1, 2) == 3",
        entry_point="add",
        version="test",
    )

    score = score_completion(task, "```python\ndef add(a, b):\n    return a + b\n```")

    assert extract_code(score.extracted_code) == score.extracted_code
    assert score.passed is True
