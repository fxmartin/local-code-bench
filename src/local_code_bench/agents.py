"""Agent-mode benchmarking through pluggable harness adapters."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Protocol

from local_code_bench.config import AgentConfig
from local_code_bench.results import append_jsonl, read_jsonl
from local_code_bench.scoring import score_completion
from local_code_bench.tasks import BenchmarkTask


@dataclass(frozen=True)
class AgentWorkspace:
    root: Path
    instructions: Path
    solution: Path
    tests: Path
    final_message: Path


@dataclass(frozen=True)
class AgentInstallation:
    name: str
    type: str
    command: str
    installed: bool
    path: str | None
    url: str | None


class AgentAdapter(Protocol):
    kind: str

    def build_command(self, agent: AgentConfig, workspace: AgentWorkspace) -> list[str]:
        """Build the non-interactive command used to run one materialized task."""

    def parse_result(
        self,
        agent: AgentConfig,
        workspace: AgentWorkspace,
        completed: subprocess.CompletedProcess[str],
    ) -> dict[str, object]:
        """Parse harness-specific output, final text, exit, usage, and cost fields."""

    def detect(self, agent: AgentConfig) -> AgentInstallation:
        """Report whether the configured harness command is installed, without installing it."""


_ADAPTERS: dict[str, AgentAdapter] = {}


def supported_harness_kinds() -> tuple[str, ...]:
    return tuple(sorted(_ADAPTERS))


def adapter_for(kind: str) -> AgentAdapter:
    try:
        return _ADAPTERS[kind]
    except KeyError as exc:
        supported = ", ".join(supported_harness_kinds()) or "(none)"
        raise ValueError(f"unknown agent harness type '{kind}'. Supported types: {supported}") from exc


@contextmanager
def register_agent_adapter(adapter: AgentAdapter):
    previous = _ADAPTERS.get(adapter.kind)
    _ADAPTERS[adapter.kind] = adapter
    try:
        yield
    finally:
        if previous is None:
            _ADAPTERS.pop(adapter.kind, None)
        else:
            _ADAPTERS[adapter.kind] = previous


class CodexAdapter:
    kind = "codex"

    def build_command(self, agent: AgentConfig, workspace: AgentWorkspace) -> list[str]:
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

    def parse_result(
        self,
        agent: AgentConfig,
        workspace: AgentWorkspace,
        completed: subprocess.CompletedProcess[str],
    ) -> dict[str, object]:
        fields = {
            "final_message": _read_optional(workspace.final_message),
            **_agent_cost_fields(completed.stderr),
        }
        if completed.returncode != 0:
            fields["failure_reason"] = f"codex exit {completed.returncode}"
        return fields

    def detect(self, agent: AgentConfig) -> AgentInstallation:
        path = shutil.which(agent.command)
        return AgentInstallation(
            name=agent.name,
            type=agent.type,
            command=agent.command,
            installed=path is not None,
            path=path,
            url=agent.url,
        )


_ADAPTERS[CodexAdapter.kind] = CodexAdapter()


def completed_agent_pairs(result_path: Path) -> set[tuple[str, str]]:
    if not result_path.exists():
        return set()
    pairs = set()
    for record in read_jsonl(result_path):
        if record.get("record_type") == "metadata":
            continue
        agent = record.get("agent")
        task_id = record.get("task_id")
        if record.get("run_mode") == "agent" and isinstance(agent, str) and isinstance(task_id, str):
            pairs.add((agent, task_id))
    return pairs


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
    return adapter_for("codex").build_command(agent, workspace)


def detect_agent_installation(agent: AgentConfig) -> AgentInstallation:
    return adapter_for(agent.type).detect(agent)


def run_agent_task(
    *,
    agent: AgentConfig,
    task: BenchmarkTask,
    result_path: Path,
    retain_workspace: bool = False,
    progress: Callable[[str], None] | None = None,
) -> dict[str, object]:
    workspace = materialize_task_workspace(task)
    started = perf_counter()
    adapter = adapter_for(agent.type)
    command = adapter.build_command(agent, workspace)
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
        parsed = adapter.parse_result(agent, workspace, completed)
        if completed.returncode == 0 and workspace.solution.exists():
            solution = workspace.solution.read_text(encoding="utf-8")
            score = score_completion(task, solution)
            passed = score.passed
            reason = score.reason
        else:
            passed = False
            reason = str(parsed.pop("failure_reason", f"{agent.type} exit {completed.returncode}"))
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
            "command": command,
            **parsed,
        }
    except subprocess.TimeoutExpired as exc:
        record = {
            "run_mode": "agent",
            "agent": agent.name,
            "task_id": task.task_id,
            "suite": task.suite,
            "passed": False,
            "failure_reason": f"{agent.type} timeout",
            "wall_time_seconds": agent.timeout_seconds,
            "sandbox_mode": agent.sandbox,
            "exit_code": None,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            **_agent_cost_fields(exc.stderr or ""),
        }
    except FileNotFoundError as exc:
        record = {
            "run_mode": "agent",
            "agent": agent.name,
            "task_id": task.task_id,
            "suite": task.suite,
            "passed": False,
            "failure_reason": f"{agent.type} executable not found: {agent.command}",
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
    if progress is not None:
        status = "passed" if record.get("passed") is True else "failed"
        progress(f"{agent.name} {task.task_id}: {status}")
    return record


def run_codex_task(
    *,
    agent: AgentConfig,
    task: BenchmarkTask,
    result_path: Path,
    retain_workspace: bool = False,
    progress: Callable[[str], None] | None = None,
) -> dict[str, object]:
    return run_agent_task(
        agent=agent,
        task=task,
        result_path=result_path,
        retain_workspace=retain_workspace,
        progress=progress,
    )


def _read_optional(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _agent_cost_fields(stderr: str) -> dict[str, object]:
    total_tokens = extract_codex_total_tokens(stderr)
    if total_tokens is None:
        return {"cost_status": "unavailable"}
    return {
        "tokens": {
            "total": total_tokens,
            "estimated": False,
        },
        "cost_status": "tokens_available",
    }


def extract_codex_total_tokens(stderr: str) -> int | None:
    match = re.search(r"tokens used\s*:?\s*\n?\s*([\d,]+)", stderr, flags=re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1).replace(",", ""))
