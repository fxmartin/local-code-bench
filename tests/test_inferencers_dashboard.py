from __future__ import annotations

import json

import pytest

from local_code_bench.config import InferencerConfig
from local_code_bench.inferencers import dashboard, manager
from local_code_bench.inferencers.manager import InferencerError, InferencerStatus


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


def _configs() -> dict[str, InferencerConfig]:
    return {
        "dflash": _server_cfg("dflash", 8000),
        "turboquant": _server_cfg("turboquant", 8002),
        "lm-studio": _app_cfg(),
    }


def _status(name: str, *, lifecycle: str = "server", running: bool = False, healthy: bool = False,
            pid: int | None = None, port: int = 8000) -> InferencerStatus:
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


# ---------------------------------------------------------------------------
# status payload
# ---------------------------------------------------------------------------


def test_status_payload_lists_every_engine_with_safe_fields(monkeypatch):
    statuses = {
        "dflash": _status("dflash", running=True, healthy=True, pid=42, port=8000),
        "lm-studio": _status("lm-studio", lifecycle="app", running=True, healthy=True, port=1234),
    }
    monkeypatch.setattr(manager, "status_all", lambda configs, state_dir: statuses)

    code, payload = dashboard.status_action(_configs(), ".runtime")

    assert code == 200
    names = {row["name"] for row in payload["inferencers"]}
    assert names == {"dflash", "lm-studio"}
    row = next(r for r in payload["inferencers"] if r["name"] == "dflash")
    assert row == {
        "name": "dflash",
        "installed": True,
        "lifecycle": "server",
        "running": True,
        "pid": 42,
        "port": 8000,
        "healthy": True,
        "detail": "ok",
    }


def test_status_payload_never_leaks_secrets(monkeypatch):
    statuses = {"dflash": _status("dflash", running=True, pid=42)}
    monkeypatch.setattr(manager, "status_all", lambda configs, state_dir: statuses)

    _, payload = dashboard.status_action(_configs(), ".runtime")

    serialized = json.dumps(payload).lower()
    for secret in ("api_key", "apikey", "authorization", "secret", "token", ".env"):
        assert secret not in serialized


# ---------------------------------------------------------------------------
# start: exclusive flow + confirmation
# ---------------------------------------------------------------------------


def test_start_with_no_others_running_starts_immediately(monkeypatch):
    monkeypatch.setattr(
        manager, "status_all",
        lambda configs, state_dir: {name: _status(name) for name in configs},
    )
    started: list[str] = []
    monkeypatch.setattr(manager, "stop", lambda cfg, state_dir: pytest.fail("should not stop"))
    monkeypatch.setattr(
        manager, "start",
        lambda cfg, state_dir: started.append(cfg.name) or _status(cfg.name, running=True, healthy=True),
    )

    code, payload = dashboard.start_action("dflash", _configs(), ".runtime", confirm=False)

    assert code == 200
    assert started == ["dflash"]
    assert payload["started"]["name"] == "dflash"


def test_start_with_other_server_running_requires_confirmation(monkeypatch):
    statuses = {
        "dflash": _status("dflash"),
        "turboquant": _status("turboquant", running=True, healthy=True, pid=99, port=8002),
        "lm-studio": _status("lm-studio", lifecycle="app"),
    }
    monkeypatch.setattr(manager, "status_all", lambda configs, state_dir: statuses)
    monkeypatch.setattr(manager, "stop", lambda cfg, state_dir: pytest.fail("must not stop before confirm"))
    monkeypatch.setattr(manager, "start", lambda cfg, state_dir: pytest.fail("must not start before confirm"))

    code, payload = dashboard.start_action("dflash", _configs(), ".runtime", confirm=False)

    assert code == 409
    assert payload["needs_confirmation"] is True
    others = {row["name"] for row in payload["others"]}
    assert others == {"turboquant"}


def test_start_confirmed_stops_others_then_starts_target(monkeypatch):
    statuses = {
        "dflash": _status("dflash"),
        "turboquant": _status("turboquant", running=True, healthy=True, pid=99, port=8002),
    }
    monkeypatch.setattr(manager, "status_all", lambda configs, state_dir: statuses)
    calls: list[str] = []
    monkeypatch.setattr(manager, "stop", lambda cfg, state_dir: calls.append(f"stop:{cfg.name}"))
    monkeypatch.setattr(
        manager, "start",
        lambda cfg, state_dir: calls.append(f"start:{cfg.name}")
        or _status(cfg.name, running=True, healthy=True),
    )

    code, payload = dashboard.start_action("dflash", _configs(), ".runtime", confirm=True)

    assert code == 200
    assert calls == ["stop:turboquant", "start:dflash"]
    assert payload["started"]["name"] == "dflash"


