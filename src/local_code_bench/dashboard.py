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

from local_code_bench.dashboard_charts import render_charts_section
from local_code_bench.dashboard_model import (
    AgentAggregate,
    DashboardData,
    DataQualityWarning,
    EndpointModelAggregate,
    RunSummary,
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
        "runs": [_safe_run(run) for run in data.runs],
        "warnings": [_safe_warning(warning) for warning in data.warnings],
    }


def _safe_endpoint(model: EndpointModelAggregate) -> dict[str, object]:
    return {
        "model": model.model,
        "engine_label": model.engine_label,
        "engine_capture_method": model.engine_capture_method,
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
        "engine_label": run.engine_label,
        "engine_capture_method": run.engine_capture_method,
        "suite": run.suite,
        "attempts": run.attempts,
        "passed": run.passed,
        "pass_rate": round(run.pass_rate, 6),
        "failure_count": run.failure_count,
        "median_wall_time_seconds": run.median_wall_time_seconds,
        "sandbox_mode": run.sandbox_mode,
    }


def _safe_run(run: RunSummary) -> dict[str, object]:
    # ``source`` is already a basename (set by the loader), so no host path leaks.
    return {
        "source": run.source,
        "timestamp": run.timestamp,
        "models": list(run.models),
        "agents": list(run.agents),
        "engines": list(run.engines),
        "suites": list(run.suites),
        "task_count": run.task_count,
        "passed": run.passed,
        "pass_rate": round(run.pass_rate, 6),
        "median_latency_seconds": run.median_latency_seconds,
        "median_wall_time_seconds": run.median_wall_time_seconds,
    }


