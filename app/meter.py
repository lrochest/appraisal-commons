"""Token meter and spending limits.

Prices: OpenAI standard tier, USD per 1M tokens (same table as lecture 11).
OpenAI reports input_tokens including cached tokens, so the uncached slice is
input - cached, billed at the full input rate.

Three limits stop runaway spend:
  1. MAX_OUTPUT_TOKENS_PER_CALL caps every single model response.
  2. MAX_TOOL_ROUNDS caps how many times one agent may loop through tools.
  3. A per-session and a daily USD budget; once spent, no more model calls.
Every model call is appended to data/usage_log.jsonl (the evidence for the report).
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import date

from . import config

PRICES = {"gpt-6-luna": {"input": 0.10, "cached_input": 0.01, "output": 0.50}}
LOG_PATH = config.DATA_DIR / "usage_log.jsonl"
_lock = threading.Lock()


class BudgetExceeded(Exception):
    pass


def cost_usd(usage: dict, model: str = config.MODEL) -> float:
    p = PRICES[model]
    cached = usage.get("cached_tokens", 0)
    uncached = max(usage.get("input_tokens", 0) - cached, 0)
    return (uncached * p["input"] + cached * p["cached_input"] + usage.get("output_tokens", 0) * p["output"]) / 1e6


def usage_from_response(resp) -> dict:
    u = resp.usage
    details = getattr(u, "input_tokens_details", None)
    out_details = getattr(u, "output_tokens_details", None)
    return {
        "input_tokens": u.input_tokens or 0,
        "cached_tokens": (getattr(details, "cached_tokens", 0) or 0) if details else 0,
        "output_tokens": u.output_tokens or 0,
        "reasoning_tokens": (getattr(out_details, "reasoning_tokens", 0) or 0) if out_details else 0,
    }


def _today_spend() -> float:
    if not LOG_PATH.exists():
        return 0.0
    today = date.today().isoformat()
    total = 0.0
    with open(LOG_PATH) as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("day") == today:
                total += rec.get("cost_usd", 0.0)
    return total


@dataclass
class Meter:
    """Usage for one run (one Offer Check, or one appraisal upload)."""

    run_id: str
    kind: str
    session_spent_before: float = 0.0
    calls: list[dict] = field(default_factory=list)

    def check(self) -> None:
        if self.session_spent_before + self.total_usd() >= config.SESSION_BUDGET_USD:
            raise BudgetExceeded(
                f"Session budget of ${config.SESSION_BUDGET_USD:.2f} reached. Start a new session to continue."
            )
        if _today_spend() >= config.DAILY_BUDGET_USD:
            raise BudgetExceeded(f"Daily app budget of ${config.DAILY_BUDGET_USD:.2f} reached. Try again tomorrow.")

    def add(self, agent: str, usage: dict) -> dict:
        rec = {
            "ts": round(time.time(), 3),
            "day": date.today().isoformat(),
            "run_id": self.run_id,
            "kind": self.kind,
            "agent": agent,
            "model": config.MODEL,
            **usage,
            "cost_usd": round(cost_usd(usage), 8),
        }
        self.calls.append(rec)
        with _lock:
            LOG_PATH.parent.mkdir(exist_ok=True)
            with open(LOG_PATH, "a") as f:
                f.write(json.dumps(rec) + "\n")
        return rec

    def total(self) -> dict:
        keys = ("input_tokens", "cached_tokens", "output_tokens", "reasoning_tokens")
        t = {k: sum(c.get(k, 0) for c in self.calls) for k in keys}
        t["calls"] = len(self.calls)
        t["cost_usd"] = round(sum(c["cost_usd"] for c in self.calls), 8)
        return t

    def total_usd(self) -> float:
        return sum(c["cost_usd"] for c in self.calls)

    def by_agent(self) -> dict:
        out: dict[str, dict] = {}
        for c in self.calls:
            a = out.setdefault(c["agent"], {"calls": 0, "input_tokens": 0, "cached_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
            a["calls"] += 1
            for k in ("input_tokens", "cached_tokens", "output_tokens"):
                a[k] += c.get(k, 0)
            a["cost_usd"] = round(a["cost_usd"] + c["cost_usd"], 8)
        return out

    def snapshot(self) -> dict:
        return {
            "run_id": self.run_id,
            "model": config.MODEL,
            "total": self.total(),
            "by_agent": self.by_agent(),
            "session_budget_usd": config.SESSION_BUDGET_USD,
            "session_spent_usd": round(self.session_spent_before + self.total_usd(), 8),
        }


def summary() -> dict:
    """All logged runs, for the report and the /api/usage page."""
    if not LOG_PATH.exists():
        return {"runs": 0}
    runs: dict[str, dict] = {}
    with open(LOG_PATH) as f:
        for line in f:
            try:
                c = json.loads(line)
            except json.JSONDecodeError:
                continue
            r = runs.setdefault(c["run_id"], {"kind": c["kind"], "calls": 0, "input_tokens": 0, "cached_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
            r["calls"] += 1
            for k in ("input_tokens", "cached_tokens", "output_tokens"):
                r[k] += c.get(k, 0)
            r["cost_usd"] += c["cost_usd"]
    by_kind: dict[str, list] = {}
    for r in runs.values():
        by_kind.setdefault(r["kind"], []).append(r)
    out = {"runs": len(runs), "by_kind": {}}
    for kind, rs in by_kind.items():
        n = len(rs)
        out["by_kind"][kind] = {
            "runs": n,
            "avg_calls": round(sum(r["calls"] for r in rs) / n, 2),
            "avg_input_tokens": round(sum(r["input_tokens"] for r in rs) / n),
            "avg_cached_tokens": round(sum(r["cached_tokens"] for r in rs) / n),
            "avg_output_tokens": round(sum(r["output_tokens"] for r in rs) / n),
            "avg_cost_usd": round(sum(r["cost_usd"] for r in rs) / n, 6),
        }
    return out
