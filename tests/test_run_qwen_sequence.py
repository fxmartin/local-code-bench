from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_command_stub(bin_dir: Path, command: str) -> None:
    stub = bin_dir / command
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "{command} %s\\n" "$*" >> "$COMMAND_LOG"\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)


def test_model_sequence_runs_benchmarks_in_order(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_command_stub(bin_dir, "uv")
    _write_command_stub(bin_dir, "ollama")
    command_log = tmp_path / "commands.log"
    environment = os.environ.copy()
    environment["PATH"] = f"{bin_dir}{os.pathsep}{environment['PATH']}"
    environment["COMMAND_LOG"] = str(command_log)

    result = subprocess.run(
        ["bash", str(REPO_ROOT / "run-qwen-sequence.sh")],
        cwd=REPO_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert command_log.read_text(encoding="utf-8").splitlines() == [
        "uv run bench --suite humaneval-plus --model local-ollama-qwen --timeout 30 "
        "--manage-inferencers --yes --warmup --resume "
        "--run-file results/ollama-qwen-humaneval-plus.jsonl",
        "uv run bench --suite mbpp-plus --model local-ollama-qwen --timeout 30 "
        "--manage-inferencers --yes --warmup --resume "
        "--run-file results/ollama-qwen-mbpp-plus.jsonl",
        "ollama stop qwen3.6:27b",
        "uv run bench --suite humaneval --model local-mlx-qwen --manage-inferencers "
        "--yes --warmup --resume --run-file results/mlx-qwen-humaneval.jsonl",
        "uv run bench inferencer stop mlx-lm",
        "uv run bench --suite humaneval --model openrouter-qwen3.6-27b --warmup "
        "--resume --run-file results/openrouter-qwen3.6-humaneval.jsonl",
        "uv run bench --suite humaneval-plus --model openrouter-qwen3.6-27b --timeout 30 "
        "--warmup --resume --run-file results/openrouter-qwen3.6-humaneval-plus.jsonl",
        "uv run bench --suite mbpp-plus --model openrouter-qwen3.6-27b --timeout 30 "
        "--warmup --resume --run-file results/openrouter-qwen3.6-mbpp-plus.jsonl",
        "uv run bench --suite humaneval --model local-mlx-ornith-9b "
        "--manage-inferencers --yes --warmup --resume "
        "--run-file results/mlx-ornith-9b-humaneval.jsonl",
        "uv run bench --suite humaneval-plus --model local-mlx-ornith-9b --timeout 30 "
        "--manage-inferencers --yes --warmup --resume "
        "--run-file results/mlx-ornith-9b-humaneval-plus.jsonl",
        "uv run bench --suite mbpp-plus --model local-mlx-ornith-9b --timeout 30 "
        "--manage-inferencers --yes --warmup --resume "
        "--run-file results/mlx-ornith-9b-mbpp-plus.jsonl",
        "uv run bench --suite humaneval --model local-mlx-ornith-35b "
        "--manage-inferencers --yes --warmup --resume "
        "--run-file results/mlx-ornith-35b-humaneval.jsonl",
        "uv run bench --suite humaneval-plus --model local-mlx-ornith-35b --timeout 30 "
        "--manage-inferencers --yes --warmup --resume "
        "--run-file results/mlx-ornith-35b-humaneval-plus.jsonl",
        "uv run bench --suite mbpp-plus --model local-mlx-ornith-35b --timeout 30 "
        "--manage-inferencers --yes --warmup --resume "
        "--run-file results/mlx-ornith-35b-mbpp-plus.jsonl",
        "uv run bench inferencer stop mlx-lm",
        "uv run bench --suite humaneval --model local-ollama-ornith-9b "
        "--manage-inferencers --yes --warmup --resume "
        "--run-file results/ollama-ornith-9b-humaneval.jsonl",
        "uv run bench --suite humaneval-plus --model local-ollama-ornith-9b --timeout 30 "
        "--manage-inferencers --yes --warmup --resume "
        "--run-file results/ollama-ornith-9b-humaneval-plus.jsonl",
        "uv run bench --suite mbpp-plus --model local-ollama-ornith-9b --timeout 30 "
        "--manage-inferencers --yes --warmup --resume "
        "--run-file results/ollama-ornith-9b-mbpp-plus.jsonl",
        "ollama stop ornith:9b",
        "uv run bench --suite humaneval --model local-ollama-ornith-35b "
        "--manage-inferencers --yes --warmup --resume "
        "--run-file results/ollama-ornith-35b-humaneval.jsonl",
        "uv run bench --suite humaneval-plus --model local-ollama-ornith-35b --timeout 30 "
        "--manage-inferencers --yes --warmup --resume "
        "--run-file results/ollama-ornith-35b-humaneval-plus.jsonl",
        "uv run bench --suite mbpp-plus --model local-ollama-ornith-35b --timeout 30 "
        "--manage-inferencers --yes --warmup --resume "
        "--run-file results/ollama-ornith-35b-mbpp-plus.jsonl",
        "ollama stop ornith:35b",
        "uv run bench inferencer stop ollama",
    ]
    assert result.stdout.strip() == "All Qwen and Ornith benchmark runs completed."
