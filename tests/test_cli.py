from __future__ import annotations

import subprocess
import sys

import pytest

from local_code_bench.cli import main


def test_main_help_prints_usage(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])

    assert exc_info.value.code == 0

    output = capsys.readouterr().out
    assert "usage: bench" in output


def test_bench_help_entrypoint_exits_successfully() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "local_code_bench.cli", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "usage: bench" in result.stdout


def test_main_version_matches_package_metadata(capsys) -> None:
    assert main(["--version"]) == 0

    assert capsys.readouterr().out.strip() == "0.4.0"