def test_start_blocked_by_running_gui_app_and_never_quits_it(monkeypatch):
    statuses = {
        "dflash": _status("dflash"),
        "lm-studio": _status("lm-studio", lifecycle="app", running=True, healthy=True, port=1234),
    }
    monkeypatch.setattr(manager, "status_all", lambda configs, state_dir: statuses)
    monkeypatch.setattr(manager, "stop", lambda cfg, state_dir: pytest.fail("never force-quit a GUI app"))
    monkeypatch.setattr(manager, "start", lambda cfg, state_dir: pytest.fail("must not start past a GUI app"))

    code, payload = dashboard.start_action("dflash", _configs(), ".runtime", confirm=True)

    assert code == 409
    assert payload["error"] == "gui_running"
    assert {row["name"] for row in payload["others"]} == {"lm-studio"}


def test_start_unknown_engine_is_404(monkeypatch):
    monkeypatch.setattr(manager, "status_all", lambda configs, state_dir: {})

    code, payload = dashboard.start_action("nope", _configs(), ".runtime", confirm=True)

    assert code == 404
    assert "nope" in payload["error"]


def test_start_of_gui_target_is_refused(monkeypatch):
    monkeypatch.setattr(
        manager, "status_all", lambda configs, state_dir: {name: _status(name) for name in configs}
    )
    monkeypatch.setattr(manager, "start", lambda cfg, state_dir: pytest.fail("cannot start a GUI app"))

    code, payload = dashboard.start_action("lm-studio", _configs(), ".runtime", confirm=True)

    assert code == 400
    assert "lm-studio" in payload["error"]


def test_start_failure_surfaces_as_502(monkeypatch):
    monkeypatch.setattr(
        manager, "status_all", lambda configs, state_dir: {name: _status(name) for name in configs}
    )

    def _boom(cfg, state_dir):
        raise InferencerError("did not become healthy")

    monkeypatch.setattr(manager, "start", _boom)

    code, payload = dashboard.start_action("dflash", _configs(), ".runtime", confirm=True)

    assert code == 502
    assert "healthy" in payload["error"]


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


def test_stop_calls_manager_stop(monkeypatch):
    stopped: list[str] = []
    monkeypatch.setattr(manager, "stop", lambda cfg, state_dir: stopped.append(cfg.name))

    code, payload = dashboard.stop_action("dflash", _configs(), ".runtime")

    assert code == 200
    assert stopped == ["dflash"]
    assert payload["stopped"] == "dflash"


def test_stop_of_gui_target_is_refused(monkeypatch):
    monkeypatch.setattr(manager, "stop", lambda cfg, state_dir: pytest.fail("never quit a GUI app"))

    code, payload = dashboard.stop_action("lm-studio", _configs(), ".runtime")

    assert code == 400
    assert "lm-studio" in payload["error"]


# ---------------------------------------------------------------------------
# request routing
# ---------------------------------------------------------------------------


def test_get_root_serves_self_contained_page(monkeypatch):
    resp = dashboard.handle_request("GET", "/", _configs(), ".runtime")

    assert resp.status == 200
    assert resp.content_type.startswith("text/html")
    body = resp.body.decode()
    assert "<table" in body
    # self-contained: no external CDN/script references
    assert "cdn" not in body.lower()
    assert "//unpkg" not in body
    assert "https://" not in body


def test_get_api_status_returns_json(monkeypatch):
    monkeypatch.setattr(
        manager, "status_all", lambda configs, state_dir: {"dflash": _status("dflash", running=True)}
    )

    resp = dashboard.handle_request("GET", "/api/status", _configs(), ".runtime")

    assert resp.status == 200
    assert resp.content_type.startswith("application/json")
    payload = json.loads(resp.body)
    assert payload["inferencers"][0]["name"] == "dflash"


def test_post_start_passes_confirm_from_query(monkeypatch):
    seen: dict = {}

    def _fake_start(name, configs, state_dir, *, confirm, force=False):
        seen["name"] = name
        seen["confirm"] = confirm
        seen["force"] = force
        return 200, {"started": {"name": name}}

    monkeypatch.setattr(dashboard, "start_action", _fake_start)

    resp = dashboard.handle_request("POST", "/api/start?name=dflash&confirm=1", _configs(), ".runtime")

    assert resp.status == 200
    assert seen == {"name": "dflash", "confirm": True, "force": False}


def test_post_start_without_confirm_defaults_false(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(
        dashboard, "start_action",
        lambda name, configs, state_dir, *, confirm, force=False: seen.update(confirm=confirm) or (200, {}),
    )

    dashboard.handle_request("POST", "/api/start?name=dflash", _configs(), ".runtime")

    assert seen["confirm"] is False


def test_post_stop_routes_to_stop_action(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(
        dashboard, "stop_action",
        lambda name, configs, state_dir: seen.update(name=name) or (200, {"stopped": name}),
    )

    resp = dashboard.handle_request("POST", "/api/stop?name=dflash", _configs(), ".runtime")

    assert resp.status == 200
    assert seen["name"] == "dflash"


def test_unknown_route_is_404():
    resp = dashboard.handle_request("GET", "/nope", _configs(), ".runtime")

    assert resp.status == 404


# ---------------------------------------------------------------------------
# server binding
# ---------------------------------------------------------------------------


def test_make_server_binds_localhost_only():
    server = dashboard.make_server(_configs(), ".runtime", port=0)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()
