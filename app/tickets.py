"""Review desk tickets (the hw5 pattern: agents and visitors can OPEN tickets,
only a named human on the desk can CLOSE them).

Kinds:
  review      a submission the Verifier flagged "needs a second look". It is held
              here instead of being published. Approve = publish it.
  correction  a visitor reports a problem with a published record.
              Approve = take the record down. Reject = keep it.

Every decision needs a staff name that isn't an agent's name, and a reason when
rejecting. Each decision is written to the audit trail.
"""

from __future__ import annotations

import json
from datetime import datetime

from . import records, safety

KINDS = ("review", "correction")
AGENT_NAMES = {"concierge", "records concierge", "guide", "the guide", "extractor", "verifier", "system", "ai", "agent", "bot"}
MAX_MESSAGE = 600

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,              -- review | correction
  status TEXT NOT NULL DEFAULT 'open',   -- open | approved | rejected
  record_id INTEGER,               -- shared_appraisals.id (corrections)
  address TEXT, town TEXT,
  message TEXT,                    -- visitor's note, personal details removed
  draft_json TEXT,                 -- held submission (reviews)
  opened_by TEXT,                  -- 'verifier' or an anonymous session tag
  created_at TEXT,
  decided_by TEXT, decided_at TEXT, decision_reason TEXT
);
"""


def ensure_schema() -> None:
    with records.connect(readonly=False) as con:
        con.executescript(SCHEMA)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _clean_message(text: str) -> str:
    text = safety.clean_user_text(text or "")[:MAX_MESSAGE]
    text, _ = safety.redact_pii(text)
    return text


def open_review(draft: dict, is_sample: bool, opened_by: str) -> int:
    held = {**draft, "_is_sample": bool(is_sample)}
    held.pop("_session", None)
    with records.connect(readonly=False) as con:
        cur = con.execute(
            "INSERT INTO tickets (kind, address, town, message, draft_json, opened_by, created_at) VALUES (?,?,?,?,?,?,?)",
            ("review", draft.get("address"), draft.get("town"),
             draft.get("verification", {}).get("summary", "")[:MAX_MESSAGE], json.dumps(held), opened_by, _now()),
        )
        return cur.lastrowid


def open_correction(record_id: int, message: str, opened_by: str) -> int | None:
    with records.connect(readonly=False) as con:
        row = con.execute("SELECT id, address, town FROM shared_appraisals WHERE id = ?", (record_id,)).fetchone()
        if not row:
            return None
        cur = con.execute(
            "INSERT INTO tickets (kind, record_id, address, town, message, opened_by, created_at) VALUES (?,?,?,?,?,?,?)",
            ("correction", row["id"], row["address"], row["town"], _clean_message(message), opened_by, _now()),
        )
        return cur.lastrowid


def list_tickets(status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM tickets"
    args: tuple = ()
    if status in ("open", "approved", "rejected"):
        sql += " WHERE status = ?"
        args = (status,)
    with records.connect() as con:
        rows = con.execute(sql + " ORDER BY (status = 'open') DESC, id DESC LIMIT 200", args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["draft"] = json.loads(d.pop("draft_json") or "null")
        if d["kind"] == "correction" and d["record_id"]:
            rec = [x for x in records.shared_appraisals(address=d["address"], town=d["town"]) if x["id"] == d["record_id"]]
            d["record"] = rec[0] if rec else None
        out.append(d)
    return out


def counts() -> dict:
    with records.connect() as con:
        rows = con.execute("SELECT status, COUNT(*) AS n FROM tickets GROUP BY status").fetchall()
    return {r["status"]: r["n"] for r in rows}


class DecisionError(ValueError):
    pass


def decide(ticket_id: int, decision: str, staff_name: str, reason: str, publish_fn, remove_fn) -> dict:
    """Close a ticket. publish_fn(draft, is_sample) and remove_fn(record_id) do the actual change."""
    name = " ".join((staff_name or "").split())[:60]
    if not name:
        raise DecisionError("Sign with your name.")
    if name.lower() in AGENT_NAMES:
        raise DecisionError("Decisions must be signed by a person, not an agent.")
    if decision not in ("approve", "reject"):
        raise DecisionError("Decision must be approve or reject.")
    reason = _clean_message(reason)
    if decision == "reject" and not reason:
        raise DecisionError("Give a reason when rejecting.")
    with records.connect(readonly=False) as con:
        t = con.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if not t:
            raise DecisionError("No such ticket.")
        # Claim the ticket before acting, so a double click can't publish twice.
        claimed = con.execute("UPDATE tickets SET status = 'deciding' WHERE id = ? AND status = 'open'", (ticket_id,)).rowcount
    if not claimed:
        raise DecisionError(f"Ticket already {t['status']}.")

    effect = None
    try:
        effect = _apply(t, decision, publish_fn, remove_fn)
    except Exception:
        with records.connect(readonly=False) as con:  # release the claim so someone can retry
            con.execute("UPDATE tickets SET status = 'open' WHERE id = ? AND status = 'deciding'", (ticket_id,))
        raise
    with records.connect(readonly=False) as con:
        con.execute(
            "UPDATE tickets SET status = ?, decided_by = ?, decided_at = ?, decision_reason = ? WHERE id = ? AND status = 'deciding'",
            ("approved" if decision == "approve" else "rejected", name, _now(), reason or None, ticket_id),
        )
    return {"ticket": ticket_id, "decision": decision, "by": name, "effect": effect}


def _apply(t, decision: str, publish_fn, remove_fn):
    effect = None
    if decision == "approve":
        if t["kind"] == "review":
            draft = json.loads(t["draft_json"])
            is_sample = draft.pop("_is_sample", False)
            draft["verification"] = {**draft.get("verification", {}), "verdict": "verified",
                                     "reviewed_by_staff": True, "summary": draft.get("verification", {}).get("summary", "")}
            record_id, _code = publish_fn(draft, is_sample)
            effect = {"published_record": record_id}
        else:
            effect = {"removed": remove_fn(t["record_id"])}
    return effect
