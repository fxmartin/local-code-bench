from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

import pytest

from local_code_bench.config import InferencerConfig, ModelConfig, TokenPrices
from local_code_bench.inferencers import manager
from local_code_bench.inferencers.manager import InferencerStatus
from local_code_bench.metrics import StreamEvent
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


def _model_cfg(name: str, *, inferencer: str | None = None) -> ModelConfig:
    return ModelConfig(
        name=name,
        type="openai",
        base_url="http://127.0.0.1:8000/v1",
        model_id=f"{name}-id",
        pinned_revision="manual",
        price_per_1k_tokens=TokenPrices(input=0.0, output=0.0),
        api_key_env="SECRET_KEY_ENV",
        inferencer=inferencer,
    )


def _models() -> dict[str, ModelConfig]:
    return {
        "local-coder": _model_cfg("local-coder", inferencer="dflash"),
        "cloud-coder": _model_cfg("cloud-coder", inferencer=None),
    }


class _FakeOrchestrator:
    """Stand-in for the launch orchestrator capturing the delegated launch call."""

    def __init__(self, result: tuple[int, dict[str, object]]) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def launch(self, **kwargs: object) -> tuple[int, dict[str, object]]:
        self.calls.append(kwargs)
        return self.result


def _status(
    name: str,
    *,
    running: bool = False,
    healthy: bool = False,
    pid: int | None = None,
    port: int = 8000,
) -> InferencerStatus:
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


def _model(name: str = "qwen") -> ModelConfig:
    return ModelConfig(
        name=name,
        type="openai",
        base_url="http://127.0.0.1:8000/v1",
        model_id=f"{name}-id",
        pinned_revision="main",
        price_per_1k_tokens=TokenPrices(input=0.0, output=0.0),
        inferencer="dflash",
    )


def _ctx(
    result_paths: list[str | Path] | None = None,
    *,
    models: dict[str, ModelConfig] | None = None,
    orchestrator: object | None = None,
    cache_dir: str | Path = ".cache/does-not-exist",
    suites_path: str | Path = "configs/does-not-exist.yaml",
) -> ud.DashboardContext:
    return ud.DashboardContext(
        configs=_configs(),
        state_dir=".runtime",
        result_paths=result_paths or [],
        models=models if models is not None else _models(),
        orchestrator=orchestrator,
        cache_dir=cache_dir,
        suites_path=suites_path,
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
        manager,
        "status_all",
        lambda configs, state_dir: {"dflash": _status("dflash", running=True)},
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
        ud.inferencer_panel,
        "start_action",
        lambda name, configs, state_dir, *, confirm, force=False: (
            seen.update(confirm=confirm, force=force) or (200, {})
        ),
    )

    ud.handle_request("POST", "/api/start?name=dflash", _ctx())

    assert seen == {"confirm": False, "force": False}


def test_api_stop_routes_to_stop_action(monkeypatch) -> None:
    seen: dict = {}
    monkeypatch.setattr(
        ud.inferencer_panel,
        "stop_action",
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


# ---------------------------------------------------------------------------
# chat section: POST /api/chat streams SSE through the existing provider
# ---------------------------------------------------------------------------


class _FakeProvider:
    def __init__(self, model, *, events) -> None:
        self.model = model
        self._events = events

    def stream_chat(self, request):
        yield from self._events


def _chat_body(model: str = "qwen") -> bytes:
    return json.dumps({"model": model, "messages": [{"role": "user", "content": "hi"}]}).encode(
        "utf-8"
    )


def test_api_chat_streams_sse_through_provider(monkeypatch) -> None:
    provider = _FakeProvider(
        _model(),
        events=[StreamEvent(content="Hi"), StreamEvent(prompt_tokens=3, completion_tokens=1)],
    )
    monkeypatch.setattr(ud.chat, "provider_for_model", lambda model: provider)

    resp = ud.handle_request("POST", "/api/chat", _ctx(models={"qwen": _model()}), _chat_body())

    assert isinstance(resp, ud.chat.ChatStreamResponse)
    assert resp.status == 200
    chunks = list(resp.events)
    assert chunks[0] == 'data: {"delta": "Hi"}\n\n'
    assert json.loads(chunks[-1][len("data: ") : -2])["done"] is True


def test_api_chat_unknown_model_is_400() -> None:
    resp = ud.handle_request("POST", "/api/chat", _ctx(), _chat_body("ghost"))

    assert isinstance(resp, ud.Response)
    assert resp.status == 400


def test_api_chat_invalid_json_body_is_400() -> None:
    resp = ud.handle_request("POST", "/api/chat", _ctx(), b"{not json")

    assert isinstance(resp, ud.Response)
    assert resp.status == 400


def test_api_chat_streams_over_http_and_cancels_cleanly(monkeypatch) -> None:
    provider = _FakeProvider(
        _model(),
        events=[
            StreamEvent(content="Hel"),
            StreamEvent(content="lo"),
            StreamEvent(prompt_tokens=3, completion_tokens=2),
        ],
    )
    monkeypatch.setattr(ud.chat, "provider_for_model", lambda model: provider)

    server = ud.make_server(_ctx(models={"qwen": _model()}), port=0)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://{host}:{port}/api/chat",
            data=_chat_body(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request) as resp:
            assert resp.headers["Content-Type"].startswith("text/event-stream")
            # read the first streamed token, then drop the connection (the "stop" path)
            first = resp.readline()
            assert first == b'data: {"delta": "Hel"}\n'
            assert resp.readline() == b"\n"  # SSE event terminator
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class _BrokenWriter:
    """A response writer that fails as if the client closed the connection."""

    def write(self, data: bytes) -> int:
        raise BrokenPipeError("client gone")

    def flush(self) -> None:  # pragma: no cover - never reached after write() raises
        pass


def _stub_stream_handler():
    handler = ud.make_handler(_ctx()).__new__(ud.make_handler(_ctx()))
    handler.wfile = _BrokenWriter()
    handler.send_response = lambda *a, **k: None
    handler.send_header = lambda *a, **k: None
    handler.end_headers = lambda: None
    return handler


def test_stream_cancels_provider_when_client_disconnects() -> None:
    # AC: a "stop"/closed tab mid-stream cancels the upstream provider connection
    closed = {"called": False}

    class _Events:
        def __iter__(self):
            return iter(["data: a\n\n", "data: b\n\n"])

        def close(self) -> None:
            closed["called"] = True

    handler = _stub_stream_handler()
    handler._stream(ud.chat.ChatStreamResponse(200, _Events()))

    assert closed["called"] is True  # the disconnect released the provider stream


def test_stream_disconnect_is_quiet_when_events_have_no_close() -> None:
    # A plain iterator has nothing to cancel; the disconnect must still be swallowed
    handler = _stub_stream_handler()

    handler._stream(ud.chat.ChatStreamResponse(200, iter(["data: a\n\n"])))


def test_load_models_safe_degrades_silently_without_progress(monkeypatch) -> None:
    from local_code_bench.config import ConfigError

    def _missing(path):
        raise ConfigError("model config not found")

    monkeypatch.setattr(ud, "load_models", _missing)

    # no progress callback: chat is disabled silently rather than crashing the dashboard
    assert ud._load_models_safe("configs/models.yaml", None) == {}


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
        manager,
        "status_all",
        lambda configs, state_dir: {"dflash": _status("dflash", running=True)},
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


def test_one_server_routes_a_real_post_through_do_post(monkeypatch, tmp_path: Path) -> None:
    # exercises the handler's do_POST dispatch over the wire, not just handle_request
    monkeypatch.setattr(
        ud.inferencer_panel,
        "stop_action",
        lambda name, configs, state_dir: (200, {"stopped": name}),
    )

    server = ud.make_server(_ctx(), port=0)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://{host}:{port}/api/stop?name=dflash", data=b"", method="POST"
        )
        with urllib.request.urlopen(request) as resp:
            assert resp.status == 200
            assert json.loads(resp.read())["stopped"] == "dflash"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# ---------------------------------------------------------------------------
# run section: catalog read endpoint (model + inferencer + suite catalogs)
# ---------------------------------------------------------------------------


