# Release readiness

The current release is an offline Python CLI that validates registries and Skills, stages reviewable configuration artifacts, and reports benchmark data. Its local release-gate score measures completion of the six required offline gates in CONTRIBUTING.md. Each gate has equal weight: `floor(100 * passed_gates / 6)`. A score of 100 requires all six to pass on unchanged source inputs with every pinned development tool present. It is not an unrestricted production-readiness score.

| Gate | Required result |
|---|---|
| Registry and Skills | Catalogs, profiles, integrations, OS registry, and every portable Skill validate |
| Unit tests | The complete offline unittest suite passes |
| Lint | Ruff reports no errors |
| Types | The strict mypy pipeline passes for the runtime package |
| Build | Both source and wheel distributions build without dependency isolation |
| Distribution | Archive allowlists and canonical code/asset/build-input hashes match; isolated installed-package smoke checks pass outside the checkout |

The score is scoped to the host OS, architecture, and Python version recorded in the report. Platform-dependent test skips remain skips, and do not establish support for the skipped platform or privilege-dependent operation. The configured CI matrix is Windows, macOS, and Linux on Python 3.11–3.13; its configuration alone is not evidence of successful runs.

## Run and inspect

After explicitly installing `.[dev]` into your chosen development environment, run:

```bash
python scripts/check-readiness.py
```

The checker uses its current Python interpreter, overrides `PYTHONPATH` with the selected source tree, and prevents package-index access in gate subprocesses. It never installs development tools, launches vendor agents, enables services, or writes live vendor settings. The distribution gate installs only the built project into temporary isolated environments without network access.

The JSON output includes the individual gate commands and exit statuses, unit-test run/skip counts, exact tool versions, relative input hashes, host metadata, and evidence limitations. Empty or missing unittest discovery cannot pass. Subprocess logs and private absolute paths are omitted. A missing or mismatched development tool produces an unknown (`null`) score and leaves the gates unrun. Source changes during the run also invalidate the score. Failed, errored, or timed-out gates earn no passing credit and return a nonzero process exit status. The default timeout is 1,200 seconds per gate and can be changed with `--timeout`. Use `--root` to select another source tree intentionally.

Save output under the ignored `reports/generated/` directory if a local record is needed. Rerun any failed command from the JSON report directly for diagnostics. Rebuild and rerun after changing source; an earlier report's hashes describe that earlier input state. Hashes provide change detection and are not signatures or certification.

## Separate evidence requirements

Production acceptance requires reviewing the current release contracts as well as passing tests: explicit/default-off MCP selection, registry-sourced identities and labeled gaps, owned-hash staging safety, validated portable Skills, unknown benchmark coverage, bundled installed resources, and documentation that matches actual behavior. The relevant evidence is in the registry-derived documentation tests, adapter goldens, staged-write and CLI tests, benchmark validation tests, and isolated distribution checks. A passing summary does not replace checking that coverage against the intended release.

For a release decision covering all primary CI targets, require successful runs of the exact candidate on Windows, macOS, and Linux across Python 3.11, 3.12, and 3.13. One Windows run cannot establish this matrix, and skipped platform tests remain unverified on that host. If the candidate is uncommitted, older remote CI results cannot stand in for its validation. Commit and push still require explicit authorization under AGENTS.md.

A full local gate score does not fill the roadmap's pending runtime evidence. Installed-client compatibility requires a separate manual record for the product, surface, version, OS variant, architecture, exact action, artifact hashes, and observed configuration-consumption result. BSD, Solaris, Android, and iOS workflows retain the conditional or remote limitations in the compatibility registry. Missing native access remains pending.

The score also makes no claim about measured token/cost savings, vendor entitlements, SSO, compliance certification, or autonomous execution. Synthetic benchmark fixtures test the data pipeline only. A deployment or vendor compatibility decision must consider the relevant external evidence independently of this repository score.

## Recorded core CLI CI evidence

On 2026-10-26, [CI run 40278860777](https://github.com/Yunushan/agent-efficiency-weaver/actions/runs/40278860777) passed all nine Windows/macOS/Linux × Python 3.11/3.12/3.13 jobs for source commit `cf5bcdc2b1bacc03df355f24e3f6e6ab55998dad`, package version `0.1.0`. Each job runs the six required gates, including original-wheel and sdist-rebuilt-wheel installation and smoke checks outside the checkout. This is an immutable evidence checkpoint; later candidates require their own successful CI run.

The Python 3.12 job logs record these concrete environments and test results:

| Job | Observed OS / image | Python | Unit result |
|---|---|---|---|
| [Windows](https://github.com/Yunushan/agent-efficiency-weaver/actions/runs/40278860777/job/121427436763) | Windows Server 2025, build 10.0.26100; `windows-2025-vs2026` | 3.12.10 | 246 tests; passed with 2 skips |
| [macOS](https://github.com/Yunushan/agent-efficiency-weaver/actions/runs/40278860777/job/121427436925) | macOS 26.6.2, build 25G83; `macos-26-arm64` | 3.12.10 | 246 tests; passed with 4 skips |
| [Linux](https://github.com/Yunushan/agent-efficiency-weaver/actions/runs/40278860777/job/121427436500) | Ubuntu 24.04.5 LTS; `ubuntu-24.04` | 3.12.15 | 246 tests; passed with 3 skips |

Skipped cases remain skipped. The macOS harness resolves only its own allocated temporary roots so system temp aliases do not weaken the writer's link-refusal tests. These records prove the core CLI checks on those CI images, not installed vendor-client consumption, native-device operation, every OS variant or architecture, or measured agent efficiency.
