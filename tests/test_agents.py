from __future__ import annotations

import subprocess
import sys

import pytest

from local_code_bench.agents import AgentWorkspace
from local_code_bench.agents import (
    adapter_for,
    build_codex_command,
    completed_agent_pairs,
    detect_agent_installation,
    extract_codex_total_tokens,
    materialize_task_workspace,
    register_agent_adapter,
    run_agent_task,
)
from local_code_bench.agents import run_codex_task
from local_code_bench.config import AgentConfig
from local_code_bench.results import append_jsonl, read_jsonl
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
    agent = AgentConfig(
        "codex",
        "codex",
        "codex",
        "workspace-write",
        10,
        model="gpt-5",
        profile="default",
    )

    command = build_codex_command(agent, workspace)

    assert command[:4] == ["codex", "exec", "--sandbox", "workspace-write"]
    assert "--output-last-message" in command
    assert "--skip-git-repo-check" in command
    assert "--model" in command
    assert "--profile" in command


def test_codex_adapter_command_matches_legacy_builder(tmp_path) -> None:
    task = BenchmarkTask("suite/1", "humaneval", "prompt", "assert True", "solution", "v")
    workspace = materialize_task_workspace(task, parent=tmp_path)
    agent = AgentConfig(
        "codex",
        "codex",
        "codex",
        "workspace-write",
        10,
        model="gpt-5",
        profile="default",
    )

    assert adapter_for("codex").build_command(agent, workspace) == build_codex_command(
        agent, workspace
    )


def test_adapter_for_unknown_type_lists_registered_harnesses() -> None:
    with pytest.raises(ValueError, match="unknown agent harness type 'missing'.*codex"):
        adapter_for("missing")


def test_register_agent_adapter_restores_previous_adapter() -> None:
    original = adapter_for("codex")

    class ReplacementAdapter:
        kind = "codex"

        def build_command(self, agent: AgentConfig, workspace: AgentWorkspace) -> list[str]:
            return ["replacement"]

        def parse_result(
            self,
            agent: AgentConfig,
            workspace: AgentWorkspace,
            completed: subprocess.CompletedProcess[str],
        ) -> dict[str, object]:
            return {"cost_status": "unavailable"}

        def detect(self, agent: AgentConfig):
            raise NotImplementedError

    replacement = ReplacementAdapter()

    with register_agent_adapter(replacement):
        assert adapter_for("codex") is replacement

    assert adapter_for("codex") is original


def test_codex_adapter_reports_failure_without_final_message(tmp_path) -> None:
    task = BenchmarkTask("suite/1", "humaneval", "prompt", "assert True", "solution", "v")
    workspace = materialize_task_workspace(task, parent=tmp_path)
    agent = AgentConfig("codex", "codex", "codex", "workspace-write", 10)
    completed = subprocess.CompletedProcess(["codex"], 7, stdout="", stderr="no usage")

    parsed = adapter_for("codex").parse_result(agent, workspace, completed)

    assert parsed == {
        "final_message": "",
        "failure_reason": "codex exit 7",
        "cost_status": "unavailable",
    }


def test_run_codex_task_with_fake_executable_scores_solution(tmp_path) -> None:
    fake = tmp_path / "codex"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "from pathlib import Path\n"
        "Path('solution.py').write_text('def add(a, b):\\n    return a + b\\n')\n"
        "args = __import__('sys').argv\n"
        "if '--output-last-message' in args:\n"
        "    Path(args[args.index('--output-last-message') + 1]).write_text('done')\n"
        "sys.stderr.write('tokens used\\n1,234\\n')\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    task = BenchmarkTask(
        "suite/1",
        "humaneval",
        "prompt",
        "assert add(1, 2) == 3",
        "add",
        "v",
    )
    agent = AgentConfig("codex", "codex", str(fake), "workspace-write", 10)
    result_path = tmp_path / "agent.jsonl"
    messages: list[str] = []

    record = run_codex_task(
        agent=agent,
        task=task,
        result_path=result_path,
        progress=messages.append,
    )

    assert record["passed"] is True
    assert record["final_message"] == "done"
    assert record["tokens"] == {"total": 1234, "estimated": False}
    assert record["cost_status"] == "tokens_available"
    assert read_jsonl(result_path)[0]["passed"] is True
    assert messages == ["codex suite/1: passed"]


def test_run_agent_task_uses_registered_codex_adapter(tmp_path) -> None:
    fake = tmp_path / "codex"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "from pathlib import Path\n"
        "Path('solution.py').write_text('def add(a, b):\\n    return a + b\\n')\n"
        "args = __import__('sys').argv\n"
        "Path(args[args.index('--output-last-message') + 1]).write_text('done')\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    task = BenchmarkTask("suite/1", "humaneval", "prompt", "assert add(1, 2) == 3", "add", "v")
    agent = AgentConfig("codex", "codex", str(fake), "workspace-write", 10)
    result_path = tmp_path / "agent.jsonl"

    record = run_agent_task(agent=agent, task=task, result_path=result_path)

    assert record["passed"] is True
    assert record["final_message"] == "done"
    assert record["cost_status"] == "unavailable"


