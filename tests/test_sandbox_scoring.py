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


def test_run_in_sandbox_blocks_network_socket() -> None:
    result = run_in_sandbox(
        "import socket\nsocket.socket()",
        "assert True",
    )

    assert result.passed is False
    assert "PermissionError" in result.reason


def test_run_in_sandbox_blocks_subprocess() -> None:
    result = run_in_sandbox(
        "import subprocess\nsubprocess.run(['echo', 'bad'])",
        "assert True",
    )

    assert result.passed is False
    assert "PermissionError" in result.reason


def test_run_in_sandbox_tolerates_main_guard() -> None:
    # Program-shaped candidates (mini-app suites) often ship a __main__ guard;
    # the sandbox namespace defines __name__ so the guard is skipped, not fatal.
    result = run_in_sandbox(
        "def add(a, b):\n"
        "    return a + b\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(99)\n",
        "assert add(1, 2) == 3",
    )

    assert result.passed is True


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


def test_extract_code_supports_unlabeled_fence_with_function() -> None:
    assert extract_code("```\ndef add(a, b):\n    return a + b\n```") == (
        "def add(a, b):\n    return a + b"
    )


def test_extract_code_returns_empty_when_no_code_block_matches() -> None:
    assert extract_code("```text\nnot code\n```") == ""


def test_score_completion_fails_when_code_cannot_be_extracted() -> None:
    task = BenchmarkTask(
        task_id="x",
        suite="humaneval",
        prompt="",
        test_code="assert True",
        entry_point="solution",
        version="test",
    )

    score = score_completion(task, "```text\nnot code\n```")

    assert score.passed is False
    assert score.reason == "code extraction failed"
    assert score.sandbox is None
