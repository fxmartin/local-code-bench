from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from local_code_bench import launch
from local_code_bench.config import InferencerConfig, ModelConfig, TokenPrices
from local_code_bench.inferencers import manager
from local_code_bench.inferencers.manager import InferencerError, InferencerStatus
from local_code_bench.tasks import BenchmarkTask


# ---------------------------------------------------------------------------
# fixtures / builders
# ---------------------------------------------------------------------------


def _model(name: str = "qwen", inferencer: str = "dflash") -> ModelConfig:
    return ModelConfig(
        name=name,
        type="openai",
        base_url="http://127.0.0.1:8000/v1",
        model_id=f"{name}-id",
        pinned_revision="main",
        price_per_1k_tokens=TokenPrices(input=0.0, output=0.0),
        inferencer=inferencer,
    )


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


def _app_cfg(name: str = "lm-studio", port: int = 1234) -> InferencerConfig:
    return InferencerConfig(
        name=name,
        lifecycle="app",
        detect_kind="app",
        detect_target="LM Studio.app",
        port=port,
        health_url="http://127.0.0.1:{port}/v1/models",
    )


def _status(
    name: str,
    *,
    lifecycle: str = "server",
    running: bool = False,
    healthy: bool = False,
    pid: int | None = None,
    port: int = 8000,
) -> InferencerStatus:
    return InferencerStatus(
        name=name,
        installed=True,
        lifecycle=lifecycle,
        running=running,
        pid=pid,
        port=port,
        healthy=healthy,
        detail="ok",
    )


def _task(suite: str, task_id: str) -> BenchmarkTask:
    return BenchmarkTask(
        task_id=task_id,
        suite=suite,  # type: ignore[arg-type]
        prompt="def f():\n",
        test_code="assert True\n",
        entry_point="f",
        version="v",
    )


def _orchestrator(tmp_path, **kwargs):
    models = kwargs.pop("models", {"qwen": _model()})
    inferencers = kwargs.pop(
        "inferencers",
        {"dflash": _server_cfg("dflash", 8000), "lm-studio": _app_cfg()},
    )
    return launch.RunOrchestrator(
        models=models,
        inferencers=inferencers,
        state_dir=str(tmp_path / "state"),
        results_dir=str(tmp_path / "results"),
        cache_dir=str(tmp_path / "cache"),
    )


def _patch_backends(monkeypatch, *, calls, suites_by_name=None, block=None):
    """Wire start_exclusive / run_endpoint_suite / load_suite to deterministic fakes."""

    monkeypatch.setattr(manager, "running_others", lambda *a, **k: [])

    def _fake_start(cfg, configs, state_dir, *, confirm, force=False):
        calls.append(f"start:{cfg.name}")
        return _status(cfg.name, running=True, healthy=True)

    monkeypatch.setattr(manager, "start_exclusive", _fake_start)

    def _fake_load(name, *, cache_dir):
        tasks = (suites_by_name or {}).get(name, [_task(name, f"{name}/0")])
        return tasks

    monkeypatch.setattr(launch.tasks, "load_suite", _fake_load)

    def _fake_run(*, models, tasks, result_path, progress=None, **kw):
        calls.append(f"suite:{list(tasks)[0].suite}")
        if block is not None:
            block.wait(timeout=5)
        if progress is not None:
            progress("[1/1] qwen t: passed")
        return {"passed": 1, "failed": 0, "infra_failed": 0, "skipped": 0}

    monkeypatch.setattr(launch.runner, "run_endpoint_suite", _fake_run)


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def test_launch_rejects_unknown_model(tmp_path):
    orch = _orchestrator(tmp_path)
    code, payload = orch.launch(model="nope", inferencer="dflash", suites=["humaneval"])
    assert code == 400
    assert "nope" in payload["error"]


def test_launch_rejects_unknown_inferencer(tmp_path):
    orch = _orchestrator(tmp_path)
    code, payload = orch.launch(model="qwen", inferencer="nope", suites=["humaneval"])
    assert code == 400
    assert "nope" in payload["error"]


def test_launch_rejects_empty_suites(tmp_path):
    orch = _orchestrator(tmp_path)
    code, payload = orch.launch(model="qwen", inferencer="dflash", suites=[])
    assert code == 400
    assert "suite" in payload["error"].lower()


def test_launch_rejects_unknown_suite(tmp_path):
    orch = _orchestrator(tmp_path)
    code, payload = orch.launch(model="qwen", inferencer="dflash", suites=["humaneval", "bogus"])
    assert code == 400
    assert "bogus" in payload["error"]


