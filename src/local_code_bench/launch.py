"""Launch orchestration endpoint for the unified dashboard (story 09.3-001).

A single ``POST /api/run`` handler that composes existing harness pieces into the
one-click benchmark launch FX wants:

1. Validate the ``model + inferencer + suites`` composition, rejecting unknown or
   incompatible selections before anything is started.
2. Bring the inferencer up **exclusively** through Epic-08's
   :func:`manager.start_exclusive`, mirroring the inferencer dashboard's two-step
   ``409 {needs_confirmation, others}`` confirmation contract so exactly one
   inference server is ever active.
3. Run the chosen suites in order, in the background, through the existing
   :func:`runner.run_endpoint_suite`, writing JSONL to ``results/`` and returning a
   run id immediately.

A single-run lock serializes launches: while one run is in flight a second launch
is rejected, so the one-active-server invariant is never violated. No new scoring
path is introduced — generated code still runs only in the existing sandbox via the
runner. The launcher holds in-memory run state (id, status, counts) while the JSONL
file remains the durable source of truth; story 09.4-001 reads this state to render
live progress.
"""

from __future__ import annotations

import json
import statistics
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from . import results, runner, tasks
from .config import InferencerConfig, ModelConfig
from .inferencers import manager
from .inferencers.dashboard import (
    _GUI_BLOCK_MESSAGE,
    _GUI_START_REFUSAL,
    _serialize,
)
from .inferencers.manager import InferencerError

# Suite ids accepted by :func:`tasks.load_suite`. The full availability-aware
# catalog (built-ins + config-registered custom suites) is story 09.5-001; this
# story only needs to reject names the runner could never load.
KNOWN_SUITES: tuple[str, ...] = (
    "humaneval",
    "mbpp",
    "canary",
    "humaneval-plus",
    "mbpp-plus",
)


@dataclass(frozen=True)
class Response:
    """A fully-formed HTTP response: status, content type, and encoded body."""

    status: int
    content_type: str
    body: bytes


@dataclass
class RunState:
    """In-memory state for one launched run; the JSONL file is the durable record."""

    id: str
    model: str
    inferencer: str
    suites: list[str]
    result_file: str
    status: str = "running"
    total: int = 0
    completed: int = 0
    passed: int = 0
    failed: int = 0
    last_event: str | None = None
    error: str | None = None

    def serialize(self) -> dict[str, object]:
        """Project onto JSON-safe fields only (``result_file`` is a name, not a path)."""

        return {
            "run_id": self.id,
            "model": self.model,
            "inferencer": self.inferencer,
            "suites": list(self.suites),
            "result_file": self.result_file,
            "status": self.status,
            "total": self.total,
            "completed": self.completed,
            "passed": self.passed,
            "failed": self.failed,
            "last_event": self.last_event,
            "error": self.error,
        }


