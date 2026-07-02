"""Agent-mode benchmarking through pluggable harness adapters."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Protocol, cast

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
        ...

    def parse_result(
        self,
        agent: AgentConfig,
        workspace: AgentWorkspace,
        completed: subprocess.CompletedProcess[str],
    ) -> dict[str, object]:
        """Parse harness-specific output, final text, exit, usage, and cost fields."""
        ...

    def detect(self, agent: AgentConfig) -> AgentInstallation:
        """Report whether the configured harness command is installed, without installing it."""
        ...


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


class ClaudeCodeAdapter:
    kind = "claude-code"
    default_url = "https://code.claude.com/docs"

    def build_command(self, agent: AgentConfig, workspace: AgentWorkspace) -> list[str]:
        if not agent.model:
            raise ValueError("claude-code agents require a configured model")
        return [
            agent.command,
            "-p",
            workspace.instructions.read_text(encoding="utf-8"),
            "--output-format",
            "json",
            "--permission-mode",
            "dontAsk",
            "--allowedTools",
            "Read,Edit,Bash",
            "--model",
            agent.model,
            "--bare",
        ]

    def build_environment(self, agent: AgentConfig) -> dict[str, str] | None:
        updates: dict[str, str] = {}
        if agent.anthropic_base_url:
            updates["ANTHROPIC_BASE_URL"] = agent.anthropic_base_url
        if agent.anthropic_api_key_env and agent.anthropic_api_key_env in os.environ:
            updates["ANTHROPIC_API_KEY"] = os.environ[agent.anthropic_api_key_env]
        return updates or None

    def parse_result(
        self,
        agent: AgentConfig,
        workspace: AgentWorkspace,
        completed: subprocess.CompletedProcess[str],
    ) -> dict[str, object]:
        fields: dict[str, object] = {}
        payload = _parse_json_object(completed.stdout)
        if isinstance(payload.get("result"), str):
            fields["final_message"] = payload["result"]
        else:
            fields["final_message"] = ""
        if isinstance(payload.get("session_id"), str):
            fields["session_id"] = payload["session_id"]
        if isinstance(payload.get("usage"), dict):
            fields["usage"] = payload["usage"]
        if isinstance(payload.get("total_cost_usd"), int | float):
            fields["total_cost_usd"] = float(payload["total_cost_usd"])
            fields["cost_status"] = "cost_available"
        elif "usage" in fields:
            fields["cost_status"] = "usage_available"
        else:
            fields["cost_status"] = "unavailable"
        gateway = _claude_gateway_fields(agent)
        if gateway is not None:
            fields["claude_code_gateway"] = gateway
        if completed.returncode != 0:
            fields["failure_reason"] = f"claude-code exit {completed.returncode}"
        return fields

    def detect(self, agent: AgentConfig) -> AgentInstallation:
        path = shutil.which(agent.command)
        return AgentInstallation(
            name=agent.name,
            type=agent.type,
            command=agent.command,
            installed=path is not None,
            path=path,
            url=agent.url or self.default_url,
        )


_ADAPTERS[ClaudeCodeAdapter.kind] = ClaudeCodeAdapter()


class QwenCodeAdapter:
    kind = "qwen-code"

    def build_command(self, agent: AgentConfig, workspace: AgentWorkspace) -> list[str]:
        command = [
            agent.command,
            "--prompt",
            workspace.instructions.read_text(encoding="utf-8"),
            "--output-format",
            "json",
        ]
        if agent.model:
            command.extend(["--model", agent.model])
        command.extend(["--approval-mode", "auto-edit"])
        if agent.sandbox and agent.sandbox.lower() not in {"none", "off", "disabled"}:
            command.append("--sandbox")
        if agent.system_prompt:
            command.extend(["--system-prompt", agent.system_prompt])
        if agent.append_system_prompt:
            command.extend(["--append-system-prompt", agent.append_system_prompt])
        return command

    def build_environment(self, agent: AgentConfig) -> dict[str, str] | None:
        env: dict[str, str] = {}
        if agent.base_url:
            env["OPENAI_BASE_URL"] = agent.base_url
        if agent.model:
            env["OPENAI_MODEL"] = agent.model
        if agent.api_key_env and agent.api_key_env in os.environ:
            env["OPENAI_API_KEY"] = os.environ[agent.api_key_env]
        return env or None

    def parse_result(
        self,
        agent: AgentConfig,
        workspace: AgentWorkspace,
        completed: subprocess.CompletedProcess[str],
    ) -> dict[str, object]:
        fields: dict[str, object] = {"cost_status": "unavailable"}
        if completed.stdout.strip():
            fields.update(_parse_qwen_json_output(completed.stdout))
        if completed.returncode != 0:
            fields["failure_reason"] = f"qwen-code exit {completed.returncode}"
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


_ADAPTERS[QwenCodeAdapter.kind] = QwenCodeAdapter()


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
        completed = subprocess.run(  # noqa: S603 - configured benchmark CLI, no shell.
            command,
            cwd=workspace.root,
            capture_output=True,
            text=True,
            timeout=agent.timeout_seconds,
            check=False,
            env=_agent_subprocess_env(adapter, agent),
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
            "stdout": _process_text(exc.stdout),
            "stderr": _process_text(exc.stderr),
            **_agent_cost_fields(_process_text(exc.stderr)),
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


def _agent_subprocess_env(adapter: AgentAdapter, agent: AgentConfig) -> dict[str, str] | None:
    build_environment = getattr(adapter, "build_environment", None)
    if not callable(build_environment):
        return None
    overrides = cast(dict[str, str] | None, build_environment(agent))
    if not overrides:
        return None
    return {**os.environ, **overrides}


def _process_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _parse_qwen_json_output(stdout: str) -> dict[str, object]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return {"final_message": stdout.strip()}

    events = payload if isinstance(payload, list) else [payload]
    fields: dict[str, object] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        if isinstance(event.get("session_id"), str):
            fields["session_id"] = event["session_id"]
        result = event.get("result")
        if isinstance(result, str):
            fields["final_message"] = result
        usage = event.get("usage")
        if isinstance(usage, dict):
            fields["usage"] = usage
            total_tokens = _usage_total_tokens(usage)
            if total_tokens is not None:
                fields["tokens"] = {"total": total_tokens, "estimated": False}
                fields["cost_status"] = "tokens_available"
    if "cost_status" not in fields:
        fields["cost_status"] = "unavailable"
    return fields


def _usage_total_tokens(usage: dict[object, object]) -> int | None:
    for key in ("total_tokens", "totalTokens", "total"):
        value = usage.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool):
            return int(value)
    token_keys = (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "prompt_tokens",
        "completion_tokens",
    )
    values = [usage.get(key) for key in token_keys]
    numeric = [int(value) for value in values if isinstance(value, int | float)]
    return sum(numeric) if numeric else None


def extract_codex_total_tokens(stderr: str) -> int | None:
    match = re.search(r"tokens used\s*:?\s*\n?\s*([\d,]+)", stderr, flags=re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def _parse_json_object(output: str) -> dict[str, object]:
    text = output.strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        for line in reversed(text.splitlines()):
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
        else:
            return {}
    return payload if isinstance(payload, dict) else {}


def _claude_gateway_fields(agent: AgentConfig) -> dict[str, object] | None:
    if not agent.anthropic_base_url and not agent.anthropic_api_key_env:
        return None
    gateway: dict[str, object] = {"enabled": True}
    if agent.anthropic_base_url:
        gateway["anthropic_base_url"] = agent.anthropic_base_url
    if agent.anthropic_api_key_env:
        gateway["api_key_env"] = agent.anthropic_api_key_env
    return gateway
