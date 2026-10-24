"""FastAPI server: JSON API under /api, the web page from /static.

Model-backed routes never crash on the user: every failure maps to a
stop_reason, a plain-English message, and an audit-trail entry.
"""

from __future__ import annotations

import hmac
import os
import secrets
import time
import uuid

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import agents, audit, config, records, sheets, tickets
from .llm import AgentLoopLimit
from .meter import BudgetExceeded, Meter, summary

STATIC = config.REPO_DIR / "static"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
RATE_LIMIT = 8  # model-backed requests per session per minute
# Staff passcode for the review desk (#/desk). Set DESK_PASSCODE in .env for real use.
# Without it, the demo passcode only works from this computer (localhost), never over a network.
DESK_PASSCODE = os.environ.get("DESK_PASSCODE", "")
DEMO_DESK_PASSCODE = "desk-demo"
# Session cookies are signed, so a visitor can't mint new sessions to dodge the per-session limits.
SESSION_SECRET = (os.environ.get("SESSION_SECRET") or secrets.token_hex(32)).encode()
IP_RATE_LIMIT = 30  # model-backed requests per IP per minute, across all sessions
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "hello@appraisalcommons.example")

app = FastAPI(title="Appraisal Commons")
records.ensure_schema()
tickets.ensure_schema()
app.mount("/static", StaticFiles(directory=STATIC), name="static")

# Per-browser-session state, keyed by a random HttpOnly cookie. In memory: restarts reset it.
_session_spend: dict[str, float] = {}
_drafts: dict[str, dict] = {}
_rate: dict[str, list[float]] = {}

FRIENDLY = {
    "budget": None,  # message comes from the exception
    "loop_limit": "The agent took too many steps and was stopped. Nothing was charged beyond the calls shown. Please try again.",
    "model_error": "The AI service didn't respond. The records on this page are still accurate; try the question again in a minute.",
    "error": "Something went wrong on our side. The records on this page are still accurate.",
    "paused": "AI features are paused by the site operator. The records on this page still work.",
    "no_key": "AI features are off because PORTKEY_API_KEY wasn't found. Put it in a .env file in the app folder or one folder up, then restart. The records on this page still work.",
}


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    resp = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"  # always revalidate, so updates show up right away
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'"
    return resp


@app.exception_handler(RequestValidationError)
async def _invalid(_: Request, exc: RequestValidationError):
    # Never echo submitted values back (they could contain personal details).
    fields = sorted({".".join(str(p) for p in e["loc"][1:]) for e in exc.errors()})
    return JSONResponse({"error": "Please check: " + ", ".join(fields or ["your input"])}, status_code=422)


def _sign(sid: str) -> str:
    return hmac.new(SESSION_SECRET, sid.encode(), "sha256").hexdigest()[:24]


def _new_session_cookie() -> str:
    sid = uuid.uuid4().hex
    return f"{sid}.{_sign(sid)}"


def _session(request: Request) -> str:
    """The session id, only if the cookie was issued (signed) by this server."""
    raw = request.cookies.get("ac_session") or ""
    sid, _, sig = raw.partition(".")
    return sid if sid and sig and hmac.compare_digest(sig, _sign(sid)) else ""


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _bucket(key: str, limit: int, window: int) -> bool:
    now = time.time()
    recent = [t for t in _rate.get(key, []) if now - t < window]
    if len(recent) >= limit:
        return False
    _rate[key] = recent + [now]
    return True


def _meter(request: Request, kind: str) -> Meter:
    if os.environ.get("AI_PAUSED") == "1":  # operator kill switch
        raise HTTPException(503, FRIENDLY["paused"])
    sid = _session(request)
    if not sid:
        raise HTTPException(400, "Missing session. Reload the page.")
    if not _bucket("ip:" + _client_ip(request), IP_RATE_LIMIT, 60) or not _bucket(sid, RATE_LIMIT, 60):
        raise HTTPException(429, "Too many requests in a minute. Please wait a moment.")
    return Meter(run_id=uuid.uuid4().hex[:10], kind=kind, session_spent_before=_session_spend.get(sid, 0.0))


