"""Tests for the live results dashboard HTTP endpoints (story 07.3-001).

These exercise the HTTP/serialization layer: routing, localhost binding, the
live re-read on every request, and data-quality warning passthrough. The
aggregation itself is owned and tested by ``dashboard_model`` (story 07.1-001).
"""

from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

from local_code_bench.dashboard_server import (
    data_action,
    dashboard_payload,
    handle_request,
    make_server,
    serve_dashboard,
)
from local_code_bench.results import append_jsonl


def _endpoint_record(
    model: str, task_id: str, *, passed: bool, **extra: object
) -> dict[str, object]:
    record: dict[str, object] = {
        "run_mode": "endpoint",
        "model": model,
        "suite": "humaneval",
        "task_id": task_id,
        "passed": passed,
        "cost_usd": 0.01,
        "metrics": {
            "latency_seconds": 1.5,
            "ttft_seconds": 0.3,
            "prefill_tokens_per_second": 200.0,
            "decode_tokens_per_second": 50.0,
        },
        "tokens": {"prompt": 100, "completion": 40},
    }
    record.update(extra)
    return record


# ---------------------------------------------------------------------------
# dashboard_payload — JSON-safe serialization of the shared aggregation model
# ---------------------------------------------------------------------------


def test_dashboard_payload_serializes_model_sections(tmp_path: Path) -> None:
    path = tmp_path / "run.jsonl"
    append_jsonl(path, _endpoint_record("m1", "HumanEval/0", passed=True))

    payload = dashboard_payload([path])

    assert set(payload) >= {"endpoint_models", "agent_runs", "sweep_points", "warnings"}
    # The payload must round-trip cleanly through JSON (no dataclasses/tuples left).
    reparsed = json.loads(json.dumps(payload))
    assert reparsed["endpoint_models"][0]["model"] == "m1"
    assert reparsed["endpoint_models"][0]["attempts"] == 1
    assert reparsed["endpoint_models"][0]["pass_rate"] == 1.0


def test_dashboard_payload_passes_through_data_quality_warnings(tmp_path: Path) -> None:
    path = tmp_path / "run.jsonl"
    append_jsonl(path, _endpoint_record("m1", "HumanEval/0", passed=True))
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not valid json}\n")  # partially written / corrupt line

    payload = dashboard_payload([path])

    # Valid records still aggregate, and the bad line is reported as a warning.
    assert payload["endpoint_models"][0]["attempts"] == 1
    assert len(payload["warnings"]) == 1
    assert payload["warnings"][0]["line"] == 2


# ---------------------------------------------------------------------------
# data_action — live re-read on every call
# ---------------------------------------------------------------------------


def test_data_action_reads_files_fresh_on_each_call(tmp_path: Path) -> None:
    path = tmp_path / "run.jsonl"
    append_jsonl(path, _endpoint_record("m1", "HumanEval/0", passed=True))

    status, first = data_action([path])
    assert status == 200
    assert first["endpoint_models"][0]["attempts"] == 1

    # A still-running benchmark appends another record.
    append_jsonl(path, _endpoint_record("m1", "HumanEval/1", passed=True))

    _, second = data_action([path])
    assert second["endpoint_models"][0]["attempts"] == 2  # reflected without restart


# ---------------------------------------------------------------------------
# handle_request routing
# ---------------------------------------------------------------------------


def test_get_root_serves_self_contained_page(tmp_path: Path) -> None:
    resp = handle_request("GET", "/", [tmp_path / "run.jsonl"])

    assert resp.status == 200
    assert resp.content_type.startswith("text/html")
    body = resp.body.decode()
    assert "<table" in body
    # self-contained: no external assets fetched from a CDN
    assert "cdn" not in body.lower()
    assert "https://" not in body
    assert "//unpkg" not in body


def test_get_api_data_returns_json(tmp_path: Path) -> None:
    path = tmp_path / "run.jsonl"
    append_jsonl(path, _endpoint_record("m1", "HumanEval/0", passed=True))

    resp = handle_request("GET", "/api/data", [path])

    assert resp.status == 200
    assert resp.content_type.startswith("application/json")
    payload = json.loads(resp.body)
    assert payload["endpoint_models"][0]["model"] == "m1"


def test_unknown_route_is_404(tmp_path: Path) -> None:
    resp = handle_request("GET", "/secrets", [tmp_path / "run.jsonl"])

    assert resp.status == 404


def test_post_to_data_endpoint_is_404(tmp_path: Path) -> None:
    # The live dashboard is read-only: it serves assets and result-derived JSON only.
    resp = handle_request("POST", "/api/data", [tmp_path / "run.jsonl"])

    assert resp.status == 404


# ---------------------------------------------------------------------------
# server binding + live HTTP roundtrip
# ---------------------------------------------------------------------------


def test_make_server_binds_localhost_only(tmp_path: Path) -> None:
    server = make_server([tmp_path / "run.jsonl"], port=0)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()


def test_handler_reflects_appended_records_over_http(tmp_path: Path) -> None:
    path = tmp_path / "run.jsonl"
    append_jsonl(path, _endpoint_record("m1", "HumanEval/0", passed=True))

    server = make_server([path], port=0)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://{host}:{port}"
        with urllib.request.urlopen(f"{base}/api/data") as resp:
            assert resp.status == 200
            first = json.loads(resp.read())
        assert first["endpoint_models"][0]["attempts"] == 1

        # File grows while the server is running.
        append_jsonl(path, _endpoint_record("m1", "HumanEval/1", passed=False))

        with urllib.request.urlopen(f"{base}/api/data") as resp:
            second = json.loads(resp.read())
        assert second["endpoint_models"][0]["attempts"] == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# ---------------------------------------------------------------------------
# serve_dashboard lifecycle
# ---------------------------------------------------------------------------


class _FakeServer:
    def __init__(self) -> None:
        self.served = False
        self.closed = False

    def serve_forever(self) -> None:
        self.served = True
        raise KeyboardInterrupt

    def server_close(self) -> None:
        self.closed = True


def test_serve_dashboard_reports_progress_and_closes(monkeypatch, tmp_path: Path) -> None:
    fake = _FakeServer()
    seen: dict[str, object] = {}
    messages: list[str] = []

    def _make_server(paths, *, host, port):
        seen["host"] = host
        seen["port"] = port
        return fake

    monkeypatch.setattr("local_code_bench.dashboard_server.make_server", _make_server)

    serve_dashboard([tmp_path / "run.jsonl"], host="127.0.0.1", port=9999, progress=messages.append)

    assert (seen["host"], seen["port"]) == ("127.0.0.1", 9999)
    assert fake.served is True
    assert fake.closed is True  # KeyboardInterrupt still runs finally: server_close()
    assert any("9999" in msg for msg in messages)


def test_serve_dashboard_runs_without_progress_callback(monkeypatch, tmp_path: Path) -> None:
    fake = _FakeServer()
    monkeypatch.setattr("local_code_bench.dashboard_server.make_server", lambda *a, **k: fake)

    serve_dashboard([tmp_path / "run.jsonl"])

    assert fake.closed is True
