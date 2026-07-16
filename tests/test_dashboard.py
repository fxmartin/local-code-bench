from __future__ import annotations

import json
import re

from local_code_bench.dashboard import generate_dashboard, main
from local_code_bench.results import append_jsonl


def _seed_records(path) -> None:
    engine = {
        "name": "ollama",
        "versions": {"ollama": "0.32.0"},
        "capture_method": "live-api",
    }
    append_jsonl(
        path,
        {
            "run_mode": "endpoint",
            "model": "m1",
            "suite": "humaneval",
            "task_id": "HumanEval/0",
            "passed": True,
            "engine": engine,
            "cost_usd": 0.01,
            "metrics": {
                "latency_seconds": 1.0,
                "ttft_seconds": 0.2,
                "prefill_tokens_per_second": 200.0,
                "decode_tokens_per_second": 50.0,
            },
        },
    )
    append_jsonl(
        path,
        {
            "run_mode": "endpoint",
            "model": "m1",
            "suite": "humaneval",
            "task_id": "HumanEval/1",
            "passed": False,
            "engine": engine,
            "cost_usd": 0.02,
            "failure_type": "infra",
            "metrics": {
                "latency_seconds": 3.0,
                "prefill_tokens_per_second": 100.0,
                "decode_tokens_per_second": 40.0,
            },
        },
    )
    append_jsonl(
        path,
        {
            "run_mode": "agent",
            "agent": "codex",
            "suite": "humaneval",
            "task_id": "HumanEval/0",
            "passed": True,
            "wall_time_seconds": 7.0,
            "sandbox_mode": "workspace-write",
        },
    )
    append_jsonl(
        path,
        {
            "run_mode": "sweep",
            "model": "m1",
            "context_tokens": 2000,
            "engine": engine,
            "metrics": {"ttft_seconds": 1.5, "prefill_tokens_per_second": 180.0},
        },
    )


def test_generate_dashboard_writes_self_contained_html(tmp_path) -> None:
    path = tmp_path / "run.jsonl"
    _seed_records(path)
    output = tmp_path / "dashboard.html"

    content = generate_dashboard([path], output)

    assert output.read_text(encoding="utf-8") == content
    assert content.startswith("<!DOCTYPE html>")
    # Embedded CSS, no external build step or CDN fetches.
    assert "<style>" in content
    assert "vite" not in content.lower()
    assert not re.search(r"<script[^>]+src=", content)
    assert not re.search(r'(href|src)\s*=\s*["\']https?://', content)
    # Embedded dashboard data as JSON.
    assert 'id="dashboard-data"' in content
    match = re.search(
        r'<script id="dashboard-data" type="application/json">(.*?)</script>',
        content,
        re.DOTALL,
    )
    assert match is not None
    embedded = json.loads(match.group(1).replace("<\\/", "</"))
    assert embedded["endpoint_models"][0]["model"] == "m1"
    assert embedded["endpoint_models"][0]["pass_rate"] == 0.5
    assert embedded["endpoint_models"][0]["engine_label"] == "ollama 0.32.0"
    assert embedded["agent_runs"][0]["agent"] == "codex"
    assert embedded["sweep_points"][0]["context_tokens"] == 2000
    assert embedded["sweep_points"][0]["engine_label"] == "ollama 0.32.0"
    # Core dashboard browsable without JS: tables rendered server-side.
    assert "<table" in content
    assert "m1" in content
    assert "ollama 0.32.0" in content
    assert "codex" in content


def test_generate_dashboard_includes_offline_tradeoff_and_sweep_charts(tmp_path) -> None:
    path = tmp_path / "run.jsonl"
    _seed_records(path)
    output = tmp_path / "dashboard.html"

    content = generate_dashboard([path], output)

    # All three story-07.4-002 charts are present.
    assert "Cost vs Quality" in content
    assert "Quality vs Speed" in content
    assert "Sweep — Prefill Throughput by Context Size" in content
    # Rendered as inline SVG, generated offline — no CDN / external script fetch.
    assert "<svg" in content
    assert not re.search(r"<script[^>]+src=", content)
    assert not re.search(r'(href|src)\s*=\s*["\']https?://', content)


def test_generate_dashboard_charts_omit_models_with_incomplete_metrics(tmp_path) -> None:
    path = tmp_path / "run.jsonl"
    # A second model with no prefill throughput must be omitted from the speed
    # chart with a visible note rather than plotted as a misleading zero.
    append_jsonl(
        path,
        {
            "run_mode": "endpoint",
            "model": "with-speed",
            "suite": "humaneval",
            "task_id": "t0",
            "passed": True,
            "cost_usd": 0.01,
            "metrics": {"prefill_tokens_per_second": 150.0, "latency_seconds": 1.0},
        },
    )
    append_jsonl(
        path,
        {
            "run_mode": "endpoint",
            "model": "no-speed",
            "suite": "humaneval",
            "task_id": "t0",
            "passed": True,
            "cost_usd": 0.01,
            "metrics": {"latency_seconds": 1.0},
        },
    )
    output = tmp_path / "dashboard.html"

    content = generate_dashboard([path], output)

    assert "chart-note" in content
    assert "no-speed" in content