class _NoKey(Exception):
    pass


def _run(request: Request, meter: Meter, asked: dict, fn):
    """Run an agent pipeline; always settle spend and write the audit trail."""
    started = time.time()
    sid = _session(request)
    out, stop, status, error = None, "error", 200, None
    try:
        if not config.api_key():
            stop, status = "no_key", 503
            raise _NoKey
        out = fn()
        stop = _stop_reason(out)
    except _NoKey:
        pass
    except BudgetExceeded as exc:
        stop, status, error = "budget", 402, str(exc)
    except AgentLoopLimit:
        stop, status = "loop_limit", 503
    except Exception as exc:  # noqa: BLE001 - user gets a friendly message, audit gets the type
        stop, status = ("model_error" if "openai" in type(exc).__module__ else "error"), 502
        asked = {**asked, "exception": type(exc).__name__}
    finally:
        _session_spend[sid] = _session_spend.get(sid, 0.0) + meter.total_usd()
    audit.record(run_id=meter.run_id, session_id=sid, kind=meter.kind, asked=asked,
                 steps=(out or {}).get("trace"), stop_reason=stop, usage=meter.total(), started=started)
    if out is None:
        return JSONResponse({"error": error or FRIENDLY[stop], "kind": stop, "usage": meter.snapshot()}, status_code=status)
    return {**out, "stop_reason": stop, "usage": meter.snapshot()}


def _stop_reason(out: dict) -> str:
    if out.get("blocked"):
        return "content_filter"
    if out.get("draft"):
        return "draft_" + out["draft"]["verification"].get("verdict", "needs_review")
    if out.get("grounding") and not out["grounding"].get("ok"):
        return "grounding_removed"
    if any(t.get("agent") == "input_guard" for t in out.get("trace", [])):
        return "input_guard"
    return "final_response"


@app.get("/")
def index(request: Request):
    resp = FileResponse(STATIC / "index.html")
    if not _session(request):
        resp.set_cookie("ac_session", _new_session_cookie(), httponly=True, samesite="lax", max_age=60 * 60 * 24 * 7)
    return resp


@app.get("/api/config")
def site_config():
    return {"contact_email": CONTACT_EMAIL}


@app.get("/api/health")
def health():
    return {"ok": True, "model": config.MODEL, "key_found": bool(config.api_key()), "db": config.DB_PATH.name,
            "ai_paused": os.environ.get("AI_PAUSED") == "1", "sources": records.meta()}


@app.get("/api/search")
def search(q: str = "", county: str = ""):
    return records.search(q[:80], county=county[:40] or None)


@app.get("/api/areas")
def areas():
    return {"areas": records.areas(), "counties": records.counties(), "county_gap_trend": records.county_gap_trend(),
            "state_appraisals": records.state_appraisal_total()}


@app.get("/api/area")
def area(town: str):
    d = records.area_detail(town[:40])
    if not d.get("found"):
        raise HTTPException(404, "We don't cover that town yet.")
    return d


@app.get("/api/area/sales")
def area_sales(town: str, tract: str):
    if not tract.isdigit() or len(tract) != 11:
        raise HTTPException(422, "Unknown neighborhood.")
    d = records.tract_sales(town[:40], tract)
    if not d.get("found"):
        raise HTTPException(404, "We don't cover that town yet.")
    return d


@app.get("/api/property")
def property_panel(address: str, town: str | None = None):
    panel = records.property_panel(address[:120], (town or "")[:40] or None)
    if not panel.get("found"):
        msg = "That address is in more than one town. Pick one." if panel.get("ambiguous") else "We couldn't find that address in the 2019+ sales records."
        raise HTTPException(404, {"message": msg, "did_you_mean": panel.get("did_you_mean", [])})
    return panel


