---
name: gitlab-ci-review
description: "Review GitLab CE CI configuration for correctness, safety, runner isolation and economical execution."
---

# gitlab ci review

1. Inspect relevant `.gitlab-ci.yml` and included templates. 2. Detect overbroad privileges, exposed secrets, unbounded jobs, bad caching and unsafe deployment gates. 3. Suggest minimal changes with tests and evidence. 4. Do not deploy or change production runners without approval.
