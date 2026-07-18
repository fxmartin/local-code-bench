"""Story 17.3-002: Chrome detection, headless PDF render, and one-at-a-time worker."""

from __future__ import annotations

import stat
import threading
from pathlib import Path

import pytest

from local_code_bench import pdf_export


# ---------------------------------------------------------------------------
# renderer detection: detect-only, Epic-08 pattern (binary lookup + .app dirs)
# ---------------------------------------------------------------------------


CANDIDATES = (
    "google-chrome",
    "chromium",
    "Google Chrome.app/Contents/MacOS/Google Chrome",
    "Chromium.app/Contents/MacOS/Chromium",
)


def test_detect_renderer_finds_binary_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pdf_export.shutil,
        "which",
        lambda name: "/usr/local/bin/chromium" if name == "chromium" else None,
    )
    monkeypatch.setattr(pdf_export, "_app_dirs", lambda: [])

    found = pdf_export.detect_renderer(CANDIDATES)

    assert found is not None
    assert found.candidate == "chromium"
    assert found.path == "/usr/local/bin/chromium"


def test_detect_renderer_finds_macos_app_bundle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = tmp_path / "Google Chrome.app/Contents/MacOS/Google Chrome"
    bundle.parent.mkdir(parents=True)
    bundle.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(pdf_export.shutil, "which", lambda name: None)
    monkeypatch.setattr(pdf_export.sys, "platform", "darwin")
    monkeypatch.setattr(pdf_export, "_app_dirs", lambda: [tmp_path])

    found = pdf_export.detect_renderer(CANDIDATES)

    assert found is not None
    assert found.candidate == "Google Chrome.app/Contents/MacOS/Google Chrome"
    assert found.path == str(bundle)


def test_detect_renderer_skips_app_bundles_off_darwin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = tmp_path / "Google Chrome.app/Contents/MacOS/Google Chrome"
    bundle.parent.mkdir(parents=True)
    bundle.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(pdf_export.shutil, "which", lambda name: None)
    monkeypatch.setattr(pdf_export.sys, "platform", "linux")
    monkeypatch.setattr(pdf_export, "_app_dirs", lambda: [tmp_path])

    assert pdf_export.detect_renderer(CANDIDATES) is None


def test_detect_renderer_none_when_nothing_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pdf_export.shutil, "which", lambda name: None)
    monkeypatch.setattr(pdf_export, "_app_dirs", lambda: [])

    assert pdf_export.detect_renderer(CANDIDATES) is None


def test_detect_renderer_respects_candidate_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pdf_export.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(pdf_export, "_app_dirs", lambda: [])

    found = pdf_export.detect_renderer(CANDIDATES)

    assert found is not None
    assert found.candidate == "google-chrome"


# ---------------------------------------------------------------------------
# render_pdf: subprocess with timeout; failure surfaces the stderr tail
# ---------------------------------------------------------------------------


def _fake_chrome(tmp_path: Path, script_body: str) -> str:
    script = tmp_path / "fake-chrome"
    script.write_text("#!/bin/sh\n" + script_body, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script)


_WRITE_PDF = """\
for arg in "$@"; do
  case "$arg" in
    --print-to-pdf=*) printf '%%PDF-1.7 fake' > "${arg#--print-to-pdf=}" ;;
  esac
done
"""


def test_render_pdf_writes_destination_and_passes_url(tmp_path: Path) -> None:
    binary = _fake_chrome(tmp_path, _WRITE_PDF + 'echo "$@" > "$(dirname "$0")/args"\n')
    destination = tmp_path / "reports" / "engine-2026-07-18.pdf"

    result = pdf_export.render_pdf(
        binary,
        "http://127.0.0.1:9999/?print=engine",
        destination,
        timeout_seconds=10.0,
    )

    assert result == destination
    assert destination.read_bytes().startswith(b"%PDF")
    args = (tmp_path / "args").read_text(encoding="utf-8")
    assert "--headless" in args
    assert "http://127.0.0.1:9999/?print=engine" in args
    # Chrome's own default header/footer stays off: the report's @page margin
    # boxes (story 17.3-001) carry the running header/footer instead.
    assert "--no-pdf-header-footer" in args


