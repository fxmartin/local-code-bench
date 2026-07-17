"""Correctness scoring for endpoint and agent outputs."""

from __future__ import annotations

import ast
import textwrap
from dataclasses import dataclass

from local_code_bench.sandbox import (
    DEFAULT_SANDBOX_TIMEOUT_SECONDS,
    SandboxResult,
    run_in_sandbox,
)
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
    candidates: list[str] = []
    parts = text.split("```")
    for index in range(1, len(parts), 2):
        block = parts[index]
        lines = block.splitlines()
        if lines and lines[0].strip().lower() in {"python", "py"}:
            candidate = textwrap.dedent("\n".join(lines[1:])).strip()
            if candidate:
                candidates.append(candidate)
            continue
        candidate = textwrap.dedent(block).strip()
        if "def " in candidate or "class " in candidate:
            candidates.append(candidate)

    for candidate in reversed(candidates):
        try:
            ast.parse(candidate)
        except SyntaxError:
            continue
        return candidate
    return candidates[-1] if candidates else ""


def score_completion(
    task: BenchmarkTask,
    completion: str,
    *,
    timeout_seconds: float = DEFAULT_SANDBOX_TIMEOUT_SECONDS,
) -> ScoreResult:
    code = extract_code(completion)
    if not code:
        return ScoreResult(False, "code extraction failed", "", None)
    sandbox = run_in_sandbox(code, task.test_code, timeout_seconds=timeout_seconds)
    return ScoreResult(sandbox.passed, sandbox.reason, code, sandbox)
