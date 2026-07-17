from __future__ import annotations

import dataclasses

import pytest

from local_code_bench.compare import MetricSummary, _verdict_inputs
from local_code_bench.compare import ConfigurationStats
from local_code_bench.config import (
    PAIRING_KEYS,
    VERDICT_METRICS,
    CohortFilter,
    ConfigError,
    cohort_model_names,
    load_comparisons,
    load_models,
)

#: The seven proposition axes the shipped catalog must declare (Epic-17).
SHIPPED_AXIS_IDS = (
    "engine",
    "architecture",
    "size-ladder",
    "quant",
    "context-scaling",
    "specialized-vs-general",
    "local-vs-cloud",
)


def _write(tmp_path, body: str):
    path = tmp_path / "comparisons.yaml"
    path.write_text(body, encoding="utf-8")
    return path


VALID_AXIS = """
comparisons:
  - id: engine
    title: "Engine: mlx-lm vs Ollama"
    description: Same base model served by both engines.
    pairing_key: base_model
    cohorts:
      - name: mlx-lm
        inferencer: mlx-lm
        name_globs: ["local-mlx-*"]
      - name: ollama
        inferencer: ollama
        name_globs: ["local-ollama-*"]
    highlighted_pairs:
      - models: [local-mlx-gpt-oss-20b, local-ollama-gpt-oss-20b]
        reason: identical native MXFP4 weights on both engines
    verdicts:
      - id: prefill-advantage
        description: engine advantage at prefill for the same weights
        metric: median_prefill_tokens_per_second
        threshold: 1.10
        unit: ratio
"""


def test_load_comparisons_parses_axis(tmp_path) -> None:
    catalog = load_comparisons(_write(tmp_path, VALID_AXIS))

    assert catalog.errors == ()
    axis = catalog.axis("engine")
    assert axis is not None
    assert axis.title == "Engine: mlx-lm vs Ollama"
    assert axis.pairing_key == "base_model"
    assert [c.name for c in axis.cohorts] == ["mlx-lm", "ollama"]
    assert axis.cohorts[0].inferencer == "mlx-lm"
    assert axis.cohorts[0].name_globs == ("local-mlx-*",)
    pair = axis.highlighted_pairs[0]
    assert pair.models == ("local-mlx-gpt-oss-20b", "local-ollama-gpt-oss-20b")
    assert "MXFP4" in pair.reason
    verdict = axis.verdicts[0]
    assert verdict.metric == "median_prefill_tokens_per_second"
    assert verdict.threshold == pytest.approx(1.10)
    assert verdict.unit == "ratio"
    assert verdict.settings_key is None


def test_comparison_axis_is_frozen(tmp_path) -> None:
    axis = load_comparisons(_write(tmp_path, VALID_AXIS)).axis("engine")

    with pytest.raises(dataclasses.FrozenInstanceError):
        axis.id = "other"  # type: ignore[misc]


def test_unknown_axis_id_returns_none(tmp_path) -> None:
    catalog = load_comparisons(_write(tmp_path, VALID_AXIS))

    assert catalog.axis("nope") is None


# ---------------------------------------------------------------------------
# malformed axes are rejected individually; valid axes still load
# ---------------------------------------------------------------------------


def test_malformed_axis_rejected_but_valid_axes_load(tmp_path) -> None:
    path = _write(
        tmp_path,
        VALID_AXIS
        + """
  - id: broken
    title: Missing pairing key
    cohorts:
      - name: a
        names: [local-mlx-qwen]
      - name: b
        names: [local-ollama-qwen]
""",
    )

    catalog = load_comparisons(path)

    assert catalog.axis("engine") is not None
    assert catalog.axis("broken") is None
    assert len(catalog.errors) == 1
    # The loader error names the offending field.
    assert "comparisons[1].pairing_key" in catalog.errors[0]


def test_non_mapping_axis_rejected_with_index(tmp_path) -> None:
    path = _write(tmp_path, VALID_AXIS + "  - just-a-string\n")

    catalog = load_comparisons(path)

    assert [axis.id for axis in catalog.axes] == ["engine"]
    assert "comparisons[1] must be a mapping" in catalog.errors[0]


def test_duplicate_axis_id_rejected_but_first_loads(tmp_path) -> None:
    path = _write(tmp_path, VALID_AXIS + VALID_AXIS.replace("comparisons:\n", ""))

    catalog = load_comparisons(path)

    assert [axis.id for axis in catalog.axes] == ["engine"]
    assert "comparisons[1].id duplicates 'engine'" in catalog.errors[0]


def test_axis_requires_two_cohorts(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
comparisons:
  - id: lonely
    title: One cohort
    pairing_key: base_model
    cohorts:
      - name: only
        names: [local-mlx-qwen]
""",
    )

    catalog = load_comparisons(path)

    assert catalog.axes == ()
    assert "comparisons[0].cohorts" in catalog.errors[0]
    assert "two" in catalog.errors[0]


def test_cohort_filter_requires_a_criterion(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
comparisons:
  - id: empty-filter
    title: Cohort without criteria
    pairing_key: base_model
    cohorts:
      - name: a
        names: [local-mlx-qwen]
      - name: b
""",
    )

    catalog = load_comparisons(path)

    assert catalog.axes == ()
    assert "comparisons[0].cohorts[1]" in catalog.errors[0]


