"""Static HTML results dashboard generator.

Renders the :class:`~local_code_bench.dashboard_model.DashboardData` aggregates
(story 07.1-001) into a single self-contained HTML file: embedded CSS and embedded
JSON data, no Node/Vite build step, and no CDN fetches, so the artifact can be
committed to the repo and opened directly in a browser.

Only a curated, safe projection of the aggregates is embedded and rendered:
per-task raw-response previews and free-text failure reasons are deliberately
excluded, and data-quality warning sources are reduced to file basenames, so API
keys, ``.env`` contents, raw secrets, and host-sensitive paths never reach the
committed output.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from local_code_bench.dashboard_model import (
    AgentAggregate,
    DashboardData,
    DataQualityWarning,
    EndpointModelAggregate,
    SweepPoint,
    load_dashboard_data,
)


def generate_dashboard(result_paths: list[Path], output_path: Path) -> str:
    """Render result JSONL into a self-contained HTML file; return its content."""

    data = load_dashboard_data([Path(path) for path in result_paths])
    content = _render_html(data)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    return content


def main(argv: list[str] | None = None) -> int:
    """Module entry point: ``python -m local_code_bench.dashboard``.

    A thin generator-only CLI. The integrated ``bench --mode dashboard`` command
    (with a serve option) is delivered separately in story 07.2-002.
    """

    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m local_code_bench.dashboard",
        description="Generate a self-contained static HTML dashboard from result JSONL.",
    )
    parser.add_argument(
        "--input",
        dest="inputs",
        action="append",
        required=True,
        metavar="PATH",
        help="Result JSONL file to include (repeatable).",
    )
    parser.add_argument(
        "--output",
        required=True,
        metavar="PATH",
        help="Destination HTML file.",
    )
    args = parser.parse_args(argv)

    output = Path(args.output)
    generate_dashboard([Path(path) for path in args.inputs], output)
    print(f"Wrote dashboard to {output}")
    return 0


# --------------------------------------------------------------------------- #
# Safe projection — only these fields are embedded/rendered. Per-task previews
# and free-text failure reasons are intentionally excluded (secret-safety).
# --------------------------------------------------------------------------- #


def _safe_data(data: DashboardData) -> dict[str, object]:
    return {
        "endpoint_models": [_safe_endpoint(model) for model in data.endpoint_models],
        "agent_runs": [_safe_agent(run) for run in data.agent_runs],
        "sweep_points": [_safe_sweep(point) for point in data.sweep_points],
        "warnings": [_safe_warning(warning) for warning in data.warnings],
    }


def _safe_endpoint(model: EndpointModelAggregate) -> dict[str, object]:
    return {
        "model": model.model,
        "suite": model.suite,
        "attempts": model.attempts,
        "passed": model.passed,
        "pass_rate": round(model.pass_rate, 6),
        "failure_count": model.failure_count,
        "infra_failures": model.infra_failures,
        "model_failures": model.model_failures,
        "median_latency_seconds": model.median_latency_seconds,
        "median_ttft_seconds": model.median_ttft_seconds,
        "median_prefill_tokens_per_second": model.median_prefill_tokens_per_second,
        "median_decode_tokens_per_second": model.median_decode_tokens_per_second,
        "total_prompt_tokens": model.total_prompt_tokens,
        "total_completion_tokens": model.total_completion_tokens,
        "total_cost_usd": round(model.total_cost_usd, 6),
        "mean_cost_usd": round(model.mean_cost_usd, 6),
    }


def _safe_agent(run: AgentAggregate) -> dict[str, object]:
    return {
        "agent": run.agent,
        "suite": run.suite,
        "attempts": run.attempts,
        "passed": run.passed,
        "pass_rate": round(run.pass_rate, 6),
        "failure_count": run.failure_count,
        "median_wall_time_seconds": run.median_wall_time_seconds,
        "sandbox_mode": run.sandbox_mode,
    }


def _safe_sweep(point: SweepPoint) -> dict[str, object]:
    return {
        "model": point.model,
        "context_tokens": point.context_tokens,
        "ttft_seconds": point.ttft_seconds,
        "prefill_tokens_per_second": point.prefill_tokens_per_second,
    }


def _safe_warning(warning: DataQualityWarning) -> dict[str, object]:
    # Reduce the source to a basename so committed artifacts never leak host paths.
    return {
        "source": Path(warning.source).name if warning.source else "",
        "line": warning.line,
        "message": warning.message,
    }


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

_CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  margin: 0; padding: 2rem; line-height: 1.5; color: #1d1d1f; background: #f5f5f7;
}
h1 { margin: 0 0 0.25rem; font-size: 1.6rem; }
h2 { margin: 2rem 0 0.75rem; font-size: 1.15rem; }
.subtitle { color: #6e6e73; margin: 0 0 1.5rem; font-size: 0.9rem; }
section { background: #fff; border-radius: 12px; padding: 1.25rem 1.5rem; margin-bottom: 1.5rem;
  box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
table { border-collapse: collapse; width: 100%; font-size: 0.9rem; }
th, td { padding: 0.5rem 0.75rem; text-align: left; border-bottom: 1px solid #e5e5ea; }
th { font-weight: 600; color: #424245; white-space: nowrap; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.empty { color: #6e6e73; font-style: italic; padding: 0.5rem 0; }
.warnings { background: #fff8e1; border: 1px solid #f0d98c; }
.warnings li { color: #6b5900; }
@media (prefers-color-scheme: dark) {
  body { color: #f5f5f7; background: #1d1d1f; }
  section { background: #2c2c2e; box-shadow: none; }
  th { color: #aeaeb2; }
  th, td { border-bottom-color: #3a3a3c; }
  .warnings { background: #3a3320; border-color: #6b5900; }
  .warnings li { color: #f0d98c; }
}
""".strip()


