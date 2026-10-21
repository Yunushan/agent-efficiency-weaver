---
name: token-efficient-retrieval
description: "Retrieve minimal code and documentation context for an AI agent without sacrificing task correctness."
---

# token efficient retrieval

1. Search filenames and symbols first. 2. Read only relevant lines/files; cap tool output. 3. Use ripgrep or AST/symbol tools where available. 4. Expand context on evidence of missing dependencies. 5. Compare completion and error rates as well as tokens.
