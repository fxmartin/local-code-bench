"""Tests for the comparison aggregation module and API (story 17.1-001)."""

from __future__ import annotations

import json
from pathlib import Path

from local_code_bench import compare
from local_code_bench.inferencers.inventory import LocalModel
from local_code_bench.results import append_jsonl


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


def _metadata(hardware_tag: str = "M3 Max 48 GB") -> dict[str, object]:
    return {
        "record_type": "metadata",
        "timestamp": "2026-07-17T00:00:00+00:00",
        "hardware_tag": hardware_tag,
        "suite": "humaneval",
    }


def _endpoint_record(
    model: str,
    task_id: str,
    *,
    passed: bool = True,
    suite: str = "humaneval",
    suite_version: str = "1.0",
    engine: dict[str, object] | None = None,
    endpoint_provider: str | None = None,
    ttft: float = 0.5,
    prefill: float = 100.0,
    decode: float = 40.0,
    latency: float = 2.0,
    cost_usd: float = 0.0,
) -> dict[str, object]:
    record: dict[str, object] = {
        "run_mode": "endpoint",
        "model": model,
        "task_id": task_id,
        "suite": suite,
        "suite_version": suite_version,
        "passed": passed,
        "cost_usd": cost_usd,
        "metrics": {
            "ttft_seconds": ttft,
            "prefill_tokens_per_second": prefill,
            "decode_tokens_per_second": decode,
            "latency_seconds": latency,
        },
    }
    if engine is not None:
        record["engine"] = engine
    if endpoint_provider is not None:
        record["endpoint_provider"] = endpoint_provider
    return record


def _engine(name: str = "mlx_lm.server") -> dict[str, object]:
    return {"name": name, "versions": {name: "1.0"}, "capture_method": "live-api"}


def _write_run(
    path: Path,
    records: list[dict[str, object]],
    *,
    hardware_tag: str = "M3 Max 48 GB",
) -> Path:
    append_jsonl(path, _metadata(hardware_tag))
    for record in records:
        append_jsonl(path, record)
    return path


def _local_model(name: str, size_bytes: int, quant: str | None = None) -> LocalModel:
    return LocalModel(
        inferencer="mlx",
        store_format="hf-safetensors",
        name=name,
        path=f"/models/{name}",
        size_bytes=size_bytes,
        quant=quant,
        provider=None,
        identity=f"id-{name}",
    )


# ---------------------------------------------------------------------------
# configuration stats (AC1)
# ---------------------------------------------------------------------------


def test_configuration_stats_summarize_medians_p95_and_provenance(tmp_path: Path) -> None:
    latencies = [float(i) for i in range(1, 21)]  # 1..20 -> median 10.5, p95 (nearest rank) 19
    records = [
        _endpoint_record(
            "Qwen2.5-Coder-7B-Q4_K_M",
            f"task/{i}",
            passed=i % 2 == 0,
            latency=value,
            ttft=value / 10,
            prefill=value * 10,
            decode=value * 4,
            cost_usd=0.01,
            engine=_engine(),
        )
        for i, value in enumerate(latencies)
    ]
    path = _write_run(tmp_path / "run-a.jsonl", records)

    stats = compare.build_configuration_stats([path])

    assert len(stats) == 1
    config = stats[0]
    assert config.model == "Qwen2.5-Coder-7B-Q4_K_M"
    assert config.quant == "Q4_K_M"
    assert config.base_model_key == "qwen2.5-coder-7b"
    assert config.suite == "humaneval"
    assert config.suite_version == "1.0"
    assert config.hardware_tag == "M3 Max 48 GB"
    assert config.run_ids == ("run-a.jsonl",)
    assert config.attempts == 20
    assert config.passed == 10
    assert config.pass_at_1 == 0.5
    assert config.latency.median == 10.5
    assert config.latency.p95 == 19.0
    assert config.latency.samples == 20
    assert config.ttft.median == 1.05
    assert config.prefill_tokens_per_second.median == 105.0
    assert config.decode_tokens_per_second.median == 42.0
    assert config.cost_per_task_usd == 0.01


def test_configuration_stats_split_by_suite_and_keep_latest_attempt(tmp_path: Path) -> None:
    path = _write_run(
        tmp_path / "run-a.jsonl",
        [
            _endpoint_record("m", "t1", passed=False, suite="humaneval", latency=9.0),
            _endpoint_record("m", "t1", passed=True, suite="humaneval", latency=1.0),
            _endpoint_record("m", "t2", passed=True, suite="mbpp"),
        ],
    )

    stats = compare.build_configuration_stats([path])

    by_suite = {config.suite: config for config in stats}
    assert set(by_suite) == {"humaneval", "mbpp"}
    # Latest attempt per task wins: only the re-run of t1 counts.
    assert by_suite["humaneval"].attempts == 1
    assert by_suite["humaneval"].passed == 1
    assert by_suite["humaneval"].latency.median == 1.0


