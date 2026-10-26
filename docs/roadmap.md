# Roadmap

## Current foundation

The foundation includes the 31-product registry (25 agents, 4 providers, and 2 hybrids), strict validation, requested-scope aliases and evidence reports, documented MCP adapters, portable instruction and skill bundles, custom-agent definition routes, offline model snapshots and reasoning validation, legacy OpenRouter handoffs, OS diagnostics, safe staging, bundled resources, and offline quality gates. Native staged exports exist only for implemented surfaces listed in [compatibility.md](compatibility.md); every other route is manual, unsupported, unknown, or a documented vendor gap. Mapping all 23 requested groups does not resolve their unknown capabilities or pending runtime evidence.

The documented OpenHands CLI 1.16.0/SDK 1.28.1 binding now supports an inheriting custom-agent file and remote/stdio MCP fragments, with model/provider/reasoning setup kept in a manual CLI handoff. The custom-agent inventory contains 18 native, 8 manual, 11 unknown, and 5 unsupported routes; stdio documentation covers 23 products and 27 matching surfaces. Cursor IDE, Claude Code CLI and Qoder CLI have constrained primary OS documentation, while all 165 requested runtime capability records remain pending. Provider-reported zero context limits remain unknown for configuration; no usable limit is invented.

## Near term

- Record real installed-client and native-device runtime evidence per product, surface, version, OS variant, and architecture without turning simulations into verification.
- Run each exact candidate through the primary-host CI matrix; the recorded earlier 20-product and initial 31-product checkpoints remain evidence for their own source commits.
- Extend documented adapters only after first-party schema confirmation and golden fixture tests.
- Preserve remote HTTPS versus local stdio transport distinctions, structured argument validation, explicit profile selection, and the remote-only API request boundary as vendor formats evolve.
- Extend custom-agent and native model-configuration routes with product-specific fixture tests, explicit reasoning semantics, and permission behavior; keep unresolved routes visible.
- Maintain provider discovery interfaces and paginated snapshot normalization as catalogs evolve, without promising an exhaustive static model inventory or inferring account access.
- Keep reviewed unknown-interface conclusions and documented OS constraints current. Reviewed sources close missing-documentation records; installed configuration consumption and inference still need their own evidence.
- Complete opt-in BSD, Solaris, Android, and iOS host/device verification where suitable isolated environments are available.
- Improve diff and merge guidance for staged fragments and preserve explicit human review.
- Continue resource, distribution, and least-privilege checks across Windows, macOS, and Linux on Python 3.11 through 3.13.

## Explicitly out of scope

- Native mobile applications and phone-local coding runtimes.
- An autonomous agent execution harness, production service, IDE extension, or telemetry scraper.
- No vendor entitlement, SSO, or compliance certification.
- Live configuration writes, automatic agent or MCP installation, credential discovery, or production-write automation.
- Advertised numerical token or cost improvements without reproducible measured evidence.
- Universal native support claims for platforms or products lacking recorded runtime evidence.