def test_render_pdf_failure_surfaces_stderr_tail(tmp_path: Path) -> None:
    binary = _fake_chrome(tmp_path, 'echo "chrome exploded: no usable GPU" >&2\nexit 3\n')

    with pytest.raises(pdf_export.PdfRenderError) as excinfo:
        pdf_export.render_pdf(
            binary, "http://127.0.0.1:9999/", tmp_path / "out.pdf", timeout_seconds=10.0
        )

    assert "exit code 3" in str(excinfo.value)
    assert "chrome exploded: no usable GPU" in str(excinfo.value)


def test_render_pdf_empty_output_is_an_error(tmp_path: Path) -> None:
    binary = _fake_chrome(tmp_path, "exit 0\n")

    with pytest.raises(pdf_export.PdfRenderError, match="no output"):
        pdf_export.render_pdf(
            binary, "http://127.0.0.1:9999/", tmp_path / "out.pdf", timeout_seconds=10.0
        )


def test_render_pdf_timeout_is_an_error(tmp_path: Path) -> None:
    binary = _fake_chrome(tmp_path, "sleep 5\n")

    with pytest.raises(pdf_export.PdfRenderError, match="timed out"):
        pdf_export.render_pdf(
            binary, "http://127.0.0.1:9999/", tmp_path / "out.pdf", timeout_seconds=0.2
        )


def test_render_pdf_leaves_no_staging_file_on_failure(tmp_path: Path) -> None:
    binary = _fake_chrome(tmp_path, "exit 1\n")
    destination = tmp_path / "reports" / "out.pdf"

    with pytest.raises(pdf_export.PdfRenderError):
        pdf_export.render_pdf(binary, "http://127.0.0.1:9999/", destination, timeout_seconds=10.0)

    assert not destination.exists()
    assert list(destination.parent.glob("*.partial")) == []


# ---------------------------------------------------------------------------
# PdfWorker: one render at a time, same convention as tier moves (12.6-003)
# ---------------------------------------------------------------------------


def test_worker_runs_job_and_reports_done(tmp_path: Path) -> None:
    worker = pdf_export.PdfWorker()
    destination = tmp_path / "engine-2026-07-18.pdf"

    def run() -> Path:
        destination.write_bytes(b"%PDF fake")
        return destination

    assert worker.start(axis_id="engine", filename=destination.name, run=run) is True
    worker.wait()

    status = worker.status()
    assert status is not None
    assert status["state"] == "done"
    assert status["axis"] == "engine"
    assert status["filename"] == destination.name
    assert worker.finished_file() == (destination.name, destination)


def test_worker_refuses_second_start_while_running(tmp_path: Path) -> None:
    worker = pdf_export.PdfWorker()
    release = threading.Event()

    def run() -> Path:
        release.wait(timeout=5.0)
        return tmp_path / "a.pdf"

    assert worker.start(axis_id="engine", filename="a.pdf", run=run) is True
    assert worker.busy is True
    assert worker.start(axis_id="engine", filename="b.pdf", run=run) is False
    release.set()
    worker.wait()
    assert worker.busy is False


def test_worker_captures_render_error() -> None:
    worker = pdf_export.PdfWorker()

    def run() -> Path:
        raise pdf_export.PdfRenderError("renderer exit code 3; stderr: boom")

    worker.start(axis_id="engine", filename="a.pdf", run=run)
    worker.wait()

    status = worker.status()
    assert status is not None
    assert status["state"] == "error"
    assert "boom" in status["error"]
    assert worker.finished_file() is None


def test_worker_never_sticks_on_unexpected_exception() -> None:
    worker = pdf_export.PdfWorker()

    def run() -> Path:
        raise RuntimeError("surprise")

    worker.start(axis_id="engine", filename="a.pdf", run=run)
    worker.wait()

    status = worker.status()
    assert status is not None
    assert status["state"] == "error"
    assert "surprise" in status["error"]
    assert worker.busy is False


def test_worker_status_none_before_any_job() -> None:
    assert pdf_export.PdfWorker().status() is None