class OfferIn(BaseModel):
    address: str = Field(max_length=120)
    town: str | None = Field(default=None, max_length=40)
    offer: int = Field(gt=10_000, lt=20_000_000)
    asking: int | None = Field(default=None, gt=10_000, lt=20_000_000)
    question: str = Field(default="", max_length=500)


@app.post("/api/offer-check")
def offer_check(body: OfferIn, request: Request):
    meter = _meter(request, "offer_check")
    asked = {"address": body.address, "town": body.town, "offer": body.offer, "asking": body.asking, "question": body.question}
    return _run(request, meter, asked, lambda: agents.offer_check(body.address, body.offer, body.question, meter, body.town, body.asking))


class HowOftenIn(BaseModel):
    address: str = Field(max_length=120)
    town: str | None = Field(default=None, max_length=40)
    offer: int = Field(gt=10_000, lt=20_000_000)
    asking: int | None = Field(default=None, gt=10_000, lt=20_000_000)


@app.post("/api/how-often")
def how_often(body: HowOftenIn, request: Request):
    """The record counts behind the Offer Check, straight from the database (no AI), so the page
    can show them right away while the written answer is still being made."""
    _slow_down(request, "how_often", 60, 60)
    prop = records.find_property(body.address, body.town)
    if not prop.get("found"):
        raise HTTPException(404, "We couldn't find that address in the 2019+ sales records.")
    return records.how_often(body.offer, prop, body.asking)


@app.post("/api/share")
async def share(request: Request, file: UploadFile | None = File(default=None), text: str = Form(default="")):
    """Upload a PDF document, or the filled-in spreadsheet (.xlsx / .csv)."""
    if file is None and not text:
        raise HTTPException(400, "Choose a file to upload.")
    if file is not None:
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "File is larger than 5 MB.")
        name = (file.filename or "").lower()
        if data.startswith(b"PK") or name.endswith((".xlsx", ".csv")):
            try:
                raw_fields = sheets.parse(data, name)
            except Exception:
                raise HTTPException(422, "We couldn't read that spreadsheet. Use our template and save it as .xlsx or .csv.")
            if not raw_fields:
                raise HTTPException(422, "The spreadsheet is empty. Fill in the 'Your value' column of our template.")
            meter = _meter(request, "share_record")
            resp = _run(request, meter, {"file": file.filename, "kind": "spreadsheet", "fields": sorted(raw_fields)},
                        lambda: agents.share_manual(raw_fields, meter, source="spreadsheet"))
            return _keep_draft(request, resp)
        if not data.startswith(b"%PDF"):
            raise HTTPException(415, "Please upload a PDF, or our spreadsheet template (.xlsx or .csv).")
        try:
            raw = agents.pdf_text(data)
        except Exception:
            raise HTTPException(422, "Could not read text from this PDF.")
    else:
        raw = text
    if len(raw.strip()) < 200:
        raise HTTPException(422, "This PDF has too little readable text. Scanned images aren't supported yet. Try typing the numbers in instead.")
    meter = _meter(request, "share_record")
    asked = {"file": getattr(file, "filename", None), "chars": len(raw)}  # never the document text
    resp = _run(request, meter, asked, lambda: agents.share_record(raw, meter))
    return _keep_draft(request, resp)


class ManualIn(BaseModel):
    fields: dict = Field(default_factory=dict)


@app.post("/api/share/manual")
def share_manual(body: ManualIn, request: Request):
    """Typed-in numbers: same checks as a document, marked as owner-reported."""
    raw = {k: (str(v)[:80] if v is not None else None) for k, v in body.fields.items() if k in agents.MANUAL_FIELDS}
    if not any(raw.get(k) for k in ("list_price", "offer_price", "contract_price", "appraised_value", "close_price")):
        raise HTTPException(422, "Add at least one price: list, offer, contract, appraisal, or closing.")
    if not raw.get("address"):
        raise HTTPException(422, "Add the street address.")
    meter = _meter(request, "share_record")
    resp = _run(request, meter, {"kind": "typed", "fields": sorted(k for k, v in raw.items() if v)},
                lambda: agents.share_manual(raw, meter, source="typed"))
    return _keep_draft(request, resp)


