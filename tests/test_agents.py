from __future__ import annotations

import json
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
    with pytest.raises(ValueError, match="unknown agent harness type 'missing'.*codex.*qwen-code"):
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


def test_claude_code_adapter_builds_locked_down_print_command(tmp_path) -> None:
    task = BenchmarkTask("suite/1", "humaneval", "prompt", "assert True", "solution", "v")
    workspace = materialize_task_workspace(task, parent=tmp_path)
    agent = AgentConfig(
        "claude-frontier",
        "claude-code",
        "claude",
        "workspace-write",
        10,
        model="claude-sonnet-4-6",
    )

    command = adapter_for("claude-code").build_command(agent, workspace)

    assert command[0] == "claude"
    assert "-p" in command
    assert workspace.instructions.read_text(encoding="utf-8") in command
    assert command[command.index("--output-format") + 1] == "json"
    assert command[command.index("--permission-mode") + 1] == "dontAsk"
    assert command[command.index("--allowedTools") + 1] == "Read,Edit,Bash"
    assert command[command.index("--model") + 1] == "claude-sonnet-4-6"
    assert "--bare" in command
    assert "--dangerously-skip-permissions" not in command


def test_claude_code_adapter_requires_configured_model(tmp_path) -> None:
    task = BenchmarkTask("suite/1", "humaneval", "prompt", "assert True", "solution", "v")
    workspace = materialize_task_workspace(task, parent=tmp_path)
    agent = AgentConfig("claude-frontier", "claude-code", "claude", "workspace-write", 10)

    with pytest.raises(ValueError, match="claude-code agents require a configured model"):
        adapter_for("claude-code").build_command(agent, workspace)


def test_claude_code_parse_result_records_cost_and_usage(tmp_path) -> None:
    task = BenchmarkTask("suite/1", "humaneval", "prompt", "assert True", "solution", "v")
    workspace = materialize_task_workspace(task, parent=tmp_path)
    agent = AgentConfig("claude-frontier", "claude-code", "claude", "workspace-write", 10)
    completed = subprocess.CompletedProcess(
        ["claude"],
        0,
        stdout=(
            '{"type":"result","result":"done","session_id":"sess_123",'
            '"total_cost_usd":0.0142,"usage":{"input_tokens":100,"output_tokens":25}}'
        ),
        stderr="",
    )

    parsed = adapter_for("claude-code").parse_result(agent, workspace, completed)

    assert parsed == {
        "final_message": "done",
        "session_id": "sess_123",
        "total_cost_usd": 0.0142,
        "usage": {"input_tokens": 100, "output_tokens": 25},
        "cost_status": "cost_available",
    }


def test_claude_code_parse_result_marks_usage_without_cost(tmp_path) -> None:
    task = BenchmarkTask("suite/1", "humaneval", "prompt", "assert True", "solution", "v")
    workspace = materialize_task_workspace(task, parent=tmp_path)
    agent = AgentConfig("claude-frontier", "claude-code", "claude", "workspace-write", 10)
    completed = subprocess.CompletedProcess(
        ["claude"],
        0,
        stdout='{"result":"done","usage":{"input_tokens":100,"output_tokens":25}}',
        stderr="",
    )

    parsed = adapter_for("claude-code").parse_result(agent, workspace, completed)

    assert parsed == {
        "final_message": "done",
        "usage": {"input_tokens": 100, "output_tokens": 25},
        "cost_status": "usage_available",
    }


def test_claude_code_parse_result_reads_json_from_last_valid_output_line(tmp_path) -> None:
    task = BenchmarkTask("suite/1", "humaneval", "prompt", "assert True", "solution", "v")
    workspace = materialize_task_workspace(task, parent=tmp_path)
    agent = AgentConfig("claude-frontier", "claude-code", "claude", "workspace-write", 10)
    completed = subprocess.CompletedProcess(
        ["claude"],
        0,
        stdout='{"result":"done"}\nnot-json',
        stderr="",
    )

    parsed = adapter_for("claude-code").parse_result(agent, workspace, completed)

    assert parsed == {
        "final_message": "done",
        "cost_status": "unavailable",
    }


def test_claude_code_parse_result_ignores_malformed_or_non_object_output(tmp_path) -> None:
    task = BenchmarkTask("suite/1", "humaneval", "prompt", "assert True", "solution", "v")
    workspace = materialize_task_workspace(task, parent=tmp_path)
    agent = AgentConfig("claude-frontier", "claude-code", "claude", "workspace-write", 10)

    for stdout in ("not-json\nstill-not-json", '["not", "an", "object"]'):
        completed = subprocess.CompletedProcess(["claude"], 0, stdout=stdout, stderr="")

        parsed = adapter_for("claude-code").parse_result(agent, workspace, completed)

        assert parsed == {
            "final_message": "",
            "cost_status": "unavailable",
        }