def test_duplicate_cohort_names_rejected(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
comparisons:
  - id: dup-cohorts
    title: Duplicate cohort names
    pairing_key: base_model
    cohorts:
      - name: same
        names: [local-mlx-qwen]
      - name: same
        names: [local-ollama-qwen]
""",
    )

    catalog = load_comparisons(path)

    assert catalog.axes == ()
    assert "comparisons[0].cohorts[1].name duplicates 'same'" in catalog.errors[0]


def test_verdict_metric_must_be_known(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
comparisons:
  - id: bad-metric
    title: Unknown verdict metric
    pairing_key: base_model
    cohorts:
      - name: a
        names: [local-mlx-qwen]
      - name: b
        names: [local-ollama-qwen]
    verdicts:
      - id: bogus
        metric: vibes_per_second
        threshold: 1.0
""",
    )

    catalog = load_comparisons(path)

    assert catalog.axes == ()
    assert "comparisons[0].verdicts[0].metric" in catalog.errors[0]


def test_verdict_threshold_must_be_a_number(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
comparisons:
  - id: bad-threshold
    title: Non-numeric threshold
    pairing_key: base_model
    cohorts:
      - name: a
        names: [local-mlx-qwen]
      - name: b
        names: [local-ollama-qwen]
    verdicts:
      - id: bogus
        metric: pass_at_1
        threshold: high
""",
    )

    catalog = load_comparisons(path)

    assert catalog.axes == ()
    assert "comparisons[0].verdicts[0].threshold" in catalog.errors[0]


def test_highlighted_pair_requires_two_models_and_reason(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
comparisons:
  - id: bad-pair
    title: Highlighted pair with one model
    pairing_key: base_model
    cohorts:
      - name: a
        names: [local-mlx-qwen]
      - name: b
        names: [local-ollama-qwen]
    highlighted_pairs:
      - models: [local-mlx-qwen]
        reason: not a pair
""",
    )

    catalog = load_comparisons(path)

    assert catalog.axes == ()
    assert "comparisons[0].highlighted_pairs[0].models" in catalog.errors[0]


def test_invalid_pairing_key_names_allowed_values(tmp_path) -> None:
    path = _write(
        tmp_path,
        """
comparisons:
  - id: bad-pairing
    title: Unknown pairing key
    pairing_key: vibes
    cohorts:
      - name: a
        names: [local-mlx-qwen]
      - name: b
        names: [local-ollama-qwen]
""",
    )

    catalog = load_comparisons(path)

    assert catalog.axes == ()
    assert "comparisons[0].pairing_key" in catalog.errors[0]
    for key in sorted(PAIRING_KEYS):
        assert key in catalog.errors[0]


# ---------------------------------------------------------------------------
# file-level failures raise like the other loaders
# ---------------------------------------------------------------------------


