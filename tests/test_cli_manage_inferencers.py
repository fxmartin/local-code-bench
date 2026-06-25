"""`--manage-inferencers` opt-in: auto-start the engine a model declares (08.5).

When the flag is absent the suite flow is byte-for-byte unchanged; when present,
the declared inferencer is brought up exclusively before the suite runs. The
manager is patched, so no server is launched.
"""

from __future__ import annotations

from local_code_bench.cli import main
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
