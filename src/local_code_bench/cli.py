"""Command-line entrypoint for the benchmark harness."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from local_code_bench.agents import completed_agent_pairs, run_codex_task
from local_code_bench.config import ConfigError, ModelConfig, load_agents, load_models
from local_code_bench.inferencers.manager import InferencerError
from local_code_bench.leaderboard import generate_leaderboard
from local_code_bench.metrics import CompletionMeasurement, capture_stream_metrics
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
        choices=["endpoint", "agent", "sweep", "leaderboard", "rescore"],
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    argv_list = list(sys.argv[1:] if argv is None else argv)
    if argv_list and argv_list[0] == "inferencer":
        return run_inferencer_command(argv_list[1:])

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        from local_code_bench import __version__

        print(__version__)
        return 0

    try:
        if args.mode == "leaderboard":
            inputs = [Path(item) for item in args.input or []]
            if not inputs:
                parser.error("--mode leaderboard requires --input")
            output = Path(args.output or "LEADERBOARD.md")
            generate_leaderboard(inputs, output)
            print(f"wrote {output}")
            return 0

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

    except (ConfigError, ProviderError, TaskLoadError, ValueError) as exc:
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


def run_inferencer_command(argv: Sequence[str]) -> int:
    """Dispatch `bench inferencer <subcommand>` (currently `dashboard`).

    Branched ahead of the flat `--mode` flow so every existing benchmark command
    stays backward compatible. Config/lifecycle failures print `bench: error: ...`
    to stderr and exit 2, consistent with the rest of the CLI.
    """

    parser = argparse.ArgumentParser(prog="bench inferencer")
    sub = parser.add_subparsers(dest="command", required=True)
    dashboard_parser = sub.add_parser("dashboard", help="serve the localhost web control panel")
    dashboard_parser.add_argument(
        "--config", default="configs/inferencers.yaml", help="path to inferencer YAML config"
    )
    dashboard_parser.add_argument(
        "--state-dir", default=".runtime/inferencers", help="directory for persisted server state"
    )
    dashboard_parser.add_argument("--host", default="127.0.0.1", help="bind host (localhost only)")
    dashboard_parser.add_argument("--port", type=int, default=8765, help="bind port")
    args = parser.parse_args(argv)

    if args.command == "dashboard":
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

    return 0


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