def test_api_catalog_returns_models_inferencers_and_suites() -> None:
    resp = ud.handle_request("GET", "/api/catalog", _ctx())

    assert resp.status == 200
    assert resp.content_type.startswith("application/json")
    payload = json.loads(resp.body)
    model_names = {m["name"] for m in payload["models"]}
    assert model_names == {"local-coder", "cloud-coder"}
    inferencer_names = {i["name"] for i in payload["inferencers"]}
    assert inferencer_names == {"dflash", "turboquant"}
    # suites come from the 09.5-001 availability-aware catalog
    suite_ids = {s["id"] for s in payload["suites"]}
    assert {"humaneval", "mbpp", "canary"} <= suite_ids


def test_api_catalog_exposes_each_models_declared_inferencer() -> None:
    # the form warns when the chosen inferencer differs from the model's declared one
    payload = json.loads(ud.handle_request("GET", "/api/catalog", _ctx()).body)
    by_name = {m["name"]: m for m in payload["models"]}
    assert by_name["local-coder"]["inferencer"] == "dflash"
    assert by_name["cloud-coder"]["inferencer"] is None


def test_api_catalog_leaks_no_secrets() -> None:
    body = ud.handle_request("GET", "/api/catalog", _ctx()).body.decode().lower()
    for secret in ("api_key", "secret_key_env", "/users/", ".env", "base_url"):
        assert secret not in body


def test_post_to_catalog_route_is_404() -> None:
    assert ud.handle_request("POST", "/api/catalog", _ctx()).status == 404


# ---------------------------------------------------------------------------
# run section: launch is a thin client over the 09.3-001 launch endpoint
# ---------------------------------------------------------------------------


def test_api_run_delegates_to_launch_endpoint() -> None:
    orch = _FakeOrchestrator((202, {"run_id": "abc123", "status": "running"}))
    body = json.dumps(
        {"model": "local-coder", "inferencer": "dflash", "suites": ["humaneval", "canary"]}
    ).encode()

    resp = ud.handle_request("POST", "/api/run", _ctx(orchestrator=orch), body)

    assert resp.status == 202
    assert json.loads(resp.body)["run_id"] == "abc123"
    assert orch.calls == [
        {
            "model": "local-coder",
            "inferencer": "dflash",
            "suites": ["humaneval", "canary"],
            "confirm": False,
            "force": False,
        }
    ]


def test_api_run_rejects_invalid_json_body() -> None:
    orch = _FakeOrchestrator((202, {}))
    resp = ud.handle_request("POST", "/api/run", _ctx(orchestrator=orch), b"{not json")
    assert resp.status == 400
    assert orch.calls == []


def test_api_run_without_orchestrator_is_unavailable() -> None:
    resp = ud.handle_request("POST", "/api/run", _ctx(orchestrator=None), b"{}")
    assert resp.status == 503


def test_run_section_page_has_launcher_form() -> None:
    body = ud.render_page()
    assert 'id="run-model"' in body
    assert 'id="run-inferencer"' in body
    assert 'id="run-suites"' in body
    assert 'id="run-launch"' in body
    # the page is a thin client over the catalog + launch endpoints
    assert "/api/catalog" in body
    assert "/api/run" in body


def test_run_launch_routes_a_real_post_through_do_post() -> None:
    orch = _FakeOrchestrator((202, {"run_id": "wire1", "status": "running"}))
    server = ud.make_server(_ctx(orchestrator=orch), port=0)
    host, port = server.server_address[0], server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        payload = json.dumps(
            {"model": "local-coder", "inferencer": "dflash", "suites": ["canary"]}
        ).encode()
        request = urllib.request.Request(
            f"http://{host}:{port}/api/run", data=payload, method="POST"
        )
        with urllib.request.urlopen(request) as resp:
            assert resp.status == 202
            assert json.loads(resp.read())["run_id"] == "wire1"
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
    monkeypatch.setattr(ud, "load_models", lambda path: seen.setdefault("models_path", path) or {})

    def _make_server(ctx, *, host, port):
        seen["host"] = host
        seen["port"] = port
        seen["result_paths"] = ctx.result_paths
        seen["has_orchestrator"] = ctx.orchestrator is not None
        seen["models"] = ctx.models
        return fake

    monkeypatch.setattr(ud, "make_server", _make_server)

    ud.serve_dashboard(
        "configs/inferencers.yaml",
        ".runtime",
        ["results/run.jsonl"],
        models_path="configs/models.yaml",
        host="127.0.0.1",
        port=9999,
        progress=messages.append,
    )

    assert seen["path"] == "configs/inferencers.yaml"
    assert seen["models_path"] == "configs/models.yaml"
    assert (seen["host"], seen["port"]) == ("127.0.0.1", 9999)
    assert seen["result_paths"] == ["results/run.jsonl"]
    assert seen["has_orchestrator"] is True
    assert fake.served is True
    assert fake.closed is True  # KeyboardInterrupt still runs the finally: server_close()
    assert any("9999" in msg for msg in messages)


def test_serve_dashboard_runs_without_progress_callback(monkeypatch) -> None:
    fake = _FakeServer()
    monkeypatch.setattr(ud, "load_inferencers", lambda path: {})
    monkeypatch.setattr(ud, "load_models", lambda path: {})
    monkeypatch.setattr(ud, "make_server", lambda *a, **k: fake)

    ud.serve_dashboard("configs/inferencers.yaml", ".runtime", [])

    assert fake.closed is True


def test_serve_dashboard_degrades_when_models_config_is_missing(monkeypatch) -> None:
    from local_code_bench.config import ConfigError

    fake = _FakeServer()
    seen: dict = {}
    messages: list[str] = []

    monkeypatch.setattr(ud, "load_inferencers", lambda path: {})

    def _missing_models(path):
        raise ConfigError("model config not found: configs/models.yaml")

    monkeypatch.setattr(ud, "load_models", _missing_models)
    monkeypatch.setattr(
        ud, "make_server", lambda ctx, **k: seen.setdefault("models", ctx.models) or fake
    )

    ud.serve_dashboard("configs/inferencers.yaml", ".runtime", [], progress=messages.append)

    assert seen["models"] == {}  # chat disabled, dashboard still serves
    assert any("chat disabled" in msg for msg in messages)


# ---------------------------------------------------------------------------
# Run section: live run progress + auto-refreshed results (story 09.4-001)
# ---------------------------------------------------------------------------


from local_code_bench import launch  # noqa: E402


def _orchestrator(tmp_path) -> launch.RunOrchestrator:
    return launch.RunOrchestrator(
        models={},
        inferencers=_configs(),
        state_dir=".runtime",
        results_dir=str(tmp_path / "results"),
    )


def _ctx_with_orchestrator(orch, *, results_dir=None, result_paths=None) -> ud.DashboardContext:
    return ud.DashboardContext(
        configs=_configs(),
        state_dir=".runtime",
        result_paths=result_paths or [],
        orchestrator=orch,
        results_dir=results_dir,
    )


def _inject_run(orch, **kwargs) -> launch.RunState:
    defaults = dict(
        id="r1", model="qwen", inferencer="dflash", suites=["humaneval"], result_file="run.jsonl"
    )
    defaults.update(kwargs)
    state = launch.RunState(**defaults)
    orch._runs[state.id] = state
    return state


def test_api_runs_lists_live_progress(tmp_path: Path) -> None:
    orch = _orchestrator(tmp_path)
    _inject_run(orch, total=5, completed=2, passed=2, failed=0, last_event="[2/5] qwen t1: passed")

    resp = ud.handle_request("GET", "/api/runs", _ctx_with_orchestrator(orch))

    assert resp.status == 200
    payload = json.loads(resp.body)
    run = payload["runs"][0]
    assert run["run_id"] == "r1"
    assert run["passed"] == 2
    assert run["remaining"] == 3
    assert run["status"] == "running"


def test_api_runs_without_orchestrator_is_empty() -> None:
    resp = ud.handle_request("GET", "/api/runs", _ctx())
    assert resp.status == 200
    assert json.loads(resp.body) == {"runs": []}


