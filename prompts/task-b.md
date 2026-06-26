You are classifying log lines by severity.

Apply the FIRST matching rule, evaluated top to bottom. Matching is
**case-sensitive** — only the exact upper-case tokens count.

1. If the line contains `ERROR` or `FATAL` → `error`
2. Otherwise, if it contains `WARN` → `warn`
3. Otherwise, if it contains `INFO` → `info`
4. Otherwise → `unknown`

Below is the input log. Each line is prefixed with its 1-based line number
followed by `: `.

{{FIXTURE}}

Return ONLY a single JSON object that maps every line number (as a string key)
to its severity level, for example:

{"1": "info", "2": "warn"}

Do not emit any code, prose, or explanation — just the JSON object.
