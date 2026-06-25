"""`--manage-inferencers` opt-in: auto-start the engine a model declares (08.5).

When the flag is absent the suite flow is byte-for-byte unchanged; when present,
the declared inferencer is brought up exclusively before the suite runs. The
manager is patched, so no server is launched.
"""

from __future__ import annotations

import argparse

import pytest

from local_code_bench.cli import _make_inferencer_confirm, main
from local_code_bench.config import InferencerConfig, ModelConfig, TokenPrices
from local_code_bench.inferencers import manager
from local_code_bench.tasks import BenchmarkTask


def _model(name: str = "local", inferencer: str | None = None) -> ModelConfig:
    return ModelConfig(
        name=name,
        type="openai",
        base_url="http://localhost:8000/v1",
        model_id="qwen",
        pinned_revision="test",
        price_per_1k_tokens=TokenPrices(input=0, output=0),
        inferencer=inferencer,
    )


def _dflash_cfg() -> InferencerConfig:
    return InferencerConfig(
        name="dflash",
        lifecycle="server",
        detect_kind="binary",
        detect_target="dflash",
        port=8000,
        health_url="http://127.0.0.1:{port}/v1/models",
        start=("dflash", "serve"),
    )


def _patch_suite(monkeypatch, model):
    task = BenchmarkTask("suite/1", "humaneval", "prompt", "assert True", "solution", "v")
    monkeypatch.setattr("local_code_bench.cli.load_models", lambda _path: {model.name: model})
    monkeypatch.setattr("local_code_bench.cli.load_suite", lambda _suite, cache_dir: [task])
    monkeypatch.setattr(
        "local_code_bench.cli.run_endpoint_suite",
        lambda **_kwargs: {"passed": 1, "failed": 0, "infra_failed": 0, "skipped": 0},
    )


def test_flag_absent_does_not_touch_inferencers(tmp_path, monkeypatch, capsys) -> None:
    _patch_suite(monkeypatch, _model(inferencer="dflash"))
    called: list[object] = []
    monkeypatch.setattr(manager, "start_exclusive", lambda *a, **k: called.append(a))

    exit_code = main(
        ["--suite", "humaneval", "--model", "local", "--run-file", str(tmp_path / "r.jsonl")]
    )

    assert exit_code == 0
    assert called == []  # default path assumes the server is already up


def test_flag_present_starts_declared_inferencer_exclusively(
    tmp_path, monkeypatch
) -> None:
    _patch_suite(monkeypatch, _model(inferencer="dflash"))
    monkeypatch.setattr(
        "local_code_bench.cli.load_inferencers", lambda _path: {"dflash": _dflash_cfg()}
    )
    captured: dict[str, object] = {}

    def fake_start_exclusive(target_cfg, configs, state_dir, **kwargs):
        captured["target"] = target_cfg
        captured["configs"] = configs
        captured["state_dir"] = state_dir
        captured["kwargs"] = kwargs
        return None

    monkeypatch.setattr(manager, "start_exclusive", fake_start_exclusive)

    exit_code = main(
        [
            "--suite",
            "humaneval",
            "--model",
            "local",
            "--run-file",
            str(tmp_path / "r.jsonl"),
            "--manage-inferencers",
            "--yes",
        ]
    )

    assert exit_code == 0
    assert captured["target"].name == "dflash"
    assert "dflash" in captured["configs"]
    # --yes makes the injected confirm auto-approve stopping others.
    assert captured["kwargs"]["confirm"]([]) is True


def test_flag_present_model_without_inferencer_is_noop(tmp_path, monkeypatch) -> None:
    _patch_suite(monkeypatch, _model(inferencer=None))
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _path: {})
    called: list[object] = []
    monkeypatch.setattr(manager, "start_exclusive", lambda *a, **k: called.append(a))

    exit_code = main(
        [
            "--suite",
            "humaneval",
            "--model",
            "local",
            "--run-file",
            str(tmp_path / "r.jsonl"),
            "--manage-inferencers",
        ]
    )

    assert exit_code == 0
    assert called == []


def test_unknown_declared_inferencer_errors(tmp_path, monkeypatch, capsys) -> None:
    _patch_suite(monkeypatch, _model(inferencer="ghost"))
    monkeypatch.setattr("local_code_bench.cli.load_inferencers", lambda _path: {})

    exit_code = main(
        [
            "--suite",
            "humaneval",
            "--model",
            "local",
            "--run-file",
            str(tmp_path / "r.jsonl"),
            "--manage-inferencers",
        ]
    )

    assert exit_code == 2
    assert "bench: error:" in capsys.readouterr().err


def _status(name: str) -> manager.InferencerStatus:
    return manager.InferencerStatus(
        name=name,
        installed=True,
        lifecycle="server",
        running=True,
        pid=123,
        port=8000,
        healthy=True,
        detail="",
    )


