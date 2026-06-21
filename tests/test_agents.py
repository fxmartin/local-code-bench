from __future__ import annotations

from local_code_bench.agents import build_codex_command, extract_codex_total_tokens, materialize_task_workspace
from local_code_bench.agents import run_codex_task
from local_code_bench.config import AgentConfig
from local_code_bench.results import read_jsonl
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
    assert "--output-last-message" in command
    assert "--skip-git-repo-check" in command
    assert "--model" in command


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


def test_extract_codex_total_tokens_from_stderr() -> None:
    stderr = "some log\n\ntokens used\n13,029\n"

    assert extract_codex_total_tokens(stderr) == 13029