def test_api_run_by_id_returns_progress(tmp_path: Path) -> None:
    orch = _orchestrator(tmp_path)
    _inject_run(orch, id="abc123", total=3, completed=3, passed=3, status="completed")

    resp = ud.handle_request("GET", "/api/run/abc123", _ctx_with_orchestrator(orch))

    assert resp.status == 200
    payload = json.loads(resp.body)
    assert payload["run_id"] == "abc123"
    assert payload["status"] == "completed"
    assert payload["remaining"] == 0


def test_api_run_by_id_unknown_is_404(tmp_path: Path) -> None:
    orch = _orchestrator(tmp_path)
    resp = ud.handle_request("GET", "/api/run/nope", _ctx_with_orchestrator(orch))
    assert resp.status == 404


def test_api_run_surfaces_failure_reason(tmp_path: Path) -> None:
    orch = _orchestrator(tmp_path)
    _inject_run(orch, id="boom", status="failed", error="inferencer did not become healthy")

    resp = ud.handle_request("GET", "/api/run/boom", _ctx_with_orchestrator(orch))

    payload = json.loads(resp.body)
    assert payload["status"] == "failed"
    assert "did not become healthy" in payload["error"]


def test_post_api_run_delegates_to_launch_action(tmp_path: Path, monkeypatch) -> None:
    orch = _orchestrator(tmp_path)
    seen: dict = {}

    def _fake_launch_action(orchestrator, body):
        seen["orchestrator"] = orchestrator
        seen["body"] = body
        return 202, {"run_id": "new", "status": "running"}

    monkeypatch.setattr(ud.launch, "launch_action", _fake_launch_action)

    body = json.dumps({"model": "qwen", "inferencer": "dflash", "suites": ["humaneval"]}).encode()
    resp = ud.handle_request("POST", "/api/run", _ctx_with_orchestrator(orch), body)

    assert resp.status == 202
    assert seen["orchestrator"] is orch
    assert seen["body"]["model"] == "qwen"
    assert json.loads(resp.body)["run_id"] == "new"


def test_post_api_run_invalid_json_is_400(tmp_path: Path) -> None:
    orch = _orchestrator(tmp_path)
    resp = ud.handle_request("POST", "/api/run", _ctx_with_orchestrator(orch), b"{not json")
    assert resp.status == 400


def test_post_api_run_without_orchestrator_is_503() -> None:
    resp = ud.handle_request("POST", "/api/run", _ctx(), b"{}")
    assert resp.status == 503


def test_api_data_picks_up_new_run_file_from_results_dir(tmp_path: Path) -> None:
    # AC2: a freshly launched run's JSONL appears in Results without a restart, even
    # when it was not in the explicit --input list.
    results_dir = tmp_path / "results"
    new_file = results_dir / "run-new.jsonl"
    append_jsonl(new_file, _endpoint_record("m9", "HumanEval/0", passed=True))

    ctx = ud.DashboardContext(
        configs=_configs(), state_dir=".runtime", result_paths=[], results_dir=results_dir
    )
    payload = json.loads(ud.handle_request("GET", "/api/data", ctx).body)

    assert payload["endpoint_models"][0]["model"] == "m9"


def test_api_run_by_id_without_orchestrator_is_404() -> None:
    # the /api/run/<id> route falls through to 404 when no orchestrator is wired.
    resp = ud.handle_request("GET", "/api/run/anything", _ctx())
    assert resp.status == 404
    assert json.loads(resp.body)["error"] == "unknown run"


def test_resolve_result_paths_ignores_missing_results_dir(tmp_path: Path) -> None:
    # results_dir set but not an existing directory -> only explicit paths are used.
    explicit = tmp_path / "explicit.jsonl"
    ctx = ud.DashboardContext(
        configs=_configs(),
        state_dir=".runtime",
        result_paths=[explicit],
        results_dir=tmp_path / "nope",
    )
    assert ud._resolve_result_paths(ctx) == [explicit]


def test_resolve_result_paths_dedupes_explicit_and_globbed(tmp_path: Path) -> None:
    # a file passed explicitly AND found under results_dir appears exactly once.
    results_dir = tmp_path / "results"
    shared = results_dir / "run.jsonl"
    append_jsonl(shared, _endpoint_record("m1", "HumanEval/0", passed=True))
    other = results_dir / "other.jsonl"
    append_jsonl(other, _endpoint_record("m2", "HumanEval/0", passed=True))

    ctx = ud.DashboardContext(
        configs=_configs(),
        state_dir=".runtime",
        result_paths=[shared],
        results_dir=results_dir,
    )
    resolved = ud._resolve_result_paths(ctx)

    assert resolved.count(shared) == 1  # not duplicated by the glob
    assert other in resolved  # the new sibling file is still picked up


def test_run_section_has_live_monitor_markup() -> None:
    body = ud.render_page()
    assert 'id="runs"' in body  # live run monitor table body
    assert "Live Runs" in body
    assert "/api/runs" in body  # the page polls the status endpoint


# ---------------------------------------------------------------------------
# chat section UI (story 09.7-002) — a thin client over /api/chat + /api/catalog
# ---------------------------------------------------------------------------


def test_nav_includes_chat_section() -> None:
    body = ud.render_page()
    assert 'data-section="chat"' in body
    assert 'id="section-chat"' in body


def test_chat_section_has_picker_reusing_catalog_selectors() -> None:
    body = ud.render_page()
    # AC1: pick a model and inferencer, populated from the same catalog the launcher uses
    assert 'id="chat-model"' in body
    assert 'id="chat-inferencer"' in body
    assert "/api/catalog" in body


def test_chat_section_has_message_pane_and_stop_control() -> None:
    body = ud.render_page()
    # AC1/AC2: a multi-turn message pane, an input, a send and a (stop) control
    assert 'id="chat-messages"' in body
    assert 'id="chat-input"' in body
    assert 'id="chat-send"' in body
    assert 'id="chat-stop"' in body


def test_chat_section_has_param_controls() -> None:
    body = ud.render_page()
    # AC3: system prompt, temperature, and max-tokens controls
    assert 'id="chat-system"' in body
    assert 'id="chat-temperature"' in body
    assert 'id="chat-max-tokens"' in body


def test_chat_section_is_thin_client_over_chat_endpoint() -> None:
    body = ud.render_page()
    # AC4: posts to the existing streaming endpoint; no new front-end stack
    assert "/api/chat" in body


def test_post_api_run_routes_over_http(tmp_path: Path, monkeypatch) -> None:
    orch = _orchestrator(tmp_path)
    monkeypatch.setattr(ud.launch, "launch_action", lambda orchestrator, b: (202, {"run_id": "x"}))
    server = ud.make_server(_ctx_with_orchestrator(orch), port=0)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://{host}:{port}/api/run",
            data=json.dumps(
                {"model": "qwen", "inferencer": "dflash", "suites": ["humaneval"]}
            ).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request) as resp:
            assert resp.status == 202
            assert json.loads(resp.read())["run_id"] == "x"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Cross-section flow + localhost-only safety (story 09.6-001)
# ---------------------------------------------------------------------------


def _app_cfg(name: str = "lmstudio", port: int = 1234) -> InferencerConfig:
    return InferencerConfig(
        name=name,
        lifecycle="app",
        detect_kind="binary",
        detect_target=name,
        port=port,
        health_url="http://127.0.0.1:{port}/v1/models",
        start=None,
    )


# -- centralized response sanitization --------------------------------------


def test_sanitize_payload_drops_secret_bearing_keys() -> None:
    cleaned = ud.sanitize_payload(
        {"name": "qwen", "api_key": "sk-abc123", "authorization": "Bearer t", "base_url": "x"}
    )

    assert cleaned == {"name": "qwen"}
    assert "sk-abc123" not in json.dumps(cleaned)


def test_sanitize_payload_drops_secret_keys_nested_in_lists() -> None:
    cleaned = ud.sanitize_payload({"runs": [{"model": "qwen", "api_key_env": "SECRET_KEY_ENV"}]})

    assert cleaned == {"runs": [{"model": "qwen"}]}


def test_sanitize_payload_redacts_absolute_host_paths_in_strings() -> None:
    cleaned = ud.sanitize_payload(
        {"error": "No such file or directory: '/Users/fxmartin/dev/results/run.jsonl'"}
    )

    assert "/Users/fxmartin" not in cleaned["error"]
    assert "run.jsonl" in cleaned["error"]  # basename kept so the message stays useful


