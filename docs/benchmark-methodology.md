# Benchmark methodology

Use frozen task fixtures with known-good graders and an explicit execution timeout. Test the same task IDs and repeat IDs under baseline and candidate configurations, isolating environment and source revision.

Report completion, blockers, cost per success, tokens per success, latency and tool calls. Include all failed attempts in denominators and resource totals. Repeat runs enough to estimate variability. Do not interpret a single paired run as causal proof. Include confidence intervals and environment metadata before publishing comparative marketing claims.

A synthetic data source validates data-handling code only, not an AI coding agent.