def test_launch_rejects_gui_inferencer_target(tmp_path):
    orch = _orchestrator(tmp_path)
    code, payload = orch.launch(model="qwen", inferencer="lm-studio", suites=["humaneval"])
    assert code == 400
    assert "lm-studio" in payload["error"]


# ---------------------------------------------------------------------------
# orchestration order + run id
# ---------------------------------------------------------------------------


def test_launch_starts_inferencer_then_runs_suites_in_order(tmp_path, monkeypatch):
    calls: list[str] = []
    _patch_backends(monkeypatch, calls=calls)
    orch = _orchestrator(tmp_path)

    code, payload = orch.launch(model="qwen", inferencer="dflash", suites=["humaneval", "mbpp"])

    assert code == 202
    assert payload["run_id"]
    assert payload["status"] == "running"
    orch.join(timeout=5)
    # inferencer is started exclusively first, then each suite runs in order.
    assert calls == ["start:dflash", "suite:humaneval", "suite:mbpp"]


def test_completed_run_records_terminal_state_and_counts(tmp_path, monkeypatch):
    calls: list[str] = []
    _patch_backends(monkeypatch, calls=calls)
    orch = _orchestrator(tmp_path)

    _, payload = orch.launch(model="qwen", inferencer="dflash", suites=["humaneval", "canary"])
    orch.join(timeout=5)

    state = orch.get_run(payload["run_id"])
    assert state.status == "completed"
    assert state.passed == 2  # one passed per suite from the fake
    assert state.failed == 0
    assert state.error is None


def test_run_jsonl_is_written_to_results_dir(tmp_path, monkeypatch):
    written: list[str] = []

    monkeypatch.setattr(manager, "running_others", lambda *a, **k: [])
    monkeypatch.setattr(
        manager,
        "start_exclusive",
        lambda cfg, configs, state_dir, *, confirm, force=False: _status(cfg.name, running=True),
    )
    monkeypatch.setattr(launch.tasks, "load_suite", lambda name, *, cache_dir: [_task(name, "x")])

    def _fake_run(*, models, tasks, result_path, progress=None, **kw):
        # The real runner writes JSONL to result_path; assert orchestration hands a
        # path under results_dir and let the runner own the file.
        written.append(str(result_path))
        result_path.write_text("{}\n", encoding="utf-8")
        return {"passed": 1, "failed": 0, "infra_failed": 0, "skipped": 0}

    monkeypatch.setattr(launch.runner, "run_endpoint_suite", _fake_run)

    orch = _orchestrator(tmp_path)
    _, payload = orch.launch(model="qwen", inferencer="dflash", suites=["humaneval"])
    orch.join(timeout=5)

    assert written and str(tmp_path / "results") in written[0]
    assert payload["result_file"] == orch.get_run(payload["run_id"]).result_file
    assert "/" not in payload["result_file"]  # filename only, never a host path


# ---------------------------------------------------------------------------
# single-run lock (one-active invariant)
# ---------------------------------------------------------------------------


def test_concurrent_launch_is_rejected_while_a_run_is_in_flight(tmp_path, monkeypatch):
    calls: list[str] = []
    gate = threading.Event()
    _patch_backends(monkeypatch, calls=calls, block=gate)
    orch = _orchestrator(tmp_path)

    code1, first = orch.launch(model="qwen", inferencer="dflash", suites=["humaneval"])
    assert code1 == 202

    code2, second = orch.launch(model="qwen", inferencer="dflash", suites=["mbpp"])
    assert code2 == 409
    assert second["error"] == "run_in_flight"
    assert second["active_run_id"] == first["run_id"]

    gate.set()
    orch.join(timeout=5)
    # only the first run ever started a server — invariant held.
    assert calls.count("start:dflash") == 1


def test_lock_is_released_after_a_run_completes(tmp_path, monkeypatch):
    calls: list[str] = []
    _patch_backends(monkeypatch, calls=calls)
    orch = _orchestrator(tmp_path)

    _, first = orch.launch(model="qwen", inferencer="dflash", suites=["humaneval"])
    orch.join(timeout=5)

    code, second = orch.launch(model="qwen", inferencer="dflash", suites=["mbpp"])
    assert code == 202
    assert second["run_id"] != first["run_id"]
    orch.join(timeout=5)