def test_sanitize_payload_leaves_safe_values_untouched() -> None:
    payload = {"passed": 3, "decode_tokens_per_second": 42.0, "result_file": "run.jsonl"}
    assert ud.sanitize_payload(payload) == payload


def test_json_responses_flow_through_sanitizer(monkeypatch) -> None:
    # a status row carrying a host path in its detail is scrubbed before it ships
    monkeypatch.setattr(
        manager,
        "status_all",
        lambda configs, state_dir: {
            "dflash": InferencerStatus(
                name="dflash",
                installed=True,
                lifecycle="server",
                running=True,
                pid=1,
                port=8000,
                healthy=True,
                detail="log at /Users/fxmartin/dev/.runtime/dflash.log",
            )
        },
    )

    body = ud.handle_request("GET", "/api/status", _ctx()).body.decode()

    assert "/Users/fxmartin" not in body


def test_run_status_redacts_host_path_in_failure_reason(tmp_path: Path) -> None:
    # an exception message that captured a real path must not reach the browser
    orch = _orchestrator(tmp_path)
    _inject_run(
        orch,
        id="boom",
        status="failed",
        error="FileNotFoundError: '/Users/fxmartin/dev/configs/models.yaml'",
    )

    body = ud.handle_request("GET", "/api/run/boom", _ctx_with_orchestrator(orch)).body.decode()

    assert "/Users/fxmartin" not in body
    assert "models.yaml" in body  # basename retained


def test_no_unified_endpoint_leaks_a_known_secret(monkeypatch, tmp_path: Path) -> None:
    # security sweep: across every JSON endpoint, the model's API-key env name and
    # base_url never reach the browser (the catalog/status/data projections plus the
    # centralized sanitizer together hold the line).
    monkeypatch.setattr(
        manager,
        "status_all",
        lambda configs, state_dir: {"dflash": _status("dflash", running=True)},
    )
    path = tmp_path / "run.jsonl"
    append_jsonl(path, _endpoint_record("m1", "HumanEval/0", passed=True))
    ctx = _ctx([path])

    for route in ("/api/status", "/api/data", "/api/catalog", "/api/runs"):
        body = ud.handle_request("GET", route, ctx).body.decode().lower()
        assert "secret_key_env" not in body
        assert "http://127.0.0.1:8000/v1" not in body


# -- GUI-app safety re-asserted at the unified layer ------------------------


def test_unified_run_refuses_gui_app_and_never_force_quits(tmp_path: Path, monkeypatch) -> None:
    configs = {"dflash": _server_cfg("dflash", 8000), "lmstudio": _app_cfg()}
    orch = launch.RunOrchestrator(
        models={"qwen": _model()},
        inferencers=configs,
        state_dir=".runtime",
        results_dir=str(tmp_path / "results"),
    )
    # If the unified layer respected the warn-and-refuse rule, no lifecycle call fires.
    monkeypatch.setattr(launch.manager, "stop", lambda *a, **k: pytest.fail("force-quit a GUI app"))
    monkeypatch.setattr(
        launch.manager, "start_exclusive", lambda *a, **k: pytest.fail("started past a GUI app")
    )

    ctx = ud.DashboardContext(
        configs=configs,
        state_dir=".runtime",
        orchestrator=orch,
        results_dir=str(tmp_path / "results"),
    )
    body = json.dumps({"model": "qwen", "inferencer": "lmstudio", "suites": ["humaneval"]}).encode()
    resp = ud.handle_request("POST", "/api/run", ctx, body)

    assert resp.status == 400
    assert "GUI app" in json.loads(resp.body)["error"]


# -- end-to-end: launch -> live progress -> completed results ---------------


def _dummy_tasks(name: str, *, cache_dir: str | Path) -> list[object]:
    return [object()]


def test_launch_flows_to_live_progress_and_results(tmp_path: Path, monkeypatch) -> None:
    results_dir = tmp_path / "results"
    started: set[str] = set()

    def _fake_start_exclusive(cfg, configs, state_dir, *, confirm, force=False):
        started.add(cfg.name)
        return _status(cfg.name, running=True)

    def _fake_run_suite(*, models, tasks, result_path, progress=None, **kwargs):
        append_jsonl(result_path, _endpoint_record("qwen", "HumanEval/0", passed=True))
        if progress is not None:
            progress("[1/1] qwen HumanEval/0: passed")
        return {"passed": 1, "failed": 0, "infra_failed": 0}

    monkeypatch.setattr(launch.manager, "running_others", lambda *a, **k: [])
    monkeypatch.setattr(launch.manager, "start_exclusive", _fake_start_exclusive)
    monkeypatch.setattr(launch.tasks, "load_suite", _dummy_tasks)
    monkeypatch.setattr(launch.runner, "run_endpoint_suite", _fake_run_suite)

    orch = launch.RunOrchestrator(
        models={"qwen": _model()},
        inferencers=_configs(),
        state_dir=".runtime",
        results_dir=str(results_dir),
    )
    ctx = ud.DashboardContext(
        configs=_configs(), state_dir=".runtime", orchestrator=orch, results_dir=str(results_dir)
    )

    # 1. launch
    body = json.dumps({"model": "qwen", "inferencer": "dflash", "suites": ["humaneval"]}).encode()
    launched = ud.handle_request("POST", "/api/run", ctx, body)
    assert launched.status == 202
    run_id = json.loads(launched.body)["run_id"]

    # the run brought up exactly the chosen engine
    assert started == {"dflash"}

    orch.join(timeout=5)

    # 2. live progress -> terminal
    runs = json.loads(ud.handle_request("GET", "/api/runs", ctx).body)["runs"]
    assert runs[0]["run_id"] == run_id
    assert runs[0]["status"] == "completed"
    assert runs[0]["passed"] == 1

    # 3. completed results show up via Epic-07 aggregates, no restart
    data = json.loads(ud.handle_request("GET", "/api/data", ctx).body)
    assert data["endpoint_models"][0]["model"] == "qwen"


def test_inferencers_section_reflects_engine_a_run_brought_up(tmp_path: Path, monkeypatch) -> None:
    started: set[str] = set()

    def _fake_start_exclusive(cfg, configs, state_dir, *, confirm, force=False):
        started.add(cfg.name)
        return _status(cfg.name, running=True)

    monkeypatch.setattr(launch.manager, "running_others", lambda *a, **k: [])
    monkeypatch.setattr(launch.manager, "start_exclusive", _fake_start_exclusive)
    monkeypatch.setattr(launch.tasks, "load_suite", _dummy_tasks)
    monkeypatch.setattr(
        launch.runner,
        "run_endpoint_suite",
        lambda **k: {"passed": 0, "failed": 0, "infra_failed": 0},
    )
    # the Inferencers panel reads the same manager.status_all the run started through
    monkeypatch.setattr(
        manager,
        "status_all",
        lambda configs, state_dir: {n: _status(n, running=(n in started)) for n in configs},
    )

    orch = launch.RunOrchestrator(
        models={"qwen": _model()},
        inferencers=_configs(),
        state_dir=".runtime",
        results_dir=str(tmp_path / "results"),
    )
    ctx = ud.DashboardContext(
        configs=_configs(),
        state_dir=".runtime",
        orchestrator=orch,
        results_dir=str(tmp_path / "results"),
    )

    body = json.dumps({"model": "qwen", "inferencer": "dflash", "suites": ["humaneval"]}).encode()
    ud.handle_request("POST", "/api/run", ctx, body)
    orch.join(timeout=5)

    status = json.loads(ud.handle_request("GET", "/api/status", ctx).body)
    running = {row["name"] for row in status["inferencers"] if row["running"]}
    assert running == {"dflash"}


def test_build_orchestrator_returns_none_without_results_dir() -> None:
    assert ud._build_orchestrator("configs/models.yaml", _configs(), ".runtime", None) is None


