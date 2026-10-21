# Benchmarks

Use JSONL with required fields `task_id`, `run_id`, `agent`, `variant`, `source`, and `status`. Allowed status values: passed, failed, blocked. Optional numeric fields: `input_tokens`, `output_tokens`, `billed_cost_usd`, `duration_seconds`, `tool_calls`.

`source: synthetic` is illustrative only. Use `measured` for reproducible real executions with documented provenance.