def test_confirm_auto_approves_with_yes() -> None:
    confirm = _make_inferencer_confirm(argparse.Namespace(yes=True))

    # --yes approves without ever touching stdin.
    assert confirm([_status("dflash")]) is True


def test_confirm_declines_when_stdin_not_a_tty(monkeypatch) -> None:
    confirm = _make_inferencer_confirm(argparse.Namespace(yes=False))
    monkeypatch.setattr("local_code_bench.cli.sys.stdin.isatty", lambda: False)

    # Unattended run: never silently force-stop a server it cannot prompt about.
    assert confirm([_status("dflash")]) is False


@pytest.mark.parametrize(
    ("reply", "expected"),
    [("y", True), ("yes", True), (" Y ", True), ("n", False), ("", False)],
)
def test_confirm_prompts_when_interactive(monkeypatch, reply, expected) -> None:
    confirm = _make_inferencer_confirm(argparse.Namespace(yes=False))
    monkeypatch.setattr("local_code_bench.cli.sys.stdin.isatty", lambda: True)
    prompts: list[str] = []

    def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        return reply

    monkeypatch.setattr("builtins.input", fake_input)

    assert confirm([_status("dflash"), _status("turboquant")]) is expected
    # The prompt names the running engines it is about to stop.
    assert "dflash, turboquant" in prompts[0]


def test_sweep_flow_starts_declared_inferencer(tmp_path, monkeypatch, capsys) -> None:
    model = _model(inferencer="dflash")
    monkeypatch.setattr("local_code_bench.cli.load_models", lambda _path: {model.name: model})
    monkeypatch.setattr(
        "local_code_bench.cli.load_inferencers", lambda _path: {"dflash": _dflash_cfg()}
    )
    monkeypatch.setattr("local_code_bench.cli.run_sweep", lambda **_kwargs: "ok")

    captured: dict[str, object] = {}

    def fake_start_exclusive(target_cfg, configs, state_dir, **kwargs):
        captured["target"] = target_cfg
        # Exercise the injected progress callback so its output reaches the run log.
        kwargs["progress"]("starting dflash")
        return None

    monkeypatch.setattr(manager, "start_exclusive", fake_start_exclusive)

    exit_code = main(
        [
            "--mode",
            "sweep",
            "--model",
            "local",
            "--run-file",
            str(tmp_path / "sweep.jsonl"),
            "--manage-inferencers",
            "--yes",
        ]
    )

    assert exit_code == 0
    assert captured["target"].name == "dflash"
    out = capsys.readouterr().out
    assert "starting dflash" in out
    assert "sweep=ok" in out


def test_sweep_flow_with_custom_context_sizes_and_power(tmp_path, monkeypatch, capsys) -> None:
    """The story wires inferencer management into the full sweep path.

    Exercise that path end-to-end with explicit `--context-sizes` (custom parsing,
    including a blank entry that is skipped) and `--power` so the power summary is
    emitted alongside the started inferencer.
    """

    from local_code_bench.power import PowerSummary

    model = _model(inferencer="dflash")
    monkeypatch.setattr("local_code_bench.cli.load_models", lambda _path: {model.name: model})
    monkeypatch.setattr(
        "local_code_bench.cli.load_inferencers", lambda _path: {"dflash": _dflash_cfg()}
    )
    monkeypatch.setattr(manager, "start_exclusive", lambda *a, **k: None)

    captured: dict[str, object] = {}

    def fake_run_sweep(*, sizes, **_kwargs):
        captured["sizes"] = sizes
        return "ok"

    monkeypatch.setattr("local_code_bench.cli.run_sweep", fake_run_sweep)
    monkeypatch.setattr(
        "local_code_bench.cli.PowerSampler.result",
        lambda _self: PowerSummary(
            available=True,
            samples=3,
            duration_s=1.0,
            avg_gpu_w=10.0,
            max_gpu_w=12.0,
            avg_cpu_w=5.0,
            avg_combined_w=15.0,
            energy_j=15.0,
        ),
    )

    exit_code = main(
        [
            "--mode",
            "sweep",
            "--model",
            "local",
            "--run-file",
            str(tmp_path / "sweep.jsonl"),
            "--manage-inferencers",
            "--yes",
            "--context-sizes",
            "1024, ,2048",
            "--power",
        ]
    )

    assert exit_code == 0
    # The blank entry is skipped; the two positive sizes survive.
    assert captured["sizes"] == (1024, 2048)
    out = capsys.readouterr().out
    assert "power: avg_gpu=10.0W" in out


def test_context_sizes_must_list_a_positive_integer(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "local_code_bench.cli.load_models", lambda _path: {"local": _model(inferencer=None)}
    )

    exit_code = main(
        [
            "--mode",
            "sweep",
            "--model",
            "local",
            "--run-file",
            str(tmp_path / "sweep.jsonl"),
            "--context-sizes",
            " , ",
        ]
    )

    assert exit_code == 2
    assert "bench: error:" in capsys.readouterr().err
