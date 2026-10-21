# Agent Efficiency Weaver

Portable, provider-independent **Agent Skills**, **MCP configuration adapters**, and reproducible **token/cost/quality benchmarks** for AI coding agents.

**Status:** v0.1 foundation. Kilo and Abacus MCP configurations are generated into staging paths, not installed automatically. This repository does **not** claim verified token savings or automatic proprietary-agent telemetry.

## Goals

- Share `AGENTS.md` instructions and `.agents/skills/` across compatible agents.
- Generate *opt-in*, reviewable MCP settings for Kilo and Abacus.
- Benchmark task success, tokens, paid cost and latency using normalized JSONL records.
- Keep private credentials, production write privileges and private code out of sample fixtures.
- Favor cost **per successful task**, not merely low total token usage.

## Getting started

Requires Python 3.11+; no runtime dependencies.

```bash
python -m pip install -e .
agentweaver validate
agentweaver render --platform kilo --profile efficient --output-dir generated
agentweaver render --platform abacus --profile efficient --output-dir generated
agentweaver render --platform kilo --profile balanced --enable context7 --output-dir generated
python -m unittest discover -s tests -v
```

Generated configurations are **staging artifacts**. Review and merge manually before applying them in any editor or agent. No MCP server is enabled by default.

## Scope and caveats

This project is a starter architecture, **not a complete Kilo/Abacus/VS Code/VSCodium integration**. It does not automatically ingest usage logs, verify third-party tool behavior, or deploy to production infrastructure. Synthetic benchmark examples must never be represented as real model evaluations.

## License

0BSD for original repository content. Third-party names, services, packages and dependencies retain their respective licenses.

See [AGENTS.md](AGENTS.md), [SECURITY.md](SECURITY.md), and [docs/architecture.md](docs/architecture.md).
