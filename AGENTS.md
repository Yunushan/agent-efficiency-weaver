# Agent instructions

- The mission is to optimize **cost and tokens per successfully completed task** without compromising quality or security.
- Prefer small, targeted changes and deterministic validation commands over repeated open-ended LLM calls.
- Never include credentials, private company code, real production logs or private benchmark fixtures in commits.
- MCP access is opt-in and least privilege; never enable production writes automatically.
- Treat benchmark missing token/cost data as **unknown**, not zero.
- Separate synthetic fixture examples from measured benchmarking evidence.
- Avoid platform claims that were not verified with installed agent versions.
- Require unit tests for Python changes and validate every portable Skill.
- Do not silently overwrite users' existing Kilo or Abacus settings.
