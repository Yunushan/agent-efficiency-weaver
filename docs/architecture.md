# Architecture

`profiles/` and `catalogs/` are the source of truth. The small Python adapter renders staged Kilo and Abacus MCP configurations. `.agents/skills/` holds portable, on-demand instructions. Add telemetry adapters only when documented interfaces exist and source permissions allow it.

No implicit tool installation, credentials, production writes or benchmark claims.
