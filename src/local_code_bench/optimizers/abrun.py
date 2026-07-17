"""Bare-vs-proxied A/B agent runs (Epic-13, Story 13.3-001).

Runs the same agent task twice under identical config — once straight at the
engine (**bare**) and once through a registered context-optimization proxy
(**proxied**) — persisting both as condition-tagged records in one JSONL file,
then renders a side-by-side report of tokens prefilled, end-to-end latency, and
task success. A token saving is never reported in isolation: the report always
pairs it with the correctness delta, and states explicitly when the correctness
signal is unavailable rather than implying parity.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from ..config import AgentConfig, OptimizerConfig
from ..engine_provenance import EngineProvenance
from ..tasks import BenchmarkTask
from . import manager
from .manager import OptimizerError

BARE = "bare"
PROXIED = "proxied"

# The proxy's own OpenAI-compatible listen URL (same shape the lifecycle
# manager uses for an engine's base URL); Anthropic clients expect the origin
# without the /v1 suffix and append their own path.
_PROXY_OPENAI_TEMPLATE = "http://127.0.0.1:{port}/v1"
_PROXY_ANTHROPIC_TEMPLATE = "http://127.0.0.1:{port}"


def proxied_agent(agent: AgentConfig, proxy: OptimizerConfig) -> AgentConfig:
    """Derive the proxied-condition agent: same config, base URL(s) swapped.

    Only the already-configured base-URL fields are redirected at the proxy's
    listen port, so everything else (model, sandbox, timeouts) stays identical
    between conditions. An agent with no configurable base URL (e.g. a codex
    entry routed via its own profile) cannot be pointed at the proxy — refuse
    rather than silently run both conditions against the engine.
    """

    updates: dict[str, str] = {}
    if agent.base_url:
        updates["base_url"] = _PROXY_OPENAI_TEMPLATE.format(port=proxy.port)
    if agent.anthropic_base_url:
        updates["anthropic_base_url"] = _PROXY_ANTHROPIC_TEMPLATE.format(port=proxy.port)
    if not updates:
        raise OptimizerError(
            f"agent '{agent.name}' has no configurable base URL "
            "(base_url/anthropic_base_url) — it cannot be routed through a proxy"
        )
    return replace(agent, **updates)


def run_ab_comparison(
    *,
    agent: AgentConfig,
    tasks: Sequence[BenchmarkTask],
    proxy: OptimizerConfig,
    state_dir: str | Path,
    result_path: Path,
    runner: Callable[..., dict[str, object]],
    progress: Callable[[str], None] | None = None,
    engine_provenance: EngineProvenance | None = None,
) -> list[dict[str, object]]:
    """Run every task bare then proxied, tagging each persisted record.

    Both conditions share the agent config, suite tasks, engine provenance, and
    result file; only the target base URL differs. The proxy must already be
    running and healthy (13.2 lifecycle) — its status supplies the upstream the
    proxied condition actually traversed, captured in each proxied record so a
    proxied run is never silently compared as if it were bare.
    """

    status = manager.status(proxy, state_dir)
    if not status.running:
        raise OptimizerError(
            f"proxy '{proxy.name}' is not running — start it first (13.2 lifecycle)"
        )
    if not status.healthy:
        raise OptimizerError(f"proxy '{proxy.name}' is running but not healthy")

    proxied = proxied_agent(agent, proxy)
    proxy_tag: dict[str, object] = {
        "name": proxy.name,
        "port": proxy.port,
        "upstream": status.upstream,
        "command": list(proxy.start),
    }
    conditions: tuple[tuple[str, AgentConfig, dict[str, object]], ...] = (
        (BARE, agent, {"condition": BARE, "proxy_in_path": False}),
        (PROXIED, proxied, {"condition": PROXIED, "proxy_in_path": True, "proxy": proxy_tag}),
    )

    def _condition_progress(index: int, condition: str) -> Callable[[str], None] | None:
        if progress is None:
            return None
        emit = progress

        def report(message: str) -> None:
            emit(f"[{index}/{total}] {condition}: {message}")

        return report

    records: list[dict[str, object]] = []
    total = len(tasks)
    for index, task in enumerate(tasks, start=1):
        for condition, condition_agent, tag in conditions:
            condition_progress = _condition_progress(index, condition)
            records.append(
                runner(
                    agent=condition_agent,
                    task=task,
                    result_path=result_path,
                    progress=condition_progress,
                    engine_provenance=engine_provenance,
                    record_extra={"optimization": tag},
                )
            )
    return records


def render_ab_report(
    records: Sequence[Mapping[str, object]],
    *,
    agent_name: str | None = None,
) -> str:
    """Render the side-by-side bare/proxied comparison table.

    Every rendered saving is paired with the task-success row, and a condition
    whose correctness signal is missing renders "unverified" instead of a
    passed count — a lossy-but-faster outcome is always visible.
    """

    by_condition: dict[str, list[Mapping[str, object]]] = {BARE: [], PROXIED: []}
    for record in records:
        optimization = record.get("optimization")
        condition = optimization.get("condition") if isinstance(optimization, dict) else None
        if condition not in by_condition:
            raise ValueError(
                "record is missing its optimization.condition tag — refusing to "
                "compare untagged runs"
            )
        by_condition[condition].append(record)

    bare = _summarize(by_condition[BARE])
    proxied = _summarize(by_condition[PROXIED])

    lines = ["A/B optimization report — bare vs proxied"]
    header_bits = []
    if agent_name:
        header_bits.append(f"agent: {agent_name}")
    proxy_line = _proxy_line(by_condition[PROXIED])
    if proxy_line:
        header_bits.append(proxy_line)
    if header_bits:
        lines.append(" | ".join(header_bits))
    lines.append("")
    lines.append(_row("metric", BARE, PROXIED, "delta"))
    lines.append(_row("task success", _success_cell(bare), _success_cell(proxied),
                      _success_delta(bare, proxied)))
    lines.append(_row("tokens prefilled", _tokens_cell(bare), _tokens_cell(proxied),
                      _tokens_delta(bare, proxied)))
    lines.append(_row("latency (s)", f"{bare.latency:.2f}", f"{proxied.latency:.2f}",
                      f"{proxied.latency - bare.latency:+.2f}s"))

    unverified = max(bare.unverified, proxied.unverified)
    total = max(bare.total, proxied.total)
    if unverified:
        lines.append("")
        lines.append(
            f"correctness unverified for {unverified} of {total} task(s) — "
            "success counts and deltas cover scored tasks only"
        )
    return "\n".join(lines)


@dataclass(frozen=True)
class _ConditionSummary:
    total: int
    passed: int
    verified: int
    unverified: int
    prefill: int | None
    latency: float


def _summarize(records: Sequence[Mapping[str, object]]) -> _ConditionSummary:
    passed = sum(1 for record in records if record.get("passed") is True)
    verified = sum(1 for record in records if isinstance(record.get("passed"), bool))
    prefill_counts = [_prefill_tokens(record) for record in records]
    prefill = None
    if prefill_counts and all(count is not None for count in prefill_counts):
        prefill = sum(count for count in prefill_counts if count is not None)
    latency = sum(
        value for record in records
        if isinstance(value := record.get("wall_time_seconds"), int | float)
    )
    return _ConditionSummary(
        total=len(records),
        passed=passed,
        verified=verified,
        unverified=len(records) - verified,
        prefill=prefill,
        latency=float(latency),
    )


def _prefill_tokens(record: Mapping[str, object]) -> int | None:
    """Engine-side request size for one record: prompt/input plus cache tokens."""

    usage = record.get("usage")
    if not isinstance(usage, dict):
        return None
    base = None
    for key in ("prompt_tokens", "input_tokens"):
        value = usage.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool):
            base = int(value)
            break
    if base is None:
        return None
    for key in ("cache_creation_input_tokens", "cache_read_input_tokens"):
        value = usage.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool):
            base += int(value)
    return base


def _success_cell(summary: _ConditionSummary) -> str:
    if not summary.verified:
        return "unverified"
    return f"{summary.passed}/{summary.verified} passed"


def _success_delta(bare: _ConditionSummary, proxied: _ConditionSummary) -> str:
    if not bare.verified or not proxied.verified:
        return "unverified"
    return f"{proxied.passed - bare.passed:+d} task(s)"


def _tokens_cell(summary: _ConditionSummary) -> str:
    return str(summary.prefill) if summary.prefill is not None else "unavailable"


def _tokens_delta(bare: _ConditionSummary, proxied: _ConditionSummary) -> str:
    if bare.prefill is None or proxied.prefill is None:
        return "unavailable"
    if bare.prefill == 0:
        return "n/a"
    reduction = (proxied.prefill - bare.prefill) / bare.prefill * 100.0
    return f"{reduction:+.1f}%"


def _proxy_line(proxied_records: Sequence[Mapping[str, object]]) -> str | None:
    for record in proxied_records:
        optimization = record.get("optimization")
        proxy = optimization.get("proxy") if isinstance(optimization, dict) else None
        if isinstance(proxy, dict):
            return (
                f"proxy in path (proxied condition): {proxy.get('name')} "
                f"(port {proxy.get('port')}, upstream {proxy.get('upstream')})"
            )
    return None


def _row(label: str, bare: str, proxied: str, delta: str) -> str:
    return f"{label:<18} {bare:<16} {proxied:<16} {delta}"
