"""Story 16.2-001 — every section restyled onto the token system.

Contract under test:

* The token layer carries the locked status semantics: ``--danger`` (a dark
  red, dual-valued per scheme like the accent) is reserved for failures and
  destructive actions; pass/ok/warn stay monochrome and are distinguished by
  glyph + weight, so no state relies on color alone (the squint test).
* The shared primitives in ``BASE_CSS`` express those semantics once — status
  text glyphs, shape-distinct status dots, consistent hover/active/focus/
  disabled button states, accent-carried primary emphasis (``.act``), a
  ``.danger`` variant for destructive controls, and a ``.progress`` live-status
  line (the 12.6-003 tier move).
* Every page consumes the shared classes instead of per-section ad-hoc rules:
  the duplicated ``#…-err`` / ``#warnings`` color overrides are gone, error
  lines carry ``class="err"``, warning items carry ``class="warn"``, and
  destructive controls carry ``class="danger"``. The JS keeps its existing
  status class names (``up``/``down``/``pass``/``fail``/``warn``) — only the
  CSS meaning changed.
"""

from __future__ import annotations

from local_code_bench import dashboard, dashboard_server, theme, unified_dashboard
from local_code_bench.dashboard_model import DataQualityWarning
from local_code_bench.inferencers import dashboard as inferencers_dashboard

# --------------------------------------------------------------------------- #
# Locked status semantics in the token layer
# --------------------------------------------------------------------------- #


def test_danger_token_is_defined_and_dual_valued() -> None:
    assert "--danger:" in theme.TOKENS_CSS
    # Like --accent, one red per scheme so it holds contrast on both anchors.
    assert theme.DANGER in theme.TOKENS_CSS
    assert theme.DANGER_DARK in theme.TOKENS_CSS
    assert f"light-dark({theme.DANGER}, {theme.DANGER_DARK})" in theme.TOKENS_CSS


def test_err_fg_resolves_to_danger() -> None:
    assert "--err-fg: var(--danger);" in theme.TOKENS_CSS


def test_pass_ok_warn_stay_monochrome() -> None:
    # pass/ok/warn resolve through neutral text tokens, never the red.
    assert "--ok-fg: var(--text-muted);" in theme.TOKENS_CSS
    assert "--warn-fg: var(--text);" in theme.TOKENS_CSS


# --------------------------------------------------------------------------- #
# Glyph + weight — state survives the no-color squint test
# --------------------------------------------------------------------------- #


def test_status_text_carries_glyphs() -> None:
    assert 'content: "✓ "' in theme.BASE_CSS  # pass / ok
    assert 'content: "✕ "' in theme.BASE_CSS  # fail / err / bad
    assert 'content: "! "' in theme.BASE_CSS  # warn


def test_status_text_carries_weight() -> None:
    pass_rule = next(line for line in theme.BASE_CSS.splitlines() if line.startswith(".pass, .ok"))
    warn_rule = next(line for line in theme.BASE_CSS.splitlines() if line.startswith("p.warn"))
    assert "font-weight: 600" in pass_rule
    assert "font-weight: 600" in warn_rule


def test_status_dots_are_distinct_shapes_not_color_fills() -> None:
    # up = filled, down = hollow, warn = half — distinguishable without color.
    assert 'content: "●"' in theme.BASE_CSS
    assert 'content: "○"' in theme.BASE_CSS
    assert 'content: "◐"' in theme.BASE_CSS
    dot_rules = [line for line in theme.BASE_CSS.splitlines() if line.lstrip().startswith(".dot")]
    assert dot_rules, "expected .dot rules in BASE_CSS"
    for rule in dot_rules:
        assert "background" not in rule, f"color-only dot fill survives: {rule}"


# --------------------------------------------------------------------------- #
# Interactive states — accent focus/primary, danger destructive, progress
# --------------------------------------------------------------------------- #


def test_buttons_have_active_state() -> None:
    assert "button:active:not(:disabled)" in theme.BASE_CSS


def test_primary_action_buttons_carry_accent() -> None:
    act_rule = next(line for line in theme.BASE_CSS.splitlines() if line.startswith("button.act "))
    assert "var(--accent)" in act_rule


def test_destructive_buttons_carry_danger() -> None:
    danger_rule = next(
        line for line in theme.BASE_CSS.splitlines() if line.startswith("button.danger ")
    )
    assert "var(--danger)" in danger_rule


def test_progress_primitive_uses_accent() -> None:
    progress_rules = [line for line in theme.BASE_CSS.splitlines() if line.startswith(".progress")]
    assert progress_rules and any("var(--accent)" in rule for rule in progress_rules)


# --------------------------------------------------------------------------- #
# Unified page — shared classes instead of per-section rules
# --------------------------------------------------------------------------- #


def test_unified_error_lines_use_shared_err_class() -> None:
    page = unified_dashboard.render_page()
    assert 'id="inf-err" class="err"' in page
    assert 'id="chat-err" class="err"' in page
    assert 'id="run-err" class="err"' in page
    # The duplicated per-section color rules are gone — one .err rule in BASE_CSS.
    assert "#inf-err {" not in page
    assert "#chat-err {" not in page
    assert "#warnings { color" not in page


def test_unified_warning_items_stamp_shared_warn_class() -> None:
    assert 'className = "warn"' in unified_dashboard.render_page()


def test_unified_destructive_controls_carry_danger_class() -> None:
    page = unified_dashboard.render_page()
    assert 'class="act danger" id="modal-confirm"' in page
    assert 'class="act danger" id="tier-apply"' in page
    assert 'class="act danger" data-stop' in page
    assert 'class="act danger" data-demote' in page
    # Promote copies onto fast storage — primary, not destructive.
    assert 'class="act danger" data-promote' not in page


def test_unified_nav_active_tab_carries_accent() -> None:
    page = unified_dashboard.render_page()
    rule = next(line for line in page.splitlines() if "nav button.active" in line)
    assert "var(--accent)" in rule


def test_tier_move_progress_line_uses_shared_progress_class() -> None:
    # 12.6-003 live progress: the status line gains .progress while a move
    # runs (accent emphasis) and drops it when the move settles.
    page = unified_dashboard.render_page()
    assert 'classList.add("progress")' in page
    assert 'classList.remove("progress")' in page


# --------------------------------------------------------------------------- #
# The two smaller pages follow the same pattern
# --------------------------------------------------------------------------- #


def test_inferencer_panel_uses_shared_classes() -> None:
    page = inferencers_dashboard.render_page()
    assert 'id="err" class="err"' in page
    assert "#err {" not in page
    assert 'class="danger" data-stop' in page
    assert 'class="danger" id="modal-confirm"' in page


def test_live_results_page_warnings_use_shared_warn_class() -> None:
    page = dashboard_server.render_page()
    assert "#warnings { color" not in page
    assert 'className = "warn"' in page


def test_static_dashboard_warning_items_use_shared_warn_class() -> None:
    warning = DataQualityWarning(source="run.jsonl", message="bad line", line=2)
    section = dashboard._warnings_section((warning,))
    assert '<li class="warn">' in section
    assert ".warnings li { color" not in dashboard._PAGE_CSS