def test_build_orchestrator_returns_none_when_models_unloadable(monkeypatch, tmp_path) -> None:
    from local_code_bench.config import ConfigError

    monkeypatch.setattr(ud, "load_models", lambda _path: (_ for _ in ()).throw(ConfigError("nope")))

    orch = ud._build_orchestrator("missing.yaml", _configs(), ".runtime", str(tmp_path))

    assert orch is None


def test_build_orchestrator_wires_models_into_run_orchestrator(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ud, "load_models", lambda _path: {"qwen": object()})

    orch = ud._build_orchestrator("configs/models.yaml", _configs(), ".runtime", str(tmp_path))

    assert isinstance(orch, launch.RunOrchestrator)
    assert orch.runs_payload() == []  # built, no runs launched yet


# ---------------------------------------------------------------------------
# stream cancellation: a dropped browser connection releases the provider
# ---------------------------------------------------------------------------


def _stub_handler_with_wfile(wfile: object) -> ud.BaseHTTPRequestHandler:
    """A handler instance wired to a fake wfile, bypassing socket setup.

    BaseHTTPRequestHandler.__init__ services the socket immediately, so for a
    direct _stream() unit test we build the instance via __new__ and stub the
    header-writing callbacks that touch the (absent) connection.
    """

    handler_cls = ud.make_handler(_ctx())
    handler = handler_cls.__new__(handler_cls)
    handler.wfile = wfile
    handler.send_response = lambda status: None
    handler.send_header = lambda *a, **k: None
    handler.end_headers = lambda: None
    return handler


class _BrokenWfile:
    """A wfile that dies on the first write, as a closed client socket would."""

    def write(self, _data: bytes) -> int:
        raise BrokenPipeError("client closed the connection")

    def flush(self) -> None:  # pragma: no cover - write raises before flush
        pass


def test_stream_releases_provider_when_client_drops_connection() -> None:
    closed = {"called": False}

    class _Events:
        def __iter__(self):
            yield 'data: {"delta": "Hi"}\n\n'

        def close(self) -> None:
            closed["called"] = True

    handler = _stub_handler_with_wfile(_BrokenWfile())
    response = ud.chat.ChatStreamResponse(200, _Events())

    handler._stream(response)  # broken pipe must be swallowed, not raised

    assert closed["called"] is True  # upstream provider connection released


def test_stream_swallows_connection_reset_when_events_have_no_close() -> None:
    class _ResetWfile:
        def write(self, _data: bytes) -> int:
            raise ConnectionResetError("peer reset")

        def flush(self) -> None:  # pragma: no cover - write raises before flush
            pass

    handler = _stub_handler_with_wfile(_ResetWfile())
    # A plain list is iterable but exposes no close(): the callable() guard skips it.
    response = ud.chat.ChatStreamResponse(200, ['data: {"delta": "Hi"}\n\n'])

    handler._stream(response)  # must not raise even with nothing to close


def test_load_models_safe_degrades_silently_without_progress_callback(monkeypatch) -> None:
    from local_code_bench.config import ConfigError

    def _missing_models(_path):
        raise ConfigError("model config not found")

    monkeypatch.setattr(ud, "load_models", _missing_models)

    # progress=None: chat is disabled to an empty catalog with no callback to notify.
    assert ud._load_models_safe("configs/models.yaml", None) == {}


# ---------------------------------------------------------------------------
# inventory section: a thin client over the Epic-11 model-store scanner
# (story 11.5-001) — per-inferencer downloads + shared-model detection
# ---------------------------------------------------------------------------


from local_code_bench.inferencers.inventory import StoredModel  # noqa: E402


def _stored(inferencer: str, fmt: str, name: str, path: str, size: int) -> StoredModel:
    return StoredModel(
        inferencer=inferencer, store_format=fmt, name=name, path=path, size_bytes=size
    )


def test_api_inventory_lists_models_with_format_quant_and_size(monkeypatch) -> None:
    # AC1: per-inferencer downloads carry format, quant, provider, and size.
    monkeypatch.setattr(
        ud.inventory,
        "scan_inferencers",
        lambda configs, **k: [
            _stored(
                "dflash",
                "hf-safetensors",
                "mlx-community/Qwen2.5-Coder-7B-4bit",
                "/store/qwen",
                4000,
            )
        ],
    )

    resp = ud.handle_request("GET", "/api/inventory", _ctx())

    assert resp.status == 200
    assert resp.content_type.startswith("application/json")
    row = json.loads(resp.body)["models"][0]
    assert row["name"] == "mlx-community/Qwen2.5-Coder-7B-4bit"
    assert row["format"] == "hf-safetensors"
    assert row["quant"] == "4bit"
    assert row["provider"] == "mlx-community"
    assert row["size_bytes"] == 4000
    assert row["inferencer"] == "dflash"


def test_api_inventory_shared_lists_every_serving_inferencer(monkeypatch) -> None:
    # AC2: one on-disk artifact reachable by two engines is reported once, both listed.
    monkeypatch.setattr(
        ud.inventory,
        "scan_inferencers",
        lambda configs, **k: [
            _stored("mlx-lm", "hf-safetensors", "org/Model", "/shared/cache", 1000),
            _stored("dflash", "hf-safetensors", "org/Model", "/shared/cache", 1000),
        ],
    )

    payload = json.loads(ud.handle_request("GET", "/api/inventory", _ctx()).body)

    assert len(payload["shared"]) == 1
    shared = payload["shared"][0]
    assert shared["name"] == "org/Model"
    assert sorted(shared["inferencers"]) == ["dflash", "mlx-lm"]


def test_api_inventory_single_owner_is_not_shared(monkeypatch) -> None:
    monkeypatch.setattr(
        ud.inventory,
        "scan_inferencers",
        lambda configs, **k: [_stored("dflash", "gguf", "solo", "/store/solo.gguf", 10)],
    )

    payload = json.loads(ud.handle_request("GET", "/api/inventory", _ctx()).body)

    assert payload["shared"] == []
    assert payload["models"][0]["name"] == "solo"


def test_api_inventory_leaks_no_host_paths(monkeypatch) -> None:
    # AC4: the projection exposes only what identifies a model — no on-disk paths.
    monkeypatch.setattr(
        ud.inventory,
        "scan_inferencers",
        lambda configs, **k: [
            _stored("dflash", "gguf", "model", "/Users/fxmartin/.cache/model.gguf", 10)
        ],
    )

    body = ud.handle_request("GET", "/api/inventory", _ctx()).body.decode()

    assert "/Users/fxmartin" not in body
    assert "/users/" not in body.lower()
    assert "model.gguf" not in body  # the on-disk path/identity is never projected


def test_api_inventory_scans_the_dashboard_configs(monkeypatch) -> None:
    seen: dict = {}
    monkeypatch.setattr(
        ud.inventory,
        "scan_inferencers",
        lambda configs, **k: seen.update(configs=list(configs)) or [],
    )

    ud.handle_request("GET", "/api/inventory", _ctx())

    assert {c.name for c in seen["configs"]} == {"dflash", "turboquant"}


def test_post_to_inventory_route_is_404() -> None:
    assert ud.handle_request("POST", "/api/inventory", _ctx()).status == 404


def test_inventory_section_is_navigable_and_thin_client_over_scanner() -> None:
    body = ud.render_page()
    assert 'data-section="inventory"' in body
    assert 'id="section-inventory"' in body
    assert 'id="inv-models"' in body  # per-inferencer downloads table body
    assert 'id="inv-shared"' in body  # shared-models table body
    assert "/api/inventory" in body


def test_inventory_section_cross_links_to_launcher() -> None:
    body = ud.render_page()
    # AC3: selecting a download pre-fills the Run launcher with a compatible inferencer
    assert "prefillRun" in body
    assert "showSection" in body


# ---------------------------------------------------------------------------
# tiered storage (story 12.6-002): tier badges + promote/demote + auto-tiering
# a thin client over the Epic-12 tiering API (12.2 inventory / 12.3 moves /
# 12.4 auto-tiering)
# ---------------------------------------------------------------------------


from local_code_bench.config import AutoTierConfig, ExternalRepoConfig  # noqa: E402
from local_code_bench.inferencers import autotier as _autotier  # noqa: E402
from local_code_bench.inferencers import tiering as _tiering  # noqa: E402
from local_code_bench.inferencers.external import (  # noqa: E402
    ExternalRepoStatus,
    TierAvailability,
)
from local_code_bench.inferencers.inventory import LocalModel  # noqa: E402
from local_code_bench.inferencers.tiered import TieredInventory, TieredModel  # noqa: E402