def test_claude_code_parse_result_marks_missing_usage_unavailable(tmp_path) -> None:
    task = BenchmarkTask("suite/1", "humaneval", "prompt", "assert True", "solution", "v")
    workspace = materialize_task_workspace(task, parent=tmp_path)
    agent = AgentConfig(
        "claude-local",
        "claude-code",
        "claude",
        "workspace-write",
        10,
        model="local-qwen",
        anthropic_base_url="http://127.0.0.1:4000",
        anthropic_api_key_env="LOCAL_GATEWAY_KEY",
    )
    completed = subprocess.CompletedProcess(
        ["claude"],
        0,
        stdout='{"type":"result","result":"done","session_id":"sess_local"}',
        stderr="",
    )

    parsed = adapter_for("claude-code").parse_result(agent, workspace, completed)

    assert parsed == {
        "final_message": "done",
        "session_id": "sess_local",
        "cost_status": "unavailable",
        "claude_code_gateway": {
            "enabled": True,
            "anthropic_base_url": "http://127.0.0.1:4000",
            "api_key_env": "LOCAL_GATEWAY_KEY",
        },
    }


@pytest.mark.parametrize(
    ("base_url", "api_key_env", "expected_gateway"),
    [
        (
            "http://127.0.0.1:4000",
            None,
            {"enabled": True, "anthropic_base_url": "http://127.0.0.1:4000"},
        ),
        ("", "LOCAL_GATEWAY_KEY", {"enabled": True, "api_key_env": "LOCAL_GATEWAY_KEY"}),
    ],
)
def test_claude_code_parse_result_records_partial_gateway_config(
    tmp_path,
    base_url: str,
    api_key_env: str | None,
    expected_gateway: dict[str, object],
) -> None:
    task = BenchmarkTask("suite/1", "humaneval", "prompt", "assert True", "solution", "v")
    workspace = materialize_task_workspace(task, parent=tmp_path)
    agent = AgentConfig(
        "claude-local",
        "claude-code",
        "claude",
        "workspace-write",
        10,
        model="local-qwen",
        anthropic_base_url=base_url,
        anthropic_api_key_env=api_key_env,
    )
    completed = subprocess.CompletedProcess(["claude"], 0, stdout='{"result":"done"}', stderr="")

    parsed = adapter_for("claude-code").parse_result(agent, workspace, completed)

    assert parsed["claude_code_gateway"] == expected_gateway


def test_run_agent_task_with_fake_claude_records_json_metadata_and_gateway_env(
    tmp_path, monkeypatch
) -> None:
    fake = tmp_path / "claude"
    env_capture = tmp_path / "env.txt"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        "from pathlib import Path\n"
        "Path('solution.py').write_text('def add(a, b):\\n    return a + b\\n')\n"
        f"Path({str(env_capture)!r}).write_text("
        "os.environ.get('ANTHROPIC_BASE_URL', '') + '\\n' + "
        "os.environ.get('ANTHROPIC_API_KEY', ''))\n"
        "print('{\"result\":\"done\",\"session_id\":\"sess_abc\","
        "\"total_cost_usd\":0.02,\"usage\":{\"input_tokens\":5,\"output_tokens\":6}}')\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    monkeypatch.setenv("LOCAL_GATEWAY_KEY", "secret-value")
    task = BenchmarkTask("suite/1", "humaneval", "prompt", "assert add(1, 2) == 3", "add", "v")
    agent = AgentConfig(
        "claude-local",
        "claude-code",
        str(fake),
        "workspace-write",
        10,
        model="local-qwen",
        anthropic_base_url="http://127.0.0.1:4000",
        anthropic_api_key_env="LOCAL_GATEWAY_KEY",
    )
    result_path = tmp_path / "agent.jsonl"

    record = run_agent_task(agent=agent, task=task, result_path=result_path)

    assert record["passed"] is True
    assert record["final_message"] == "done"
    assert record["session_id"] == "sess_abc"
    assert record["total_cost_usd"] == 0.02
    assert record["usage"] == {"input_tokens": 5, "output_tokens": 6}
    assert record["cost_status"] == "cost_available"
    assert record["claude_code_gateway"]["enabled"] is True
    assert "secret-value" not in str(read_jsonl(result_path)[0])
    assert env_capture.read_text(encoding="utf-8") == "http://127.0.0.1:4000\nsecret-value"


def test_run_agent_task_records_claude_nonzero_as_infra_failure(tmp_path) -> None:
    fake = tmp_path / "claude"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.stderr.write('gateway unavailable')\n"
        "sys.exit(17)\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    task = BenchmarkTask("suite/1", "humaneval", "prompt", "assert True", "solution", "v")
    agent = AgentConfig(
        "claude-frontier",
        "claude-code",
        str(fake),
        "workspace-write",
        10,
        model="claude-sonnet-4-6",
    )
    result_path = tmp_path / "agent.jsonl"

    record = run_agent_task(agent=agent, task=task, result_path=result_path)

    assert record["passed"] is False
    assert record["failure_reason"] == "claude-code exit 17"
    assert record["exit_code"] == 17
    assert record["cost_status"] == "unavailable"


