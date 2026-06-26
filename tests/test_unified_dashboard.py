from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

from local_code_bench.config import InferencerConfig
from local_code_bench.inferencers import manager
from local_code_bench.inferencers.manager import InferencerStatus
from local_code_bench.results import append_jsonl

from local_code_bench import unified_dashboard as ud


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


def _server_cfg(name: str, port: int) -> InferencerConfig:
    return InferencerConfig(
        name=name,
        lifecycle="server",
        detect_kind="binary",
        detect_target=name,
        port=port,
        health_url="http://127.0.0.1:{port}/v1/models",
        start=(name, "serve"),
    )


def _configs() -> dict[str, InferencerConfig]:
    return {
        "dflash": _server_cfg("dflash", 8000),
        "turboquant": _server_cfg("turboquant", 8002),
    }


def _status(name: str, *, running: bool = False, healthy: bool = False,
            pid: int | None = None, port: int = 8000) -> InferencerStatus:
    return InferencerStatus(
        name=name,
        installed=True,
        lifecycle="server",
        running=running,
        pid=pid,
        port=port,
        healthy=healthy,
        detail="ok",
    )


def _ctx(result_paths: list[str | Path] | None = None) -> ud.DashboardContext:
    return ud.DashboardContext(
        configs=_configs(),
        state_dir=".runtime",
        result_paths=result_paths or [],
    )


def _endpoint_record(model: str, task_id: str, *, passed: bool) -> dict[str, object]:
    return {
        "run_mode": "endpoint",
        "model": model,
        "suite": "humaneval",
        "task_id": task_id,
        "passed": passed,
        "metrics": {"latency_seconds": 1.0},
    }


# ---------------------------------------------------------------------------
# unified page: three navigable, self-contained sections
# ---------------------------------------------------------------------------


def test_get_root_serves_one_page_with_three_sections() -> None:
    resp = ud.handle_request("GET", "/", _ctx())

    assert resp.status == 200
    assert resp.content_type.startswith("text/html")
    body = resp.body.decode()
    # the three navigable sections live on one page
    assert 'data-section="inferencers"' in body
    assert 'data-section="results"' in body
    assert 'data-section="run"' in body
    assert "<table" in body


def test_page_is_self_contained_no_build_step_or_cdn() -> None:
    body = ud.render_page()
    assert "cdn" not in body.lower()
    assert "//unpkg" not in body
    assert "https://" not in body
    # navigation is client-side: inline script, no external bundle import
    assert "<script" in body
    assert "import " not in body


def test_page_leaks_no_secrets_or_host_paths() -> None:
    body = ud.render_page().lower()
    for secret in ("api_key", "apikey", "authorization", "secret", "/users/", ".env"):
        assert secret not in body


# ---------------------------------------------------------------------------
# inferencers section reuses Epic-08 actions (no duplicated business logic)
# ---------------------------------------------------------------------------


def test_api_status_delegates_to_inferencer_action(monkeypatch) -> None:
    monkeypatch.setattr(
        manager, "status_all", lambda configs, state_dir: {"dflash": _status("dflash", running=True)}
    )

    resp = ud.handle_request("GET", "/api/status", _ctx())

    assert resp.status == 200
    assert resp.content_type.startswith("application/json")
    payload = json.loads(resp.body)
    assert payload["inferencers"][0]["name"] == "dflash"


def test_api_start_passes_confirm_and_force_from_query(monkeypatch) -> None:
    seen: dict = {}

    def _fake_start(name, configs, state_dir, *, confirm, force=False):
        seen.update(name=name, confirm=confirm, force=force)
        return 200, {"started": {"name": name}}

    monkeypatch.setattr(ud.inferencer_panel, "start_action", _fake_start)

    resp = ud.handle_request("POST", "/api/start?name=dflash&confirm=1&force=1", _ctx())

    assert resp.status == 200
    assert seen == {"name": "dflash", "confirm": True, "force": True}


def test_api_start_without_flags_defaults_false(monkeypatch) -> None:
    seen: dict = {}
    monkeypatch.setattr(
        ud.inferencer_panel, "start_action",
        lambda name, configs, state_dir, *, confirm, force=False: seen.update(
            confirm=confirm, force=force
        )
        or (200, {}),
    )

    ud.handle_request("POST", "/api/start?name=dflash", _ctx())

    assert seen == {"confirm": False, "force": False}


