"""Live results dashboard served over a localhost HTTP server.

The CLI-served dashboard reads result JSONL files *through* HTTP endpoints so the
browser can be refreshed while a benchmark run is still appending records. The
server holds only the result-file paths (never preloaded data), so every
``GET /api/data`` request rebuilds aggregates from the files on disk and reflects
newly appended records without a restart.

Two routes, read-only by design:
- ``GET /``          -> a self-contained dashboard page (inlined CSS/JS, no CDN)
- ``GET /api/data``  -> current aggregates as JSON, plus data-quality warnings

The server binds ``127.0.0.1`` only, so no authentication is required: it never
exposes anything but local dashboard assets and result-derived JSON.

Aggregation is delegated to :func:`local_code_bench.dashboard_model.load_dashboard_data`
(story 07.1-001) so the live view, the static artifact, and the Markdown
leaderboard all share one interpretation of the same JSONL. Unreadable or
partially written lines surface as data-quality warnings rather than crashing.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .dashboard_model import DashboardData, load_dashboard_data


@dataclass(frozen=True)
class Response:
    """A fully-formed HTTP response: status, content type, and encoded body."""

    status: int
    content_type: str
    body: bytes


def dashboard_payload(paths: list[str | Path]) -> dict[str, object]:
    """Read the result files now and return JSON-safe dashboard aggregates.

    Re-reads the files on every call, so a still-running benchmark's appended
    records appear without restarting the server. Delegates reading and
    aggregation (including data-quality warnings) to the shared dashboard model.
    """

    data: DashboardData = load_dashboard_data([Path(path) for path in paths])
    return asdict(data)


def data_action(paths: list[str | Path]) -> tuple[int, dict[str, object]]:
    """Build the ``/api/data`` payload by rebuilding aggregates from disk now."""

    return 200, dashboard_payload(paths)


def _json(status: int, payload: dict[str, object]) -> Response:
    return Response(status, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"))


def handle_request(method: str, path: str, paths: list[str | Path]) -> Response:
    """Route one request to the dashboard page or the result-derived JSON.

    Only ``GET /`` and ``GET /api/data`` are served; everything else (including
    any write method) is a 404, keeping the live server strictly read-only.
    """

    route = urlsplit(path).path
    if method == "GET" and route == "/":
        return Response(200, "text/html; charset=utf-8", render_page().encode("utf-8"))
    if method == "GET" and route == "/api/data":
        return _json(*data_action(paths))
    return _json(404, {"error": "not found"})


def make_handler(paths: list[str | Path]) -> type[BaseHTTPRequestHandler]:
    """Build a request-handler class closed over the result-file paths."""

    class _DashboardHandler(BaseHTTPRequestHandler):
        def _dispatch(self, method: str) -> None:
            response = handle_request(method, self.path, paths)
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            self.end_headers()
            self.wfile.write(response.body)

        def do_GET(self) -> None:  # noqa: N802 - http.server callback name
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802 - http.server callback name
            self._dispatch("POST")

        def log_message(self, format: str, *args: object) -> None:  # silence default logging
            return

    return _DashboardHandler


def make_server(
    paths: list[str | Path],
    *,
    host: str = "127.0.0.1",
    port: int = 8770,
) -> HTTPServer:
    """Create an ``HTTPServer`` bound to localhost only."""

    return HTTPServer((host, port), make_handler(paths))


def serve_dashboard(
    paths: list[str | Path],
    *,
    host: str = "127.0.0.1",
    port: int = 8770,
    progress: Callable[[str], None] | None = None,
) -> None:
    """Serve the live results dashboard on localhost until interrupted."""

    server = make_server(paths, host=host, port=port)
    if progress is not None:
        progress(f"results dashboard on http://{host}:{port} (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def render_page() -> str:
    """Return the self-contained dashboard page (inlined CSS/JS, no external assets)."""

    return _PAGE


_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Live Results</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: -apple-system, system-ui, sans-serif; margin: 2rem; }
  h1 { font-size: 1.3rem; }
  h2 { font-size: 1.05rem; margin-top: 1.6rem; }
  h3 { font-size: 0.95rem; margin: 0.8rem 0 0.2rem; }
  table { border-collapse: collapse; width: 100%; max-width: 80rem; margin-top: 0.4rem; }
  th, td { text-align: left; padding: 0.35rem 0.6rem; border-bottom: 1px solid #8884; }
  th { font-weight: 600; }
  th[data-sort-key] { cursor: pointer; user-select: none; }
  th[data-sort-key]:hover { text-decoration: underline; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  tr.row-clickable { cursor: pointer; }
  tr.row-clickable:hover { background: #8881; }
  #leaderboard-filter { margin-top: 0.6rem; padding: 0.3rem 0.5rem; width: 22rem; max-width: 100%; }
  #drilldown { margin-top: 0.8rem; }
  #drilldown table { max-width: 100%; }
  #drilldown .preview { font-family: ui-monospace, monospace; font-size: 0.8rem; white-space: pre-wrap;
    max-width: 28rem; }
  .pass { color: #1e8449; } .fail { color: #c0392b; }
  #warnings { color: #c0392b; }
  #warnings li { font-family: ui-monospace, monospace; font-size: 0.85rem; }
  .empty { color: #888; }
</style>
</head>
<body>
<h1>Live Benchmark Results</h1>
<p class="empty" id="updated"></p>

<h2>Leaderboard</h2>
<p class="empty">Click a column header to sort; click a row to drill into its tasks.</p>
<input id="leaderboard-filter" type="search" placeholder="Filter by model, agent, suite, or run mode">
<table>
  <thead>
    <tr>
      <th data-sort-key="name">Model / Agent</th>
      <th data-sort-key="engine_label">Engine</th>
      <th data-sort-key="run_mode">Run Mode</th>
      <th data-sort-key="suite">Suite</th>
      <th class="num" data-sort-key="pass_rate">pass@1</th>
      <th class="num" data-sort-key="median_speed_seconds">Median Latency / Wall</th>
      <th class="num" data-sort-key="median_prefill_tokens_per_second">Prefill tok/s</th>
      <th class="num" data-sort-key="median_decode_tokens_per_second">Decode tok/s</th>
      <th class="num" data-sort-key="mean_cost_usd">$/task</th>
      <th class="num" data-sort-key="failure_count">Failures</th>
    </tr>
  </thead>
  <tbody id="leaderboard"></tbody>
</table>
<div id="drilldown"></div>

<h2>Run History</h2>
<table>
  <thead>
    <tr>
      <th>Run</th><th>Timestamp</th><th>Models / Agents</th><th>Engines</th><th>Suites</th>
      <th class="num">Tasks</th><th class="num">pass@1</th><th class="num">Median Speed</th>
    </tr>
  </thead>
  <tbody id="run-history"></tbody>
</table>

<h2>Sweep</h2>
<table>
  <thead>
    <tr>
      <th>Model</th><th>Engine</th><th class="num">Context Tokens</th>
      <th class="num">TTFT</th><th class="num">Prefill tok/s</th>
    </tr>
  </thead>
  <tbody id="sweep"></tbody>
</table>

<h2 id="warnings-title" hidden>Data-quality warnings</h2>
<ul id="warnings"></ul>

<script>
let DATA = { endpoint_models: [], agent_runs: [], sweep_points: [], runs: [], warnings: [] };
let SORT = { key: "pass_rate", dir: -1 };
let OPEN = null;  // { kind, name, suite } of the open drilldown row

function pct(value) { return (Number(value || 0) * 100).toFixed(1) + "%"; }
function num(value, digits) {
  if (value === null || value === undefined) return "-";
  return Number(value).toFixed(digits === undefined ? 3 : digits);
}

function cell(text, numeric) {
  const td = document.createElement("td");
  if (numeric) td.className = "num";
  td.textContent = text;
  return td;
}

function fillEmpty(tbody, cols, label) {
  const tr = document.createElement("tr");
  const td = document.createElement("td");
  td.colSpan = cols;
  td.className = "empty";
  td.textContent = label;
  tr.appendChild(td);
  tbody.appendChild(tr);
}

// Merge endpoint and agent aggregates into one comparable leaderboard row set.
function leaderboardRows() {
  const rows = [];
  for (const m of DATA.endpoint_models || []) {
    rows.push({
      kind: "endpoint", name: m.model, engine_label: m.engine_label,
      run_mode: "endpoint", suite: m.suite,
      pass_rate: m.pass_rate, median_speed_seconds: m.median_latency_seconds,
      median_prefill_tokens_per_second: m.median_prefill_tokens_per_second,
      median_decode_tokens_per_second: m.median_decode_tokens_per_second,
      mean_cost_usd: m.mean_cost_usd, failure_count: m.failure_count, tasks: m.tasks || [],
    });
  }
  for (const a of DATA.agent_runs || []) {
    rows.push({
      kind: "agent", name: a.agent, engine_label: a.engine_label,
      run_mode: "agent", suite: a.suite,
      pass_rate: a.pass_rate, median_speed_seconds: a.median_wall_time_seconds,
      median_prefill_tokens_per_second: null, median_decode_tokens_per_second: null,
      mean_cost_usd: null, failure_count: a.failure_count, tasks: a.tasks || [],
    });
  }
  return rows;
}

function applyFilterAndSort(rows) {
  const q = (document.getElementById("leaderboard-filter").value || "").toLowerCase().trim();
  let out = rows;
  if (q) {
    out = rows.filter((r) =>
      [r.name, r.engine_label, r.run_mode, r.suite]
        .some((v) => (v || "").toLowerCase().includes(q)));
  }
  const key = SORT.key, dir = SORT.dir;
  return out.slice().sort((a, b) => {
    let x = a[key], y = b[key];
    if (typeof x === "string" || typeof y === "string") {
      return String(x || "").localeCompare(String(y || "")) * dir;
    }
    if (x === null || x === undefined) return 1;   // missing values sink
    if (y === null || y === undefined) return -1;
    return (x - y) * dir;
  });
}

function renderLeaderboard() {
  const tbody = document.getElementById("leaderboard");
  tbody.innerHTML = "";
  const rows = applyFilterAndSort(leaderboardRows());
  if (!rows.length) { fillEmpty(tbody, 10, "No leaderboard rows yet."); return; }
  for (const r of rows) {
    const tr = document.createElement("tr");
    tr.className = "row-clickable";
    tr.append(
      cell(r.name), cell(r.engine_label || "unknown (legacy)"),
      cell(r.run_mode), cell(r.suite || "-"),
      cell(pct(r.pass_rate), true), cell(num(r.median_speed_seconds), true),
      cell(num(r.median_prefill_tokens_per_second), true),
      cell(num(r.median_decode_tokens_per_second), true),
      cell(r.mean_cost_usd === null ? "-" : num(r.mean_cost_usd, 6), true),
      cell(r.failure_count, true),
    );
    tr.addEventListener("click", () => {
      OPEN = { kind: r.kind, name: r.name, engine_label: r.engine_label, suite: r.suite };
      renderDrilldown();
    });
    tbody.appendChild(tr);
  }
}

function findRow(open) {
  return leaderboardRows().find(
    (r) => r.kind === open.kind && r.name === open.name &&
      r.engine_label === open.engine_label && r.suite === open.suite);
}

// Per-task drilldown: task id, pass/fail, failure reason, latency, cost, tokens, preview.
function renderDrilldown() {
  const host = document.getElementById("drilldown");
  host.innerHTML = "";
  if (!OPEN) return;
  const row = findRow(OPEN);
  if (!row) { OPEN = null; return; }

  const title = document.createElement("h3");
  title.textContent = "Tasks - " + row.name + " / " + row.engine_label +
    (row.suite ? " (" + row.suite + ")" : "");
  host.appendChild(title);

  const table = document.createElement("table");
  const head = document.createElement("thead");
  const cols = row.kind === "endpoint"
    ? ["Task", "Result", "Failure", "Latency", "$/task", "Prompt tok", "Completion tok", "Preview"]
    : ["Task", "Result", "Failure", "Wall Time", "Exit Code", "Cost Status"];
  const htr = document.createElement("tr");
  for (const c of cols) { const th = document.createElement("th"); th.textContent = c; htr.appendChild(th); }
  head.appendChild(htr);
  table.appendChild(head);

  const body = document.createElement("tbody");
  for (const t of row.tasks) {
    const tr = document.createElement("tr");
    const result = document.createElement("td");
    result.textContent = t.passed === true ? "pass" : (t.passed === false ? "fail" : "-");
    result.className = t.passed === true ? "pass" : (t.passed === false ? "fail" : "");
    if (row.kind === "endpoint") {
      const preview = document.createElement("td");
      preview.className = "preview";
      preview.textContent = t.raw_response_preview || "";
      tr.append(
        cell(t.task_id), result, cell(t.failure_reason || "-"),
        cell(num(t.latency_seconds), true), cell(num(t.cost_usd, 6), true),
        cell(t.prompt_tokens === null ? "-" : t.prompt_tokens, true),
        cell(t.completion_tokens === null ? "-" : t.completion_tokens, true), preview,
      );
    } else {
      tr.append(
        cell(t.task_id), result, cell(t.failure_reason || "-"),
        cell(num(t.wall_time_seconds), true),
        cell(t.exit_code === null ? "-" : t.exit_code, true), cell(t.cost_status || "-"),
      );
    }
    body.appendChild(tr);
  }
  if (!row.tasks.length) fillEmpty(body, cols.length, "No tasks recorded.");
  table.appendChild(body);
  host.appendChild(table);
}

function renderRunHistory() {
  const tbody = document.getElementById("run-history");
  tbody.innerHTML = "";
  const rows = DATA.runs || [];
  if (!rows.length) { fillEmpty(tbody, 8, "No runs yet."); return; }
  for (const r of rows) {
    const actors = (r.models || []).concat(r.agents || []);
    const speed = r.median_latency_seconds !== null && r.median_latency_seconds !== undefined
      ? r.median_latency_seconds : r.median_wall_time_seconds;
    const tr = document.createElement("tr");
    tr.append(
      cell(r.source), cell(r.timestamp || "-"), cell(actors.join(", ") || "-"),
      cell((r.engines || []).join(", ") || "unknown (legacy)"),
      cell((r.suites || []).join(", ") || "-"), cell(r.task_count, true),
      cell(pct(r.pass_rate), true), cell(num(speed), true),
    );
    tbody.appendChild(tr);
  }
}

function renderSweep(rows) {
  const tbody = document.getElementById("sweep");
  tbody.innerHTML = "";
  if (!rows.length) { fillEmpty(tbody, 5, "No sweep records yet."); return; }
  for (const r of rows) {
    const tr = document.createElement("tr");
    tr.append(
      cell(r.model), cell(r.engine_label || "unknown (legacy)"), cell(r.context_tokens, true),
      cell(num(r.ttft_seconds), true), cell(num(r.prefill_tokens_per_second), true),
    );
    tbody.appendChild(tr);
  }
}

function renderWarnings(items) {
  const list = document.getElementById("warnings");
  const title = document.getElementById("warnings-title");
  list.innerHTML = "";
  title.hidden = items.length === 0;
  for (const w of items) {
    const li = document.createElement("li");
    const where = w.line === null ? w.source : (w.source + ":" + w.line);
    li.textContent = where + " - " + w.message;
    list.appendChild(li);
  }
}

function renderAll() {
  renderLeaderboard();
  renderDrilldown();
  renderRunHistory();
  renderSweep(DATA.sweep_points || []);
  renderWarnings(DATA.warnings || []);
}

document.getElementById("leaderboard-filter").addEventListener("input", renderLeaderboard);
for (const th of document.querySelectorAll("th[data-sort-key]")) {
  th.addEventListener("click", () => {
    const key = th.getAttribute("data-sort-key");
    SORT = { key, dir: SORT.key === key ? -SORT.dir : -1 };
    renderLeaderboard();
  });
}

async function refresh() {
  try {
    const res = await fetch("/api/data");
    DATA = await res.json();
    renderAll();
    document.getElementById("updated").textContent = "Refreshed";
  } catch (e) {
    document.getElementById("updated").textContent = "data unavailable: " + e;
  }
}

refresh();
setInterval(refresh, 3000);
</script>
</body>
</html>
"""