def _render_html(data: DashboardData) -> str:
    safe = _safe_data(data)
    embedded = json.dumps(safe, sort_keys=True, separators=(",", ":"))
    # Prevent the embedded JSON from prematurely closing the <script> element.
    embedded = embedded.replace("</", "<\\/")

    sections = [
        _warnings_section(data.warnings),
        _endpoint_section(data.endpoint_models),
        _agent_section(data.agent_runs),
        _sweep_section(data.sweep_points),
    ]
    body = "\n".join(section for section in sections if section)

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>local-code-bench — Results Dashboard</title>\n"
        f"<style>\n{_CSS}\n</style>\n"
        "</head>\n"
        "<body>\n"
        "<h1>local-code-bench — Results Dashboard</h1>\n"
        '<p class="subtitle">Self-contained benchmark dashboard generated from stored '
        "result JSONL. Open directly in a browser — no server required.</p>\n"
        f"{body}\n"
        f'<script id="dashboard-data" type="application/json">{embedded}</script>\n'
        "</body>\n"
        "</html>\n"
    )


def _endpoint_section(models: tuple[EndpointModelAggregate, ...]) -> str:
    headers = [
        "Model",
        "Suite",
        "pass@1",
        "Median Latency (s)",
        "Median TTFT (s)",
        "Prefill tok/s",
        "Decode tok/s",
        "$/task",
        "Infra Failures",
    ]
    rows = [
        [
            _cell(model.model),
            _cell(model.suite or "—"),
            _num(f"{model.passed}/{model.attempts}"),
            _num(_fmt(model.median_latency_seconds, 3)),
            _num(_fmt(model.median_ttft_seconds, 3)),
            _num(_fmt(model.median_prefill_tokens_per_second, 1)),
            _num(_fmt(model.median_decode_tokens_per_second, 1)),
            _num(f"{model.mean_cost_usd:.6f}"),
            _num(str(model.infra_failures)),
        ]
        for model in models
    ]
    return _section("Endpoint Models", headers, rows, "No endpoint records found.")


def _agent_section(runs: tuple[AgentAggregate, ...]) -> str:
    headers = ["Agent", "Suite", "pass@1", "Median Wall Time (s)", "Sandbox", "Failures"]
    rows = [
        [
            _cell(run.agent),
            _cell(run.suite or "—"),
            _num(f"{run.passed}/{run.attempts}"),
            _num(_fmt(run.median_wall_time_seconds, 3)),
            _cell(run.sandbox_mode or "—"),
            _num(str(run.failure_count)),
        ]
        for run in runs
    ]
    return _section("Agent Runs", headers, rows, "No agent records found.")


def _sweep_section(points: tuple[SweepPoint, ...]) -> str:
    headers = ["Model", "Context Tokens", "TTFT (s)", "Prefill tok/s"]
    rows = [
        [
            _cell(point.model),
            _num(f"{point.context_tokens:,}"),
            _num(_fmt(point.ttft_seconds, 3)),
            _num(_fmt(point.prefill_tokens_per_second, 1)),
        ]
        for point in points
    ]
    return _section("Sweep — Prefill vs Context", headers, rows, "No sweep records found.")


def _warnings_section(warnings: tuple[DataQualityWarning, ...]) -> str:
    if not warnings:
        return ""
    items = "".join(f"<li>{html.escape(_warning_label(warning))}</li>" for warning in warnings)
    return f'<section class="warnings"><h2>Data Quality Warnings</h2><ul>{items}</ul></section>'


def _warning_label(warning: DataQualityWarning) -> str:
    source = Path(warning.source).name if warning.source else ""
    location = f"{source}:{warning.line}" if warning.line is not None else source
    return f"{location}: {warning.message}" if location else warning.message


def _section(title: str, headers: list[str], rows: list[list[str]], empty: str) -> str:
    head = "".join(
        f'<th class="num">{html.escape(header)}</th>'
        if _is_num_header(header)
        else f"<th>{html.escape(header)}</th>"
        for header in headers
    )
    if rows:
        body = "".join("<tr>" + "".join(cells) + "</tr>" for cells in rows)
        table = f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    else:
        table = (
            f"<table><thead><tr>{head}</tr></thead></table>"
            f'<p class="empty">{html.escape(empty)}</p>'
        )
    return f"<section><h2>{html.escape(title)}</h2>{table}</section>"


def _is_num_header(header: str) -> bool:
    return header not in {"Model", "Suite", "Agent", "Sandbox"}


def _fmt(value: float | None, places: int) -> str:
    return "—" if value is None else f"{value:.{places}f}"


def _cell(value: object) -> str:
    return f"<td>{html.escape(str(value))}</td>"


def _num(value: object) -> str:
    return f'<td class="num">{html.escape(str(value))}</td>'


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