def _keep_draft(request: Request, resp):
    if isinstance(resp, dict) and resp.get("draft"):
        _drafts[resp["draft"]["draft_id"]] = {**resp["draft"], "_session": _session(request)}
    return resp


class PublishIn(BaseModel):
    draft_id: str = Field(max_length=40)
    consent: bool
    is_sample: bool = False


@app.post("/api/share/publish")
def publish(body: PublishIn, request: Request):
    """The human gate: only the uploader, with explicit consent, can publish. No agent has this ability.

    A submission the Verifier flagged "needs a second look" is held on the review
    desk instead of going live; a staff member approves or rejects it.
    """
    draft = _drafts.get(body.draft_id)
    if not draft or draft["_session"] != _session(request):
        raise HTTPException(404, "Draft not found. Submit your numbers again.")
    if not body.consent:
        raise HTTPException(400, "You must confirm you own this record and agree to share it.")
    verdict = draft["verification"].get("verdict")
    if verdict == "rejected":
        raise HTTPException(409, "Rejected records can't be published.")
    if verdict == "needs_review":
        code = agents.new_removal_code()
        held = {k: v for k, v in draft.items() if k != "_session"}
        held["_removal_hash"] = agents._hash_code(code)
        ticket_id = tickets.open_review(held, body.is_sample, opened_by="verifier")
        _drafts.pop(body.draft_id, None)
        audit.record(run_id=body.draft_id, session_id=_session(request), kind="publish",
                     asked={"address": draft["address"], "is_sample": body.is_sample}, steps=None,
                     stop_reason="held_for_review", usage=None, started=time.time(), extra={"ticket": ticket_id})
        return {"ok": True, "held_for_review": True, "ticket": ticket_id, "address": draft["address"],
                "town": draft.get("town"), "removal_code": code}
    try:
        new_id, code = agents.publish(draft, body.is_sample)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    _drafts.pop(body.draft_id, None)
    audit.record(run_id=body.draft_id, session_id=_session(request), kind="publish",
                 asked={"address": draft["address"], "is_sample": body.is_sample}, steps=None,
                 stop_reason="published", usage=None, started=time.time(), extra={"record_id": new_id})
    return {"ok": True, "id": new_id, "address": draft["address"], "town": draft.get("town"), "removal_code": code}


def _slow_down(request: Request, bucket: str, limit: int, window: int = 600) -> None:
    who = _session(request) or "ip:" + _client_ip(request)  # no cookie: don't share one bucket with every such client
    ok_session = _bucket(f"{bucket}:{who}", limit, window)
    ok_ip = _bucket(f"{bucket}:ip:{_client_ip(request)}", limit * 3, window)
    if not (ok_session and ok_ip):
        raise HTTPException(429, "Too many attempts. Please wait a few minutes and try again.")


class RemoveIn(BaseModel):
    code: str = Field(min_length=6, max_length=40)


@app.post("/api/share/remove")
def remove(body: RemoveIn, request: Request):
    """Owners take their own record down with the private code they got at publish time. No account needed."""
    _slow_down(request, "remove", 5)  # makes guessing codes impractical
    gone = agents.remove_with_code(body.code)
    audit.record(run_id=uuid.uuid4().hex[:10], session_id=_session(request), kind="remove", asked={"found": bool(gone)},
                 steps=None, stop_reason="removed" if gone else "remove_failed", usage=None, started=time.time(),
                 extra={"removed": gone})
    if not gone:
        raise HTTPException(404, "No record matches that removal code. Check for typos.")
    return {"ok": True, "removed": gone}


class CorrectionIn(BaseModel):
    record_id: int
    message: str = Field(min_length=5, max_length=600)


