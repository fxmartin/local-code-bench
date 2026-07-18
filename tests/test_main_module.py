"""`python -m local_code_bench` must work (Story 18.1-002).

The macOS app bundles a relocatable CPython with the harness wheel installed.
Console-script shims carry absolute shebangs, so the bundled runtime launches
the CLI as a module instead: ``python3 -m local_code_bench dashboard ...``.
"""

from __future__ import annotations

import subprocess
import sys


def test_python_dash_m_reaches_the_cli() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "local_code_bench", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode == 0
    assert "dashboard" in completed.stdout
