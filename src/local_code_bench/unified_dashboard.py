"""Single-page unified dashboard: Inferencers, Results, and Run on one localhost page.

This is the Epic-09 shell (story 09.1-001). It does not reinvent the inferencer
control panel (Epic-08) or the live results view (Epic-07); it *composes* them under
one stdlib ``http.server`` bound to ``127.0.0.1`` and serves a single self-contained
page (inlined CSS/JS, no CDN, no build step) whose three sections are switched
client-side without reloading the app.

All business logic stays where it already lives — every endpoint here delegates:

- ``GET /``           -> the unified page (inlined assets)
- ``GET /api/status`` -> :func:`inferencers.dashboard.status_action` (Epic-08)
- ``POST /api/start`` -> :func:`inferencers.dashboard.start_action`  (Epic-08, exclusive)
- ``POST /api/stop``  -> :func:`inferencers.dashboard.stop_action`   (Epic-08)
- ``GET /api/data``   -> :func:`dashboard_server.data_action`        (Epic-07 aggregates)

Both delegated surfaces already project onto JSON-safe fields only (no API keys,
``.env`` contents, or host-sensitive paths), and the server binds localhost only,
so no authentication is required — a single-user benchmark-box tool. The Run
section is the navigable seam the benchmark launcher (story 09.2-001) plugs into.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import dashboard_server as results_panel
from .config import InferencerConfig, load_inferencers
from .inferencers import dashboard as inferencer_panel

_TRUTHY = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class DashboardContext:
    """Everything the unified server needs to answer a request, held by reference.

    The inferencer registry and state dir drive the Inferencers section; the result
    paths drive the Results section. Result files are re-read on every ``/api/data``
    request (never preloaded), so a still-running benchmark's records appear on
    refresh without a restart.
    """

    configs: dict[str, InferencerConfig]
    state_dir: str | Path
    result_paths: list[str | Path] = field(default_factory=list)


@dataclass(frozen=True)
class Response:
    """A fully-formed HTTP response: status, content type, and encoded body."""

    status: int
    content_type: str
    body: bytes


def _json(status: int, payload: dict) -> Response:
    return Response(status, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"))


def _is_truthy(values: list[str]) -> bool:
    return bool(values) and values[0].lower() in _TRUTHY


def handle_request(method: str, path: str, ctx: DashboardContext) -> Response:
    """Route one request to the unified page or a delegated section action."""

    parts = urlsplit(path)
    route = parts.path
    query = parse_qs(parts.query)
    name = query.get("name", [""])[0]

    if method == "GET" and route == "/":
        return Response(200, "text/html; charset=utf-8", render_page().encode("utf-8"))
    if method == "GET" and route == "/api/status":
        return _json(*inferencer_panel.status_action(ctx.configs, ctx.state_dir))
    if method == "POST" and route == "/api/start":
        confirm = _is_truthy(query.get("confirm", []))
        force = _is_truthy(query.get("force", []))
        return _json(
            *inferencer_panel.start_action(
                name, ctx.configs, ctx.state_dir, confirm=confirm, force=force
            )
        )
    if method == "POST" and route == "/api/stop":
        return _json(*inferencer_panel.stop_action(name, ctx.configs, ctx.state_dir))
    if method == "GET" and route == "/api/data":
        return _json(*results_panel.data_action(ctx.result_paths))
    return _json(404, {"error": "not found"})


def make_handler(ctx: DashboardContext) -> type[BaseHTTPRequestHandler]:
    """Build a request-handler class closed over the dashboard context."""

    class _DashboardHandler(BaseHTTPRequestHandler):
        def _dispatch(self, method: str) -> None:
            response = handle_request(method, self.path, ctx)
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
    ctx: DashboardContext,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> HTTPServer:
    """Create an ``HTTPServer`` bound to localhost only."""

    return HTTPServer((host, port), make_handler(ctx))


def serve_dashboard(
    config_path: str | Path,
    state_dir: str | Path,
    result_paths: list[str | Path],
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    progress: Callable[[str], None] | None = None,
) -> None:
    """Load the inferencer registry and serve the unified dashboard until interrupted."""

    configs = load_inferencers(config_path)
    ctx = DashboardContext(configs=configs, state_dir=state_dir, result_paths=list(result_paths))
    server = make_server(ctx, host=host, port=port)
    if progress is not None:
        progress(f"unified dashboard on http://{host}:{port} (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def render_page() -> str:
    """Return the self-contained unified page (inlined CSS/JS, no external assets)."""

    return _PAGE


_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>local-code-bench Dashboard</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: -apple-system, system-ui, sans-serif; margin: 0; }
  header { padding: 1.2rem 2rem 0; border-bottom: 1px solid #8884; }
  h1 { font-size: 1.3rem; margin: 0 0 0.8rem; }
  h2 { font-size: 1.05rem; margin-top: 1.6rem; }
  h3 { font-size: 0.95rem; margin: 0.8rem 0 0.2rem; }
  nav { display: flex; gap: 0.4rem; }
  nav button { font: inherit; padding: 0.4rem 0.9rem; cursor: pointer; border: 1px solid #8884;
    border-bottom: none; border-radius: 0.4rem 0.4rem 0 0; background: transparent; }
  nav button.active { font-weight: 600; background: #8881; }
  main { margin: 1.4rem 2rem 3rem; }
  table { border-collapse: collapse; width: 100%; max-width: 80rem; margin-top: 0.4rem; }
  th, td { text-align: left; padding: 0.35rem 0.6rem; border-bottom: 1px solid #8884; }
  th { font-weight: 600; }
  th[data-sort-key] { cursor: pointer; user-select: none; }
  th[data-sort-key]:hover { text-decoration: underline; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  tr.row-clickable { cursor: pointer; }
  tr.row-clickable:hover { background: #8881; }
  button.act { font: inherit; padding: 0.25rem 0.7rem; cursor: pointer; }
  button.act:disabled { opacity: 0.4; cursor: default; }
  .dot { display: inline-block; width: 0.7rem; height: 0.7rem; border-radius: 50%; }
  .up { background: #2e9e44; } .down { background: #999; }
  #inf-err { color: #c0392b; min-height: 1.2rem; }
  #modal { position: fixed; inset: 0; background: #0008; display: none;
           align-items: center; justify-content: center; }
  #modal.show { display: flex; }
  .card { background: Canvas; color: CanvasText; padding: 1.2rem 1.4rem; border-radius: 0.6rem;
          max-width: 26rem; box-shadow: 0 0.5rem 2rem #0006; }
  .card ul { margin: 0.5rem 0 1rem; }
  #leaderboard-filter { margin-top: 0.6rem; padding: 0.3rem 0.5rem; width: 22rem; max-width: 100%; }
  #drilldown { margin-top: 0.8rem; }
  #drilldown table { max-width: 100%; }
  #drilldown .preview { font-family: ui-monospace, monospace; font-size: 0.8rem;
    white-space: pre-wrap; max-width: 28rem; }
  .pass { color: #1e8449; } .fail { color: #c0392b; }
  #warnings { color: #c0392b; }
  #warnings li { font-family: ui-monospace, monospace; font-size: 0.85rem; }
  .empty { color: #888; }
  .note { color: #888; max-width: 44rem; line-height: 1.5; }
</style>
</head>
<body>
<header>
  <h1>local-code-bench</h1>
  <nav id="nav">
    <button data-section="inferencers" class="active">Inferencers</button>
    <button data-section="results">Results</button>
    <button data-section="run">Run</button>
  </nav>
</header>
<main>

<section id="section-inferencers" class="section">
  <h2>Inferencer Control</h2>
  <p id="inf-err"></p>
  <table>
    <thead>
      <tr><th></th><th>Engine</th><th>Lifecycle</th><th>Port</th><th>PID</th><th>State</th><th></th></tr>
    </thead>
    <tbody id="rows"></tbody>
  </table>
</section>

<section id="section-results" class="section" hidden>
  <h2>Live Benchmark Results</h2>
  <p class="empty" id="updated"></p>
  <h3>Leaderboard</h3>
  <p class="empty">Click a column header to sort; click a row to drill into its tasks.</p>
  <input id="leaderboard-filter" type="search" placeholder="Filter by model, agent, suite, or run mode">
  <table>
    <thead>
      <tr>
        <th data-sort-key="name">Model / Agent</th>
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

  <h3>Run History</h3>
  <table>
    <thead>
      <tr>
        <th>Run</th><th>Timestamp</th><th>Models / Agents</th><th>Suites</th>
        <th class="num">Tasks</th><th class="num">pass@1</th><th class="num">Median Speed</th>
      </tr>
    </thead>
    <tbody id="run-history"></tbody>
  </table>

  <h3>Sweep</h3>
  <table>
    <thead>
      <tr>
        <th>Model</th><th class="num">Context Tokens</th>
        <th class="num">TTFT</th><th class="num">Prefill tok/s</th>
      </tr>
    </thead>
    <tbody id="sweep"></tbody>
  </table>

  <h3 id="warnings-title" hidden>Data-quality warnings</h3>
  <ul id="warnings"></ul>
</section>

<section id="section-run" class="section" hidden>
  <h2>Run a Benchmark</h2>
  <p class="note">Compose a benchmark from a model, an inferencer, and one or more test
    suites, then launch it here. The launcher and live run monitoring plug into this
    section (stories 09.2&ndash;09.4); until then, drive runs from the
    <code>bench</code> CLI.</p>
</section>

</main>

<div id="modal">
  <div class="card">
    <p id="modal-msg"></p>
    <ul id="modal-list"></ul>
    <button class="act" id="modal-confirm">Stop them &amp; start</button>
    <button class="act" id="modal-cancel">Cancel</button>
  </div>
</div>

<script>
// Client-side section navigation: show one section, no reload, no build step.
(function () {
  const buttons = document.querySelectorAll("#nav button");
  const sections = {
    inferencers: document.getElementById("section-inferencers"),
    results: document.getElementById("section-results"),
    run: document.getElementById("section-run"),
  };
  function show(name) {
    for (const key in sections) sections[key].hidden = key !== name;
    buttons.forEach((b) => b.classList.toggle("active", b.dataset.section === name));
  }
  buttons.forEach((b) => b.addEventListener("click", () => show(b.dataset.section)));
  show("inferencers");
})();

// Inferencers section: thin client over Epic-08's /api/status, /api/start, /api/stop.
(function () {
  const rows = document.getElementById("rows");
  const err = document.getElementById("inf-err");
  const modal = document.getElementById("modal");
  let pending = null;

  function setError(msg) { err.textContent = msg || ""; }

  async function refresh() {
    try {
      const res = await fetch("/api/status");
      const data = await res.json();
      render(data.inferencers || []);
    } catch (e) {
      setError("status unavailable: " + e);
    }
  }

  function render(items) {
    rows.innerHTML = "";
    for (const it of items) {
      const tr = document.createElement("tr");
      const dot = it.running ? "up" : "down";
      const action = it.lifecycle === "app"
        ? "<span>manage in app</span>"
        : (it.running
            ? `<button class="act" data-stop="${it.name}">Stop</button>`
            : `<button class="act" data-start="${it.name}">Start</button>`);
      tr.innerHTML =
        `<td><span class="dot ${dot}"></span></td>` +
        `<td>${it.name}</td><td>${it.lifecycle}</td><td>${it.port}</td>` +
        `<td>${it.pid ?? ""}</td><td>${it.detail}</td><td>${action}</td>`;
      rows.appendChild(tr);
    }
  }

  async function post(url) {
    const res = await fetch(url, { method: "POST" });
    let body = {};
    try { body = await res.json(); } catch (e) { body = {}; }
    return { status: res.status, body };
  }

  async function startEngine(name, confirm) {
    setError("");
    const url = "/api/start?name=" + encodeURIComponent(name) + (confirm ? "&confirm=1" : "");
    const { status, body } = await post(url);
    if (status === 409 && body.needs_confirmation) { openModal(name, body); return; }
    if (status >= 400) setError(body.message || body.error || ("start failed (" + status + ")"));
    refresh();
  }

  function openModal(name, body) {
    pending = name;
    document.getElementById("modal-msg").textContent = body.message || "Confirm exclusive start.";
    const list = document.getElementById("modal-list");
    list.innerHTML = "";
    for (const o of body.others || []) {
      const li = document.createElement("li");
      li.textContent = o.name + " (port " + o.port + ")";
      list.appendChild(li);
    }
    modal.classList.add("show");
  }

  function closeModal() { modal.classList.remove("show"); pending = null; }

  document.getElementById("modal-confirm").onclick = () => {
    const name = pending; closeModal();
    if (name) startEngine(name, true);
  };
  document.getElementById("modal-cancel").onclick = closeModal;

  rows.addEventListener("click", (ev) => {
    const start = ev.target.getAttribute("data-start");
    const stop = ev.target.getAttribute("data-stop");
    if (start) startEngine(start, false);
    if (stop) post("/api/stop?name=" + encodeURIComponent(stop)).then(refresh);
  });

  refresh();
  setInterval(refresh, 2000);
})();

// Results section: thin client over Epic-07's /api/data live aggregates.
(function () {
  let DATA = { endpoint_models: [], agent_runs: [], sweep_points: [], runs: [], warnings: [] };
  let SORT = { key: "pass_rate", dir: -1 };
  let OPEN = null;

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

  function leaderboardRows() {
    const rows = [];
    for (const m of DATA.endpoint_models || []) {
      rows.push({
        kind: "endpoint", name: m.model, run_mode: "endpoint", suite: m.suite,
        pass_rate: m.pass_rate, median_speed_seconds: m.median_latency_seconds,
        median_prefill_tokens_per_second: m.median_prefill_tokens_per_second,
        median_decode_tokens_per_second: m.median_decode_tokens_per_second,
        mean_cost_usd: m.mean_cost_usd, failure_count: m.failure_count, tasks: m.tasks || [],
      });
    }
    for (const a of DATA.agent_runs || []) {
      rows.push({
        kind: "agent", name: a.agent, run_mode: "agent", suite: a.suite,
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
        [r.name, r.run_mode, r.suite].some((v) => (v || "").toLowerCase().includes(q)));
    }
    const key = SORT.key, dir = SORT.dir;
    return out.slice().sort((a, b) => {
      let x = a[key], y = b[key];
      if (typeof x === "string" || typeof y === "string") {
        return String(x || "").localeCompare(String(y || "")) * dir;
      }
      if (x === null || x === undefined) return 1;
      if (y === null || y === undefined) return -1;
      return (x - y) * dir;
    });
  }

  function renderLeaderboard() {
    const tbody = document.getElementById("leaderboard");
    tbody.innerHTML = "";
    const rows = applyFilterAndSort(leaderboardRows());
    if (!rows.length) { fillEmpty(tbody, 9, "No leaderboard rows yet."); return; }
    for (const r of rows) {
      const tr = document.createElement("tr");
      tr.className = "row-clickable";
      tr.append(
        cell(r.name), cell(r.run_mode), cell(r.suite || "-"),
        cell(pct(r.pass_rate), true), cell(num(r.median_speed_seconds), true),
        cell(num(r.median_prefill_tokens_per_second), true),
        cell(num(r.median_decode_tokens_per_second), true),
        cell(r.mean_cost_usd === null ? "-" : num(r.mean_cost_usd, 6), true),
        cell(r.failure_count, true),
      );
      tr.addEventListener("click", () => {
        OPEN = { kind: r.kind, name: r.name, suite: r.suite };
        renderDrilldown();
      });
      tbody.appendChild(tr);
    }
  }

  function findRow(open) {
    return leaderboardRows().find(
      (r) => r.kind === open.kind && r.name === open.name && r.suite === open.suite);
  }

  function renderDrilldown() {
    const host = document.getElementById("drilldown");
    host.innerHTML = "";
    if (!OPEN) return;
    const row = findRow(OPEN);
    if (!row) { OPEN = null; return; }

    const title = document.createElement("h3");
    title.textContent = "Tasks - " + row.name + (row.suite ? " (" + row.suite + ")" : "");
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
    if (!rows.length) { fillEmpty(tbody, 7, "No runs yet."); return; }
    for (const r of rows) {
      const actors = (r.models || []).concat(r.agents || []);
      const speed = r.median_latency_seconds !== null && r.median_latency_seconds !== undefined
        ? r.median_latency_seconds : r.median_wall_time_seconds;
      const tr = document.createElement("tr");
      tr.append(
        cell(r.source), cell(r.timestamp || "-"), cell(actors.join(", ") || "-"),
        cell((r.suites || []).join(", ") || "-"), cell(r.task_count, true),
        cell(pct(r.pass_rate), true), cell(num(speed), true),
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
})();
</script>
</body>
</html>
"""