class RunOrchestrator:
    """Serializes benchmark launches and runs them in the background.

    One orchestrator owns the single-run lock for a dashboard process. Every launch
    goes through :func:`manager.start_exclusive`, so the one-active-server invariant
    is enforced in the one place Epic-08 put it rather than re-implemented here.
    """

    def __init__(
        self,
        *,
        models: dict[str, ModelConfig],
        inferencers: dict[str, InferencerConfig],
        state_dir: str | Path,
        results_dir: str | Path,
        cache_dir: str | Path = ".cache/benchmarks",
    ) -> None:
        self._models = models
        self._inferencers = inferencers
        self._state_dir = state_dir
        self._results_dir = results_dir
        self._cache_dir = cache_dir
        self._lock = threading.Lock()
        self._active_run_id: str | None = None
        self._runs: dict[str, RunState] = {}
        self._thread: threading.Thread | None = None

    # -- public API -------------------------------------------------------

    def launch(
        self,
        *,
        model: str,
        inferencer: str,
        suites: list[str],
        confirm: bool = False,
        force: bool = False,
    ) -> tuple[int, dict[str, object]]:
        """Validate, exclusively start the inferencer, then run suites in background.

        Returns ``(202, {run_id, ...})`` once the run is accepted. A composition
        error is ``400``; a running server needing confirmation is ``409
        {needs_confirmation}``; a blocking GUI app is ``409 {gui_running}``; a run
        already in flight is ``409 {run_in_flight}``; a failed start is ``502``.
        """

        error = self._validate(model, inferencer, suites)
        if error is not None:
            return 400, error

        # Reserve the single-run slot before any server work so two concurrent
        # launches cannot both bring an engine up.
        with self._lock:
            if self._active_run_id is not None:
                return 409, {
                    "error": "run_in_flight",
                    "message": "A benchmark run is already in flight; wait for it to finish.",
                    "active_run_id": self._active_run_id,
                }
            run_id = uuid4().hex[:12]
            self._active_run_id = run_id

        start_code, start_payload = self._start_inferencer(inferencer, confirm=confirm, force=force)
        if start_code != 200:
            # Nothing was launched (needs confirmation, blocked, or failed): free
            # the slot so the corrected re-submit can proceed.
            with self._lock:
                self._active_run_id = None
            return start_code, start_payload

        result_path = results.new_run_path(self._results_dir)
        state = RunState(
            id=run_id,
            model=model,
            inferencer=inferencer,
            suites=list(suites),
            result_file=result_path.name,
        )
        self._runs[run_id] = state
        # Snapshot the accept payload before the background thread can advance the
        # (mutable) state, so the response always reflects the just-accepted run.
        accepted = state.serialize()
        self._thread = threading.Thread(
            target=self._execute_run,
            args=(state, result_path),
            daemon=True,
        )
        self._thread.start()
        return 202, accepted

    def get_run(self, run_id: str) -> RunState | None:
        """Return the in-memory state for a run id, or ``None`` if unknown."""

        return self._runs.get(run_id)

    def runs(self) -> list[RunState]:
        """Return all tracked runs (newest tracking order preserved)."""

        return list(self._runs.values())

    def run_payload(self, run_id: str) -> dict[str, object] | None:
        """Serialize one run's live progress, or ``None`` if the id is unknown.

        Merges the in-memory counts (passed/failed/remaining, current task, terminal
        status and reason) with cost and decode speed accumulated so far by tailing
        the run's JSONL — the source of truth story 09.4-001 renders for the user.
        """

        state = self._runs.get(run_id)
        return self._payload(state) if state is not None else None

    def runs_payload(self) -> list[dict[str, object]]:
        """Serialize every tracked run's live progress (tracking order preserved)."""

        return [self._payload(state) for state in self._runs.values()]

    def join(self, timeout: float | None = None) -> None:
        """Wait for the current background run thread to finish (test/shutdown aid)."""

        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    # -- internals --------------------------------------------------------

    def _validate(self, model: str, inferencer: str, suites: list[str]) -> dict[str, object] | None:
        if model not in self._models:
            return {"error": f"unknown model: {model}"}
        cfg = self._inferencers.get(inferencer)
        if cfg is None:
            return {"error": f"unknown inferencer: {inferencer}"}
        if cfg.lifecycle == "app":
            return {"error": _GUI_START_REFUSAL.format(name=inferencer)}
        if not suites:
            return {"error": "select at least one test suite"}
        unknown = [name for name in suites if name not in KNOWN_SUITES]
        if unknown:
            return {"error": f"unknown suite(s): {', '.join(unknown)}"}
        return None

    def _start_inferencer(
        self, name: str, *, confirm: bool, force: bool
    ) -> tuple[int, dict[str, object]]:
        """Exclusive start mirroring the inferencer dashboard's confirmation contract."""

        cfg = self._inferencers[name]
        others = manager.running_others(name, self._inferencers, self._state_dir)
        gui = [s for s in others if self._inferencers[s.name].lifecycle == "app"]
        servers = [s for s in others if self._inferencers[s.name].lifecycle == "server"]

        if gui and not force:
            return 409, {
                "error": "gui_running",
                "message": _GUI_BLOCK_MESSAGE,
                "others": [_serialize(s) for s in gui],
            }
        if servers and not confirm:
            return 409, {
                "needs_confirmation": True,
                "message": "Confirm stopping the running server(s) before launching.",
                "others": [_serialize(s) for s in servers],
            }

        try:
            started = manager.start_exclusive(
                cfg,
                self._inferencers,
                self._state_dir,
                confirm=lambda _servers: True,
                force=force,
            )
        except InferencerError as exc:
            return 502, {"error": str(exc)}
        return 200, {"started": _serialize(started)}

    def _execute_run(self, state: RunState, result_path: Path) -> None:
        """Run each selected suite in order, then release the single-run lock."""

        try:
            model = self._models[state.model]
            for suite_name in state.suites:
                suite_tasks = tasks.load_suite(suite_name, cache_dir=self._cache_dir)
                state.total += len(suite_tasks)
                summary = runner.run_endpoint_suite(
                    models=[model],
                    tasks=suite_tasks,
                    result_path=result_path,
                    progress=lambda message, s=state: self._on_progress(s, message),
                )
                state.passed += summary.get("passed", 0)
                state.failed += summary.get("failed", 0) + summary.get("infra_failed", 0)
            state.status = "completed"
        except Exception as exc:  # noqa: BLE001 - surface any failure as a run reason
            state.status = "failed"
            state.error = str(exc)
        finally:
            with self._lock:
                self._active_run_id = None

    @staticmethod
    def _on_progress(state: RunState, message: str) -> None:
        state.completed += 1
        state.last_event = message

    def _payload(self, state: RunState) -> dict[str, object]:
        payload = state.serialize()
        payload["remaining"] = max(state.total - state.completed, 0)
        payload.update(accumulated_metrics(Path(self._results_dir) / state.result_file))
        return payload