def test_configuration_stats_tolerate_missing_metrics_and_bad_lines(tmp_path: Path) -> None:
    path = _write_run(
        tmp_path / "run-a.jsonl",
        [
            _endpoint_record("m", "t1"),
            {"run_mode": "endpoint", "model": "m", "task_id": "t2", "suite": "humaneval", "suite_version": "1.0", "passed": False},
            {"run_mode": "sweep", "model": "m"},
        ],
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"truncated": \n')
        handle.write("[1, 2, 3]\n")

    stats = compare.build_configuration_stats([path])

    assert len(stats) == 1
    config = stats[0]
    assert config.attempts == 2
    assert config.latency.samples == 1
    assert config.pass_at_1 == 0.5


def test_configuration_stats_attach_memory_footprint_from_inventory(tmp_path: Path) -> None:
    path = _write_run(
        tmp_path / "run-a.jsonl",
        [_endpoint_record("Qwen2.5-Coder-7B-Q4_K_M", "t1")],
    )
    index = compare.memory_index(
        [
            _local_model("Qwen2.5-Coder-7B-Q4_K_M", 4_000_000_000, quant="Q4_K_M"),
            _local_model("Other-Model-1B", 1_000_000_000),
        ]
    )

    stats = compare.build_configuration_stats([path], memory=index)

    assert stats[0].memory_bytes == 4_000_000_000


def test_memory_footprint_is_none_when_inventory_has_no_match(tmp_path: Path) -> None:
    path = _write_run(tmp_path / "run-a.jsonl", [_endpoint_record("ghost-model", "t1")])

    stats = compare.build_configuration_stats(
        [path], memory=compare.memory_index([_local_model("Other-Model-1B", 1)])
    )

    assert stats[0].memory_bytes is None


def test_memory_index_falls_back_to_sole_base_entry_when_quant_differs() -> None:
    index = compare.memory_index([_local_model("Qwen2.5-Coder-7B", 3_000_000_000)])

    assert compare.memory_for(index, "qwen2.5-coder-7b", "Q4_K_M") == 3_000_000_000


# ---------------------------------------------------------------------------
# pairing via base_model_key (AC2)
# ---------------------------------------------------------------------------


def test_engine_axis_pairs_same_base_model_across_engines(tmp_path: Path) -> None:
    path = _write_run(
        tmp_path / "run-a.jsonl",
        [
            _endpoint_record(
                "Qwen2.5-Coder-7B-Q4_K_M", "t1", engine=_engine("ollama"), latency=4.0
            ),
            _endpoint_record(
                "mlx-community/Qwen2.5-Coder-7B", "t1", engine=_engine("mlx_lm.server"), latency=2.0
            ),
        ],
    )

    comparison = compare.compare_axis(compare.build_configuration_stats([path]), "engine")

    assert comparison is not None
    assert len(comparison.cohorts) == 1
    cohort = comparison.cohorts[0]
    assert cohort.base_model_key == "qwen2.5-coder-7b"
    assert cohort.controlled is False
    assert len(cohort.configurations) == 2
    assert {config.engine_label for config in cohort.configurations} == {
        "ollama 1.0",
        "mlx_lm.server 1.0",
    }
    # Verdict inputs carry one value per configuration for each metric.
    assert set(cohort.verdict_inputs["median_latency_seconds"].values()) == {4.0, 2.0}
    assert set(cohort.verdict_inputs["pass_at_1"].values()) == {1.0}


def test_quant_axis_pairs_same_base_model_and_engine_across_quants(tmp_path: Path) -> None:
    path = _write_run(
        tmp_path / "run-a.jsonl",
        [
            _endpoint_record("Qwen2.5-Coder-7B-Q4_K_M", "t1", engine=_engine()),
            _endpoint_record("Qwen2.5-Coder-7B-Q8_0", "t1", engine=_engine()),
        ],
    )

    comparison = compare.compare_axis(compare.build_configuration_stats([path]), "quant")

    assert comparison is not None
    assert len(comparison.cohorts) == 1
    assert {config.quant for config in comparison.cohorts[0].configurations} == {
        "Q4_K_M",
        "Q8_0",
    }


def test_gpt_oss_axis_flags_controlled_identical_weights_pair(tmp_path: Path) -> None:
    path = _write_run(
        tmp_path / "run-a.jsonl",
        [
            _endpoint_record("gpt-oss-20b", "t1", engine=_engine("ollama")),
            _endpoint_record("gpt-oss-20b-mlx", "t1", engine=_engine("mlx_lm.server")),
            _endpoint_record("Qwen2.5-Coder-7B", "t1", engine=_engine()),
        ],
    )

    comparison = compare.compare_axis(compare.build_configuration_stats([path]), "gpt-oss")

    assert comparison is not None
    assert len(comparison.cohorts) == 1
    cohort = comparison.cohorts[0]
    assert cohort.base_model_key == "gpt-oss-20b"
    assert cohort.controlled is True


def test_unpaired_configuration_yields_no_cohort(tmp_path: Path) -> None:
    path = _write_run(tmp_path / "run-a.jsonl", [_endpoint_record("solo-model", "t1")])

    comparison = compare.compare_axis(compare.build_configuration_stats([path]), "engine")

    assert comparison is not None
    assert comparison.cohorts == ()
    assert comparison.excluded == ()


# ---------------------------------------------------------------------------
# incomparable runs are excluded, never averaged (AC3)
# ---------------------------------------------------------------------------


def test_hardware_tag_mismatch_is_excluded_with_reason(tmp_path: Path) -> None:
    _write_run(
        tmp_path / "run-a.jsonl",
        [
            _endpoint_record("model-x-Q4_K_M", "t1", engine=_engine("ollama")),
            _endpoint_record("model-x-Q8_0", "t1", engine=_engine("ollama")),
        ],
    )
    _write_run(
        tmp_path / "run-b.jsonl",
        [_endpoint_record("model-x-Q6_K", "t1", engine=_engine("ollama"))],
        hardware_tag="M4 64 GB",
    )

    stats = compare.build_configuration_stats(
        [tmp_path / "run-a.jsonl", tmp_path / "run-b.jsonl"]
    )
    comparison = compare.compare_axis(stats, "quant")

    assert comparison is not None
    assert len(comparison.cohorts) == 1
    assert len(comparison.cohorts[0].configurations) == 2
    assert len(comparison.excluded) == 1
    excluded = comparison.excluded[0]
    assert excluded.model == "model-x-Q6_K"
    assert "hardware_tag" in excluded.reason
    assert "M4 64 GB" in excluded.reason


def test_suite_version_mismatch_is_excluded_not_averaged(tmp_path: Path) -> None:
    path = _write_run(
        tmp_path / "run-a.jsonl",
        [
            _endpoint_record("model-x-Q4_K_M", "t1", suite_version="1.0"),
            _endpoint_record("model-x-Q8_0", "t1", suite_version="1.0"),
            _endpoint_record("model-x-Q8_0", "t1", suite_version="1.1", latency=99.0),
        ],
    )

    stats = compare.build_configuration_stats([path])
    comparison = compare.compare_axis(stats, "quant")

    assert comparison is not None
    cohort = comparison.cohorts[0]
    # The 1.1 run never leaks into the paired 1.0 stats.
    q8 = next(c for c in cohort.configurations if c.quant == "Q8_0")
    assert q8.suite_version == "1.0"
    assert q8.latency.median == 2.0
    assert len(comparison.excluded) == 1
    assert "suite_version" in comparison.excluded[0].reason


def test_incomparable_pair_with_no_shared_context_is_fully_excluded(tmp_path: Path) -> None:
    _write_run(
        tmp_path / "run-a.jsonl",
        [_endpoint_record("model-x-Q4_K_M", "t1", engine=_engine("ollama"))],
    )
    _write_run(
        tmp_path / "run-b.jsonl",
        [_endpoint_record("model-x-Q8_0", "t1", engine=_engine("ollama"))],
        hardware_tag="M4 64 GB",
    )

    stats = compare.build_configuration_stats(
        [tmp_path / "run-a.jsonl", tmp_path / "run-b.jsonl"]
    )
    comparison = compare.compare_axis(stats, "quant")

    assert comparison is not None
    assert comparison.cohorts == ()
    assert len(comparison.excluded) == 1
    assert "hardware_tag" in comparison.excluded[0].reason


# ---------------------------------------------------------------------------
# API action (AC4)
# ---------------------------------------------------------------------------


def test_compare_action_returns_cohorts_and_provenance_as_json(tmp_path: Path) -> None:
    path = _write_run(
        tmp_path / "run-a.jsonl",
        [
            _endpoint_record("Qwen2.5-Coder-7B-Q4_K_M", "t1", engine=_engine("ollama")),
            _endpoint_record(
                "mlx-community/Qwen2.5-Coder-7B", "t1", engine=_engine("mlx_lm.server")
            ),
        ],
    )

    status, payload = compare.compare_action([path], "engine")

    assert status == 200
    assert payload["axis"]["id"] == "engine"
    assert len(payload["cohorts"]) == 1
    cohort = payload["cohorts"][0]
    assert cohort["verdict_inputs"]["pass_at_1"]
    config = cohort["configurations"][0]
    assert config["run_ids"] == ["run-a.jsonl"]
    assert config["suite_version"] == "1.0"
    assert config["hardware_tag"] == "M3 Max 48 GB"
    json.dumps(payload)  # payload must be JSON-serializable as-is


def test_compare_action_unknown_axis_is_404() -> None:
    status, payload = compare.compare_action([], "nonsense")

    assert status == 404
    assert "unknown axis" in payload["error"]
    assert set(payload["axes"]) == {"engine", "quant", "gpt-oss"}


def test_compare_action_missing_axis_is_404() -> None:
    status, _payload = compare.compare_action([], "")

    assert status == 404
