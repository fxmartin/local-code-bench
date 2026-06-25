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
  table { border-collapse: collapse; width: 100%; max-width: 70rem; margin-top: 0.4rem; }
  th, td { text-align: left; padding: 0.35rem 0.6rem; border-bottom: 1px solid #8884; }
  th { font-weight: 600; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  #warnings { color: #c0392b; }
  #warnings li { font-family: ui-monospace, monospace; font-size: 0.85rem; }
  .empty { color: #888; }
</style>
</head>
<body>
<h1>Live Benchmark Results</h1>
<p class="empty" id="updated"></p>

<h2>Endpoint Models</h2>
<table>
  <thead>
    <tr>
      <th>Model</th><th>Suite</th><th class="num">pass@1</th><th class="num">Attempts</th>
      <th class="num">Median Latency</th><th class="num">Prefill tok/s</th>
      <th class="num">Decode tok/s</th><th class="num">$/task</th><th class="num">Failures</th>
    </tr>
  </thead>
  <tbody id="endpoint"></tbody>
</table>

<h2>Agent Runs</h2>
<table>
  <thead>
    <tr>
      <th>Agent</th><th>Suite</th><th class="num">pass@1</th><th class="num">Attempts</th>
      <th class="num">Median Wall</th><th>Sandbox</th><th class="num">Failures</th>
    </tr>
  </thead>
  <tbody id="agent"></tbody>
</table>

<h2>Sweep</h2>
<table>
  <thead>
    <tr>
      <th>Model</th><th class="num">Context Tokens</th>
      <th class="num">TTFT</th><th class="num">Prefill tok/s</th>
    </tr>
  </thead>
  <tbody id="sweep"></tbody>
</table>

<h2 id="warnings-title" hidden>Data-quality warnings</h2>
<ul id="warnings"></ul>

<script>
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

function renderEndpoint(rows) {
  const tbody = document.getElementById("endpoint");
  tbody.innerHTML = "";
  if (!rows.length) { fillEmpty(tbody, 9, "No endpoint records yet."); return; }
  for (const r of rows) {
    const tr = document.createElement("tr");
    tr.append(
      cell(r.model), cell(r.suite || "-"), cell(pct(r.pass_rate), true), cell(r.attempts, true),
      cell(num(r.median_latency_seconds), true), cell(num(r.median_prefill_tokens_per_second), true),
      cell(num(r.median_decode_tokens_per_second), true), cell(num(r.mean_cost_usd, 6), true),
      cell(r.failure_count, true),
    );
    tbody.appendChild(tr);
  }
}

function renderAgent(rows) {
  const tbody = document.getElementById("agent");
  tbody.innerHTML = "";
  if (!rows.length) { fillEmpty(tbody, 7, "No agent records yet."); return; }
  for (const r of rows) {
    const tr = document.createElement("tr");
    tr.append(
      cell(r.agent), cell(r.suite || "-"), cell(pct(r.pass_rate), true), cell(r.attempts, true),
      cell(num(r.median_wall_time_seconds), true), cell(r.sandbox_mode || "-"),
      cell(r.failure_count, true),
    );
    tbody.appendChild(tr);
  }
}

function renderSweep(rows) {
  const tbody = document.getElementById("sweep");
  tbody.innerHTML = "";
  if (!rows.length) { fillEmpty(tbody, 4, "No sweep records yet."); return; }
  for (const r of rows) {
    const tr = document.createElement("tr");
    tr.append(
      cell(r.model), cell(r.context_tokens, true),
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

async function refresh() {
  try {
    const res = await fetch("/api/data");
    const data = await res.json();
    renderEndpoint(data.endpoint_models || []);
    renderAgent(data.agent_runs || []);
    renderSweep(data.sweep_points || []);
    renderWarnings(data.warnings || []);
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
