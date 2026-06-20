"""Correctness scoring for endpoint and agent outputs."""

from __future__ import annotations

from dataclasses import dataclass

from local_code_bench.sandbox import SandboxResult, run_in_sandbox
from local_code_bench.tasks import BenchmarkTask


@dataclass(frozen=True)
class ScoreResult:
    passed: bool
    reason: str
    extracted_code: str
    sandbox: SandboxResult | None


def extract_code(text: str) -> str:
    if "```" not in text:
        return text.strip()
    parts = text.split("```")
    for index in range(1, len(parts), 2):
        block = parts[index]
        lines = block.splitlines()
        if lines and lines[0].strip().lower() in {"python", "py"}:
            return "\n".join(lines[1:]).strip()
        candidate = block.strip()
        if "def " in candidate or "class " in candidate:
            return candidate
    return ""


def score_completion(
    task: BenchmarkTask,
    completion: str,
    *,
    timeout_seconds: float = 5.0,
) -> ScoreResult:
    code = extract_code(completion)
    if not code:
        return ScoreResult(False, "code extraction failed", "", None)
    sandbox = run_in_sandbox(code, task.test_code, timeout_seconds=timeout_seconds)
    return ScoreResult(sandbox.passed, sandbox.reason, code, sandbox)
