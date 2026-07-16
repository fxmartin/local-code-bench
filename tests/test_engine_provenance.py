from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_code_bench.engine_provenance import (
    EngineProvenance,
    EngineProvenanceError,
    capture_mlx_provenance,
    capture_ollama_provenance,
)


def test_engine_provenance_round_trips_and_formats_canonical_label() -> None:
    provenance = EngineProvenance(
        name="mlx-lm",
        versions={"mlx-lm": "0.31.3", "mlx": "0.32.0"},
        capture_method="managed-process",
    )

    assert EngineProvenance.from_dict(provenance.as_dict()) == provenance
    assert provenance.label == "mlx-lm 0.31.3 / mlx 0.32.0"
    assert provenance.fingerprint == ("mlx-lm", (("mlx", "0.32.0"), ("mlx-lm", "0.31.3")))


def test_engine_provenance_rejects_incomplete_payload() -> None:
    with pytest.raises(EngineProvenanceError, match="versions"):
        EngineProvenance.from_dict(
            {"name": "ollama", "versions": {}, "capture_method": "live-api"}
        )


def test_capture_ollama_provenance_uses_live_version_endpoint() -> None:
    seen: dict[str, object] = {}

    def fetch(url: str, timeout: float) -> str:
        seen.update(url=url, timeout=timeout)
        return '{"version":"0.32.0"}'

    provenance = capture_ollama_provenance(
        "http://127.0.0.1:11434/v1", fetch=fetch
    )

    assert seen == {"url": "http://127.0.0.1:11434/api/version", "timeout": 1.0}
    assert provenance.as_dict() == {
        "name": "ollama",
        "versions": {"ollama": "0.32.0"},
        "capture_method": "live-api",
    }


@pytest.mark.parametrize("body", ["", "not json", "{}", "[]", '{"version":""}'])
def test_capture_ollama_provenance_rejects_missing_version(body: str) -> None:
    with pytest.raises(EngineProvenanceError, match="Ollama version"):
        capture_ollama_provenance(
            "http://127.0.0.1:11434/v1", fetch=lambda _url, _timeout: body
        )


def test_capture_ollama_provenance_wraps_transport_failure() -> None:
    def fail(_url: str, _timeout: float) -> str:
        raise OSError("connection refused")

    with pytest.raises(EngineProvenanceError, match="could not capture Ollama version"):
        capture_ollama_provenance("http://127.0.0.1:11434/v1", fetch=fail)


def test_capture_mlx_provenance_queries_launcher_interpreter(tmp_path: Path) -> None:
    interpreter = tmp_path / "python3"
    interpreter.write_text("", encoding="utf-8")
    launcher = tmp_path / "mlx_lm.server"
    launcher.write_text(f"#!{interpreter}\n", encoding="utf-8")
    seen: dict[str, object] = {}

    def run(command, **kwargs):
        seen.update(command=command, kwargs=kwargs)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"mlx-lm": "0.31.3", "mlx": "0.32.0"}),
            stderr="",
        )

    provenance = capture_mlx_provenance(
        ["mlx_lm.server", "--port", "8080"],
        which=lambda executable: str(launcher) if executable == "mlx_lm.server" else None,
        run=run,
    )

    assert seen["command"][0] == str(interpreter)  # type: ignore[index]
    assert provenance.versions == {"mlx-lm": "0.31.3", "mlx": "0.32.0"}
    assert provenance.capture_method == "managed-process"


def test_capture_mlx_provenance_rejects_missing_component(tmp_path: Path) -> None:
    launcher = tmp_path / "mlx_lm.server"
    launcher.write_text("#!/usr/bin/python3\n", encoding="utf-8")

    def run(_command, **_kwargs):
        return SimpleNamespace(returncode=0, stdout='{"mlx-lm":"0.31.3"}', stderr="")

    with pytest.raises(EngineProvenanceError, match="mlx"):
        capture_mlx_provenance(
            ["mlx_lm.server"], which=lambda _executable: str(launcher), run=run
        )


def test_capture_mlx_provenance_rejects_unresolvable_launcher() -> None:
    with pytest.raises(EngineProvenanceError, match="could not resolve"):
        capture_mlx_provenance(["mlx_lm.server"], which=lambda _executable: None)