def test_run_agent_task_uses_adapter_failure_reason_and_can_retain_workspace(tmp_path) -> None:
    task = BenchmarkTask("suite/1", "humaneval", "prompt", "assert add(1, 2) == 3", "add", "v")
    agent = AgentConfig("custom", "custom", sys.executable, "workspace-write", 10)
    result_path = tmp_path / "agent.jsonl"

    class FailingAdapter:
        kind = "custom"
        workspace: AgentWorkspace | None = None

        def build_command(self, agent: AgentConfig, workspace: AgentWorkspace) -> list[str]:
            self.workspace = workspace
            return [agent.command, "-c", "import sys; sys.exit(9)"]

        def parse_result(
            self,
            agent: AgentConfig,
            workspace: AgentWorkspace,
            completed: subprocess.CompletedProcess[str],
        ) -> dict[str, object]:
            return {"failure_reason": "custom adapter rejected output", "cost_status": "unavailable"}

        def detect(self, agent: AgentConfig):
            raise NotImplementedError

    adapter = FailingAdapter()

    with register_agent_adapter(adapter):
        record = run_agent_task(
            agent=agent,
            task=task,
            result_path=result_path,
            retain_workspace=True,
        )

    assert record["passed"] is False
    assert record["failure_reason"] == "custom adapter rejected output"
    assert record["exit_code"] == 9
    assert adapter.workspace is not None
    assert adapter.workspace.root.exists()


def test_run_agent_task_records_timeout_for_registered_harness(tmp_path) -> None:
    task = BenchmarkTask("suite/1", "humaneval", "prompt", "assert True", "solution", "v")
    agent = AgentConfig("slow", "slow", sys.executable, "workspace-write", 0.01)
    result_path = tmp_path / "agent.jsonl"

    class SlowAdapter:
        kind = "slow"

        def build_command(self, agent: AgentConfig, workspace: AgentWorkspace) -> list[str]:
            return [agent.command, "-c", "import time; time.sleep(5)"]

        def parse_result(
            self,
            agent: AgentConfig,
            workspace: AgentWorkspace,
            completed: subprocess.CompletedProcess[str],
        ) -> dict[str, object]:
            raise AssertionError("timeout should bypass result parsing")

        def detect(self, agent: AgentConfig):
            raise NotImplementedError

    with register_agent_adapter(SlowAdapter()):
        record = run_agent_task(agent=agent, task=task, result_path=result_path)

    assert record["passed"] is False
    assert record["failure_reason"] == "slow timeout"
    assert record["exit_code"] is None
    assert record["cost_status"] == "unavailable"


def test_extract_codex_total_tokens_from_stderr() -> None:
    stderr = "some log\n\ntokens used\n13,029\n"

    assert extract_codex_total_tokens(stderr) == 13029


def test_completed_agent_pairs_reads_existing_results(tmp_path) -> None:
    result_path = tmp_path / "agent.jsonl"
    append_jsonl(result_path, {"record_type": "metadata"})
    append_jsonl(result_path, {"run_mode": "endpoint", "model": "m", "task_id": "suite/1"})
    append_jsonl(result_path, {"run_mode": "agent", "agent": "codex", "task_id": "suite/1"})

    assert completed_agent_pairs(result_path) == {("codex", "suite/1")}


def test_completed_agent_pairs_missing_file_is_empty(tmp_path) -> None:
    assert completed_agent_pairs(tmp_path / "missing.jsonl") == set()


def test_run_codex_task_records_executable_not_found(tmp_path) -> None:
    task = BenchmarkTask("suite/1", "humaneval", "prompt", "assert True", "solution", "v")
    agent = AgentConfig("codex", "codex", str(tmp_path / "missing-codex"), "workspace-write", 10)
    result_path = tmp_path / "agent.jsonl"
    messages: list[str] = []

    record = run_codex_task(agent=agent, task=task, result_path=result_path, progress=messages.append)

    assert record["passed"] is False
    assert record["failure_reason"] == f"codex executable not found: {agent.command}"
    assert record["cost_status"] == "unavailable"
    assert read_jsonl(result_path)[0]["failure_reason"] == record["failure_reason"]
    assert messages == ["codex suite/1: failed"]


def test_detect_agent_installation_reports_missing_command_and_url(tmp_path) -> None:
    agent = AgentConfig(
        "codex",
        "codex",
        str(tmp_path / "missing-codex"),
        "workspace-write",
        10,
        url="https://github.com/openai/codex",
    )

    detection = detect_agent_installation(agent)

    assert detection.installed is False
    assert detection.path is None
    assert detection.url == "https://github.com/openai/codex"


def test_extract_codex_total_tokens_returns_none_without_usage() -> None:
    assert extract_codex_total_tokens("no usage here") is None
