---
name: rke2-troubleshooting
description: "Investigate RKE2 Kubernetes cluster failures while minimizing token use and avoiding unauthorized production changes."
---

# rke2 troubleshooting

1. Determine affected namespace, workload and timeframe. 2. Collect concise `kubectl get`, `describe` and filtered log output. 3. Examine scheduling, probes, networking, storage and control-plane health. 4. Separate hypotheses from evidence. 5. Do not mutate production workloads or reveal Kubernetes Secrets.
