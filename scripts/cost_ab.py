"""Cost-cut A/B test for the Records Concierge (uses the course key, costs < $0.02).

Runs the same Offer Checks under three setups and saves tokens, cost, time, and
quality checks to output/cost_ab.json, which the report uses as evidence:

  A  before   default reasoning effort, long answer prompt (up to 180 words)
  B  low      low reasoning effort, long answer prompt
  C  current  low reasoning effort, short answer prompt (under 70 words)  <- what ships

Quality is checked in code, not by eye: did an answer come back at all (no
plain-facts fallback), did every figure pass the grounding guard, does it cite
at least one record, and does it say SAMPLE when the shared records are demos.

Run: ./.venv/bin/python scripts/cost_ab.py
Note: Portkey replays a request it has seen before, so a second run of this
script reports the first run's numbers. Change the offers to measure afresh.
"""

import json
import re
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import agents, config, llm  # noqa: E402
from app.meter import Meter  # noqa: E402

CASES = [  # (address, town, asking, offer): unusual offers so no earlier request is replayed
    ("5 POPE ST", "New Haven", 449900, 472300),
    ("73 HEMLOCK RD", "New Haven", 459000, 481700),
    ("19 SHOREHAM RD", "New Haven", 419000, 436100),
    ("25 MILESFIELD AVE", "Milford", 679000, 712400),
    ("75 RACHELLE AVENUE", "Stamford", 675000, 703900),
]

# The answer section of the Concierge prompt before the cut (from the version the user tested).
LONG_RULES = """Call `compare_offer` first. Its `how_often` section answers the buyer's real question: when homes here went this far over asking, how often did the appraisal come in that high? Call the other tools only if you need detail it did not return. Then write a short answer (under 180 words) in plain English:

1. **One-line bottom line** about how often appraisals here have reached this price, for example "In this neighborhood, 23.7% of purchases had a price higher than the appraisal." Describe what already happened. Never give a chance or probability for this home.
2. **What the records show:** 3 to 5 bullets, each one fact, each ending with its source ref in square brackets, like [hmda:2024:141301]. Lead with `how_often`: how many shared sales nearby appraised this far over asking (say if there aren't enough yet, and say SAMPLE if they're demo samples), how often purchases here were priced above the appraisal, how many homes here were appraised at the offer or more. If `how_often.over_asking.similar` has homes, one bullet must name how far over asking they went and where each appraised versus asking. If `similar` is empty but `compare_offer` returned nearby shared records with a list price, use those instead.
3. **What the records can't tell you:** one sentence (for example, condition, renovations, or that there is no shared appraisal yet for this home).
4. **One question to ask your agent or lender.**

"""

SETUPS = {
    "A_before": {"effort": None, "long": True},
    "B_low_effort": {"effort": "low", "long": True},
    "C_current": {"effort": "low", "long": False},
}

_real_prompt = llm.prompt


def complete(text: str) -> bool:
    """A finished answer: at least a couple of sentences, ending like a sentence (not cut off mid-word)."""
    t = text.strip()
    return len(t.split()) >= 25 and t[-1:] in ".?!)]*"


def _prompt_for(long: bool):
    def prompt(name: str) -> str:
        text = _real_prompt(name)
        if name == "concierge" and long:
            start = text.index("Call `compare_offer` first.")
            end = text.index("Write dollar amounts like")
            text = text[:start] + LONG_RULES + text[end:]
        return text
    return prompt


def main():
    rows = []
    for setup, opts in SETUPS.items():
        config.REASONING_EFFORT = {"concierge": opts["effort"]} if opts["effort"] else {}
        llm.prompt = _prompt_for(opts["long"])
        for address, town, asking, offer in CASES:
            m = Meter(run_id=uuid.uuid4().hex[:10], kind="cost_ab")
            t0 = time.time()
            r = agents.offer_check(address, offer, "", m, town=town, asking=asking)
            secs = time.time() - t0
            text = r["answer"] or ""
            fallback = any(t.get("agent") == "fallback" for t in r["trace"])
            o = (r.get("facts") or {}).get("how_often", {}).get("over_asking") or {}
            needs_sample = bool(o.get("all_samples"))
            u = m.total()
            rows.append({
                "setup": setup, "address": address, "town": town, "asking": asking, "offer": offer,
                "seconds": round(secs, 1), **{k: u[k] for k in ("calls", "input_tokens", "cached_tokens", "output_tokens", "reasoning_tokens", "cost_usd")},
                "words": len(re.sub(r"\[[^\]]*\]", "", text).split()),
                "answered": bool(text.strip()) and not fallback,
                "complete": complete(text) and not fallback,
                "grounding_ok": r["grounding"]["ok"],
                "cites_record": bool(re.search(r"\[(hmda|fhfa|shared|sale|sales|user):", text)),
                "says_sample_when_needed": (not needs_sample) or ("SAMPLE" in text.upper()),
                "answer": text,
            })
            x = rows[-1]
            print(f"{setup:13} {address:20} {x['seconds']:5}s out={x['output_tokens']:4} reason={x['reasoning_tokens']:4} "
                  f"words={x['words']:3} ${x['cost_usd']:.5f} answered={x['answered']} grounded={x['grounding_ok']}")
    llm.prompt = _real_prompt

    summary = {}
    for setup in SETUPS:
        g = [x for x in rows if x["setup"] == setup]
        n = len(g)
        summary[setup] = {
            "runs": n,
            "avg_cost_usd": round(sum(x["cost_usd"] for x in g) / n, 6),
            "avg_input_tokens": round(sum(x["input_tokens"] for x in g) / n),
            "avg_cached_tokens": round(sum(x["cached_tokens"] for x in g) / n),
            "avg_output_tokens": round(sum(x["output_tokens"] for x in g) / n),
            "avg_reasoning_tokens": round(sum(x["reasoning_tokens"] for x in g) / n),
            "avg_seconds": round(sum(x["seconds"] for x in g) / n, 1),
            "avg_words": round(sum(x["words"] for x in g) / n),
            "answered": sum(x["answered"] for x in g),
            "complete": sum(x["complete"] for x in g),
            "grounding_ok": sum(x["grounding_ok"] for x in g),
            "cites_record": sum(x["cites_record"] for x in g),
            "says_sample_when_needed": sum(x["says_sample_when_needed"] for x in g),
        }
    out = ROOT / "output" / "cost_ab.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"model": config.MODEL, "cases": CASES, "summary": summary, "runs": rows}, indent=2))
    print(json.dumps(summary, indent=2))
    print("wrote", out)


if __name__ == "__main__":
    main()