def _store_cfg(name: str, port: int, fmt: str) -> InferencerConfig:
    return InferencerConfig(
        name=name,
        lifecycle="server",
        detect_kind="binary",
        detect_target=name,
        port=port,
        health_url="http://127.0.0.1:{port}/v1/models",
        start=(name, "serve"),
        model_store=("~/store",),
        store_format=fmt,
    )


def _tier_ctx(
    *,
    external: bool = True,
    autotier: AutoTierConfig | None = None,
) -> ud.DashboardContext:
    return ud.DashboardContext(
        configs={"dflash": _store_cfg("dflash", 8000, "gguf")},
        state_dir=".runtime",
        external_cfg=ExternalRepoConfig(root="~/ext", volume_marker=".marker")
        if external
        else None,
        autotier_cfg=autotier,
    )


def _tiered_model(name: str, *, tiers: tuple[str, ...], size: int = 100) -> TieredModel:
    return TieredModel(
        store_format="gguf",
        name=name,
        size_bytes=size,
        quant="Q4_K_M",
        provider="bartowski",
        identity="/Users/fxmartin/.cache/" + name + ".gguf",  # host path: never projected
        inferencers=("dflash",),
        tiers=tiers,
    )


def _mounted(monkeypatch, *, mounted: bool = True) -> None:
    status = ExternalRepoStatus(
        availability=TierAvailability.MOUNTED if mounted else TierAvailability.OFFLINE,
        root=Path("/ext"),
        marker=Path("/ext/.marker"),
    )
    monkeypatch.setattr(ud, "check_availability", lambda cfg, **k: status)


def _ext_model(name: str, *, fmt: str = "gguf", size: int = 100) -> LocalModel:
    return LocalModel(
        inferencer="",
        store_format=fmt,
        name=name,
        path="/ext/gguf/" + name + ".gguf",
        size_bytes=size,
        quant=None,
        provider=None,
        identity="/ext/gguf/" + name + ".gguf",
        tier="external",
    )


# --- /api/tiers: tier-aware inventory --------------------------------------


def test_api_tiers_lists_models_with_tier_and_availability(monkeypatch) -> None:
    # AC1: each model shows its tier and the external tier's availability.
    monkeypatch.setattr(ud.inventory, "scan_inferencers", lambda configs, **k: [])
    monkeypatch.setattr(
        ud.tiered,
        "build_tiered_inventory",
        lambda *a, **k: TieredInventory(
            models=(
                _tiered_model("local-only", tiers=("local",)),
                _tiered_model("ext-only", tiers=("external",)),
            ),
            external_availability=TierAvailability.MOUNTED,
            external_cached=False,
        ),
    )

    resp = ud.handle_request("GET", "/api/tiers", _tier_ctx())

    assert resp.status == 200
    payload = json.loads(resp.body)
    assert payload["external_availability"] == "mounted"
    assert payload["external_cached"] is False
    rows = {row["name"]: row for row in payload["models"]}
    assert rows["local-only"]["tiers"] == ["local"]
    assert rows["local-only"]["present_in_both"] is False
    assert rows["ext-only"]["tiers"] == ["external"]
    assert rows["local-only"]["quant"] == "Q4_K_M"
    assert rows["local-only"]["format"] == "gguf"


def test_api_tiers_flags_present_in_both_and_reclaimable(monkeypatch) -> None:
    # AC1: a model on both tiers is flagged redundant and its bytes are reclaimable.
    monkeypatch.setattr(ud.inventory, "scan_inferencers", lambda configs, **k: [])
    monkeypatch.setattr(
        ud.tiered,
        "build_tiered_inventory",
        lambda *a, **k: TieredInventory(
            models=(
                _tiered_model("both", tiers=("external", "local"), size=500),
                _tiered_model("local-only", tiers=("local",), size=100),
            ),
            external_availability=TierAvailability.MOUNTED,
            external_cached=False,
        ),
    )

    payload = json.loads(ud.handle_request("GET", "/api/tiers", _tier_ctx()).body)

    both = next(row for row in payload["models"] if row["name"] == "both")
    assert both["present_in_both"] is True
    assert payload["reclaimable_bytes"] == 500  # the redundant copy
    assert payload["total_bytes"] == 600


def test_api_tiers_offline_reports_offline_availability(monkeypatch) -> None:
    # AC4: when the SSD is offline the availability says so (the client disables moves).
    monkeypatch.setattr(ud.inventory, "scan_inferencers", lambda configs, **k: [])
    monkeypatch.setattr(
        ud.tiered,
        "build_tiered_inventory",
        lambda *a, **k: TieredInventory(
            models=(_tiered_model("ext-only", tiers=("external",)),),
            external_availability=TierAvailability.OFFLINE,
            external_cached=True,
        ),
    )

    payload = json.loads(ud.handle_request("GET", "/api/tiers", _tier_ctx()).body)

    assert payload["external_availability"] == "offline"
    assert payload["external_cached"] is True


def test_api_tiers_leaks_no_host_paths(monkeypatch) -> None:
    # AC4: the tier projection exposes only what identifies a model — never a path.
    monkeypatch.setattr(ud.inventory, "scan_inferencers", lambda configs, **k: [])
    monkeypatch.setattr(
        ud.tiered,
        "build_tiered_inventory",
        lambda *a, **k: TieredInventory(
            models=(_tiered_model("secret", tiers=("local",)),),
            external_availability=TierAvailability.MOUNTED,
            external_cached=False,
        ),
    )

    body = ud.handle_request("GET", "/api/tiers", _tier_ctx()).body.decode()

    assert "/Users/fxmartin" not in body
    assert "/users/" not in body.lower()
    assert "secret.gguf" not in body  # the identity/path is never projected


def test_post_to_tiers_route_is_404() -> None:
    assert ud.handle_request("POST", "/api/tiers", _tier_ctx()).status == 404


# --- /api/promote and /api/demote: verified moves --------------------------


def test_api_promote_runs_verified_move(monkeypatch) -> None:
    # AC2: a promote runs the verified move server-side and reports the new tier.
    _mounted(monkeypatch)
    monkeypatch.setattr(ud.tiered, "scan_external_tier", lambda cfg, infs, **k: [_ext_model("m")])
    captured: dict = {}

    def fake_promote(source, inferencer, *a, **k):
        captured["source"] = source.name
        captured["inferencer"] = inferencer.name
        return _tiering.PromoteResult(
            plan=_tiering.PromotePlan(
                name="m",
                store_format="gguf",
                source=Path("/ext/gguf/m.gguf"),
                destination=Path("/Users/fxmartin/store/m.gguf"),
                size_bytes=100,
            ),
            destination=Path("/Users/fxmartin/store/m.gguf"),
            bytes_copied=100,
            verified=True,
        )

    monkeypatch.setattr(ud.tiering, "promote_model", fake_promote)

    resp = ud.handle_request("POST", "/api/promote?name=m&format=gguf", _tier_ctx())

    assert resp.status == 200
    body = json.loads(resp.body)
    assert body["promoted"]["name"] == "m"
    assert body["promoted"]["tier"] == "local"
    assert body["promoted"]["bytes_copied"] == 100
    assert captured == {"source": "m", "inferencer": "dflash"}
    # AC4: no host path from the result leaks into the response.
    assert "/Users/fxmartin" not in resp.body.decode()


def test_api_promote_offline_is_refused_without_moving(monkeypatch) -> None:
    # AC: an offline SSD refuses before any bytes move.
    _mounted(monkeypatch, mounted=False)
    moved = {"called": False}
    monkeypatch.setattr(
        ud.tiering,
        "promote_model",
        lambda *a, **k: moved.update(called=True),
    )

    resp = ud.handle_request("POST", "/api/promote?name=m&format=gguf", _tier_ctx())

    assert resp.status == 409
    assert "offline" in json.loads(resp.body)["error"]
    assert moved["called"] is False