@app.post("/api/tickets/correction")
def report_problem(body: CorrectionIn, request: Request):
    """Anyone can flag a shared record. A person on the review desk decides."""
    _slow_down(request, "ticket", 3)
    tid = tickets.open_correction(body.record_id, body.message, opened_by=audit.who(_session(request)))
    if not tid:
        raise HTTPException(404, "That record no longer exists.")
    audit.record(run_id=f"ticket-{tid}", session_id=_session(request), kind="ticket", asked={"record_id": body.record_id},
                 steps=None, stop_reason="ticket_opened", usage=None, started=time.time(), extra={"ticket": tid})
    return {"ok": True, "ticket": tid}


# ---------------------------------------------------------------- review desk (staff)

_desk_tokens: set[str] = set()


def _desk_staff(request: Request) -> None:
    if request.cookies.get("ac_desk") not in _desk_tokens:
        raise HTTPException(401, "Sign in to the review desk first.")


class DeskLoginIn(BaseModel):
    passcode: str = Field(min_length=1, max_length=80)


@app.post("/api/desk/login")
def desk_login(body: DeskLoginIn, request: Request):
    _slow_down(request, "desk-login", 5, 900)
    expected = DESK_PASSCODE
    if not expected and _client_ip(request) in ("127.0.0.1", "::1", "localhost", "testclient"):
        expected = DEMO_DESK_PASSCODE  # local demo only
    if not expected or not hmac.compare_digest(body.passcode.encode(), expected.encode()):
        audit.record(run_id=uuid.uuid4().hex[:10], session_id=_session(request), kind="desk", asked={}, steps=None,
                     stop_reason="desk_login_failed", usage=None, started=time.time())
        raise HTTPException(401, "Wrong passcode.")
    token = secrets.token_urlsafe(24)
    _desk_tokens.add(token)
    resp = JSONResponse({"ok": True})
    resp.set_cookie("ac_desk", token, httponly=True, samesite="strict", max_age=60 * 60 * 8)
    return resp


@app.post("/api/desk/logout")
def desk_logout(request: Request):
    _desk_tokens.discard(request.cookies.get("ac_desk") or "")
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("ac_desk")
    return resp


@app.get("/api/desk/tickets")
def desk_tickets(request: Request, status: str | None = None):
    _desk_staff(request)
    return {"tickets": tickets.list_tickets(status), "counts": tickets.counts()}


class DecideIn(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    name: str = Field(min_length=1, max_length=60)
    reason: str = Field(default="", max_length=600)


@app.post("/api/desk/tickets/{ticket_id}/decide")
def desk_decide(ticket_id: int, body: DecideIn, request: Request):
    """Only a signed-in person closes a ticket. No agent has a tool for this."""
    _desk_staff(request)
    try:
        out = tickets.decide(ticket_id, body.decision, body.name, body.reason, agents.publish, agents.remove)
    except tickets.DecisionError as exc:
        raise HTTPException(409, str(exc))
    audit.record(run_id=f"ticket-{ticket_id}", session_id="desk:" + body.name, kind="desk_decision",
                 asked={"ticket": ticket_id, "decision": body.decision, "reason": body.reason}, steps=None,
                 stop_reason="ticket_" + ("approved" if body.decision == "approve" else "rejected"),
                 usage=None, started=time.time(), extra={"effect": out["effect"], "by": out["by"]})
    return out


class ChatTurn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=2000)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)
    page: dict = Field(default_factory=dict)


@app.post("/api/chat")
def chat(body: ChatIn, request: Request):
    meter = _meter(request, "chat")
    page = {k: str(v)[:80] for k, v in (body.page or {}).items() if k in ("kind", "town", "address")}
    asked = {"message": body.message, "page": page, "turns": len(body.history)}
    return _run(request, meter, asked,
                lambda: agents.chat(body.message, [h.model_dump() for h in body.history], page, meter))


@app.get("/api/usage")
def usage(request: Request):
    return {"session_spent_usd": round(_session_spend.get(_session(request), 0.0), 6),
            "session_budget_usd": config.SESSION_BUDGET_USD, "all_runs": summary()}
