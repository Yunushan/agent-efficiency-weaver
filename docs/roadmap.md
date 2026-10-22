# Roadmap

## Current foundation

The registry, strict validation, documented MCP adapters, portable instruction and skill bundles, OpenRouter provider handoff, OS diagnostics, safe staging writer, bundled resources, and offline quality gates form the current foundation. Native staged exports exist only for implemented surfaces listed in [compatibility.md](compatibility.md); every other route is manual, unsupported, unknown, or a documented vendor gap.

## Near term

- Record real installed-client and native-device runtime evidence per product, surface, version, OS variant, and architecture without turning simulations into verification.
- Extend documented adapters only after first-party schema confirmation and golden fixture tests.
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
