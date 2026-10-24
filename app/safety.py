"""Safety checks that run in plain Python, outside the model.

1. scan_for_injection: flags instructions hidden inside uploaded appraisal text.
2. redact_pii: strips borrower names, loan numbers, emails, phones, SSNs before
   anything is stored or shown.
3. check_grounding: every dollar amount and percentage in an agent's answer must
   appear in the records the tools returned. Invented numbers are caught here.
4. clean_user_text: length limit and control-character cleanup for user input.
"""

from __future__ import annotations

import json
import re
from typing import Any

MAX_USER_TEXT = 500

INJECTION_PATTERNS = [
    r"ignore (all|any|the)? ?(previous|prior|above|earlier) (instructions|rules|prompts?)",
    r"disregard (the|all|any|your)? ?(previous|prior|above|system)",
    r"you are now",
    r"system prompt",
    r"(mark|set|report|treat) (this|the) (report|appraisal|value)? ?(as )?(verified|approved|valid)",
    r"do not (flag|verify|check|mention)",
    r"\b(assistant|chatgpt|llm|ai model|language model)\s*:",
    r"new instructions",
    r"override",
    r"</?(system|instructions?|prompt)>",
    r"reveal (your|the) (prompt|instructions|key)",
    r"api[_ ]?key",
]

_PARTY = r"(?:buyers?|sellers?|borrowers?|co-borrowers?|purchasers?|owners? of public record|owners?|grantors?|grantees?|lender/client|lenders?|clients?|contact|listing agent|buyer's agent|loan officer)"

PII_PATTERNS = {
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "email": r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b",
    "phone": r"\(?\b\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b",
    "password": r"(?i)\b(password|passcode|pin)\s*(is|:)\s*\S+",
    "loan_number": r"(?i)\b(loan|file|case|account|acct|escrow|order)\s*(id|no\.?|number|#)\s*(#|no\.?|number)?\s*[:#]?\s*[A-Z0-9][A-Z0-9-]{4,}",
    # "Buyer: Jordan Sample    Seller: Pat Example" -> both names go, labels stay.
    # Name words are joined by single spaces, so "Jordan Sample    Seller: ..." stops before the next label.
    "person_name": _PARTY + r"[ \t]*[:\-][ \t]*[A-Z][A-Za-z.'-]+(?: (?!(?:" + _PARTY[3:-1] + r")\b)[A-Z][A-Za-z.'-]+){0,3}",
}

# Card numbers come in groups of four ("4111 1111 1111 1111" or "4111-1111-..."), or as one 13-19 digit run.
# Dates like "2025-07-10 2025-07-20" have 2-digit groups, so they no longer look like cards.
_CARD = re.compile(r"\b(?:\d{4}([ -])\d{4}\1\d{4}\1\d{1,7}|\d{13,19})\b")


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


def scan_for_injection(text: str) -> list[dict]:
    hits = []
    low = text.lower()
    for pat in INJECTION_PATTERNS:
        for m in re.finditer(pat, low):
            start = max(m.start() - 60, 0)
            hits.append({"pattern": pat, "excerpt": text[start : m.end() + 60].replace("\n", " ").strip()})
    return hits


def redact_pii(text: str) -> tuple[str, dict]:
    """Strip personal details before any model sees the text. Returns (clean text, counts)."""
    counts: dict[str, int] = {}

    def card(m):
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            counts["card_number"] = counts.get("card_number", 0) + 1
            return "[CARD NUMBER REMOVED]"
        return m.group()

    text = _CARD.sub(card, text)
    for name, pat in PII_PATTERNS.items():
        if name == "person_name":
            text, n = re.subn(pat, lambda m: re.split(r"[:\-]", m.group(), maxsplit=1)[0] + ": [NAME REMOVED]", text, flags=re.I)
        else:
            text, n = re.subn(pat, f"[{name.upper()} REMOVED]", text)
        if n:
            counts[name] = counts.get(name, 0) + n
    return text, counts


def clean_user_text(text: str) -> str:
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", text or "")
    return text.strip()[:MAX_USER_TEXT]


# ---------- grounding ----------

_MONEY = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s?(k|K|m|M|million|thousand)?")
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s?%")


def _numbers_in(obj: Any) -> set[float]:
    found: set[float] = set()

    def walk(o):
        if isinstance(o, bool):
            return
        if isinstance(o, (int, float)):
            found.add(float(o))
        elif isinstance(o, str):
            for m in re.finditer(r"-?\d[\d,]*\.?\d*", o):
                try:
                    found.add(float(m.group().replace(",", "")))
                except ValueError:
                    pass
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)

    walk(obj)
    return found


def _money_value(num: str, suffix: str | None) -> float:
    v = float(num.replace(",", ""))
    if suffix:
        s = suffix.lower()
        if s in ("k", "thousand"):
            v *= 1_000
        elif s in ("m", "million"):
            v *= 1_000_000
    return v


def check_grounding(answer: str, evidence: Any, user_numbers: list[float] | None = None) -> dict:
    """Return the dollar and percent figures in `answer` that no record supports.

    A figure is supported if it matches a number in the tool outputs (or the
    user's own offer), allowing for rounding ($1,000 for dollars; for percents,
    either the value or value/100 with 0.6 points of slack), or is a simple
    difference between two supported dollar figures.
    """
    known = _numbers_in(evidence) | set(user_numbers or [])
    money_known = {k for k in known if k >= 1000}
    unsupported = []

    for m in _MONEY.finditer(answer):
        v = _money_value(m.group(1), m.group(2))
        if v < 100:
            continue
        ok = any(abs(v - k) <= max(1000, 0.005 * k) for k in money_known)
        if not ok:  # allow "your offer is $30,000 above the last sale"
            ok = any(abs(abs(a - b) - v) <= 1000 for a in money_known for b in money_known if a != b)
        if not ok:
            unsupported.append(m.group(0).strip())

    for m in _PCT.finditer(answer):
        v = float(m.group(1))
        ok = any(abs(v - k) <= 0.6 or abs(v - k * 100) <= 0.6 for k in known)
        if not ok:  # "3 of 10" -> 30%
            ints = [k for k in known if k == int(k) and 0 < k <= 100000]
            ok = any(b and abs(100 * a / b - v) <= 1.0 for a in ints for b in ints if a <= b)
        if not ok:
            unsupported.append(m.group(0).strip())

    return {"ok": not unsupported, "unsupported": unsupported}


def evidence_digest(outputs: list[Any]) -> str:
    return json.dumps(outputs, default=str)[:20000]