def test_api_promote_refusal_maps_to_error(monkeypatch) -> None:
    # AC: a tiering refusal (in-use / no space) surfaces verbatim as an error.
    _mounted(monkeypatch)
    monkeypatch.setattr(ud.tiered, "scan_external_tier", lambda cfg, infs, **k: [_ext_model("m")])

    def boom(*a, **k):
        raise _tiering.PromoteError("dflash is running and could be serving m")

    monkeypatch.setattr(ud.tiering, "promote_model", boom)

    resp = ud.handle_request("POST", "/api/promote?name=m&format=gguf", _tier_ctx())

    assert resp.status == 409
    assert "could be serving" in json.loads(resp.body)["error"]


def test_api_promote_unknown_external_model_is_404(monkeypatch) -> None:
    _mounted(monkeypatch)
    monkeypatch.setattr(ud.tiered, "scan_external_tier", lambda cfg, infs, **k: [])

    resp = ud.handle_request("POST", "/api/promote?name=ghost&format=gguf", _tier_ctx())

    assert resp.status == 404


def test_api_demote_runs_verified_move(monkeypatch) -> None:
    # AC2: a demote runs the verified move server-side and reports the new tier.
    _mounted(monkeypatch)
    monkeypatch.setattr(
        ud.inventory,
        "scan_inferencers",
        lambda configs, **k: [_stored("dflash", "gguf", "m", "/store/m.gguf", 100)],
    )
    captured: dict = {}

    def fake_demote(source, *a, **k):
        captured["source"] = source.name
        return _tiering.DemoteResult(
            plan=_tiering.DemotePlan(
                name="m",
                store_format="gguf",
                source=Path("/store/m.gguf"),
                destination=Path("/ext/gguf/m.gguf"),
                size_bytes=100,
            ),
            destination=Path("/ext/gguf/m.gguf"),
            bytes_reclaimed=100,
            verified=True,
            reused_existing=False,
        )

    monkeypatch.setattr(ud.tiering, "demote_model", fake_demote)

    resp = ud.handle_request("POST", "/api/demote?name=m&format=gguf", _tier_ctx())

    assert resp.status == 200
    body = json.loads(resp.body)
    assert body["demoted"]["name"] == "m"
    assert body["demoted"]["tier"] == "external"
    assert body["demoted"]["bytes_reclaimed"] == 100
    assert body["demoted"]["reused_existing"] is False
    assert captured == {"source": "m"}


def test_api_demote_offline_is_refused_without_moving(monkeypatch) -> None:
    _mounted(monkeypatch, mounted=False)
    moved = {"called": False}
    monkeypatch.setattr(
        ud.tiering,
        "demote_model",
        lambda *a, **k: moved.update(called=True),
    )

    resp = ud.handle_request("POST", "/api/demote?name=m&format=gguf", _tier_ctx())

    assert resp.status == 409
    assert moved["called"] is False


def test_api_demote_refusal_maps_to_error(monkeypatch) -> None:
    _mounted(monkeypatch)
    monkeypatch.setattr(
        ud.inventory,
        "scan_inferencers",
        lambda configs, **k: [_stored("dflash", "gguf", "m", "/store/m.gguf", 100)],
    )

    def boom(*a, **k):
        raise _tiering.DemoteError("insufficient external free space to demote m")

    monkeypatch.setattr(ud.tiering, "demote_model", boom)

    resp = ud.handle_request("POST", "/api/demote?name=m&format=gguf", _tier_ctx())

    assert resp.status == 409
    assert "insufficient external free space" in json.loads(resp.body)["error"]


def test_api_promote_without_external_tier_configured_is_409() -> None:
    resp = ud.handle_request("POST", "/api/promote?name=m&format=gguf", _tier_ctx(external=False))
    assert resp.status == 409


# --- /api/tier-plan and /api/tier-apply: auto-tiering ----------------------


def _plan(**over) -> _autotier.AutoTierPlan:
    base = dict(
        evictions=(),
        bytes_to_reclaim=0,
        bytes_reclaimed=0,
        local_total_bytes=0,
        satisfied=True,
        paused=False,
        pinned=(),
        warnings=(),
    )
    base.update(over)
    return _autotier.AutoTierPlan(**base)


def _eviction(name: str, size: int = 100) -> _autotier.Eviction:
    return _autotier.Eviction(
        name=name,
        store_format="gguf",
        identity="/Users/fxmartin/.cache/" + name + ".gguf",  # host path: never projected
        size_bytes=size,
        last_used=1.0,
        model=_ext_model(name),
    )


def test_api_tier_plan_shows_dry_run_plan(monkeypatch) -> None:
    # AC3: the plan lists evictions and the bytes it would reclaim.
    monkeypatch.setattr(ud.inventory, "scan_inferencers", lambda configs, **k: [])
    _mounted(monkeypatch)
    monkeypatch.setattr(
        ud.autotier,
        "plan_autotier",
        lambda *a, **k: _plan(
            evictions=(_eviction("old", 300),),
            bytes_to_reclaim=300,
            bytes_reclaimed=300,
            local_total_bytes=900,
            pinned=("keepme",),
        ),
    )

    resp = ud.handle_request(
        "GET",
        "/api/tier-plan",
        _tier_ctx(autotier=AutoTierConfig(max_local_gb=1.0, pins=("keepme",))),
    )

    assert resp.status == 200
    body = json.loads(resp.body)
    assert body["configured"] is True
    assert body["paused"] is False
    assert body["bytes_reclaimed"] == 300
    assert body["pinned"] == ["keepme"]
    assert body["evictions"][0]["name"] == "old"
    assert body["evictions"][0]["size_bytes"] == 300
    # AC4: an eviction's identity/path never leaks.
    assert "/Users/fxmartin" not in resp.body.decode()


def test_api_tier_plan_not_configured_is_inert(monkeypatch) -> None:
    resp = ud.handle_request("GET", "/api/tier-plan", _tier_ctx())

    assert resp.status == 200
    body = json.loads(resp.body)
    assert body["configured"] is False
    assert body["evictions"] == []


def test_api_tier_plan_paused_when_offline(monkeypatch) -> None:
    monkeypatch.setattr(ud.inventory, "scan_inferencers", lambda configs, **k: [])
    _mounted(monkeypatch, mounted=False)
    monkeypatch.setattr(
        ud.autotier,
        "plan_autotier",
        lambda *a, **k: _plan(
            paused=True, warnings=("auto-tiering paused: external repo offline",)
        ),
    )

    body = json.loads(
        ud.handle_request(
            "GET", "/api/tier-plan", _tier_ctx(autotier=AutoTierConfig(max_local_gb=1.0))
        ).body
    )

    assert body["paused"] is True
    assert "paused" in body["warnings"][0]


def test_api_tier_apply_evicts_through_demote(monkeypatch) -> None:
    # AC3: apply runs the plan through the verified demote path and reports results.
    monkeypatch.setattr(ud.inventory, "scan_inferencers", lambda configs, **k: [])
    _mounted(monkeypatch)
    monkeypatch.setattr(
        ud.autotier,
        "plan_autotier",
        lambda *a, **k: _plan(evictions=(_eviction("old", 300),), bytes_reclaimed=300),
    )

    def fake_apply(plan, *a, **k):
        return [
            _tiering.DemoteResult(
                plan=_tiering.DemotePlan(
                    name="old",
                    store_format="gguf",
                    source=Path("/store/old.gguf"),
                    destination=Path("/ext/gguf/old.gguf"),
                    size_bytes=300,
                ),
                destination=Path("/ext/gguf/old.gguf"),
                bytes_reclaimed=300,
                verified=True,
                reused_existing=False,
            )
        ]

    monkeypatch.setattr(ud.autotier, "apply_plan", fake_apply)

    resp = ud.handle_request(
        "POST",
        "/api/tier-apply",
        _tier_ctx(autotier=AutoTierConfig(max_local_gb=1.0)),
    )

    assert resp.status == 200
    body = json.loads(resp.body)
    assert body["count"] == 1
    assert body["applied"][0]["name"] == "old"
    assert body["applied"][0]["bytes_reclaimed"] == 300


