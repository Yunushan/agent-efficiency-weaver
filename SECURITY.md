# Security policy

Do not commit secrets, API tokens, authentication files, private source code, real customer data or infrastructure credentials. Review all generated MCP permissions and destinations before use. Configuration rendering does not install or launch MCP services.

Avoid pointing write-enabled MCP tools at production Kubernetes, GitLab, cloud accounts or databases. Use disposable fixtures, explicit approvals and read-only access for infrastructure validation. Report security issues privately to repository maintainers instead of publishing exploit details.
