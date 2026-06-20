from __future__ import annotations

from local_code_bench.agents import build_codex_command, materialize_task_workspace
from local_code_bench.config import AgentConfig
from local_code_bench.tasks import BenchmarkTask


def test_materialize_task_workspace_is_deterministic_enough(tmp_path) -> None:
    task = BenchmarkTask("suite/1", "humaneval", "prompt", "assert solution() == 1", "solution", "v")

    workspace = materialize_task_workspace(task, parent=tmp_path)

    assert workspace.instructions.name == "INSTRUCTIONS.md"
    assert workspace.solution.name == "solution.py"
    assert "prompt" in workspace.instructions.read_text(encoding="utf-8")


def test_build_codex_command_uses_explicit_sandbox(tmp_path) -> None:
    task = BenchmarkTask("suite/1", "humaneval", "prompt", "assert True", "solution", "v")
    workspace = materialize_task_workspace(task, parent=tmp_path)
    agent = AgentConfig("codex", "codex", "codex", "workspace-write", 10, model="gpt-5")

    command = build_codex_command(agent, workspace)

    assert command[:4] == ["codex", "exec", "--sandbox", "workspace-write"]
    assert "--model" in command
