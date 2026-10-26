# Benchmark methodology

Use frozen task fixtures with known-good graders and an explicit execution timeout. Test the same task IDs and repeat IDs under baseline and candidate configurations, isolating environment and source revision.

Report completion, blockers, cost per success, tokens per success, latency and tool calls. Include all failed attempts in denominators and resource totals. Repeat runs enough to estimate variability. Do not interpret a single paired run as causal proof. Include confidence intervals and environment metadata before publishing comparative marketing claims.

A synthetic data source validates data-handling code only, not an AI coding agent.

Benchmark JSONL must use unique object keys, valid UTF-8, and finite nonnegative numeric metrics. Duplicate fields, nonstandard `NaN`/`Infinity`, unrepresentable numbers, and overflowing totals or comparison percentages fail with a controlled error. JSON reports and comparisons are complete JSON documents ending with a newline. Missing tokens or costs remain unknown; parser rejection never converts them to zero or silently drops an attempt.

`report` and `compare` write to standard output unless an explicit `--output` is provided. That option creates parent directories and writes or replaces an ordinary report file using normal filesystem semantics, including following links. Reports do not use the staged vendor-artifact manifest, ownership checks, or `--force`. Choose a dedicated report destination and review potentially private benchmark data before sharing it.
