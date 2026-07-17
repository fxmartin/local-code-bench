"""Read-only installed-proxy detection for optimizer configs (Story 13.1-001).

The detect kinds are identical to inferencers, so `inferencers/detect.py`'s
`is_installed` is reused directly on `OptimizerConfig` entries.
"""

from __future__ import annotations

from local_code_bench.config import OptimizerConfig
from local_code_bench.inferencers import detect


def _cfg(kind: str, target: str) -> OptimizerConfig:
    return OptimizerConfig(
        name="headroom",
        detect_kind=kind,  # type: ignore[arg-type]
        detect_target=target,
        port=8787,
        health_url="http://127.0.0.1:{port}/v1/models",
        start=("headroom", "proxy", "--port", "8787", "{upstream}"),
        url="https://headroom-docs.vercel.app/docs",
    )


def test_is_installed_optimizer_binary_uses_which(monkeypatch) -> None:
    monkeypatch.setattr(
        detect.shutil, "which", lambda name: "/usr/bin/headroom" if name == "headroom" else None
    )

    assert detect.is_installed(_cfg("binary", "headroom")) is True
    assert detect.is_installed(_cfg("binary", "missing")) is False


def test_absent_proxy_detection_is_read_only(monkeypatch) -> None:
    """Absent proxy → not-installed via read-only `shutil.which`; no install attempted."""
    calls: list[str] = []

    def fake_which(name: str) -> None:
        calls.append(name)
        return None

    monkeypatch.setattr(detect.shutil, "which", fake_which)

    cfg = _cfg("binary", "headroom")
    assert detect.is_installed(cfg) is False
    assert calls == ["headroom"]  # detection only looked it up; nothing else ran
    # The entry's url is the manual-install reference to surface when absent.
    assert cfg.url == "https://headroom-docs.vercel.app/docs"


def test_is_installed_optimizer_module_uses_find_spec(monkeypatch) -> None:
    monkeypatch.setattr(
        detect.importlib.util,
        "find_spec",
        lambda name: object() if name == "headroom" else None,
    )

    assert detect.is_installed(_cfg("module", "headroom")) is True
    assert detect.is_installed(_cfg("module", "absent")) is False


def test_detect_all_maps_optimizer_names(monkeypatch) -> None:
    monkeypatch.setattr(
        detect.shutil, "which", lambda name: "/bin/headroom" if name == "headroom" else None
    )
    configs = {
        "headroom": _cfg("binary", "headroom"),
        "ghost": _cfg("binary", "ghost"),
    }

    assert detect.detect_all(configs) == {"headroom": True, "ghost": False}
