# local-code-bench

Benchmark harness for local, cloud, and agentic coding models on Apple Silicon.

## Development

Install the project and development tools:

```bash
uv sync
```

Run the test suite:

```bash
uv run pytest
```

Show the current benchmark CLI stub:

```bash
uv run bench --help
```

## Endpoint Mode

Endpoint models are configured in `configs/models.yaml`. Add a new OpenAI-compatible
backend by adding another `models` entry with a unique `name`, endpoint `base_url`,
`model_id`, pinned revision label, and input/output prices per 1k tokens.

Run one prompt against a configured model:

```bash
uv run bench --model local-example --prompt "Write a Python function that adds two numbers."
```

The command streams `/v1/chat/completions`, measures TTFT, latency, token counts,
prefill tok/s, and decode tok/s, then writes one raw JSONL record under `results/`.
If an endpoint omits usage data, token counts are estimated locally and flagged in
the record.

Run a benchmark suite against selected endpoint models:

```bash
uv run bench --suite humaneval --model local-example --limit 10
uv run bench --suite mbpp --skip openrouter-glm-4.6 --run-file results/mbpp.jsonl --resume
```

Use `OPENROUTER_API_KEY` for the OpenRouter entries and `ANTHROPIC_API_KEY` for
the Anthropic baseline. API keys are read from the environment and are not written
to result records. Local MLX servers are configured as OpenAI-compatible endpoints;
`scripts/bring-up-local.sh dflash` and `scripts/bring-up-local.sh turboquant`
print the expected manual server commands.

## Agent Mode

Codex agent targets live in `configs/agents.yaml`. A bounded run materializes each
task into an isolated workspace, invokes `codex exec` with an explicit sandbox,
then scores the resulting `solution.py` with the same tests as endpoint runs:

```bash
uv run bench --mode agent --agent codex --suite humaneval --limit 3
```

## Leaderboard And Sweep

Generate a Markdown leaderboard from stored JSONL:

```bash
uv run bench --mode leaderboard --input results/run.jsonl --output LEADERBOARD.md
```

Generate deterministic sweep prompts or summarize stored sweep records:

```bash
uv run bench --mode sweep --prompt "Return 1"
uv run bench --mode sweep --input results/sweep.jsonl
```
