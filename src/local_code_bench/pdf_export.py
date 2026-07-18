"""One-click PDF export of the Benchmarks report via a detected Chrome (story 17.3-002).

Three pieces, all renderer-side only — the dashboard endpoints in
:mod:`local_code_bench.unified_dashboard` are thin seams over them:

- :func:`detect_renderer` finds an installed Chrome/Chromium following the
  Epic-08 detect pattern (binary via ``shutil.which``, macOS ``.app`` bundles
  via the standard Application directories). Detect-only: the harness never
  installs a browser, and the candidate list is configurable in
  ``configs/settings.yaml`` (``pdf.renderer_candidates``) per nothing-hardcoded.
- :func:`render_pdf` runs one ``--headless --print-to-pdf`` subprocess with a
  timeout against the dashboard's own localhost URL, staging the output and
  publishing it atomically; any failure raises :class:`PdfRenderError` carrying
  the stderr tail so the button can surface why.
- :class:`PdfWorker` runs one render at a time in a background thread — the
  same one-at-a-time convention as tier moves (story 12.6-003), and a hard
  requirement here: the single-threaded dashboard server must stay free to
  serve the page Chrome is printing, so the render can never run on the
  request thread.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

#: Virtual-time budget handed to Chrome so the report's async fetches and
#: renders complete before printing (ms of virtual page time, not wall time).
VIRTUAL_TIME_BUDGET_MS = 10_000

#: How many trailing stderr characters a render failure carries.
_STDERR_TAIL_CHARS = 400


class PdfRenderError(RuntimeError):
    """A headless render failed: bad exit, no output, or timeout."""


@dataclass(frozen=True)
class DetectedRenderer:
    """One detected Chrome/Chromium: the configured candidate and its resolved path."""

    candidate: str
    path: str


def _app_dirs() -> list[Path]:
    """macOS bundle search roots; module-level so tests can monkeypatch it."""

    return [Path("/Applications"), Path.home() / "Applications"]


def detect_renderer(candidates: Sequence[str]) -> DetectedRenderer | None:
    """First installed renderer from ``candidates``, or ``None``.

    Bare names resolve on ``PATH`` via ``shutil.which``; entries containing a
    slash are ``.app``-relative paths probed under the Application directories
    (Darwin only, mirroring the Epic-08 app detect kind). Read-only — a miss
    never installs anything.
    """

    for candidate in candidates:
        if "/" in candidate:
            if sys.platform != "darwin":
                continue
            for directory in _app_dirs():
                bundle = directory / candidate
                if bundle.exists():
                    return DetectedRenderer(candidate=candidate, path=str(bundle))
        else:
            resolved = shutil.which(candidate)
            if resolved is not None:
                return DetectedRenderer(candidate=candidate, path=resolved)
    return None


def render_pdf(
    binary: str, url: str, destination: Path, *, timeout_seconds: float
) -> Path:
    """Render ``url`` to ``destination`` with one headless-Chrome subprocess.

    Writes to a staging file and publishes with an atomic rename, so a killed or
    failed render never leaves a partial PDF at the archive path. Chrome's own
    header/footer stays disabled — the report's ``@page`` margin boxes (story
    17.3-001) already carry the running header/footer.
    """

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.parent / (destination.name + ".partial")
    command = [
        binary,
        "--headless",
        "--disable-gpu",
        "--hide-scrollbars",
        "--no-pdf-header-footer",
        f"--virtual-time-budget={VIRTUAL_TIME_BUDGET_MS}",
        f"--print-to-pdf={staging}",
        url,
    ]
    try:
        completed = subprocess.run(command, capture_output=True, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        staging.unlink(missing_ok=True)
        raise PdfRenderError(
            f"renderer timed out after {timeout_seconds:g}s{_stderr_tail(exc.stderr)}"
        ) from exc
    if completed.returncode != 0:
        staging.unlink(missing_ok=True)
        raise PdfRenderError(
            f"renderer exit code {completed.returncode}{_stderr_tail(completed.stderr)}"
        )
    if not staging.exists() or staging.stat().st_size == 0:
        staging.unlink(missing_ok=True)
        raise PdfRenderError(f"renderer produced no output{_stderr_tail(completed.stderr)}")
    staging.replace(destination)
    return destination


def _stderr_tail(stderr: bytes | None) -> str:
    text = (stderr or b"").decode("utf-8", errors="replace").strip()
    if not text:
        return ""
    return "; stderr: " + text[-_STDERR_TAIL_CHARS:]


class PdfWorker:
    """Runs one PDF render at a time in a background thread.

    Same shape as the tier-move worker (story 12.6-003): ``start`` launches the
    render in a daemon thread and returns immediately, ``status`` reports the
    one current/last job, and a second ``start`` while a render is running is
    refused. Off-thread execution is load-bearing — the single-threaded
    dashboard server must answer Chrome's page fetch while the render runs.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._job: dict | None = None
        #: The dashboard's own bound address (``http://127.0.0.1:<port>``), set
        #: by ``make_server`` once the listening socket exists; the render URL
        #: always targets it, never a caller-supplied host.
        self.base_url: str | None = None

    @property
    def busy(self) -> bool:
        """True while a render is running (a second Download PDF must wait)."""

        with self._lock:
            return self._job is not None and self._job["state"] == "running"

    def start(self, *, axis_id: str, filename: str, run: Callable[[], Path]) -> bool:
        """Launch ``run`` in the background; False when a render is already running."""

        with self._lock:
            if self._job is not None and self._job["state"] == "running":
                return False
            self._job = {
                "axis": axis_id,
                "filename": filename,
                "state": "running",
                "error": None,
                "destination": None,
                "started": time.monotonic(),
                "finished": None,
            }
            self._thread = threading.Thread(target=self._run, args=(run,), daemon=True)
            self._thread.start()
        return True

    def _run(self, run: Callable[[], Path]) -> None:
        try:
            destination = run()
        except PdfRenderError as exc:
            self._finish(state="error", error=str(exc))
            return
        except Exception as exc:  # never leave a job stuck "running"
            self._finish(state="error", error=f"render failed unexpectedly: {exc}")
            return
        self._finish(state="done", destination=destination)

    def _finish(
        self, *, state: str, error: str | None = None, destination: Path | None = None
    ) -> None:
        with self._lock:
            if self._job is None:  # pragma: no cover - start() always sets it
                return
            self._job["state"] = state
            self._job["error"] = error
            self._job["destination"] = destination
            self._job["finished"] = time.monotonic()

    def status(self) -> dict | None:
        """The current/last job as a client payload, or None before any render.

        Identity fields only — the archive path itself never reaches the
        browser (:func:`finished_file` serves the download server-side).
        """

        with self._lock:
            if self._job is None:
                return None
            job = dict(self._job)
        end = job["finished"] if job["finished"] is not None else time.monotonic()
        return {
            "axis": job["axis"],
            "filename": job["filename"],
            "state": job["state"],
            "error": job["error"],
            "elapsed_seconds": round(end - job["started"], 1),
        }

    def finished_file(self) -> tuple[str, Path] | None:
        """``(filename, archived path)`` of the last completed render, else None."""

        with self._lock:
            if self._job is None or self._job["state"] != "done":
                return None
            return self._job["filename"], self._job["destination"]

    def wait(self, timeout: float | None = 30.0) -> None:
        """Block until the current render thread exits (tests and shutdown hooks)."""

        thread = self._thread
        if thread is not None:
            thread.join(timeout)
