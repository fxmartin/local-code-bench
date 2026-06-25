"""Localhost web control panel for inference engines.

A self-contained dashboard (inlined CSS/JS, no CDN) served by the stdlib
`http.server` bound to `127.0.0.1`. It reuses `manager.py` for every lifecycle
operation, so no business logic is duplicated here; this module only routes HTTP
requests and renders the page.

The two-step `409 {needs_confirmation, others}` → confirm modal → re-post with
`confirm=1` is the web realization of the injected confirm contract from the
mutual-exclusion rule: exactly one headless server ever holds the GPU. GUI
(`app`-lifecycle) engines are never force-quit — a running GUI app blocks a start
with a warning to quit it from its own UI.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ..config import InferencerConfig, load_inferencers
from . import manager
from .manager import InferencerError, InferencerStatus

_TRUTHY = {"1", "true", "yes", "on"}
_GUI_START_REFUSAL = "{name} is a GUI app — start it from its own UI, not the dashboard."
_GUI_STOP_REFUSAL = "{name} is a GUI app — quit it from its own UI, not the dashboard."
_GUI_BLOCK_MESSAGE = "Quit the running GUI app(s) from their own UI before starting a server."


@dataclass(frozen=True)
class Response:
    """A fully-formed HTTP response: status, content type, and encoded body."""

    status: int
    content_type: str
    body: bytes


def _serialize(status: InferencerStatus) -> dict:
    """Project a status onto JSON-safe fields only (never host-sensitive secrets)."""

    return {
        "name": status.name,
        "installed": status.installed,
        "lifecycle": status.lifecycle,
        "running": status.running,
        "pid": status.pid,
        "port": status.port,
        "healthy": status.healthy,
        "detail": status.detail,
    }


def running_others(
    target: str, configs: dict[str, InferencerConfig], state_dir: str | Path
) -> list[InferencerStatus]:
    """Return the live engines other than `target` (servers and GUI apps alike)."""

    statuses = manager.status_all(configs, state_dir)
    return [status for name, status in statuses.items() if name != target and status.running]


def status_action(
    configs: dict[str, InferencerConfig], state_dir: str | Path
) -> tuple[int, dict]:
    """Build the `/api/status` payload — one safe row per engine."""

    statuses = manager.status_all(configs, state_dir)
    return 200, {"inferencers": [_serialize(status) for status in statuses.values()]}


def start_action(
    name: str,
    configs: dict[str, InferencerConfig],
    state_dir: str | Path,
    *,
    confirm: bool,
    force: bool = False,
) -> tuple[int, dict]:
    """Exclusive start: stop running servers (after confirmation), then start `name`.

    A running GUI app blocks the start (it is never force-quit) unless `force`; a
    running server requires `confirm` before it is stopped. Returns an HTTP status
    and a JSON-safe payload describing the next step or the outcome.
    """

    cfg = configs.get(name)
    if cfg is None:
        return 404, {"error": f"unknown inferencer: {name}"}
    if cfg.lifecycle == "app":
        return 400, {"error": _GUI_START_REFUSAL.format(name=name)}

    others = running_others(name, configs, state_dir)
    gui = [status for status in others if status.lifecycle == "app"]
    servers = [status for status in others if status.lifecycle == "server"]

    if gui and not force:
        return 409, {
            "error": "gui_running",
            "message": _GUI_BLOCK_MESSAGE,
            "others": [_serialize(status) for status in gui],
        }
    if servers and not confirm:
        return 409, {
            "needs_confirmation": True,
            "message": "Confirm stopping the running server(s) before starting.",
            "others": [_serialize(status) for status in servers],
        }

    try:
        for status in servers:
            manager.stop(configs[status.name], state_dir)
        started = manager.start(cfg, state_dir)
    except InferencerError as exc:
        return 502, {"error": str(exc)}
    return 200, {"started": _serialize(started)}


def stop_action(
    name: str, configs: dict[str, InferencerConfig], state_dir: str | Path
) -> tuple[int, dict]:
    """Stop a running server idempotently; refuse to touch a GUI app."""

    cfg = configs.get(name)
    if cfg is None:
        return 404, {"error": f"unknown inferencer: {name}"}
    if cfg.lifecycle == "app":
        return 400, {"error": _GUI_STOP_REFUSAL.format(name=name)}

    try:
        manager.stop(cfg, state_dir)
    except InferencerError as exc:
        return 502, {"error": str(exc)}
    return 200, {"stopped": name}


def _json(status: int, payload: dict) -> Response:
    return Response(status, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"))


def _is_truthy(values: list[str]) -> bool:
    return bool(values) and values[0].lower() in _TRUTHY


def handle_request(
    method: str,
    path: str,
    configs: dict[str, InferencerConfig],
    state_dir: str | Path,
) -> Response:
    """Route one request to a page render or a manager-backed JSON action."""

    parts = urlsplit(path)
    route = parts.path
    query = parse_qs(parts.query)
    name = query.get("name", [""])[0]

    if method == "GET" and route == "/":
        return Response(200, "text/html; charset=utf-8", render_page().encode("utf-8"))
    if method == "GET" and route == "/api/status":
        return _json(*status_action(configs, state_dir))
    if method == "POST" and route == "/api/start":
        confirm = _is_truthy(query.get("confirm", []))
        force = _is_truthy(query.get("force", []))
        return _json(*start_action(name, configs, state_dir, confirm=confirm, force=force))
    if method == "POST" and route == "/api/stop":
        return _json(*stop_action(name, configs, state_dir))
    return _json(404, {"error": "not found"})


def make_handler(
    configs: dict[str, InferencerConfig], state_dir: str | Path
) -> type[BaseHTTPRequestHandler]:
    """Build a request-handler class closed over the configs and state dir."""

    class _DashboardHandler(BaseHTTPRequestHandler):
        def _dispatch(self, method: str) -> None:
            response = handle_request(method, self.path, configs, state_dir)
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
    configs: dict[str, InferencerConfig],
    state_dir: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> HTTPServer:
    """Create an `HTTPServer` bound to localhost only."""

    return HTTPServer((host, port), make_handler(configs, state_dir))


def serve_dashboard(
    config_path: str | Path,
    state_dir: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    progress: Callable[[str], None] | None = None,
) -> None:
    """Load the registry and serve the dashboard on localhost until interrupted."""

    configs = load_inferencers(config_path)
    server = make_server(configs, state_dir, host=host, port=port)
    if progress is not None:
        progress(f"inferencer dashboard on http://{host}:{port} (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def render_page() -> str:
    """Return the self-contained dashboard HTML (inlined CSS/JS, no external assets)."""

    return _PAGE


_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Inferencer Control</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: -apple-system, system-ui, sans-serif; margin: 2rem; }
  h1 { font-size: 1.3rem; }
  table { border-collapse: collapse; width: 100%; max-width: 56rem; }
  th, td { text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid #8884; }
  th { font-weight: 600; }
  .dot { display: inline-block; width: 0.7rem; height: 0.7rem; border-radius: 50%; }
  .up { background: #2e9e44; }
  .down { background: #999; }
  button { font: inherit; padding: 0.25rem 0.7rem; cursor: pointer; }
  button:disabled { opacity: 0.4; cursor: default; }
  #err { color: #c0392b; min-height: 1.2rem; }
  #modal { position: fixed; inset: 0; background: #0008; display: none;
           align-items: center; justify-content: center; }
  #modal.show { display: flex; }
  .card { background: Canvas; color: CanvasText; padding: 1.2rem 1.4rem; border-radius: 0.6rem;
          max-width: 26rem; box-shadow: 0 0.5rem 2rem #0006; }
  .card ul { margin: 0.5rem 0 1rem; }
</style>
</head>
<body>
<h1>Inferencer Control</h1>
<p id="err"></p>
<table>
  <thead>
    <tr><th></th><th>Engine</th><th>Lifecycle</th><th>Port</th><th>PID</th><th>State</th><th></th></tr>
  </thead>
  <tbody id="rows"></tbody>
</table>

<div id="modal">
  <div class="card">
    <p id="modal-msg"></p>
    <ul id="modal-list"></ul>
    <button id="modal-confirm">Stop them &amp; start</button>
    <button id="modal-cancel">Cancel</button>
  </div>
</div>

<script>
const rows = document.getElementById("rows");
const err = document.getElementById("err");
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
    const dot = it.healthy ? "up" : (it.running ? "up" : "down");
    const action = it.lifecycle === "app"
      ? "<span>manage in app</span>"
      : (it.running
          ? `<button data-stop="${it.name}">Stop</button>`
          : `<button data-start="${it.name}">Start</button>`);
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
  if (status === 409 && body.needs_confirmation) {
    openModal(name, body);
    return;
  }
  if (status >= 400) {
    setError(body.message || body.error || ("start failed (" + status + ")"));
  }
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
</script>
</body>
</html>
"""