def test_api_stop_routes_to_stop_action(monkeypatch) -> None:
    seen: dict = {}
    monkeypatch.setattr(
        ud.inferencer_panel, "stop_action",
        lambda name, configs, state_dir: seen.update(name=name) or (200, {"stopped": name}),
    )

    resp = ud.handle_request("POST", "/api/stop?name=dflash", _ctx())

    assert resp.status == 200
    assert seen["name"] == "dflash"


# ---------------------------------------------------------------------------
# results section reuses Epic-07 live aggregates (no duplicated business logic)
# ---------------------------------------------------------------------------


def test_api_data_delegates_to_results_aggregates(tmp_path: Path) -> None:
    path = tmp_path / "run.jsonl"
    append_jsonl(path, _endpoint_record("m1", "HumanEval/0", passed=True))

    resp = ud.handle_request("GET", "/api/data", _ctx([path]))

    assert resp.status == 200
    assert resp.content_type.startswith("application/json")
    payload = json.loads(resp.body)
    assert payload["endpoint_models"][0]["model"] == "m1"


def test_api_data_reflects_appended_records_without_restart(tmp_path: Path) -> None:
    path = tmp_path / "run.jsonl"
    append_jsonl(path, _endpoint_record("m1", "HumanEval/0", passed=True))
    ctx = _ctx([path])

    first = json.loads(ud.handle_request("GET", "/api/data", ctx).body)
    assert first["endpoint_models"][0]["attempts"] == 1

    append_jsonl(path, _endpoint_record("m1", "HumanEval/1", passed=False))
    second = json.loads(ud.handle_request("GET", "/api/data", ctx).body)
    assert second["endpoint_models"][0]["attempts"] == 2


# ---------------------------------------------------------------------------
# routing / safety
# ---------------------------------------------------------------------------


def test_unknown_route_is_404() -> None:
    resp = ud.handle_request("GET", "/secrets", _ctx())
    assert resp.status == 404


def test_post_to_data_route_is_404() -> None:
    resp = ud.handle_request("POST", "/api/data", _ctx())
    assert resp.status == 404


def test_make_server_binds_localhost_only() -> None:
    server = ud.make_server(_ctx(), port=0)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()


# ---------------------------------------------------------------------------
# live HTTP roundtrip: both sections' endpoints answer on one server
# ---------------------------------------------------------------------------


def test_one_server_answers_both_status_and_data_over_http(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        manager, "status_all", lambda configs, state_dir: {"dflash": _status("dflash", running=True)}
    )
    path = tmp_path / "run.jsonl"
    append_jsonl(path, _endpoint_record("m1", "HumanEval/0", passed=True))

    server = ud.make_server(_ctx([path]), port=0)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://{host}:{port}"
        with urllib.request.urlopen(f"{base}/api/status") as resp:
            assert json.loads(resp.read())["inferencers"][0]["name"] == "dflash"
        with urllib.request.urlopen(f"{base}/api/data") as resp:
            assert json.loads(resp.read())["endpoint_models"][0]["model"] == "m1"
        with urllib.request.urlopen(f"{base}/") as resp:
            assert resp.status == 200
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


def test_serve_dashboard_loads_configs_reports_progress_and_closes(monkeypatch) -> None:
    fake = _FakeServer()
    seen: dict = {}
    messages: list[str] = []

    monkeypatch.setattr(ud, "load_inferencers", lambda path: seen.setdefault("path", path) or {})

    def _make_server(ctx, *, host, port):
        seen["host"] = host
        seen["port"] = port
        seen["result_paths"] = ctx.result_paths
        return fake

    monkeypatch.setattr(ud, "make_server", _make_server)

    ud.serve_dashboard(
        "configs/inferencers.yaml",
        ".runtime",
        ["results/run.jsonl"],
        host="127.0.0.1",
        port=9999,
        progress=messages.append,
    )

    assert seen["path"] == "configs/inferencers.yaml"
    assert (seen["host"], seen["port"]) == ("127.0.0.1", 9999)
    assert seen["result_paths"] == ["results/run.jsonl"]
    assert fake.served is True
    assert fake.closed is True  # KeyboardInterrupt still runs the finally: server_close()
    assert any("9999" in msg for msg in messages)


def test_serve_dashboard_runs_without_progress_callback(monkeypatch) -> None:
    fake = _FakeServer()
    monkeypatch.setattr(ud, "load_inferencers", lambda path: {})
    monkeypatch.setattr(ud, "make_server", lambda *a, **k: fake)

    ud.serve_dashboard("configs/inferencers.yaml", ".runtime", [])

    assert fake.closed is True