def _safe_sweep(point: SweepPoint) -> dict[str, object]:
    return {
        "model": point.model,
        "engine_label": point.engine_label,
        "engine_capture_method": point.engine_capture_method,
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
th.sortable-th { cursor: pointer; user-select: none; }
th.sortable-th:hover { text-decoration: underline; }
.filter { margin-bottom: 0.75rem; padding: 0.35rem 0.55rem; width: 22rem; max-width: 100%;
  border: 1px solid #d2d2d7; border-radius: 8px; font-size: 0.9rem; }
.warnings { background: #fff8e1; border: 1px solid #f0d98c; }
.warnings li { color: #6b5900; }
.chart-svg { width: 100%; max-width: 520px; height: auto; display: block; }
.chart-svg .axis { stroke: #c7c7cc; stroke-width: 1; }
.chart-svg .tick { fill: #6e6e73; font-size: 10px; }
.chart-svg .axis-title { fill: #424245; font-size: 11px; }
.chart-note { color: #6b5900; font-size: 0.85rem; margin: 0.5rem 0 0; }
.legend { list-style: none; padding: 0; margin: 0.5rem 0 0; display: flex; flex-wrap: wrap;
  gap: 0.25rem 1rem; font-size: 0.85rem; }
.legend .swatch { margin-right: 0.35rem; }
@media (prefers-color-scheme: dark) {
  body { color: #f5f5f7; background: #1d1d1f; }
  section { background: #2c2c2e; box-shadow: none; }
  th { color: #aeaeb2; }
  th, td { border-bottom-color: #3a3a3c; }
  .warnings { background: #3a3320; border-color: #6b5900; }
  .warnings li { color: #f0d98c; }
  .chart-svg .axis { stroke: #48484a; }
  .chart-svg .tick { fill: #aeaeb2; }
  .chart-svg .axis-title { fill: #d1d1d6; }
  .chart-note { color: #f0d98c; }
  .filter { background: #1c1c1e; border-color: #48484a; color: #f5f5f7; }
}
""".strip()


# Progressive enhancement: the server-rendered tables are fully readable without
# JavaScript; this inline script adds client-side filtering and column sorting on
# top of the existing DOM (no external assets, no embedded-data dependency).
_ENHANCE_JS = """
(function () {
  function numeric(text) {
    var cleaned = text.replace(/[^0-9eE.+-]/g, "");
    if (cleaned === "" || cleaned === "-" || cleaned === "—") return NaN;
    return parseFloat(cleaned);
  }
  function rowsOf(table) {
    return Array.prototype.slice.call(table.tBodies[0] ? table.tBodies[0].rows : []);
  }
  var filterInput = document.getElementById("leaderboard-filter");
  var leaderboard = document.getElementById("leaderboard-table");
  if (filterInput && leaderboard) {
    filterInput.addEventListener("input", function () {
      var q = filterInput.value.toLowerCase();
      rowsOf(leaderboard).forEach(function (row) {
        row.style.display = row.textContent.toLowerCase().indexOf(q) >= 0 ? "" : "none";
      });
    });
  }
  var directions = new WeakMap();
  document.querySelectorAll("table.sortable").forEach(function (table) {
    if (!table.tHead) return;
    table.tHead.addEventListener("click", function (event) {
      var th = event.target.closest("th[data-sort-key]");
      if (!th) return;
      var key = parseInt(th.getAttribute("data-sort-key"), 10);
      var state = directions.get(table) || {};
      var dir = state[key] === 1 ? -1 : 1;
      state[key] = dir;
      directions.set(table, state);
      var rows = rowsOf(table);
      rows.sort(function (a, b) {
        var x = a.cells[key].textContent.trim();
        var y = b.cells[key].textContent.trim();
        var nx = numeric(x), ny = numeric(y);
        if (!isNaN(nx) && !isNaN(ny)) return (nx - ny) * dir;
        return x.localeCompare(y) * dir;
      });
      var tbody = table.tBodies[0];
      rows.forEach(function (row) { tbody.appendChild(row); });
    });
  });
})();
""".strip()


def _render_html(data: DashboardData) -> str:
    safe = _safe_data(data)
    embedded = json.dumps(safe, sort_keys=True, separators=(",", ":"))
    # Prevent the embedded JSON from prematurely closing the <script> element.
    embedded = embedded.replace("</", "<\\/")

    sections = [
        _warnings_section(data.warnings),
        _endpoint_section(data.endpoint_models),
        render_charts_section(data.endpoint_models, data.sweep_points),
        _agent_section(data.agent_runs),
        _run_history_section(data.runs),
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
        f"<script>\n{_ENHANCE_JS}\n</script>\n"
        "</body>\n"
        "</html>\n"
    )


def _endpoint_section(models: tuple[EndpointModelAggregate, ...]) -> str:
    headers = [
        "Model",
        "Engine",
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
            _cell(model.engine_label),
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
    return _section(
        "Endpoint Models",
        headers,
        rows,
        "No endpoint records found.",
        table_id="leaderboard-table",
        filter_id="leaderboard-filter",
    )


def _agent_section(runs: tuple[AgentAggregate, ...]) -> str:
    headers = [
        "Agent",
        "Engine",
        "Suite",
        "pass@1",
        "Median Wall Time (s)",
        "Sandbox",
        "Failures",
    ]
    rows = [
        [
            _cell(run.agent),
            _cell(run.engine_label),
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
    headers = ["Model", "Engine", "Context Tokens", "TTFT (s)", "Prefill tok/s"]
    rows = [
        [
            _cell(point.model),
            _cell(point.engine_label),
            _num(f"{point.context_tokens:,}"),
            _num(_fmt(point.ttft_seconds, 3)),
            _num(_fmt(point.prefill_tokens_per_second, 1)),
        ]
        for point in points
    ]
    return _section("Sweep — Prefill vs Context", headers, rows, "No sweep records found.")


def _run_history_section(runs: tuple[RunSummary, ...]) -> str:
    headers = [
        "Run",
        "Timestamp",
        "Models / Agents",
        "Engines",
        "Suites",
        "Tasks",
        "pass@1",
        "Median Speed (s)",
    ]
    rows = []
    for run in runs:
        actors = ", ".join((*run.models, *run.agents)) or "—"
        # Endpoint latency is the primary speed signal; agent runs fall back to wall time.
        speed = (
            run.median_latency_seconds
            if run.median_latency_seconds is not None
            else run.median_wall_time_seconds
        )
        rows.append(
            [
                _cell(run.source),
                _cell(run.timestamp or "—"),
                _cell(actors),
                _cell(", ".join(run.engines) or "unknown (legacy)"),
                _cell(", ".join(run.suites) or "—"),
                _num(str(run.task_count)),
                _num(f"{run.passed}/{run.task_count}"),
                _num(_fmt(speed, 3)),
            ]
        )
    return _section("Run History", headers, rows, "No runs found.")


def _warnings_section(warnings: tuple[DataQualityWarning, ...]) -> str:
    if not warnings:
        return ""
    items = "".join(f"<li>{html.escape(_warning_label(warning))}</li>" for warning in warnings)
    return f'<section class="warnings"><h2>Data Quality Warnings</h2><ul>{items}</ul></section>'


def _warning_label(warning: DataQualityWarning) -> str:
    source = Path(warning.source).name if warning.source else ""
    location = f"{source}:{warning.line}" if warning.line is not None else source
    return f"{location}: {warning.message}" if location else warning.message


def _section(
    title: str,
    headers: list[str],
    rows: list[list[str]],
    empty: str,
    *,
    table_id: str | None = None,
    filter_id: str | None = None,
) -> str:
    # When a table_id is given the table is enhanced client-side (sort + filter);
    # headers carry a stable column index so the inline script can reorder rows.
    sortable = table_id is not None
    head = "".join(_header_cell(header, index, sortable) for index, header in enumerate(headers))
    table_attrs = f' id="{html.escape(table_id)}" class="sortable"' if table_id else ""
    filter_html = (
        f'<input id="{html.escape(filter_id)}" class="filter" type="search" '
        f'placeholder="Filter rows…" aria-label="Filter {html.escape(title)}">'
        if filter_id
        else ""
    )
    if rows:
        body = "".join("<tr>" + "".join(cells) + "</tr>" for cells in rows)
        table = f"<table{table_attrs}><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    else:
        table = (
            f"<table{table_attrs}><thead><tr>{head}</tr></thead></table>"
            f'<p class="empty">{html.escape(empty)}</p>'
        )
    return f"<section><h2>{html.escape(title)}</h2>{filter_html}{table}</section>"


def _header_cell(header: str, index: int, sortable: bool) -> str:
    classes = "num" if _is_num_header(header) else ""
    if sortable:
        classes = (classes + " sortable-th").strip()
        attrs = f' data-sort-key="{index}"'
    else:
        attrs = ""
    class_attr = f' class="{classes}"' if classes else ""
    return f"<th{class_attr}{attrs}>{html.escape(header)}</th>"


def _is_num_header(header: str) -> bool:
    return header not in {
        "Model",
        "Suite",
        "Agent",
        "Engine",
        "Sandbox",
        "Run",
        "Timestamp",
        "Models / Agents",
        "Suites",
    }


def _fmt(value: float | None, places: int) -> str:
    return "—" if value is None else f"{value:.{places}f}"


def _cell(value: object) -> str:
    return f"<td>{html.escape(str(value))}</td>"


def _num(value: object) -> str:
    return f'<td class="num">{html.escape(str(value))}</td>'


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
