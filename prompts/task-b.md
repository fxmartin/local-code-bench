# Task B — Classify each log line (structured output only)

You are given a fixed log fixture below. Classify **every** line by its severity
level and return the result as JSON. Do not write any code — return only the
classification artifact.

## Severity rules

Classify each line by the **first** matching rule, evaluated in this order
(matching is case-sensitive):

1. The line contains `ERROR` or `FATAL` → `error`
2. Otherwise, the line contains `WARN` → `warn`
3. Otherwise, the line contains `INFO` → `info`
4. Otherwise → `unknown`

## Output format

Return a single JSON object that maps each 1-based line number (as a string key) to
its severity level, covering every line in the fixture and nothing else. Example
shape:

```json
{"1": "info", "2": "error", "3": "unknown"}
```

## Fixture

```
2024-01-01T00:00:00Z INFO service started
2024-01-01T00:00:01Z WARN disk usage at 85%
2024-01-01T00:00:02Z ERROR failed to open socket
2024-01-01T00:00:03Z DEBUG cache warm complete
2024-01-01T00:00:04Z FATAL out of memory
2024-01-01T00:00:05Z INFO request handled in 12ms
```

Return only the JSON object.