def test_detect_claude_code_installation_reports_missing_command_and_docs_url(tmp_path) -> None:
    agent = AgentConfig(
        "claude-frontier",
        "claude-code",
        str(tmp_path / "missing-claude"),
        "workspace-write",
        10,
        model="claude-sonnet-4-6",
    )

    detection = detect_agent_installation(agent)

    assert detection.installed is False
    assert detection.path is None
    assert detection.url == "https://code.claude.com/docs"


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


def test_qwen_code_adapter_builds_headless_json_command(tmp_path) -> None:
    task = BenchmarkTask("suite/1", "humaneval", "prompt", "assert True", "solution", "v")
    workspace = materialize_task_workspace(task, parent=tmp_path)
    agent = AgentConfig(
        "qwen-local",
        "qwen-code",
        "qwen",
        "workspace-write",
        10,
        model="mlx-community/Qwen3.6-27B-4bit",
        system_prompt="Custom system prompt.",
        append_system_prompt="Extra benchmark instructions.",
    )

    command = adapter_for("qwen-code").build_command(agent, workspace)

    assert command[:5] == ["qwen", "--prompt", workspace.instructions.read_text(encoding="utf-8"), "--output-format", "json"]
    assert command[5:] == [
        "--model",
        "mlx-community/Qwen3.6-27B-4bit",
        "--approval-mode",
        "auto-edit",
        "--sandbox",
        "--system-prompt",
        "Custom system prompt.",
        "--append-system-prompt",
        "Extra benchmark instructions.",
    ]


def test_run_qwen_code_task_with_fake_executable_scores_solution_and_usage(
    tmp_path, monkeypatch
) -> None:
    fake = tmp_path / "qwen"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "import sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "if '--prompt' not in args or '--output-format' not in args:\n"
        "    sys.exit(41)\n"
        "if os.environ.get('OPENAI_BASE_URL') != 'http://127.0.0.1:8000/v1':\n"
        "    sys.exit(42)\n"
        "if os.environ.get('OPENAI_API_KEY') != 'local-secret':\n"
        "    sys.exit(43)\n"
        "if os.environ.get('OPENAI_MODEL') != 'local-qwen':\n"
        "    sys.exit(44)\n"
        "Path('solution.py').write_text('def add(a, b):\\n    return a + b\\n')\n"
        "print(json.dumps([\n"
        "    {'type': 'system', 'session_id': 's1'},\n"
        "    {'type': 'result', 'session_id': 's1', 'result': 'done', 'usage': {\n"
        "        'input_tokens': 3, 'output_tokens': 4, 'total_tokens': 7\n"
        "    }},\n"
        "]))\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    monkeypatch.setenv("QWEN_LOCAL_API_KEY", "local-secret")
    task = BenchmarkTask("suite/1", "humaneval", "prompt", "assert add(1, 2) == 3", "add", "v")
    agent = AgentConfig(
        "qwen-local",
        "qwen-code",
        str(fake),
        "workspace-write",
        10,
        model="local-qwen",
        base_url="http://127.0.0.1:8000/v1",
        api_key_env="QWEN_LOCAL_API_KEY",
    )
    result_path = tmp_path / "agent.jsonl"

    record = run_agent_task(agent=agent, task=task, result_path=result_path)

    assert record["passed"] is True
    assert record["final_message"] == "done"
    assert record["session_id"] == "s1"
    assert record["usage"] == {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}
    assert record["tokens"] == {"total": 7, "estimated": False}
    assert record["cost_status"] == "tokens_available"
    assert "local-secret" not in json.dumps(read_jsonl(result_path))


def test_qwen_code_adapter_records_nonzero_exit_as_infra_failure(tmp_path) -> None:
    fake = tmp_path / "qwen"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.stderr.write('provider unavailable')\n"
        "sys.exit(12)\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    task = BenchmarkTask("suite/1", "humaneval", "prompt", "assert True", "solution", "v")
    agent = AgentConfig("qwen", "qwen-code", str(fake), "workspace-write", 10)
    result_path = tmp_path / "agent.jsonl"

    record = run_agent_task(agent=agent, task=task, result_path=result_path)

    assert record["passed"] is False
    assert record["failure_reason"] == "qwen-code exit 12"
    assert record["exit_code"] == 12
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


def test_qwen_code_detection_reports_missing_command_and_url(tmp_path) -> None:
    agent = AgentConfig(
        "qwen-code",
        "qwen-code",
        str(tmp_path / "missing-qwen"),
        "workspace-write",
        10,
        url="https://github.com/QwenLM/qwen-code",
    )

    detection = detect_agent_installation(agent)

    assert detection.installed is False
    assert detection.path is None
    assert detection.url == "https://github.com/QwenLM/qwen-code"


def test_extract_codex_total_tokens_returns_none_without_usage() -> None:
    assert extract_codex_total_tokens("no usage here") is None
