"""Normalized benchmark reporting. Missing usage/cost stays unknown."""
import html
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any

from .config import WeaverError

REQUIRED = ("task_id", "run_id", "agent", "variant", "source", "status")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(value: str) -> Any:
    raise ValueError("Non-finite JSON value")


def _finite_number(value: int | float) -> bool:
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _json_float(value: str) -> float:
    result = float(value)
    if not _finite_number(result):
        raise ValueError("Non-finite JSON value")
    return result


def read_runs(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    try:
        stream = path.open(encoding="utf-8")
        with stream:
            for lineno, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    row: Any = json.loads(line, object_pairs_hook=_unique_object,
                                          parse_constant=_invalid_constant, parse_float=_json_float)
                except (ValueError, RecursionError) as exc:
                    raise WeaverError(f"Line {lineno}: malformed JSON") from exc
                if not isinstance(row, dict) or any(not isinstance(row.get(k), str) or not row[k] for k in REQUIRED):
                    raise WeaverError(f"Line {lineno}: required string fields missing")
                if row["source"] not in ("synthetic", "measured") or row["status"] not in ("passed", "failed", "blocked"):
                    raise WeaverError(f"Line {lineno}: invalid source/status")
                for key in ("input_tokens", "output_tokens", "tool_calls"):
                    value = row.get(key)
                    if value is not None and (type(value) is not int or value < 0 or not _finite_number(value)):
                        raise WeaverError(f"Line {lineno}: bad {key}")
                for key in ("duration_seconds", "billed_cost_usd"):
                    value = row.get(key)
                    if value is not None and (type(value) not in (float, int) or not _finite_number(value) or value < 0):
                        raise WeaverError(f"Line {lineno}: bad {key}")
                identifier = tuple(row[k] for k in ("source", "agent", "variant", "task_id", "run_id"))
                if identifier in seen:
                    raise WeaverError(f"Line {lineno}: duplicate attempt")
                seen.add(identifier)
                rows.append(row)
    except (OSError, UnicodeError) as exc:
        raise WeaverError("Cannot read benchmark input as UTF-8") from exc
    if not rows:
        raise WeaverError("No benchmark rows found")
    return rows

def summarize(runs: list[dict[str, Any]]) -> dict[str, Any]:
    groups: defaultdict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in runs:
        groups[(row["source"], row["agent"], row["variant"])].append(row)
    summaries: list[dict[str, Any]] = []
    for (source, agent, variant), rows in sorted(groups.items()):
        total = len(rows)
        passed = sum(r["status"] == "passed" for r in rows)
        blocked = sum(r["status"] == "blocked" for r in rows)
        full_tokens = all(r.get("input_tokens") is not None and r.get("output_tokens") is not None for r in rows)
        full_costs = all(r.get("billed_cost_usd") is not None for r in rows)
        try:
            tokens: int | None = sum(r["input_tokens"] + r["output_tokens"] for r in rows) if full_tokens else None
            cost: float | None = sum(r["billed_cost_usd"] for r in rows) if full_costs else None
            duration = sorted(r["duration_seconds"] for r in rows if r.get("duration_seconds") is not None)
            median_duration = median(duration) if duration else None
        except OverflowError as exc:
            raise WeaverError("Benchmark aggregate exceeds finite numeric range") from exc
        if any(value is not None and not _finite_number(value) for value in (tokens, cost, median_duration)):
            raise WeaverError("Benchmark aggregate exceeds finite numeric range")
        summaries.append({
            "source": source, "agent": agent, "variant": variant,
            "attempts": total, "passed": passed, "blocked": blocked,
            "failed": total - passed - blocked, "success_rate": passed / total,
            "tokens_per_successful_task": tokens / passed if tokens is not None and passed else None,
            "cost_per_successful_task_usd": cost / passed if cost is not None and passed else None,
            "total_tokens": tokens, "total_billed_cost_usd": cost,
            "p95_duration_seconds": duration[max(0, math.ceil(0.95 * len(duration)) - 1)] if duration else None,
            "median_duration_seconds": median_duration,
            "token_coverage": f"{sum(r.get('input_tokens') is not None and r.get('output_tokens') is not None for r in rows)}/{total}",
            "cost_coverage": f"{sum(r.get('billed_cost_usd') is not None for r in rows)}/{total}"
        })
    return {"disclaimer": "Synthetic data is illustrative. Unknown cost/tokens are null, not zero.", "groups": summaries}

def _markdown_cell(value: str) -> str:
    text = html.escape(value, quote=True)
    text = "".join("\\" + character if character in "\\`*_[]()!~" else character for character in text)
    text = text.replace("|", "&#124;")
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")


def as_markdown(result: dict[str, Any]) -> str:
    lines = ["# Benchmark report", "", result["disclaimer"], "", "| Source | Agent | Variant | Passed | Token / success | Cost / success (USD) |", "|---|---|---|---:|---:|---:|"]
    for g in result["groups"]:
        tok = "N/A" if g["tokens_per_successful_task"] is None else f"{g['tokens_per_successful_task']:.1f}"
        cost = "N/A" if g["cost_per_successful_task_usd"] is None else f"{g['cost_per_successful_task_usd']:.4f}"
        source, agent, variant = (_markdown_cell(g[key]) for key in ("source", "agent", "variant"))
        lines.append(f"| {source} | {agent} | {variant} | {g['passed']}/{g['attempts']} | {tok} | {cost} |")
    return "\n".join(lines) + "\n"
