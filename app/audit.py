"""Append-only audit trail: output/audit_trail.json.

One entry per AI run (Offer Check or Share), never wiped between runs. Same
pattern as hw4/hw5: every write happens under a lock, re-reads the JSON array,
appends, writes a .tmp file, then os.replace() swaps it in atomically. A file
that can't be parsed is renamed audit_trail.corrupt-<ts>.json, never
overwritten.

What is logged: time, run id, an anonymous session tag (never a name), what
was asked (clipped), each agent step (tool calls, guard results, token usage),
why the run stopped, total usage, and duration. What is never logged: the
document text, anything the privacy redactor removed, or the API key.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config

AUDIT_PATH = config.REPO_DIR / "output" / "audit_trail.json"
SHORT = 240
_lock = threading.Lock()

STOP_REASONS = (
    "final_response",        # answered normally
    "grounding_removed",     # answered, but the grounding guard removed unsupported figures
    "input_guard",           # user text looked like instructions and was dropped (run still answered)
    "content_filter",        # the provider's safety filter refused the request
    "budget",                # session or daily spending cap reached
    "loop_limit",            # agent used all tool rounds
    "rate_limit",            # too many requests in a minute
    "draft_verified", "draft_needs_review", "draft_rejected",
    "published", "held_for_review", "removed", "remove_failed",
    "ticket_opened", "ticket_approved", "ticket_rejected", "desk_login_failed",
    "model_error", "error",
)


def who(session_id: str) -> str:
    return "session-" + hashlib.sha256(session_id.encode()).hexdigest()[:8] if session_id else "anonymous"


def _clip(v: Any, n: int = SHORT) -> Any:
    """Clip long text and black out personal details in every string we keep."""
    if isinstance(v, str):
        from .safety import redact_pii  # local import: safety has no app dependencies
        v = redact_pii(v)[0]
        return v if len(v) <= n else v[:n] + "..."
    if isinstance(v, dict):
        return {k: _clip(x, n) for k, x in v.items()}
    if isinstance(v, list):
        return [_clip(x, n) for x in v[:40]]
    return v


def record(*, run_id: str, session_id: str, kind: str, asked: dict, steps: list[dict] | None,
           stop_reason: str, usage: dict | None, started: float, extra: dict | None = None) -> dict:
    entry = {
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_id": run_id,
        "who": who(session_id),
        "kind": kind,
        "asked": _clip(asked),
        "steps": [_clip(s) for s in (steps or [])],
        "stop_reason": stop_reason if stop_reason in STOP_REASONS else "error",
        "usage": usage,
        "duration_ms": int((time.time() - started) * 1000),
        **(extra or {}),
    }
    with _lock:
        AUDIT_PATH.parent.mkdir(exist_ok=True)
        entries: list = []
        if AUDIT_PATH.exists():
            try:
                entries = json.loads(AUDIT_PATH.read_text() or "[]")
                if not isinstance(entries, list):
                    raise ValueError("audit trail is not a list")
            except (json.JSONDecodeError, ValueError):
                AUDIT_PATH.rename(AUDIT_PATH.with_name(f"audit_trail.corrupt-{int(time.time())}.json"))
                entries = []
        entries.append(entry)
        tmp = AUDIT_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(entries, indent=1, default=str))
        os.replace(tmp, AUDIT_PATH)
    return entry


def recent(n: int = 20) -> list[dict]:
    if not AUDIT_PATH.exists():
        return []
    try:
        return json.loads(AUDIT_PATH.read_text())[-n:]
    except json.JSONDecodeError:
        return []
