from __future__ import annotations

from pathlib import Path

from local_code_bench.config import InferencerConfig
from local_code_bench.inferencers import detect


def _cfg(kind: str, target: str, *, lifecycle: str = "server") -> InferencerConfig:
    return InferencerConfig(
        name="x",
        lifecycle=lifecycle,  # type: ignore[arg-type]
        detect_kind=kind,  # type: ignore[arg-type]
        detect_target=target,
        port=8000,
        health_url="http://127.0.0.1:{port}/v1/models",
        start=("x", "serve") if lifecycle == "server" else None,
    )


def test_is_installed_binary_uses_which(monkeypatch) -> None:
    monkeypatch.setattr(
        detect.shutil, "which", lambda name: "/usr/bin/dflash" if name == "dflash" else None
    )

    assert detect.is_installed(_cfg("binary", "dflash")) is True
    assert detect.is_installed(_cfg("binary", "missing")) is False


def test_is_installed_mtplx_not_installed_is_read_only(monkeypatch) -> None:
    """MTPLX absent → not-installed via read-only `shutil.which` (no install attempted)."""
    calls: list[str] = []

    def fake_which(name: str) -> None:
        calls.append(name)
        return None

    monkeypatch.setattr(detect.shutil, "which", fake_which)

    assert detect.is_installed(_cfg("binary", "mtplx")) is False
    assert calls == ["mtplx"]  # detection only looked it up; nothing else ran


def test_is_installed_module_uses_find_spec(monkeypatch) -> None:
    monkeypatch.setattr(
        detect.importlib.util,
        "find_spec",
        lambda name: object() if name == "mlx_lm" else None,
    )

    assert detect.is_installed(_cfg("module", "mlx_lm")) is True
    assert detect.is_installed(_cfg("module", "absent")) is False


def test_is_installed_module_swallows_import_error(monkeypatch) -> None:
    def boom(name: str):
        raise ModuleNotFoundError("broken namespace package")

    monkeypatch.setattr(detect.importlib.util, "find_spec", boom)

    assert detect.is_installed(_cfg("module", "broken")) is False


def test_is_installed_app_checks_application_dirs(monkeypatch, tmp_path) -> None:
    apps = tmp_path / "Applications"
    apps.mkdir()
    (apps / "LM Studio.app").mkdir()
    monkeypatch.setattr(detect.sys, "platform", "darwin")
    monkeypatch.setattr(detect, "_app_dirs", lambda: [apps])

    assert detect.is_installed(_cfg("app", "LM Studio.app", lifecycle="app")) is True
    assert detect.is_installed(_cfg("app", "GPT4All.app", lifecycle="app")) is False


def test_is_installed_app_not_installed_off_darwin(monkeypatch) -> None:
    monkeypatch.setattr(detect.sys, "platform", "linux")
    # _app_dirs must not even be consulted on a non-Darwin platform.
    monkeypatch.setattr(detect, "_app_dirs", lambda: [Path("/never")])

    assert detect.is_installed(_cfg("app", "LM Studio.app", lifecycle="app")) is False


def test_app_dirs_returns_standard_application_roots() -> None:
    dirs = detect._app_dirs()

    assert Path("/Applications") in dirs
    assert Path.home() / "Applications" in dirs


def test_is_installed_unknown_kind_returns_false() -> None:
    # A malformed config whose detect_kind is none of binary/module/app.
    cfg = _cfg("mystery", "whatever")

    assert detect.is_installed(cfg) is False


def test_detect_all_maps_each_name(monkeypatch) -> None:
    monkeypatch.setattr(
        detect.shutil, "which", lambda name: "/bin/here" if name == "here" else None
    )
    configs = {
        "a": _cfg("binary", "here"),
        "b": _cfg("binary", "gone"),
    }

    assert detect.detect_all(configs) == {"a": True, "b": False}
