"""Tests for the unified dashboard's Optimizers panel (Epic-13, Story 13.4-001).

The proxy layer is a distinct "Optimizers" section composed into the Epic-09
page — never mixed into the Inferencers one. It is read-only: lifecycle stays on
the CLI (`bench optimizer start/stop`).
"""

from __future__ import annotations

import json

from local_code_bench import unified_dashboard as ud
from local_code_bench.cli import main
from local_code_bench.config import OptimizerConfig
from local_code_bench.optimizers.manager import OptimizerStatus


def _proxy_cfg(name: str = "headroom", port: int = 8787) -> OptimizerConfig:
    return OptimizerConfig(
        name=name,
        detect_kind="binary",
        detect_target=name,
        port=port,
        health_url="http://127.0.0.1:{port}/v1/models",
        start=(name, "proxy", "--port", "{port}", "{upstream}"),
        url="https://headroom-docs.vercel.app/docs",
    )


def _proxy_status(cfg: OptimizerConfig, *, running: bool = True) -> OptimizerStatus:
    return OptimizerStatus(
        name=cfg.name,
        installed=True,
        running=running,
        pid=4321 if running else None,
        port=cfg.port,
        upstream="http://127.0.0.1:8080/v1" if running else None,
        healthy=running,
        detail="running and healthy" if running else "not running",
    )


def _ctx(optimizer_configs: dict[str, OptimizerConfig] | None = None) -> ud.DashboardContext:
    return ud.DashboardContext(
        configs={},
        state_dir="/state",
        optimizer_configs=optimizer_configs or {},
        optimizer_state_dir="/opt-state",
    )


def test_optimizers_action_reports_status_rows(monkeypatch) -> None:
    cfg = _proxy_cfg()
    seen: list[tuple[str, str]] = []

    def fake_status(config, state_dir):
        seen.append((config.name, str(state_dir)))
        return _proxy_status(config)

    monkeypatch.setattr("local_code_bench.optimizers.manager.status", fake_status)

    status_code, payload = ud.optimizers_action(_ctx({"headroom": cfg}))

    assert status_code == 200
    assert seen == [("headroom", "/opt-state")]
    (row,) = payload["optimizers"]
    assert row["name"] == "headroom"
    assert row["installed"] is True
    assert row["running"] is True
    assert row["healthy"] is True
    assert row["port"] == 8787
    assert row["upstream"] == "http://127.0.0.1:8080/v1"
    assert row["url"] == "https://headroom-docs.vercel.app/docs"


def test_optimizers_action_empty_registry_yields_no_rows() -> None:
    status_code, payload = ud.optimizers_action(_ctx())

    assert status_code == 200
    assert payload == {"optimizers": []}


def test_api_optimizers_route_serves_json(monkeypatch) -> None:
    cfg = _proxy_cfg()
    monkeypatch.setattr(
        "local_code_bench.optimizers.manager.status",
        lambda config, state_dir: _proxy_status(config, running=False),
    )

    resp = ud.handle_request("GET", "/api/optimizers", _ctx({"headroom": cfg}))

    assert resp.status == 200
    payload = json.loads(resp.body)
    assert payload["optimizers"][0]["name"] == "headroom"
    assert payload["optimizers"][0]["running"] is False


def test_page_has_distinct_optimizers_section() -> None:
    body = ud.render_page()

    assert 'data-section="optimizers"' in body
    assert 'id="section-optimizers"' in body
    # Distinct panel: the optimizers section starts only after the inferencers
    # section has closed — never nested inside it.
    inferencers_start = body.index('id="section-inferencers"')
    inferencers_end = body.index("</section>", inferencers_start)
    assert body.index('id="section-optimizers"') > inferencers_end


def test_load_optimizers_safe_degrades_to_empty_registry(tmp_path) -> None:
    messages: list[str] = []

    configs = ud._load_optimizers_safe(tmp_path / "missing.yaml", messages.append)

    assert configs == {}
    assert messages and "optimizers" in messages[0]


def test_dashboard_command_passes_optimizer_registry(monkeypatch) -> None:
    captured: dict = {}

    def fake_serve(config, state_dir, result_paths, **kwargs) -> None:
        captured.update(kwargs, config=config)

    monkeypatch.setattr("local_code_bench.unified_dashboard.serve_dashboard", fake_serve)

    exit_code = main(
        [
            "dashboard",
            "--optimizers",
            "custom/optimizers.yaml",
            "--optimizer-state-dir",
            "/tmp/opt-state",
            "--input",
            "results/a.jsonl",
        ]
    )

    assert exit_code == 0
    assert captured["optimizers_path"] == "custom/optimizers.yaml"
    assert captured["optimizer_state_dir"] == "/tmp/opt-state"