def test_failed_inferencer_start_releases_the_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "running_others", lambda *a, **k: [])

    def _boom(cfg, configs, state_dir, *, confirm, force=False):
        raise InferencerError("did not become healthy")

    monkeypatch.setattr(manager, "start_exclusive", _boom)
    orch = _orchestrator(tmp_path)

    code, payload = orch.launch(model="qwen", inferencer="dflash", suites=["humaneval"])
    assert code == 502
    assert "healthy" in payload["error"]
    # lock released: a subsequent (working) launch is accepted.
    calls: list[str] = []
    _patch_backends(monkeypatch, calls=calls)
    code2, _ = orch.launch(model="qwen", inferencer="dflash", suites=["humaneval"])
    assert code2 == 202
    orch.join(timeout=5)


# ---------------------------------------------------------------------------
# exclusive-start confirmation contract (mirrors Epic-08)
# ---------------------------------------------------------------------------


def test_launch_needs_confirmation_when_another_server_runs(tmp_path, monkeypatch):
    others = [_status("turboquant", running=True, healthy=True, pid=9, port=8002)]
    monkeypatch.setattr(manager, "running_others", lambda *a, **k: others)
    monkeypatch.setattr(
        manager, "start_exclusive", lambda *a, **k: pytest.fail("must not start before confirm")
    )
    inferencers = {
        "dflash": _server_cfg("dflash", 8000),
        "turboquant": _server_cfg("turboquant", 8002),
    }
    orch = _orchestrator(tmp_path, inferencers=inferencers)

    code, payload = orch.launch(model="qwen", inferencer="dflash", suites=["humaneval"])

    assert code == 409
    assert payload["needs_confirmation"] is True
    assert {row["name"] for row in payload["others"]} == {"turboquant"}
    # reservation released: confirming re-submit proceeds.
    assert orch.get_run(payload.get("run_id", "")) is None


def test_launch_with_confirm_stops_others_via_start_exclusive(tmp_path, monkeypatch):
    others = [_status("turboquant", running=True, healthy=True, pid=9, port=8002)]
    monkeypatch.setattr(manager, "running_others", lambda *a, **k: others)
    seen: dict = {}

    def _fake_start(cfg, configs, state_dir, *, confirm, force=False):
        seen["confirm_result"] = confirm(others)
        return _status(cfg.name, running=True)

    monkeypatch.setattr(manager, "start_exclusive", _fake_start)
    monkeypatch.setattr(launch.tasks, "load_suite", lambda name, *, cache_dir: [_task(name, "x")])
    monkeypatch.setattr(
        launch.runner,
        "run_endpoint_suite",
        lambda **kw: {"passed": 1, "failed": 0, "infra_failed": 0, "skipped": 0},
    )
    inferencers = {
        "dflash": _server_cfg("dflash", 8000),
        "turboquant": _server_cfg("turboquant", 8002),
    }
    orch = _orchestrator(tmp_path, inferencers=inferencers)

    code, _ = orch.launch(model="qwen", inferencer="dflash", suites=["humaneval"], confirm=True)
    orch.join(timeout=5)

    assert code == 202
    assert seen["confirm_result"] is True  # confirmation contract honoured


def test_launch_blocked_by_running_gui_never_quits_it(tmp_path, monkeypatch):
    others = [_status("lm-studio", lifecycle="app", running=True, healthy=True, port=1234)]
    monkeypatch.setattr(manager, "running_others", lambda *a, **k: others)
    monkeypatch.setattr(
        manager, "start_exclusive", lambda *a, **k: pytest.fail("must not start past a GUI app")
    )
    orch = _orchestrator(tmp_path)

    code, payload = orch.launch(model="qwen", inferencer="dflash", suites=["humaneval"])

    assert code == 409
    assert payload["error"] == "gui_running"
    assert {row["name"] for row in payload["others"]} == {"lm-studio"}


# ---------------------------------------------------------------------------
# background failure surfacing
# ---------------------------------------------------------------------------


def test_background_failure_marks_run_failed_with_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "running_others", lambda *a, **k: [])
    monkeypatch.setattr(
        manager,
        "start_exclusive",
        lambda cfg, configs, state_dir, *, confirm, force=False: _status(cfg.name, running=True),
    )

    def _boom(name, *, cache_dir):
        raise RuntimeError("missing evalplus cache")

    monkeypatch.setattr(launch.tasks, "load_suite", _boom)
    orch = _orchestrator(tmp_path)

    _, payload = orch.launch(model="qwen", inferencer="dflash", suites=["humaneval-plus"])
    orch.join(timeout=5)

    state = orch.get_run(payload["run_id"])
    assert state.status == "failed"
    assert "evalplus" in state.error
    # the lock is released even when the background run fails.
    code, _ = orch.launch(model="qwen", inferencer="dflash", suites=["humaneval-plus"])
    assert code in (202, 502)


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------