def test_api_tier_apply_not_configured_is_409() -> None:
    resp = ud.handle_request("POST", "/api/tier-apply", _tier_ctx())
    assert resp.status == 409


def test_api_tier_apply_paused_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(ud.inventory, "scan_inferencers", lambda configs, **k: [])
    _mounted(monkeypatch, mounted=False)
    monkeypatch.setattr(ud.autotier, "plan_autotier", lambda *a, **k: _plan(paused=True))
    applied = {"called": False}
    monkeypatch.setattr(ud.autotier, "apply_plan", lambda *a, **k: applied.update(called=True))

    resp = ud.handle_request(
        "POST",
        "/api/tier-apply",
        _tier_ctx(autotier=AutoTierConfig(max_local_gb=1.0)),
    )

    assert resp.status == 409
    assert applied["called"] is False


def test_get_to_promote_route_is_404() -> None:
    assert ud.handle_request("GET", "/api/promote?name=m&format=gguf", _tier_ctx()).status == 404


# --- UI: tier badges + move/tier controls in the inventory section ---------


def test_inventory_section_has_tier_view_and_controls() -> None:
    body = ud.render_page()
    # AC1/AC2: tier table with promote/demote controls.
    assert 'id="tier-models"' in body
    assert "/api/tiers" in body
    assert "data-promote" in body
    assert "data-demote" in body
    # AC3: an auto-tiering plan view with an explicit apply action.
    assert 'id="tier-plan"' in body
    assert 'id="tier-apply"' in body
    assert "/api/tier-plan" in body
    assert "/api/tier-apply" in body


# --- coverage gaps: move-control edge cases + tier-config resilience -------


def test_api_promote_no_compatible_inferencer_is_404(monkeypatch) -> None:
    # The external model exists, but no local store can serve its format: 404.
    _mounted(monkeypatch)
    monkeypatch.setattr(
        ud.tiered,
        "scan_external_tier",
        lambda cfg, infs, **k: [_ext_model("m", fmt="mlx")],
    )

    resp = ud.handle_request("POST", "/api/promote?name=m&format=mlx", _tier_ctx())

    assert resp.status == 404
    assert "no inferencer" in json.loads(resp.body)["error"]


def test_api_demote_without_external_tier_configured_is_409() -> None:
    # Nothing to demote to when no external tier is configured.
    resp = ud.handle_request(
        "POST", "/api/demote?name=m&format=gguf", _tier_ctx(external=False)
    )

    assert resp.status == 409
    assert "nowhere to demote" in json.loads(resp.body)["error"]


def test_api_demote_unknown_local_model_is_404(monkeypatch) -> None:
    # A model absent from the local tier cannot be demoted: 404.
    _mounted(monkeypatch)
    monkeypatch.setattr(ud.inventory, "scan_inferencers", lambda configs, **k: [])

    resp = ud.handle_request("POST", "/api/demote?name=ghost&format=gguf", _tier_ctx())

    assert resp.status == 404
    assert "not found on the local tier" in json.loads(resp.body)["error"]


def test_find_external_without_external_tier_is_none() -> None:
    # The lookup short-circuits to None when no external tier is configured.
    assert ud._find_external(_tier_ctx(external=False), "m", "gguf") is None


def _no_store_cfg(name: str = "app", port: int = 4321) -> InferencerConfig:
    return InferencerConfig(
        name=name,
        lifecycle="server",
        detect_kind="binary",
        detect_target=name,
        port=port,
        health_url="http://127.0.0.1:{port}/v1/models",
        start=(name, "serve"),
        model_store=(),
        store_format="gguf",
    )


def test_local_free_bytes_skips_inferencers_without_a_store() -> None:
    # An inferencer with no local store contributes no volume: None when none do.
    ctx = ud.DashboardContext(configs={"app": _no_store_cfg()}, state_dir=".runtime")

    assert ud._local_free_bytes(ctx) is None


def test_local_free_bytes_returns_none_when_volume_probe_fails(monkeypatch) -> None:
    # A volume that cannot be stat'd is skipped; with no other store, None.
    def boom(_path):
        raise OSError("device not ready")

    monkeypatch.setattr(ud.shutil, "disk_usage", boom)
    ctx = ud.DashboardContext(configs={"dflash": _store_cfg("dflash", 8000, "gguf")}, state_dir=".x")

    assert ud._local_free_bytes(ctx) is None


def test_api_tier_apply_surfaces_demote_failure_as_409(monkeypatch) -> None:
    # A failure mid-apply (in-use / no space) surfaces verbatim as a 409.
    monkeypatch.setattr(ud.inventory, "scan_inferencers", lambda configs, **k: [])
    _mounted(monkeypatch)
    monkeypatch.setattr(
        ud.autotier,
        "plan_autotier",
        lambda *a, **k: _plan(evictions=(_eviction("old", 300),), bytes_reclaimed=300),
    )

    def boom(*a, **k):
        raise _tiering.DemoteError("old is in use and could be serving requests")

    monkeypatch.setattr(ud.autotier, "apply_plan", boom)

    resp = ud.handle_request(
        "POST", "/api/tier-apply", _tier_ctx(autotier=AutoTierConfig(max_local_gb=1.0))
    )

    assert resp.status == 409
    assert "could be serving" in json.loads(resp.body)["error"]


class _CollectingWfile:
    """A wfile that records every write, as a healthy client connection would."""

    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def write(self, data: bytes) -> int:
        self.chunks.append(data)
        return len(data)

    def flush(self) -> None:
        pass


def test_stream_writes_every_event_to_a_healthy_client() -> None:
    # The happy path: all SSE events flow through to a client that never drops.
    wfile = _CollectingWfile()
    handler = _stub_handler_with_wfile(wfile)
    response = ud.chat.ChatStreamResponse(200, ["data: a\n\n", "data: b\n\n"])

    handler._stream(response)

    assert b"".join(wfile.chunks) == b"data: a\n\ndata: b\n\n"


def test_load_tier_configs_safe_disables_both_tiers_on_malformed_config(monkeypatch) -> None:
    # A malformed external/auto-tier block degrades that feature to disabled and
    # reports it through the progress callback rather than taking the page down.
    monkeypatch.setattr(
        ud, "load_external_repo", lambda p: (_ for _ in ()).throw(ud.ConfigError("bad external"))
    )
    monkeypatch.setattr(
        ud, "load_autotier", lambda p: (_ for _ in ()).throw(ud.ConfigError("bad autotier"))
    )
    notices: list[str] = []

    external_cfg, autotier_cfg = ud._load_tier_configs_safe("cfg.yaml", notices.append)

    assert external_cfg is None
    assert autotier_cfg is None
    assert any("external tier disabled" in n for n in notices)
    assert any("auto-tiering disabled" in n for n in notices)


def test_load_tier_configs_safe_disables_silently_without_progress(monkeypatch) -> None:
    # With no progress callback, malformed blocks still degrade to disabled quietly.
    monkeypatch.setattr(
        ud, "load_external_repo", lambda p: (_ for _ in ()).throw(ud.ConfigError("bad"))
    )
    monkeypatch.setattr(
        ud, "load_autotier", lambda p: (_ for _ in ()).throw(ud.ConfigError("bad"))
    )

    assert ud._load_tier_configs_safe("cfg.yaml", None) == (None, None)


def test_find_external_skips_non_matching_models(monkeypatch) -> None:
    # The scan is filtered by name + format; earlier non-matches are skipped.
    monkeypatch.setattr(
        ud.tiered,
        "scan_external_tier",
        lambda cfg, infs, **k: [_ext_model("other"), _ext_model("m")],
    )

    found = ud._find_external(_tier_ctx(), "m", "gguf")

    assert found is not None and found.name == "m"


def test_find_local_skips_non_matching_models(monkeypatch) -> None:
    # The local scan is filtered by name + format; earlier non-matches are skipped.
    monkeypatch.setattr(
        ud.inventory,
        "scan_inferencers",
        lambda configs, **k: [
            _stored("dflash", "gguf", "other", "/store/other.gguf", 100),
            _stored("dflash", "gguf", "m", "/store/m.gguf", 100),
        ],
    )

    found = ud._find_local(_tier_ctx(), "m", "gguf")

    assert found is not None and found.name == "m"