def test_load_comparisons_missing_file(tmp_path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_comparisons(tmp_path / "nope.yaml")


def test_load_comparisons_rejects_invalid_yaml(tmp_path) -> None:
    path = _write(tmp_path, "comparisons: [unterminated\n")

    with pytest.raises(ConfigError, match="invalid YAML"):
        load_comparisons(path)


def test_load_comparisons_rejects_non_mapping_root(tmp_path) -> None:
    path = _write(tmp_path, "- not-a-mapping\n")

    with pytest.raises(ConfigError, match="top-level mapping"):
        load_comparisons(path)


def test_load_comparisons_rejects_non_list_comparisons(tmp_path) -> None:
    path = _write(tmp_path, "comparisons: not-a-list\n")

    with pytest.raises(ConfigError, match="must be a list"):
        load_comparisons(path)


# ---------------------------------------------------------------------------
# cohort filter matching: globs, explicit names, inferencer, quant token
# ---------------------------------------------------------------------------


def test_filter_matches_name_glob() -> None:
    cohort = CohortFilter(name="mlx", name_globs=("local-mlx-*",))

    assert cohort.matches("local-mlx-qwen")
    assert not cohort.matches("local-ollama-qwen")


def test_filter_matches_explicit_names() -> None:
    cohort = CohortFilter(name="picked", names=("local-mlx-qwen",))

    assert cohort.matches("local-mlx-qwen")
    assert not cohort.matches("local-mlx-qwen3-coder-30b")


def test_filter_matches_inferencer() -> None:
    cohort = CohortFilter(name="ollama", inferencer="ollama")

    assert cohort.matches("anything", inferencer="ollama")
    assert not cohort.matches("anything", inferencer="mlx-lm")
    assert not cohort.matches("anything", inferencer=None)


def test_filter_matches_quant_token_in_supplied_quant() -> None:
    cohort = CohortFilter(name="q4", quant="q4")

    assert cohort.matches("anything", quant="Q4_K_M")
    assert not cohort.matches("anything", quant="Q8_0")


def test_filter_matches_quant_token_in_name() -> None:
    cohort = CohortFilter(name="4bit", quant="4bit")

    assert cohort.matches("local-mlx-qwen3.6-35b-a3b-4bit")
    # A quant token only matches on a token boundary, never inside another token.
    assert not cohort.matches("local-mlx-qwen-14bit")
    assert not cohort.matches("local-mlx-qwen3.6-35b-a3b-8bit")


def test_filter_criteria_combine_with_and() -> None:
    cohort = CohortFilter(name="mlx-q4", inferencer="mlx-lm", name_globs=("local-mlx-*",))

    assert cohort.matches("local-mlx-qwen", inferencer="mlx-lm")
    assert not cohort.matches("local-mlx-qwen", inferencer="ollama")
    assert not cohort.matches("local-ollama-qwen", inferencer="mlx-lm")


def test_cohort_model_names_lists_matrix_models() -> None:
    models = load_models("configs/models.yaml")
    mlx = CohortFilter(name="mlx", inferencer="mlx-lm")
    ollama = CohortFilter(name="ollama", inferencer="ollama")

    mlx_names = cohort_model_names(mlx, models)
    ollama_names = cohort_model_names(ollama, models)

    assert "local-mlx-gpt-oss-20b" in mlx_names
    assert "local-ollama-gpt-oss-20b" in ollama_names
    assert not set(mlx_names) & set(ollama_names)


# ---------------------------------------------------------------------------
# verdict metrics stay aligned with the compare module's verdict inputs
# ---------------------------------------------------------------------------


def test_verdict_metrics_match_compare_verdict_inputs() -> None:
    stats = ConfigurationStats(
        model="m",
        engine_label="mlx-lm",
        quant=None,
        base_model_key="m",
        suite=None,
        suite_version=None,
        hardware_tag=None,
        run_ids=(),
        attempts=0,
        passed=0,
        pass_at_1=0.0,
        ttft=MetricSummary(None, None, 0),
        prefill_tokens_per_second=MetricSummary(None, None, 0),
        decode_tokens_per_second=MetricSummary(None, None, 0),
        latency=MetricSummary(None, None, 0),
        cost_per_task_usd=0.0,
        memory_bytes=None,
    )

    assert set(_verdict_inputs([stats])) == set(VERDICT_METRICS)


# ---------------------------------------------------------------------------
# the shipped catalog: seven proposition axes, all populated from the matrix
# ---------------------------------------------------------------------------


def test_shipped_catalog_declares_the_seven_axes() -> None:
    catalog = load_comparisons("configs/comparisons.yaml")

    assert catalog.errors == ()
    assert tuple(axis.id for axis in catalog.axes) == SHIPPED_AXIS_IDS


def test_shipped_axes_have_title_pairing_and_two_cohorts() -> None:
    catalog = load_comparisons("configs/comparisons.yaml")

    for axis in catalog.axes:
        assert axis.title
        assert axis.pairing_key in PAIRING_KEYS
        assert len(axis.cohorts) >= 2


def test_shipped_cohorts_all_match_configured_models() -> None:
    # Every cohort filter must select at least one model from the shipped
    # matrix — otherwise the axis could never leave "no comparable runs yet".
    models = load_models("configs/models.yaml")
    catalog = load_comparisons("configs/comparisons.yaml")

    for axis in catalog.axes:
        for cohort in axis.cohorts:
            assert cohort_model_names(cohort, models), (axis.id, cohort.name)


def test_shipped_highlighted_pairs_reference_configured_models() -> None:
    models = load_models("configs/models.yaml")
    catalog = load_comparisons("configs/comparisons.yaml")

    for axis in catalog.axes:
        for pair in axis.highlighted_pairs:
            for name in pair.models:
                assert name in models, (axis.id, name)
            assert pair.reason


def test_shipped_engine_axis_highlights_gpt_oss_controlled_pair() -> None:
    axis = load_comparisons("configs/comparisons.yaml").axis("engine")

    assert axis is not None
    assert ("local-mlx-gpt-oss-20b", "local-ollama-gpt-oss-20b") in tuple(
        pair.models for pair in axis.highlighted_pairs
    )


def test_shipped_quality_bar_verdict_resolves_through_settings_key() -> None:
    # The locked quality bar: pass@1 within 5 pp of the best local model,
    # overridable via the Epic-15 settings layer once it lands.
    axis = load_comparisons("configs/comparisons.yaml").axis("size-ladder")

    assert axis is not None
    quality = [v for v in axis.verdicts if v.settings_key == "benchmark_dashboard.quality_bar"]
    assert quality
    assert quality[0].metric == "pass_at_1"
    assert quality[0].threshold == pytest.approx(5.0)
    assert quality[0].unit == "pp"


def test_shipped_verdicts_have_unique_ids_per_axis() -> None:
    catalog = load_comparisons("configs/comparisons.yaml")

    for axis in catalog.axes:
        ids = [verdict.id for verdict in axis.verdicts]
        assert len(ids) == len(set(ids)), axis.id
