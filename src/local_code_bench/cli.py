"""Command-line entrypoint for the benchmark harness."""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from local_code_bench.agents import completed_agent_pairs, run_codex_task
from local_code_bench.config import (
    ConfigError,
    InferencerConfig,
    ModelConfig,
    load_agents,
    load_inferencers,
    load_models,
)
from local_code_bench.inferencers import detect, manager
from local_code_bench.inferencers.manager import InferencerError, InferencerStatus
from local_code_bench.leaderboard import generate_leaderboard
from local_code_bench.metrics import CompletionMeasurement, capture_stream_metrics
from local_code_bench.opencode.invoke import OpenCodeOverrides, run_opencode
from local_code_bench.power import PowerSampler
from local_code_bench.provider import ChatRequest, ProviderError, provider_for_model
from local_code_bench.results import append_jsonl, new_run_path
from local_code_bench.rescore import rescore_endpoint_records
from local_code_bench.runner import run_endpoint_suite, select_models
from local_code_bench.sweep import CONTEXT_SIZES, run_sweep, summarize_sweep, sweep_prompts
from local_code_bench.tasks import TaskLoadError, limit_tasks, load_suite


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bench",
        description="Run coding-model benchmark tasks.",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="print the package version and exit",
    )
    parser.add_argument(
        "--config",
        default="configs/models.yaml",
        help="path to endpoint model YAML config",
    )
    parser.add_argument(
        "--model",
        help="configured model name to run",
    )
    parser.add_argument(
        "--prompt",
        help="single prompt to send to the selected model",
    )
    parser.add_argument(
        "--results-dir",
        default="results",
        help="directory for raw JSONL run output",
    )
    parser.add_argument(
        "--mode",
        choices=["endpoint", "agent", "sweep", "leaderboard", "rescore", "dashboard"],
        default="endpoint",
    )
    parser.add_argument(
        "--suite",
        choices=["humaneval", "mbpp", "canary", "humaneval-plus", "mbpp-plus"],
        help=(
            "benchmark suite to run (canary = curated HumanEval anchor subset; "
            "humaneval-plus/mbpp-plus = EvalPlus differential suites)"
        ),
    )
    parser.add_argument("--limit", type=int, help="limit benchmark tasks")
    parser.add_argument("--skip", help="comma-separated model names to skip")
    parser.add_argument(
        "--concurrency",
        type=int,
        help="override per-model in-flight requests for endpoint suite runs (keep 1 for local servers)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        help="override generation cap for endpoint suite runs",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        help="per-task sandbox scoring timeout in seconds (raise for large EvalPlus input sets)",
    )
    parser.add_argument(
        "--warmup",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="send a discarded warmup request per model before timing (avoids cold-start skew)",
    )
    parser.add_argument(
        "--power",
        action="store_true",
        help="record GPU/CPU power and energy via macOS powermetrics (needs passwordless sudo)",
    )
    parser.add_argument(
        "--context-sizes",
        help=(
            "comma-separated context token sizes for sweep mode "
            "(default 2000,8000,16000,24000; lower the top to stay out of swap)"
        ),
    )
    parser.add_argument("--resume", action="store_true", help="resume an existing JSONL run")
    parser.add_argument("--run-file", help="explicit JSONL run file for suite/resume modes")
    parser.add_argument("--cache-dir", default=".cache/benchmarks", help="benchmark dataset cache")
    parser.add_argument("--agent", help="configured agent name for agent mode")
    parser.add_argument("--agents-config", default="configs/agents.yaml", help="path to agents YAML")
    parser.add_argument("--input", nargs="*", help="input JSONL files for leaderboard/sweep summaries")
    parser.add_argument("--output", help="output file for generated leaderboard")
    parser.add_argument(
        "--serve",
        action="store_true",
        help="dashboard mode: serve a live localhost dashboard instead of writing static HTML",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="dashboard serve bind host (localhost only)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8770,
        help="dashboard serve bind port",
    )
    parser.add_argument(
        "--manage-inferencers",
        action="store_true",
        help="auto-start the inferencer a selected model declares (exclusively) before the run",
    )
    parser.add_argument(
        "--inferencers-config",
        default="configs/inferencers.yaml",
        help="path to inferencer registry YAML (used with --manage-inferencers)",
    )
    parser.add_argument(
        "--inferencer-state-dir",
        default=".runtime/inferencers",
        help="directory holding inferencer process state files",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="auto-confirm stopping other engines when auto-starting an inferencer",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="permit auto-start past a running GUI app (never force-quits it)",
    )

    subparsers = parser.add_subparsers(dest="command")
    inferencer = subparsers.add_parser(
        "inferencer",
        help="detect and control local inference engines",
        description="List, inspect, start, stop, and serve a dashboard for local inference engines.",
    )
    inferencer.add_argument(
        "action",
        choices=["list", "status", "start", "stop", "dashboard"],
        help="inferencer operation to perform",
    )
    inferencer.add_argument("name", nargs="?", help="engine name (required for start/stop)")
    inferencer.add_argument(
        "--config",
        default="configs/inferencers.yaml",
        help="path to inferencer YAML config",
    )
    inferencer.add_argument(
        "--state-dir",
        default=".runtime/inferencers",
        help="directory holding per-engine PID/state files",
    )
    inferencer.add_argument(
        "--watch",
        action="store_true",
        help="re-render the status table on an interval (status only)",
    )
    inferencer.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="seconds between refreshes when --watch is set",
    )
    inferencer.add_argument(
        "--yes",
        action="store_true",
        help="auto-confirm stopping other running engines on start",
    )
    inferencer.add_argument(
        "--force",
        action="store_true",
        help="start past a running GUI app instead of refusing",
    )
    inferencer.add_argument(
        "--host",
        default="127.0.0.1",
        help="dashboard bind host (localhost only)",
    )
    inferencer.add_argument(
        "--port",
        type=int,
        default=8765,
        help="dashboard bind port",
    )

    dashboard = subparsers.add_parser(
        "dashboard",
        help="serve the unified Inferencers / Results / Run dashboard",
        description=(
            "Serve one localhost page composing the inferencer control panel, the live "
            "results view, and the benchmark Run section (supersedes 'inferencer dashboard')."
        ),
    )
    dashboard.add_argument(
        "--config",
        default="configs/inferencers.yaml",
        help="path to inferencer registry YAML",
    )
    dashboard.add_argument(
        "--models",
        default="configs/models.yaml",
        help="path to model registry YAML (populates the Run launcher and powers the Chat section)",
    )
    dashboard.add_argument(
        "--suites",
        default="configs/suites.yaml",
        help="path to optional custom-suite registry YAML for the Run launcher",
    )
    dashboard.add_argument(
        "--state-dir",
        default=".runtime/inferencers",
        help="directory holding per-engine PID/state files",
    )
    dashboard.add_argument(
        "--input",
        nargs="*",
        help="result JSONL files for the Results section (default: every results-dir/*.jsonl)",
    )
    dashboard.add_argument(
        "--results-dir",
        default="results",
        help="directory scanned for result JSONL when --input is omitted",
    )
    dashboard.add_argument(
        "--host",
        default="127.0.0.1",
        help="dashboard bind host (localhost only)",
    )
    dashboard.add_argument(
        "--port",
        type=int,
        default=8765,
        help="dashboard bind port",
    )

    opencode = subparsers.add_parser(
        "opencode",
        help="run the OpenCode local-model benchmark (Task A coding + Task B rule-following)",
        description=(
            "Send the fixed prompts/task-a.md and prompts/task-b.md to a chosen local "
            "model under identical, deterministic conditions and capture raw output, "
            "timing, tokens, and provenance (quant, provider, engine, mode, seed)."
        ),
    )
    opencode.add_argument("--model", required=False, help="configured model name to run")
    opencode.add_argument(
        "--mode",
        dest="opencode_mode",
        choices=["default", "thinking"],
        default="default",
        help="run mode: 'thinking' merges the model's thinking_extra_body (GPT-OSS lesson)",
    )
    opencode.add_argument("--endpoint", help="override the model base URL (OpenAI-compatible /v1)")
    opencode.add_argument(
        "--engine",
        help="select a known engine's default /v1 endpoint (dflash, ollama, lm-studio, ...)",
    )
    opencode.add_argument("--quant", help="override the logged quant string (e.g. IQ3_XXS)")
    opencode.add_argument(
        "--provider",
        help="override the logged quant provider (the Unsloth-vs-Bartowski lesson)",
    )
    opencode.add_argument(
        "--seed",
        type=int,
        default=0,
        help="seed logged for reproducibility (default 0)",
    )
    opencode.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="sampling temperature, pinned to 0 by default for determinism",
    )
    opencode.add_argument(
        "--max-tokens",
        type=int,
        help="cap per-task generation (defaults to the model's configured max_tokens)",
    )
    opencode.add_argument(
        "--prompts-dir",
        default="prompts",
        help="directory holding task-a.md and task-b.md",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        from local_code_bench import __version__

        print(__version__)
        return 0

    try:
        if getattr(args, "command", None) == "inferencer":
            return run_inferencer_command(args)

        if getattr(args, "command", None) == "dashboard":
            return run_unified_dashboard_command(args)

        if getattr(args, "command", None) == "opencode":
            return run_opencode_command(args)

        if args.mode == "leaderboard":
            inputs = [Path(item) for item in args.input or []]
            if not inputs:
                parser.error("--mode leaderboard requires --input")
            output = Path(args.output or "LEADERBOARD.md")
            generate_leaderboard(inputs, output)
            print(f"wrote {output}")
            return 0

        if args.mode == "dashboard":
            return run_dashboard_mode(args, parser)

        if args.mode == "rescore":
            if not args.input or not args.suite:
                parser.error("--mode rescore requires --input and --suite")
            output = Path(args.output or "results/rescored.jsonl")
            tasks = limit_tasks(load_suite(args.suite, cache_dir=args.cache_dir), args.limit)
            summary = rescore_endpoint_records(
                input_path=Path(args.input[0]),
                output_path=output,
                tasks=tasks,
            )
            print(f"rescored={summary} output={output}")
            return 0

        if args.mode == "sweep":
            if args.input:
                from local_code_bench.results import read_jsonl

                records = [record for item in args.input for record in read_jsonl(item)]
                print(summarize_sweep(records))
                return 0
            question = args.prompt or "Write a Python function that returns 1."
            sweep_sizes = _parse_context_sizes(args.context_sizes) if args.context_sizes else CONTEXT_SIZES
            if args.model:
                models = select_models(load_models(args.config), include=args.model, skip=args.skip)
                _maybe_manage_inferencers(args, models)
                result_path = (
                    Path(args.run_file) if args.run_file else new_run_path(args.results_dir, prefix="sweep")
                )
                with PowerSampler(enabled=args.power) as sampler:
                    summary = run_sweep(
                        models=models,
                        question=question,
                        result_path=result_path,
                        sizes=sweep_sizes,
                    )
                _emit_power(sampler, result_path, models=models, requested=args.power)
                print(f"sweep={summary} results={result_path}")
                return 0
            for size, prompt in sweep_prompts(question, sweep_sizes):
                print(f"{size}\t{len(prompt.split())}\t{prompt[:80]}")
            return 0

        if args.mode == "agent":
            if not args.agent or not args.suite:
                parser.error("--mode agent requires --agent and --suite")
            result_path = Path(args.run_file) if args.run_file else new_run_path(args.results_dir, prefix=args.agent)
            agents = load_agents(args.agents_config)
            if args.agent not in agents:
                available = ", ".join(sorted(agents))
                raise ConfigError(f"unknown agent '{args.agent}'. Available agents: {available}")
            tasks = limit_tasks(load_suite(args.suite, cache_dir=args.cache_dir), args.limit)
            done = completed_agent_pairs(result_path) if args.resume else set()
            for index, task in enumerate(tasks, start=1):
                if (args.agent, task.task_id) in done:
                    print(f"[{index}/{len(tasks)}] {args.agent} {task.task_id}: skipped", flush=True)
                    continue
                run_codex_task(
                    agent=agents[args.agent],
                    task=task,
                    result_path=result_path,
                    progress=lambda message, index=index, total=len(tasks): print(
                        f"[{index}/{total}] {message}",
                        flush=True,
                    ),
                )
            print(f"agent={args.agent} tasks={len(tasks)} results={result_path}")
            return 0

        if args.suite:
            models = select_models(load_models(args.config), include=args.model, skip=args.skip)
            _maybe_manage_inferencers(args, models)
            tasks = limit_tasks(load_suite(args.suite, cache_dir=args.cache_dir), args.limit)
            result_path = Path(args.run_file) if args.run_file else new_run_path(args.results_dir, prefix=args.suite)
            with PowerSampler(enabled=args.power) as sampler:
                summary = run_endpoint_suite(
                    models=models,
                    tasks=tasks,
                    result_path=result_path,
                    resume=args.resume,
                    progress=lambda message: print(message, flush=True),
                    max_tokens=args.max_tokens,
                    concurrency_override=args.concurrency,
                    timeout_seconds=args.timeout,
                    warmup=args.warmup,
                )
            _emit_power(sampler, result_path, models=models, requested=args.power)
            print(f"suite={args.suite} results={result_path} summary={summary}")
            return 0

    except (ConfigError, ProviderError, TaskLoadError, ValueError, InferencerError) as exc:
        print(f"bench: error: {exc}", file=sys.stderr)
        return 2

    if args.model or args.prompt:
        if not args.model or not args.prompt:
            parser.error("--model and --prompt must be provided together")
        try:
            result_path, measurement = run_single_prompt(
                config_path=Path(args.config),
                model_name=args.model,
                prompt=args.prompt,
                results_dir=Path(args.results_dir),
            )
        except (ConfigError, ProviderError) as exc:
            print(f"bench: error: {exc}", file=sys.stderr)
            return 2

        print(
            "model={model} prompt_tokens={prompt_tokens} completion_tokens={completion_tokens} "
            "ttft={ttft} latency={latency:.3f}s results={path}".format(
                model=args.model,
                prompt_tokens=measurement.prompt_tokens,
                completion_tokens=measurement.completion_tokens,
                ttft=_format_optional_seconds(measurement.ttft_seconds),
                latency=measurement.latency_seconds,
                path=result_path,
            )
        )
        return 0

    parser.print_help()
    return 0


def run_dashboard_mode(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Generate or serve the results dashboard from stored result JSONL.

    Static generation is the default path (write a self-contained HTML file);
    ``--serve`` instead starts a localhost HTTP server that re-reads the result
    files on every request, so a still-running benchmark's appended records show
    up on refresh. Both share one interpretation of the JSONL via the dashboard
    model. Missing ``--input`` is an argparse usage error (exit 2), consistent
    with the other input-driven modes.
    """

    inputs = [Path(item) for item in args.input or []]
    if not inputs:
        parser.error("--mode dashboard requires --input")

    if args.serve:
        from local_code_bench.dashboard_server import serve_dashboard

        serve_paths: list[str | Path] = [path for path in inputs]
        serve_dashboard(
            serve_paths,
            host=args.host,
            port=args.port,
            progress=lambda message: print(message, flush=True),
        )
        return 0

    from local_code_bench.dashboard import generate_dashboard

    output = Path(args.output or "results/dashboard.html")
    generate_dashboard(inputs, output)
    print(f"wrote {output}")
    return 0


def run_unified_dashboard_command(args: argparse.Namespace) -> int:
    """Serve the Epic-09 unified dashboard (Inferencers / Results / Run) on localhost.

    Composes the existing inferencer control panel and live results view under one
    page. The Results section reads either the explicit ``--input`` JSONL files or,
    by default, every ``*.jsonl`` under ``--results-dir``. Config/lifecycle failures
    surface as ``bench: error: ...`` on stderr with exit 2, like the rest of the CLI.
    """

    from local_code_bench.unified_dashboard import serve_dashboard

    try:
        serve_dashboard(
            args.config,
            args.state_dir,
            _resolve_dashboard_inputs(args),
            models_path=args.models,
            results_dir=args.results_dir,
            suites_path=args.suites,
            host=args.host,
            port=args.port,
            progress=lambda message: print(message, flush=True),
        )
    except (ConfigError, InferencerError) as exc:
        print(f"bench: error: {exc}", file=sys.stderr)
        return 2
    return 0


def run_opencode_command(args: argparse.Namespace) -> int:
    """Drive the OpenCode benchmark for one model (Story 10.1-001).

    Resolves the configured model, layers the CLI provenance/endpoint overrides on
    top, and invokes both fixed task prompts deterministically. Missing/unknown
    models raise ``ConfigError`` so the shared outer handler reports them on stderr
    with exit 2, like the rest of the CLI.
    """

    if not args.model:
        raise ConfigError("opencode requires --model")
    models = load_models(args.config)
    try:
        model = models[args.model]
    except KeyError as exc:
        available = ", ".join(sorted(models)) or "(none)"
        raise ConfigError(f"unknown model '{args.model}'. Available models: {available}") from exc

    overrides = OpenCodeOverrides(
        endpoint=args.endpoint,
        engine=args.engine,
        quant=args.quant,
        provider=args.provider,
    )
    result_path, records = run_opencode(
        model=model,
        overrides=overrides,
        mode=args.opencode_mode,
        prompts_dir=args.prompts_dir,
        results_dir=args.results_dir,
        seed=args.seed,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        progress=lambda message: print(message, flush=True),
    )
    print(
        f"opencode model={model.name} mode={args.opencode_mode} "
        f"tasks={len(records)} results={result_path}"
    )
    return 0


def _resolve_dashboard_inputs(args: argparse.Namespace) -> list[str | Path]:
    """Pick the Results-section JSONL: explicit ``--input``, else results-dir/*.jsonl."""

    if args.input:
        return [Path(item) for item in args.input]
    results_dir = Path(args.results_dir)
    if results_dir.is_dir():
        return sorted(results_dir.glob("*.jsonl"))
    return []


def run_inferencer_command(args: argparse.Namespace) -> int:
    """Dispatch `bench inferencer <action>`; the manager enforces one-active.

    `dashboard` serves the localhost web control panel; `list`/`status`/`start`/
    `stop` drive engine lifecycle. Config/lifecycle failures surface as
    `bench: error: ...` on stderr with exit 2, consistent with the rest of the CLI.
    """

    if args.action == "dashboard":
        from local_code_bench.inferencers.dashboard import serve_dashboard

        try:
            serve_dashboard(
                args.config,
                args.state_dir,
                host=args.host,
                port=args.port,
                progress=lambda message: print(message, flush=True),
            )
        except (ConfigError, InferencerError) as exc:
            print(f"bench: error: {exc}", file=sys.stderr)
            return 2
        return 0

    configs = load_inferencers(args.config)

    if args.action == "list":
        _print_inferencer_list(configs)
        return 0

    if args.action == "status":
        if args.watch:
            _watch_status(configs, args.state_dir, args.interval)
        else:
            _print_status_table(manager.status_all(configs, args.state_dir), configs)
        return 0

    # start / stop both need a named engine.
    cfg = _select_inferencer(configs, args.name, args.action)

    def progress(message: str) -> None:
        print(message, flush=True)

    if args.action == "stop":
        manager.stop(cfg, args.state_dir, progress=progress)
        print(f"stopped {cfg.name}")
        return 0

    status = manager.start_exclusive(
        cfg,
        configs,
        args.state_dir,
        confirm=_make_confirm(assume_yes=args.yes),
        force=args.force,
        progress=progress,
    )
    print(f"started {status.name}: {status.detail}")
    return 0


def _select_inferencer(
    configs: dict[str, InferencerConfig], name: str | None, action: str
) -> InferencerConfig:
    if not name:
        raise ConfigError(f"inferencer {action} requires an engine name")
    try:
        return configs[name]
    except KeyError as exc:
        available = ", ".join(sorted(configs)) or "(none)"
        raise ConfigError(f"unknown inferencer '{name}'. Available: {available}") from exc


def _make_confirm(*, assume_yes: bool) -> Callable[[list[InferencerStatus]], bool]:
    """Build the stdin y/N prompt the CLI injects into `start_exclusive`.

    `--yes` auto-confirms; a non-interactive stdin defaults to no so an unattended
    run never silently stops another engine.
    """

    def confirm(others: list[InferencerStatus]) -> bool:
        if assume_yes:
            return True
        if not sys.stdin.isatty():
            return False
        names = ", ".join(st.name for st in others)
        reply = input(f"Stop running engine(s) [{names}] to start exclusively? [y/N] ")
        return reply.strip().lower() in {"y", "yes"}

    return confirm


_MANUAL_INSTALL_NOTE = (
    "Note: the harness never installs engines — installation is manual. "
    "Install an engine yourself from its URL above, then it is detected here."
)


def _print_inferencer_list(configs: dict[str, InferencerConfig]) -> None:
    rows = [("ENGINE", "INSTALLED", "LIFECYCLE", "PORT", "URL")]
    for name, cfg in configs.items():
        installed = "yes" if detect.is_installed(cfg) else "no"
        rows.append((name, installed, cfg.lifecycle, str(cfg.port), cfg.url or "-"))
    _print_rows(rows)
    print(_MANUAL_INSTALL_NOTE)


def _print_status_table(
    statuses: dict[str, InferencerStatus],
    configs: dict[str, InferencerConfig] | None = None,
) -> None:
    configs = configs or {}
    rows = [("ENGINE", "INSTALLED", "RUNNING", "HEALTHY", "PID", "URL", "DETAIL")]
    for st in statuses.values():
        cfg = configs.get(st.name)
        rows.append(
            (
                st.name,
                "yes" if st.installed else "no",
                "yes" if st.running else "no",
                "yes" if st.healthy else "no",
                str(st.pid) if st.pid is not None else "-",
                (cfg.url if cfg and cfg.url else "-"),
                st.detail,
            )
        )
    _print_rows(rows)
    print(_MANUAL_INSTALL_NOTE)


def _watch_status(
    configs: dict[str, InferencerConfig], state_dir: str, interval: float
) -> None:
    """Re-render the status table on `interval`, clearing the screen with ANSI codes.

    No curses dependency: `\\033[2J\\033[H` clears and homes the cursor each tick.
    Ctrl-C exits cleanly.
    """

    try:
        while True:
            sys.stdout.write("\033[2J\033[H")
            _print_status_table(manager.status_all(configs, state_dir), configs)
            sys.stdout.flush()
            time.sleep(interval)
    except KeyboardInterrupt:
        print()


def _print_rows(rows: list[tuple[str, ...]]) -> None:
    widths = [max(len(row[col]) for row in rows) for col in range(len(rows[0]))]
    for row in rows:
        print("  ".join(cell.ljust(widths[col]) for col, cell in enumerate(row)))


def run_single_prompt(
    *,
    config_path: Path,
    model_name: str,
    prompt: str,
    results_dir: Path,
) -> tuple[Path, CompletionMeasurement]:
    models = load_models(config_path)
    try:
        model = models[model_name]
    except KeyError as exc:
        available = ", ".join(sorted(models)) or "(none)"
        raise ConfigError(f"unknown model '{model_name}'. Available models: {available}") from exc

    provider = provider_for_model(model)
    measurement = capture_stream_metrics(
        provider.stream_chat(ChatRequest(prompt=prompt, temperature=0.0)),
        prompt,
    )
    result_path = new_run_path(results_dir, prefix=model.name)
    append_jsonl(
        result_path,
        {
            "run_mode": "endpoint",
            "model": model.name,
            "provider_type": model.type,
            "model_id": model.model_id,
            "pinned_revision": model.pinned_revision,
            "prompt": prompt,
            "raw_response": measurement.response,
            "metrics": {
                "ttft_seconds": measurement.ttft_seconds,
                "latency_seconds": measurement.latency_seconds,
                "prefill_tokens_per_second": measurement.prefill_tokens_per_second,
                "decode_tokens_per_second": measurement.decode_tokens_per_second,
            },
            "tokens": {
                "prompt": measurement.prompt_tokens,
                "completion": measurement.completion_tokens,
                "estimated": measurement.token_counts_estimated,
            },
        },
    )
    return result_path, measurement


def _maybe_manage_inferencers(args: argparse.Namespace, models: list[ModelConfig]) -> None:
    """Opt-in: bring up each selected model's declared inferencer exclusively.

    Strictly gated on `--manage-inferencers`; without it the default "assume the
    server is already up" path is untouched. The injected `confirm` reuses the same
    mutual-exclusion rule as every other surface, so exactly one engine stays active.
    """

    if not args.manage_inferencers:
        return

    declared = [model for model in models if model.inferencer is not None]
    if not declared:
        return

    configs = load_inferencers(args.inferencers_config)
    confirm = _make_inferencer_confirm(args)
    for model in declared:
        if model.inferencer not in configs:
            available = ", ".join(sorted(configs)) or "(none)"
            raise ConfigError(
                f"model '{model.name}' declares unknown inferencer "
                f"'{model.inferencer}'. Available: {available}"
            )
        manager.start_exclusive(
            configs[model.inferencer],
            configs,
            args.inferencer_state_dir,
            confirm=confirm,
            force=args.force,
            progress=lambda message: print(message, flush=True),
        )


def _make_inferencer_confirm(
    args: argparse.Namespace,
) -> Callable[[list[manager.InferencerStatus]], bool]:
    """Build the y/N confirmation used before stopping other engines.

    `--yes` auto-confirms; a non-interactive stdin defaults to no so an unattended
    run never silently force-stops a server it cannot prompt about.
    """

    def confirm(others: list[manager.InferencerStatus]) -> bool:
        if args.yes:
            return True
        if not sys.stdin.isatty():
            return False
        names = ", ".join(status.name for status in others)
        reply = input(f"Stop running engine(s) {names} to start exclusively? [y/N] ")
        return reply.strip().lower() in {"y", "yes"}

    return confirm


def _parse_context_sizes(raw: str) -> tuple[int, ...]:
    sizes: list[int] = []
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        try:
            value = int(token)
        except ValueError as exc:
            raise ValueError(f"invalid --context-sizes value: {token!r}") from exc
        if value <= 0:
            raise ValueError("--context-sizes values must be positive integers")
        sizes.append(value)
    if not sizes:
        raise ValueError("--context-sizes must list at least one positive integer")
    return tuple(sizes)


def _emit_power(
    sampler: PowerSampler,
    result_path: Path,
    *,
    models: list[ModelConfig],
    requested: bool,
) -> None:
    summary = sampler.result()
    if summary.available:
        record = summary.as_record()
        names = [model.name for model in models]
        record["models"] = names
        if len(names) == 1:
            # A single-model run (the local sweep case) can attribute power directly.
            record["model"] = names[0]
        append_jsonl(result_path, record)
        print(
            "power: avg_gpu={a}W max_gpu={m}W avg_combined={c}W energy={e}J over {d}s".format(
                a=summary.avg_gpu_w,
                m=summary.max_gpu_w,
                c=summary.avg_combined_w,
                e=summary.energy_j,
                d=summary.duration_s,
            ),
            flush=True,
        )
    elif requested:
        print(
            "power: powermetrics produced no samples (needs macOS + passwordless sudo, "
            "or run the whole command under sudo)",
            file=sys.stderr,
            flush=True,
        )


def _format_optional_seconds(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    return f"{seconds:.3f}s"


if __name__ == "__main__":
    raise SystemExit(main())