def test_launch_action_parses_body(tmp_path, monkeypatch):
    calls: list[str] = []
    _patch_backends(monkeypatch, calls=calls)
    orch = _orchestrator(tmp_path)

    code, payload = launch.launch_action(
        orch, {"model": "qwen", "inferencer": "dflash", "suites": ["humaneval"]}
    )
    assert code == 202
    orch.join(timeout=5)


def test_launch_action_rejects_missing_fields(tmp_path):
    orch = _orchestrator(tmp_path)
    code, payload = launch.launch_action(orch, {"model": "qwen"})
    assert code == 400
    assert "error" in payload


def test_handle_request_routes_post_api_run(tmp_path, monkeypatch):
    calls: list[str] = []
    _patch_backends(monkeypatch, calls=calls)
    orch = _orchestrator(tmp_path)

    body = json.dumps({"model": "qwen", "inferencer": "dflash", "suites": ["humaneval"]}).encode()
    resp = launch.handle_request("POST", "/api/run", body, orch)
    assert resp.status == 202
    assert resp.content_type.startswith("application/json")
    orch.join(timeout=5)


def test_handle_request_rejects_invalid_json(tmp_path):
    orch = _orchestrator(tmp_path)
    resp = launch.handle_request("POST", "/api/run", b"{not json", orch)
    assert resp.status == 400


def test_handle_request_unknown_route_is_404(tmp_path):
    orch = _orchestrator(tmp_path)
    resp = launch.handle_request("GET", "/nope", b"", orch)
    assert resp.status == 404


def test_make_server_binds_localhost_only(tmp_path):
    orch = _orchestrator(tmp_path)
    server = launch.make_server(orch, port=0)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()


def test_handler_serves_real_post_over_http(tmp_path, monkeypatch):
    calls: list[str] = []
    _patch_backends(monkeypatch, calls=calls)
    orch = _orchestrator(tmp_path)

    server = launch.make_server(orch, port=0)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body = json.dumps(
            {"model": "qwen", "inferencer": "dflash", "suites": ["humaneval"]}
        ).encode()
        req = urllib.request.Request(
            f"http://{host}:{port}/api/run",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 202
            payload = json.loads(resp.read())
        assert payload["run_id"]
        orch.join(timeout=5)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_runs_lists_tracked_runs_in_order(tmp_path, monkeypatch):
    calls: list[str] = []
    _patch_backends(monkeypatch, calls=calls)
    orch = _orchestrator(tmp_path)

    assert orch.runs() == []

    _, first = orch.launch(model="qwen", inferencer="dflash", suites=["humaneval"])
    orch.join(timeout=5)
    _, second = orch.launch(model="qwen", inferencer="dflash", suites=["mbpp"])
    orch.join(timeout=5)

    tracked = orch.runs()
    assert [state.id for state in tracked] == [first["run_id"], second["run_id"]]


def test_join_is_a_noop_before_any_run(tmp_path):
    orch = _orchestrator(tmp_path)
    # no background thread has started yet; join must return without error.
    orch.join(timeout=0.1)


def test_launch_action_rejects_non_dict_body(tmp_path):
    orch = _orchestrator(tmp_path)
    code, payload = launch.launch_action(orch, ["not", "a", "dict"])
    assert code == 400
    assert "JSON object" in payload["error"]


def test_handler_serves_real_get_over_http(tmp_path):
    orch = _orchestrator(tmp_path)
    server = launch.make_server(orch, port=0)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        req = urllib.request.Request(f"http://{host}:{port}/nope", method="GET")
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(req)
        assert excinfo.value.code == 404
        assert json.loads(excinfo.value.read())["error"] == "not found"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_launch_response_leaks_no_secrets(tmp_path, monkeypatch):
    calls: list[str] = []
    _patch_backends(monkeypatch, calls=calls)
    orch = _orchestrator(tmp_path)

    _, payload = orch.launch(model="qwen", inferencer="dflash", suites=["humaneval"])
    orch.join(timeout=5)

    serialized = json.dumps(payload).lower()
    for secret in ("api_key", "apikey", "authorization", "secret", "token", ".env", str(tmp_path)):
        assert secret.lower() not in serialized
