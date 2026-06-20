# local-code-bench — benchmark local, cloud, and Codex coding runs

## Project Context

`local-code-bench` is a Python CLI benchmark harness for comparing coding model
setups on FX's Apple Silicon machine. It has two benchmark surfaces:

- **Endpoint mode**: direct OpenAI-compatible `/v1/chat/completions` calls against
  local MLX servers and cloud providers, plus the Anthropic API baseline.
- **Agent mode**: Codex CLI runs through `codex exec`, scored against the same task
  suites where practical.

The goal is to identify the fastest usable local coding setup, quantify the gap to
cloud options, and measure Codex as a first-class coding-agent baseline.

## Tech Stack

- Python, managed with `uv`
- CLI-only application, no framework
- Tests with `pytest`; lint with `ruff`

## Development Rules

- Use TDD for behavior changes when practical.
- Keep changes scoped to the story being implemented.
- Run `uv run pytest` and `uv run ruff check .` before committing code changes.
- Use `rg`/`fd`/`bat` for repo inspection.
- Never commit secrets or raw benchmark outputs from `results/`.

## Story Source of Truth

- `REQUIREMENTS.md` defines product requirements and release scope.
- `docs/STORIES.md` is the story index.
- `docs/stories/` contains epic-level story definitions and progress.
- Link implementation work to story IDs, for example `06.1-002`.

## Codex-Specific Expectations

- Prefer `codex exec` for scriptable Codex agent runs.
- Use explicit sandbox settings; do not use dangerous bypass flags for benchmark runs.
- Capture final output and command metadata so Codex agent results are reproducible.
- Keep endpoint-mode and agent-mode results distinct in JSONL and leaderboard output.