def accumulated_metrics(path: str | Path) -> dict[str, float | None]:
    """Tail a run's JSONL for accumulated cost and a representative decode speed.

    Reads the durable result file line by line, tolerating a partially-written
    trailing line a status poll can catch mid-append, and ignoring non-task records
    such as the run-metadata header. Returns ``{"cost_usd",
    "decode_tokens_per_second"}`` with ``None`` for a metric no record carries yet,
    so an in-flight run renders cleanly before its first task lands.
    """

    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return {"cost_usd": None, "decode_tokens_per_second": None}

    cost = 0.0
    saw_cost = False
    decode_speeds: list[float] = []
    for line in lines:
        text = line.strip()
        if not text:
            continue
        try:
            record = json.loads(text)
        except json.JSONDecodeError:
            continue  # partially-written tail line; the next poll catches the rest
        if not isinstance(record, dict) or record.get("run_mode") != "endpoint":
            continue
        if "task_id" not in record:
            continue
        value = record.get("cost_usd")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            cost += float(value)
            saw_cost = True
        metrics = record.get("metrics")
        if isinstance(metrics, dict):
            decode = metrics.get("decode_tokens_per_second")
            if isinstance(decode, (int, float)) and not isinstance(decode, bool):
                decode_speeds.append(float(decode))

    return {
        "cost_usd": cost if saw_cost else None,
        "decode_tokens_per_second": statistics.median(decode_speeds) if decode_speeds else None,
    }


def _json(status: int, payload: dict[str, object]) -> Response:
    return Response(status, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"))


def launch_action(orchestrator: RunOrchestrator, body: object) -> tuple[int, dict[str, object]]:
    """Parse a launch request body (arbitrary decoded JSON) and delegate to the run."""

    if not isinstance(body, dict):
        return 400, {"error": "request body must be a JSON object"}
    model = body.get("model")
    inferencer = body.get("inferencer")
    suites = body.get("suites")
    if (
        not isinstance(model, str)
        or not isinstance(inferencer, str)
        or not isinstance(suites, list)
    ):
        return 400, {"error": "request must include model, inferencer, and a suites list"}
    return orchestrator.launch(
        model=model,
        inferencer=inferencer,
        suites=[str(s) for s in suites],
        confirm=bool(body.get("confirm", False)),
        force=bool(body.get("force", False)),
    )


def handle_request(method: str, path: str, body: bytes, orchestrator: RunOrchestrator) -> Response:
    """Route one request to the launch endpoint; everything else is a 404."""

    route = urlsplit(path).path
    if method == "POST" and route == "/api/run":
        try:
            parsed = json.loads(body or b"{}")
        except json.JSONDecodeError:
            return _json(400, {"error": "invalid JSON body"})
        return _json(*launch_action(orchestrator, parsed))
    return _json(404, {"error": "not found"})


def make_handler(orchestrator: RunOrchestrator) -> type[BaseHTTPRequestHandler]:
    """Build a request-handler class closed over the orchestrator."""

    class _LaunchHandler(BaseHTTPRequestHandler):
        def _send(self, response: Response) -> None:
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            self.end_headers()
            self.wfile.write(response.body)

        def do_POST(self) -> None:  # noqa: N802 - http.server callback name
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            self._send(handle_request("POST", self.path, body, orchestrator))

        def do_GET(self) -> None:  # noqa: N802 - http.server callback name
            self._send(handle_request("GET", self.path, b"", orchestrator))

        def log_message(self, format: str, *args: object) -> None:  # silence default logging
            return

    return _LaunchHandler


def make_server(
    orchestrator: RunOrchestrator,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> HTTPServer:
    """Create an ``HTTPServer`` bound to localhost only."""

    return HTTPServer((host, port), make_handler(orchestrator))
