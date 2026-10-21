"""Paired baseline/candidate comparison, never conflate unmatched tasks."""
from .config import WeaverError
from .report import summarize

def compare_runs(runs, *, agent, source, baseline="baseline", candidate="optimized"):
    def select(variant):
        return {(r["task_id"], r["run_id"]): r for r in runs
                if r["agent"] == agent and r["source"] == source and r["variant"] == variant}
    base, trial = select(baseline), select(candidate)
    if not base or not trial:
        raise WeaverError("Both baseline and candidate attempts are required")
    if set(base) != set(trial):
        raise WeaverError("Baseline and candidate task/run IDs are not identical")
    baseline_summary = summarize(list(base.values()))["groups"][0]
    candidate_summary = summarize(list(trial.values()))["groups"][0]
    regressions = [str(k) for k in sorted(base) if base[k]["status"] == "passed" and trial[k]["status"] != "passed"]
    recoveries = [str(k) for k in sorted(base) if base[k]["status"] != "passed" and trial[k]["status"] == "passed"]
    def improvement(name):
        x, y = baseline_summary[name], candidate_summary[name]
        return (x - y) / x * 100 if x is not None and y is not None and x != 0 else None
    return {"agent": agent, "source": source, "baseline": baseline, "candidate": candidate,
            "matched_attempts": len(base), "regressions": regressions, "recoveries": recoveries,
            "success_rate_delta_pp": (candidate_summary["success_rate"] - baseline_summary["success_rate"]) * 100,
            "tokens_per_success_reduction_pct": improvement("tokens_per_successful_task"),
            "cost_per_success_reduction_pct": improvement("cost_per_successful_task_usd"),
            "p95_duration_reduction_pct": improvement("p95_duration_seconds"),
            "baseline_summary": baseline_summary, "candidate_summary": candidate_summary,
            "disclaimer": "A paired difference is not automatically statistically significant."}