def test_generate_dashboard_omits_secrets_and_host_paths(tmp_path) -> None:
    path = tmp_path / "run.jsonl"
    append_jsonl(
        path,
        {
            "run_mode": "endpoint",
            "model": "m1",
            "suite": "humaneval",
            "task_id": "HumanEval/0",
            "passed": True,
            "api_key": "sk-secret-DEADBEEF",
            "base_url": "https://internal.example/v1",
            "config_path": "/Users/fxmartin/.env",
            "raw_response": "token /Users/fxmartin/secrets",
            "metrics": {"latency_seconds": 1.0},
        },
    )
    output = tmp_path / "dashboard.html"

    content = generate_dashboard([path], output)

    assert "sk-secret-DEADBEEF" not in content
    assert "internal.example" not in content
    assert "/Users/fxmartin" not in content
    assert ".env" not in content


def test_generate_dashboard_renders_warnings_without_host_paths(tmp_path) -> None:
    path = tmp_path / "run.jsonl"
    append_jsonl(
        path,
        {"run_mode": "endpoint", "model": "m1", "task_id": "t0", "passed": True},
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{broken json\n")
    output = tmp_path / "dashboard.html"

    content = generate_dashboard([path], output)

    assert "Data Quality Warnings" in content
    assert "run.jsonl" in content  # basename is shown
    assert str(path.parent) not in content  # full host path is not


def test_generate_dashboard_handles_empty_inputs(tmp_path) -> None:
    output = tmp_path / "nested" / "dashboard.html"

    content = generate_dashboard([tmp_path / "missing.jsonl"], output)

    assert output.exists()
    assert "No endpoint records" in content
    assert "<table" in content


def test_generate_dashboard_escapes_html_metacharacters_in_cells(tmp_path) -> None:
    path = tmp_path / "run.jsonl"
    append_jsonl(
        path,
        {
            "run_mode": "endpoint",
            "model": "<script>alert(1)</script>",
            "suite": "a&b",
            "task_id": "HumanEval/0",
            "passed": True,
            "metrics": {"latency_seconds": 1.0},
        },
    )
    output = tmp_path / "dashboard.html"

    content = generate_dashboard([path], output)

    # The model name must never reach the rendered table as live markup.
    assert "<script>alert(1)</script>" not in content
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in content
    # Ampersands in cell text are entity-encoded, not left raw.
    assert "<td>a&amp;b</td>" in content


def test_generate_dashboard_escapes_script_close_in_embedded_json(tmp_path) -> None:
    path = tmp_path / "run.jsonl"
    append_jsonl(
        path,
        {
            "run_mode": "endpoint",
            "model": "m</script><script>evil()</script>",
            "suite": "humaneval",
            "task_id": "HumanEval/0",
            "passed": True,
            "metrics": {"latency_seconds": 1.0},
        },
    )
    output = tmp_path / "dashboard.html"

    content = generate_dashboard([path], output)

    # The embedded JSON must not contain a literal closing </script> that would
    # prematurely terminate the data <script> element.
    match = re.search(
        r'<script id="dashboard-data" type="application/json">(.*?)</script>',
        content,
        re.DOTALL,
    )
    assert match is not None
    embedded_raw = match.group(1)
    assert "</script>" not in embedded_raw
    assert "<\\/script>" in embedded_raw
    # Round-trips back to the original value once the escape is reversed.
    embedded = json.loads(embedded_raw.replace("<\\/", "</"))
    assert embedded["endpoint_models"][0]["model"] == "m</script><script>evil()</script>"


def test_dashboard_embeds_run_history_and_renders_section(tmp_path) -> None:
    path = tmp_path / "run.jsonl"
    append_jsonl(path, {"record_type": "metadata", "timestamp": "2026-06-25T10:00:00+00:00"})
    _seed_records(path)
    output = tmp_path / "dashboard.html"

    content = generate_dashboard([path], output)

    # Run history is a first-class section in the committed artifact.
    assert "Run History" in content
    match = re.search(
        r'<script id="dashboard-data" type="application/json">(.*?)</script>',
        content,
        re.DOTALL,
    )
    assert match is not None
    embedded = json.loads(match.group(1).replace("<\\/", "</"))
    assert embedded["runs"][0]["source"] == "run.jsonl"
    assert embedded["runs"][0]["timestamp"] == "2026-06-25T10:00:00+00:00"
    # Two endpoint tasks plus one agent task in the seeded run.
    assert embedded["runs"][0]["task_count"] == 3


def test_dashboard_run_history_uses_basenames_not_host_paths(tmp_path) -> None:
    path = tmp_path / "run.jsonl"
    _seed_records(path)
    output = tmp_path / "dashboard.html"

    content = generate_dashboard([path], output)

    assert "run.jsonl" in content  # basename shown
    assert str(tmp_path) not in content  # host directory never leaks


def test_dashboard_leaderboard_is_sortable_and_filterable(tmp_path) -> None:
    path = tmp_path / "run.jsonl"
    _seed_records(path)
    output = tmp_path / "dashboard.html"

    content = generate_dashboard([path], output)

    # Progressive enhancement: a filter box and clickable, keyed sort headers.
    assert 'id="leaderboard-filter"' in content
    assert 'id="leaderboard-table"' in content
    assert "data-sort-key" in content
    # The enhancement ships as an inline script with no external fetches.
    assert not re.search(r"<script[^>]+src=", content)
    assert not re.search(r'(href|src)\s*=\s*["\']https?://', content)


def test_main_writes_dashboard_from_cli_args(tmp_path, capsys) -> None:
    path = tmp_path / "run.jsonl"
    _seed_records(path)
    output = tmp_path / "out" / "dashboard.html"

    exit_code = main(["--input", str(path), "--output", str(output)])

    assert exit_code == 0
    assert output.exists()
    assert "m1" in output.read_text(encoding="utf-8")
    assert str(output) in capsys.readouterr().out
