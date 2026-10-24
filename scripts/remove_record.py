"""Operator tool: remove a shared record after an owner emails a removal request.

  python scripts/remove_record.py --list "5 POPE ST"     # find the record id
  python scripts/remove_record.py --id 4                 # delete it (logged to the audit trail)
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import agents, audit, records  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--list", metavar="ADDRESS")
ap.add_argument("--id", type=int)
a = ap.parse_args()
if a.list:
    for r in records.shared_appraisals(address=a.list):
        print(r["id"], r["address"], r["town"], "SAMPLE" if r["is_sample"] else "", r.get("shared_at"))
elif a.id:
    gone = agents.remove(a.id)
    audit.record(run_id=f"remove-{a.id}", session_id="operator", kind="remove", asked={"id": a.id}, steps=None,
                 stop_reason="removed" if gone else "remove_failed", usage=None, started=time.time(), extra={"removed": gone})
    print("removed" if gone else "no such record", gone or "")
else:
    ap.print_help()
