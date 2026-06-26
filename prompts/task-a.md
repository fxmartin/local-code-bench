# Task A — Write a log-line classifier CLI in Go

Write a single self-contained Go program (`package main`) that implements a small
command-line log-line **classifier**. The program has no network access and must be
fully deterministic.

## Behaviour

- `classify <file>` reads the log file at `<file>`, tags each line by its severity
  level, and prints a count per level to standard output.
- `classify --json <file>` emits the result as structured JSON instead of a count
  table.
- `classify --filter <level> <file>` prints only the lines whose severity matches
  `<level>`.

## Severity rules

Classify each line by the **first** matching rule, evaluated in this order
(matching is case-sensitive):

1. The line contains `ERROR` or `FATAL` → `error`
2. Otherwise, the line contains `WARN` → `warn`
3. Otherwise, the line contains `INFO` → `info`
4. Otherwise → `unknown`

## Exit codes

- `0` — success
- `1` — the input file does not exist
- `2` — bad arguments (unknown flag, missing file argument, unknown `--filter`
  level)

Return only the complete Go source inside a single fenced ```go code block.
