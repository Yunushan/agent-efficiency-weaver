# Agent instructions

- The mission is to optimize **cost and tokens per successfully completed task** without compromising quality or security.
- Prefer small, targeted changes and deterministic, offline validation commands over repeated open-ended LLM calls.
- Never include credentials, private company code, real production logs, private benchmark fixtures, or generated evidence containing private paths in commits.
- MCP access is opt-in and least privilege; generated artifacts are default-off and never enable production writes automatically.
- Treat benchmark missing token/cost data as **unknown**, not zero, and keep synthetic fixture examples separate from measured benchmarking evidence.
- Treat registry documentation evidence, fixture tests, and runtime checks as separate. Do not claim installed-version or native-device support without recorded evidence for the product, surface, version, OS variant, and architecture.
- Keep product and surface IDs sourced from `catalogs/integrations.json`; do not infer an exporter from a similar vendor name or describe a manual/unknown route as native support.
- Update owned documentation with the actual registry, CLI, migration, evidence, and limitation behavior. Do not add numerical performance claims, entitlement/SSO/compliance certification, native mobile, or autonomous execution claims.
- Preserve staged-artifact safety: `--force` only replaces owned unchanged hashes, links/junctions and traversal are refused, and hashes are not signatures or atomic bundle guarantees.
- Require unit tests for Python changes, validate every portable Skill, and run the documented gates before reporting completion.
- Do not silently overwrite users' existing Kilo, Abacus, or other vendor settings, and do not commit, push, install agents, or enable services without explicit authorization.
