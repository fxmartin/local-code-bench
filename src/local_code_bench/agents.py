"""Codex agent-mode benchmarking."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from local_code_bench.config import AgentConfig
from local_code_bench.results import append_jsonl
from local_code_bench.scoring import score_completion
from local_code_bench.tasks import BenchmarkTask


@dataclass(frozen=True)
class AgentWorkspace:
    root: Path
    instructions: Path
    solution: Path
    tests: Path
    final_message: Path


def materialize_task_workspace(
    task: BenchmarkTask,
    *,
    parent: str | Path | None = None,
) -> AgentWorkspace:
    root = Path(tempfile.mkdtemp(prefix=f"codex-{task.task_id.replace('/', '-')}-", dir=parent))
    instructions = root / "INSTRUCTIONS.md"
    solution = root / "solution.py"
    tests = root / "test_solution.py"
    final_message = root / "codex-final.txt"
    instructions.write_text(
        f"Implement `{task.entry_point}` in `solution.py`.\n\n{task.prompt}\n",
        encoding="utf-8",
    )
    solution.write_text("# Codex should replace this file.\n", encoding="utf-8")
    tests.write_text(f"from solution import *\n\n{task.test_code}", encoding="utf-8")
    return AgentWorkspace(root, instructions, solution, tests, final_message)


def build_codex_command(agent: AgentConfig, workspace: AgentWorkspace) -> list[str]:
    command = [
        agent.command,
        "exec",
        "--sandbox",
        agent.sandbox,
        "--cd",
        str(workspace.root),
        "--skip-git-repo-check",
        "--output-last-message",
        str(workspace.final_message),
    ]
    if agent.model:
        command.extend(["--model", agent.model])
    if agent.profile:
        command.extend(["--profile", agent.profile])
    command.append(workspace.instructions.read_text(encoding="utf-8"))
    return command


def run_codex_task(
    *,
    agent: AgentConfig,
    task: BenchmarkTask,
    result_path: Path,
    retain_workspace: bool = False,
) -> dict[str, object]:
    workspace = materialize_task_workspace(task)
    started = perf_counter()
    command = build_codex_command(agent, workspace)
    try:
        completed = subprocess.run(
            command,
            cwd=workspace.root,
            capture_output=True,
            text=True,
            timeout=agent.timeout_seconds,
            check=False,
        )
        wall_time = perf_counter() - started
        if completed.returncode == 0 and workspace.solution.exists():
            solution = workspace.solution.read_text(encoding="utf-8")
            score = score_completion(task, solution)
            passed = score.passed
            reason = score.reason
        else:
            passed = False
            reason = f"codex exit {completed.returncode}"
        record = {
            "run_mode": "agent",
            "agent": agent.name,
            "task_id": task.task_id,
            "suite": task.suite,
            "passed": passed,
            "failure_reason": reason,
            "wall_time_seconds": wall_time,
            "sandbox_mode": agent.sandbox,
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "final_message": _read_optional(workspace.final_message),
            "command": command,
            "cost_status": "unavailable",
        }
    except subprocess.TimeoutExpired as exc:
        record = {
            "run_mode": "agent",
            "agent": agent.name,
            "task_id": task.task_id,
            "suite": task.suite,
            "passed": False,
            "failure_reason": "codex timeout",
            "wall_time_seconds": agent.timeout_seconds,
            "sandbox_mode": agent.sandbox,
            "exit_code": None,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "cost_status": "unavailable",
        }
    except FileNotFoundError as exc:
        record = {
            "run_mode": "agent",
            "agent": agent.name,
            "task_id": task.task_id,
            "suite": task.suite,
            "passed": False,
            "failure_reason": f"codex executable not found: {agent.command}",
            "wall_time_seconds": 0.0,
            "sandbox_mode": agent.sandbox,
            "exit_code": None,
            "stdout": "",
            "stderr": str(exc),
            "cost_status": "unavailable",
        }
    finally:
        if not retain_workspace:
            shutil.rmtree(workspace.root, ignore_errors=True)
    append_jsonl(result_path, record)
    return record


def _read_optional(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")
