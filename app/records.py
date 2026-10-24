"""Read-only access to the public records in data/appraisal_commons.db.

Every function returns plain dicts. Each record carries a `ref` string
(e.g. "sale:1234", "fhfa:142700:Purchase:2022:MEDIAN") so agents can cite it
and the grounding check can trace every number back to a record.
"""

from __future__ import annotations

import json
import re
import sqlite3
import statistics
from contextlib import contextmanager
from datetime import date

from . import config

SERIES_LABELS = {
    "COUNT": "number of appraisals",
    "MEDIAN": "median appraised value",
    "P25": "25th percentile appraised value",
    "P75": "75th percentile appraised value",
    "MEAN": "mean appraised value",
    "RATIO1": "mean appraised value / contract price",
    "PERBEL": "share of purchases where the contract price was higher than the appraisal",
    "PEREQ": "share of appraisals equal to contract price",
    "PERABV": "share of appraisals above contract price",
}


@contextmanager
def connect(readonly: bool = True):
    uri = f"file:{config.DB_PATH}?mode={'ro' if readonly else 'rw'}"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    try:
        yield con
        if not readonly:
            con.commit()
    finally:
        con.close()


# Columns added after the first release; ensure_schema() adds any that are missing.
SHARED_EXTRA_COLUMNS = {
    "document_types": "TEXT", "unit": "TEXT", "zip": "TEXT", "neighborhood": "TEXT", "lot_size": "TEXT",
    "list_price": "INTEGER", "list_date": "TEXT", "days_on_market": "INTEGER",
    "offer_price": "INTEGER", "offer_date": "TEXT", "contract_date": "TEXT",
    "close_price": "INTEGER", "close_date": "TEXT", "seller_concessions": "INTEGER",
    "assignment_type": "TEXT", "public_sale_ref": "TEXT", "town": "TEXT",
    "removal_hash": "TEXT",       # sha256 of the owner's private removal code; the code itself is never stored
}


def ensure_schema() -> None:
    with connect(readonly=False) as con:
        have = {r["name"] for r in con.execute("PRAGMA table_info(shared_appraisals)")}
        for col, typ in SHARED_EXTRA_COLUMNS.items():
            if col not in have:
                con.execute(f"ALTER TABLE shared_appraisals ADD COLUMN {col} {typ}")


_SUFFIX = re.compile(r"\b(CT|CONNECTICUT)\b.*$")
_SWAPS = {"STREET": "ST", "AVENUE": "AV", "AVE": "AV", "ROAD": "RD", "DRIVE": "DR", "PLACE": "PL",
          "COURT": "CT", "TERRACE": "TER", "LANE": "LA", "LN": "LA", "BOULEVARD": "BLVD", "CIRCLE": "CIR"}


def towns() -> list[str]:
    with connect() as con:
        r = con.execute("SELECT value FROM meta WHERE key = 'towns'").fetchone()
    return json.loads(r["value"]) if r else ["New Haven"]


def match_town(text: str | None) -> str | None:
    if not text:
        return None
    t = " ".join(re.sub(r"[^A-Za-z ]", " ", text).split()).lower()
    for town in sorted(towns(), key=len, reverse=True):
        if town.lower() == t:
            return town
    return None


def split_address(text: str) -> tuple[str, str | None]:
    """'5 Pope Street, New Haven, CT 06512' -> ('5 POPE ST', 'New Haven')."""
    raw = re.sub(r"[^A-Za-z0-9 ,]", " ", text or "").upper()
    town = None
    for t in sorted(towns(), key=len, reverse=True):
        # Only a town at the END counts ("..., New Haven, CT 06511"), so "103 Portland St" stays a street.
        m = re.search(r"(,|\s)\s*" + t.upper() + r"\s*(,?\s*(CT|CONNECTICUT))?\s*,?\s*(\d{5}(-\d{4})?)?\s*$", raw)
        if m:
            town, raw = t, raw[: m.start()]
            break
    raw = _SUFFIX.sub("", raw.replace(",", " "))
    words = [_SWAPS.get(w, w) for w in raw.split()]
    return " ".join(words), town


def normalize_address(text: str) -> str:
    return split_address(text)[0]


# Towns spell street suffixes differently in the state data (New Haven "AV", Milford "AVE").
# ...and some towns write the word out in full (Stamford "ROAD", "STREET").
_SUFFIX_VARIANTS = [{"AV", "AVE", "AVENUE"}, {"LA", "LN", "LANE"}, {"TER", "TERR", "TERRACE"}, {"CT", "CRT", "COURT"},
                    {"CIR", "CIRCLE"}, {"PKWY", "PKY", "PARKWAY"}, {"HWY", "HGWY", "HIGHWAY"}, {"TPKE", "TPK", "TURNPIKE"},
                    {"ST", "STREET"}, {"RD", "ROAD"}, {"DR", "DRIVE"}, {"PL", "PLACE"}, {"BLVD", "BOULEVARD"},
                    {"SQ", "SQUARE"}, {"EXT", "EXTENSION"}, {"WAY"}, {"TRL", "TRAIL"}]


def address_variants(addr: str) -> list[str]:
    words = addr.split()
    if not words:
        return [addr]
    for group in _SUFFIX_VARIANTS:
        if words[-1] in group:
            return [" ".join(words[:-1] + [w]) for w in sorted(group)]
    return [addr]


def _sale(r: sqlite3.Row) -> dict:
    return {
        "ref": f"sale:{r['id']}",
        "address": r["address"],
        "town": r["town"],
        "date": r["date"],
        "sale_price": r["sale_amount"],
        "assessed_value": r["assessed_value"],
        "type": r["residential_type"],
        "arms_length": not r["non_use_code"],
        "non_use_code": r["non_use_code"] or None,
        "tract": r["tract_id"],
    }


def _edit_distance(a: str, b: str) -> int:
    """Damerau-Levenshtein distance (an adjacent swap counts as one edit)."""
    d = [[i] + [0] * len(b) for i in range(len(a) + 1)]
    for j in range(1, len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[len(a)][len(b)]


def fuzzy_towns(query: str, limit: int = 6) -> list[str]:
    """Towns matching a prefix, a word inside the name, or a small typo ("haevn" -> New Haven)."""
    q = (query or "").strip().lower()
    if not q or q[:1].isdigit():
        return []
    scored = []
    allowed = 0 if len(q) <= 3 else 1 if len(q) <= 6 else 2
    for town in towns():
        t = town.lower()
        words = t.split()
        if t.startswith(q):
            score = 0
        elif any(w.startswith(q) for w in words):
            score = 1
        elif len(q) >= 3 and q in t:
            score = 2
        elif len(q.split()) > 1 and len(q.split()) <= len(words) and all(words[i].startswith(w) for i, w in enumerate(q.split())):
            score = 1  # "w hart" -> West Hartford
        else:
            best = min(min(_edit_distance(q, w), _edit_distance(q, w[: len(q)])) for w in [t] + words)
            score = 3 + best if best <= allowed else None
        if score is not None:
            scored.append((score, town))
    return [town for _, town in sorted(scored)[:limit]]


def search(query: str, limit: int = 8, county: str | None = None) -> dict:
    """Towns and addresses matching what the user typed. With a county (e.g. "Fairfield County"),
    that county's addresses come first, then a few from elsewhere."""
    q, town = split_address(query)
    ql = (query or "").strip().lower()
    area_hits = fuzzy_towns(ql)
    if len(q) < 2:
        return {"areas": area_hits, "addresses": []}
    likes = address_variants(q)
    cond = " OR ".join("address LIKE ?" for _ in likes)

    def run(towns: list[str] | None, n: int, exclude: list[str] | None = None) -> list[dict]:
        where = f" WHERE ({cond})"
        args: list = [f"%{v}%" for v in likes]
        if towns is not None:
            where += f" AND town IN ({','.join('?' * len(towns))})"
            args += towns
        if exclude:
            where += f" AND town NOT IN ({','.join('?' * len(exclude))})"
            args += exclude
        sql = ("SELECT address, town, MAX(date) AS last_date, COUNT(*) AS n, residential_type FROM sales" + where +
               " GROUP BY address, town ORDER BY (address LIKE ?) DESC, address LIMIT ?")
        with connect() as con:
            return [dict(r) for r in con.execute(sql, args + [f"{q}%", n]).fetchall()]

    in_county = [a["town"] for a in areas() if a.get("county_name") == county] if county and not town else []
    if town:
        rows = run([town], limit)
    elif in_county:
        rows = run(in_county, limit) + run(None, 4, exclude=in_county)
    else:
        rows = run(None, limit)
    return {"areas": area_hits, "addresses": rows}


def search_addresses(query: str, limit: int = 8) -> list[dict]:
    return search(query, limit)["addresses"]


def find_property(address: str, town: str | None = None) -> dict:
    """Exact (normalized) address match in the CT sales records."""
    q, town_in_text = split_address(address)
    town = match_town(town) or town_in_text
    with connect() as con:
        variants = address_variants(q)
        marks = ",".join("?" * len(variants))
        sql = f"SELECT * FROM sales WHERE address IN ({marks})" + (" AND town = ?" if town else "") + " ORDER BY date DESC"
        rows = con.execute(sql, variants + ([town] if town else [])).fetchall()
        if rows:
            q = rows[0]["address"]  # use the state's own spelling from here on
        towns_hit = sorted({r["town"] for r in rows})
        if not rows or len(towns_hit) > 1:
            if rows:
                choices = [{"address": q, "town": t} for t in towns_hit]
            else:
                close = con.execute(
                    "SELECT DISTINCT address, town FROM sales WHERE address LIKE ?" + (" AND town = ?" if town else "") + " LIMIT 6",
                    (f"%{q}%", town) if town else (f"%{q}%",),
                ).fetchall()
                choices = [dict(r) for r in close]
            return {"found": False, "query": q, "town": town, "ambiguous": len(towns_hit) > 1, "did_you_mean": choices}
    tract = next((r["tract_id"] for r in rows if r["tract_id"]), None)
    return {
        "found": True,
        "address": q,
        "town": rows[0]["town"],
        "tract": tract,
        "tract_name": tract_name(tract),
        "tract_label": tract_label(tract, rows[0]["town"]),
        "type": rows[0]["residential_type"],
        "sales": [_sale(r) for r in rows],
    }


_STREET_CACHE: dict[tuple, list[str]] = {}


def tract_streets(tract_id: str, town: str | None = None, n: int = 2) -> list[str]:
    """The most common streets with sales in a tract, e.g. ['Townsend Ave', 'Fort Hale Rd']."""
    key = (tract_id, town, n)
    if key not in _STREET_CACHE:
        with connect() as con:
            rows = con.execute(
                "SELECT address FROM sales WHERE tract_id = ?" + (" AND town = ?" if town else ""),
                (tract_id, town) if town else (tract_id,)).fetchall()
        counts: dict[str, int] = {}
        for r in rows:
            addr = re.sub(r"\s+(U|UNIT|APT|#)\s*[\w-]+$", "", r["address"])
            addr = re.sub(r"\s+[A-Z]?\d+[A-Z]?$", "", addr) if re.search(r"\b(ST|AV|AVE|RD|DR|LA|LN|CT|PL|TER|CIR|BLVD)\s+[A-Z]?\d+[A-Z]?$", addr) else addr
            parts = addr.split()
            street = " ".join(parts[1:]) if parts and parts[0][:1].isdigit() else addr
            if street:
                counts[street] = counts.get(street, 0) + 1
        top = sorted(counts, key=lambda k: (-counts[k], k))[:n]
        pretty = {"AV": "Ave", "AVE": "Ave", "AVENUE": "Ave", "LA": "Ln", "LN": "Ln", "LANE": "Ln", "TER": "Ter",
                  "TERR": "Ter", "STREET": "St", "ROAD": "Rd", "DRIVE": "Dr", "PLACE": "Pl", "COURT": "Ct"}
        def nice(street: str) -> str:  # abbreviate only the suffix: "COURT ST" -> "Court St"
            words = street.split()
            return " ".join([w.capitalize() for w in words[:-1]] + [pretty.get(words[-1], words[-1].capitalize())])
        _STREET_CACHE[key] = [nice(st) for st in top]
    return _STREET_CACHE[key]


def tract_label(tract_id: str | None, town: str | None = None) -> str | None:
    """A human name for a tract: the neighborhood/village most of its sales sit closest to
    (OpenStreetMap), falling back to its busiest street."""
    if not tract_id:
        return None
    with connect() as con:
        try:
            r = con.execute("SELECT area_name FROM tract_areas WHERE tract_id = ? AND (town = ? OR ? IS NULL)"
                            " ORDER BY share DESC LIMIT 1", (tract_id, town, town)).fetchone()
        except sqlite3.OperationalError:
            r = None
    if r and r["area_name"] and r["area_name"] != town:
        return r["area_name"]
    streets = tract_streets(tract_id, town, n=1)
    return streets[0] if streets else None


def tract_name(tract_id: str | None) -> str | None:
    if not tract_id:
        return None
    with connect() as con:
        r = con.execute("SELECT name FROM tracts WHERE tract_id = ?", (tract_id,)).fetchone()
    return r["name"] if r else f"Census Tract {tract_id}"


def _fhfa(con, geo: str, purpose: str | None = None, quarter: int = 5) -> list[dict]:
    sql = "SELECT * FROM tract_appraisals WHERE tract_id = ? AND quarter = ?"
    args: list = [geo, quarter]
    if purpose:
        sql += " AND purpose = ?"
        args.append(purpose)
    rows = con.execute(sql + " ORDER BY year DESC", args).fetchall()
    return [
        {
            "ref": f"fhfa:{geo}:{r['purpose']}:{r['year']}{'' if r['quarter'] == 5 else 'Q' + str(r['quarter'])}:{r['series']}",
            "geo": geo_name(geo),
            "purpose": r["purpose"],
            "year": r["year"],
            "series": r["series"],
            "label": SERIES_LABELS.get(r["series"], r["series"]),
            "value": r["value"],
        }
        for r in rows
    ]


def _latest_by_series(records: list[dict], purpose_order=("Purchase", "Both", "Refinance")) -> dict:
    """Most recent record per series, preferring purchase appraisals."""
    best: dict[str, dict] = {}
    for rec in records:
        cur = best.get(rec["series"])
        key = (rec["year"], -purpose_order.index(rec["purpose"]) if rec["purpose"] in purpose_order else -9)
        if cur is None or key > (cur["year"], -purpose_order.index(cur["purpose"]) if cur["purpose"] in purpose_order else -9):
            best[rec["series"]] = rec
    return best


COUNTY_NAMES = {"09001": "Fairfield County", "09003": "Hartford County", "09005": "Litchfield County",
                "09007": "Middlesex County", "09009": "New Haven County", "09011": "New London County",
                "09013": "Tolland County", "09015": "Windham County", "CT": "Connecticut"}


def geo_name(geo: str) -> str:
    if geo in COUNTY_NAMES:
        return COUNTY_NAMES[geo]
    return f"tract {geo}"


def county_of(tract_id: str | None, town: str | None = None) -> str:
    if tract_id and len(tract_id) == 11:
        return tract_id[:5]
    tc = _meta_json("town_county")
    return tc.get(town or "", "CT")  # unknown town: statewide figures, never another county's


_META: dict = {}


def _meta_json(key: str):
    if key not in _META:
        with connect() as con:
            r = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        _META[key] = json.loads(r["value"]) if r else {}
    return _META[key]


def appraisal_stats(tract_id: str | None, town: str | None = None) -> dict:
    """Real appraisal statistics (FHFA UAD) for the tract, with county figures as fallback."""
    county = county_of(tract_id, town)
    with connect() as con:
        tract_recs = _fhfa(con, tract_id) if tract_id else []
        county_recs = _fhfa(con, county)
    tract_latest = _latest_by_series(tract_recs)
    county_latest = _latest_by_series([r for r in county_recs if r["purpose"] == "Purchase"])
    trend = sorted(
        ({"year": r["year"], "purpose": r["purpose"], "median": r["value"], "ref": r["ref"]}
         for r in tract_recs if r["series"] == "MEDIAN" and r["purpose"] in ("Both", "Purchase")),
        key=lambda x: (x["year"], x["purpose"] != "Purchase"),
    )
    seen, median_trend = set(), []
    for t in trend:  # one point per year, purchase preferred
        if t["year"] not in seen:
            seen.add(t["year"])
            median_trend.append(t)
        elif t["purpose"] == "Purchase":
            median_trend[-1] = t
    return {
        "source": "FHFA Uniform Appraisal Dataset Aggregate Statistics (real appraisals for Fannie Mae / Freddie Mac loans)",
        "tract": tract_id,
        "tract_latest": tract_latest,
        "county": county, "county_name": geo_name(county),
        "county_latest_purchase": county_latest,
        "tract_median_trend": median_trend,
        "note": "FHFA hides tract figures when there are too few appraisals; county figures fill the gap.",
    }


def lender_values(tract_id: str | None) -> dict:
    """HMDA 2024: the property value each lender relied on (usually the appraisal) for loans in the tract."""
    if not tract_id:
        return {"found": False}
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM lender_values WHERE tract_id = ? ORDER BY property_value", (tract_id,)
        ).fetchall()
    values = [r["property_value"] for r in rows]
    if not values:
        return {"found": False, "tract": tract_id}
    return {
        "found": True,
        "source": "HMDA 2024 (CFPB). property_value = the home value the lender relied on (usually the appraisal, sometimes the purchase price). Not the loan amount; no asking price. Published in $10,000 bands (e.g. 285000 = $280k-$290k)",
        "ref": f"hmda:2024:{tract_id}",
        "tract": tract_id,
        "loans": len(values),
        "min": min(values),
        "median": int(statistics.median(values)),
        "max": max(values),
        "values": values,
    }


def shared_appraisals(address: str | None = None, tract_id: str | None = None, town: str | None = None) -> list[dict]:
    with connect() as con:
        if address:
            q, t = split_address(address)
            t = match_town(town) or t
            variants = address_variants(q)
            marks = ",".join("?" * len(variants))
            rows = con.execute(
                f"SELECT * FROM shared_appraisals WHERE address IN ({marks})" + (" AND town = ?" if t else "") +
                " ORDER BY COALESCE(close_date, effective_date) DESC", variants + ([t] if t else []),
            ).fetchall()
        elif tract_id:
            rows = con.execute(
                "SELECT * FROM shared_appraisals WHERE tract_id = ? ORDER BY COALESCE(close_date, effective_date) DESC", (tract_id,)
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM shared_appraisals" + (" WHERE town = ?" if town else "") +
                " ORDER BY COALESCE(close_date, effective_date) DESC", (town,) if town else (),
            ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["ref"] = f"shared:{r['id']}"
        d.pop("removal_hash", None)
        d["comps"] = json.loads(d.pop("comps_json") or "[]")
        d["verification"] = json.loads(d.pop("verification_json") or "{}")
        d["is_sample"] = bool(d["is_sample"])
        d["document_types"] = json.loads(d.get("document_types") or "[]") or ["appraisal"]
        out.append(d)
    return out


def _pct(a: float, b: float) -> float:
    return round((a - b) / b * 100, 1)


def how_often(offer: int, prop: dict, asking: int | None = None) -> dict:
    """How often real homes nearby reached this price, and how often appraisals held up.

    Every figure is a count or share taken straight from a record; nothing is estimated.
    """
    tract, town = prop.get("tract"), prop.get("town")
    out: dict = {"offer": offer, "asking": asking}
    with connect() as con:
        # 1. Sales: how many arm's-length sales of the same home type in this neighborhood hit the offer.
        if tract:
            start, end = _window(con, town)
            rows = [r["sale_amount"] for r in con.execute(
                "SELECT sale_amount FROM sales WHERE tract_id = ? AND town = ? AND non_use_code = '' AND residential_type IS ?"
                " AND date > ? AND date <= ?", (tract, town, prop.get("type"), start, end))]
            if len(rows) < 5:  # thin market: widen to two years
                y, m, d = (int(x) for x in end.split("-"))
                start = f"{y - 2}-{m:02d}-{d:02d}"
                rows = [r["sale_amount"] for r in con.execute(
                    "SELECT sale_amount FROM sales WHERE tract_id = ? AND town = ? AND non_use_code = '' AND residential_type IS ?"
                    " AND date > ? AND date <= ?", (tract, town, prop.get("type"), start, end))]
            if rows:
                out["sales"] = {"ref": f"sales:{tract}:{start}:{end}", "at_or_above": sum(v >= offer for v in rows),
                                "total": len(rows), "home_type": prop.get("type"), "window": [start, end]}
        # 2. Mortgages: how many 2024 loans here used a value (usually the appraisal) at or above the offer.
        lv = [r["property_value"] for r in con.execute("SELECT property_value FROM lender_values WHERE tract_id = ?", (tract,))] if tract else []
        if lv:
            out["mortgages"] = {"ref": f"hmda:2024:{tract}", "at_or_above": sum(v >= offer for v in lv), "total": len(lv)}
    # 3. Appraisals vs. the agreed price (FHFA): neighborhood if published, else county.
    stats = appraisal_stats(tract, town)
    tl, cl = stats["tract_latest"], stats["county_latest_purchase"]
    src = tl if "PERBEL" in tl else cl
    geo = "neighborhood" if src is tl else stats["county_name"]
    if "PERBEL" in src:
        out["appraisal_vs_price"] = {
            "ref": src["PERBEL"]["ref"], "where": geo, "year": src["PERBEL"]["year"],
            "below": src["PERBEL"]["value"], "equal": src.get("PEREQ", {}).get("value"), "above": src.get("PERABV", {}).get("value"),
            "avg_appraisal_to_price": src.get("RATIO1", {}).get("value"),
        }
    # Where the offer sits among this neighborhood's appraisals (quartiles only; no estimate).
    if all(k in tl for k in ("P25", "MEDIAN", "P75")):
        p25, med, p75 = tl["P25"]["value"], tl["MEDIAN"]["value"], tl["P75"]["value"]
        band = ("below the lowest quarter" if offer < p25 else "in the lower middle" if offer < med
                else "in the upper middle" if offer <= p75 else "above three out of four")
        out["appraisal_range"] = {"ref": tl["MEDIAN"]["ref"], "year": tl["MEDIAN"]["year"], "p25": p25, "median": med, "p75": p75,
                                  "band": band}
    # 4. Over asking: only shared records carry an asking (list) price.
    if asking:
        over = (offer - asking) / asking * 100
        # This home's own sale is already shown at the top of its page, so it isn't compared with itself.
        own = set(address_variants(prop.get("address") or ""))

        def others(rows: list[dict]) -> list[dict]:
            return [x for x in rows if x.get("list_price") and not (x["town"] == town and (x["address"] in own or set(address_variants(x["address"])) & own))]
        pool = others(shared_appraisals(tract_id=tract)) if tract else []
        scope = "neighborhood"
        if len(pool) < 3 and town:
            pool = others(shared_appraisals(town=town))
            scope = town
        # The question buyers actually ask: how often did an appraisal come in this far over asking?
        appraised = [x for x in pool if x.get("appraised_value")]
        reached = [x for x in appraised if (x["appraised_value"] - x["list_price"]) / x["list_price"] * 100 >= over - 0.05]
        out["over_asking"] = {"ref": "shared:" + ",".join(str(x["id"]) for x in appraised) if appraised else "shared:none",
                              "your_over_asking_pct": round(over, 1), "scope": scope,
                              "with_appraisal_and_list": len(appraised), "appraised_this_far_over": len(reached),
                              "all_samples": bool(appraised) and all(x["is_sample"] for x in appraised),
                              **_similar_over_asking(appraised, over),
                              }
    return out


SIMILAR_BAND = 3.0  # percentage points either side of the buyer's own % over asking


def _similar_over_asking(appraised: list[dict], over: float) -> dict:
    """Shared homes that went about as far over asking as this offer, and where each appraised vs. asking.

    "Went over" uses the agreed (contract) price, else the accepted offer. Within +/- SIMILAR_BAND points;
    if none are that close, the nearest three are returned and marked as not close.
    """
    rows = []
    for x in appraised:
        paid = x.get("contract_price") or x.get("offer_price")
        if not paid:
            continue
        went = (paid - x["list_price"]) / x["list_price"] * 100
        rows.append({"id": x["id"], "address": x["address"], "town": x["town"], "is_sample": bool(x["is_sample"]),
                     "list_price": x["list_price"], "paid": paid, "appraised_value": x["appraised_value"],
                     "went_over_asking_pct": round(went, 1),
                     "appraised_vs_asking_pct": round((x["appraised_value"] - x["list_price"]) / x["list_price"] * 100, 1),
                     "appraised_at_or_above_paid": x["appraised_value"] >= paid})
    rows.sort(key=lambda r: abs(r["went_over_asking_pct"] - over))
    close = [r for r in rows if abs(r["went_over_asking_pct"] - over) <= SIMILAR_BAND]
    return {"similar_band_pts": SIMILAR_BAND, "similar_is_close": bool(close), "similar": (close or rows[:3])[:6]}


def compare_offer(offer: int, address: str, town: str | None = None, asking: int | None = None) -> dict:
    """Place an offer (and the asking price the buyer sees) next to the actual records.
    Pure arithmetic, no forecasting."""
    prop = find_property(address, town)
    if not prop.get("found"):
        return {"found": False, "address": address, "did_you_mean": prop.get("did_you_mean")}
    tract = prop["tract"]
    lv = lender_values(tract)
    stats = appraisal_stats(tract, prop["town"])
    shared = shared_appraisals(address=prop["address"], town=prop["town"])
    facts: list[dict] = []

    if asking:
        facts.append({
            "ref": "user:asking",
            "fact": f"the offer is {abs(offer - asking)} {'above' if offer >= asking else 'below'} the asking price the buyer entered ({_pct(offer, asking)}% {'over' if offer >= asking else 'under'} asking)",
            "offer_minus_asking": offer - asking,
            "offer_vs_asking_pct": _pct(offer, asking),
        })

    # Real over-asking evidence: shared records nearby that include a list price.
    nearby = [x for x in shared_appraisals(tract_id=tract)  # other homes only; this home's own record is listed separately
              if x.get("list_price") and (x["address"], x["town"]) != (prop["address"], prop["town"])] if tract else []
    for x in nearby[:4]:
        row = {"ref": x["ref"], "address": x["address"], "is_sample": x["is_sample"], "list_price": x["list_price"]}
        bits = []
        if x.get("contract_price"):
            row["contract_vs_list_pct"] = _pct(x["contract_price"], x["list_price"])
            bits.append(f"went under contract {row['contract_vs_list_pct']}% vs. list")
        if x.get("appraised_value"):
            row["appraisal_vs_list_pct"] = _pct(x["appraised_value"], x["list_price"])
            bits.append(f"appraised {row['appraisal_vs_list_pct']}% vs. list")
        if x.get("close_price"):
            row["close_vs_list_pct"] = _pct(x["close_price"], x["list_price"])
            bits.append(f"closed {row['close_vs_list_pct']}% vs. list")
        row["fact"] = f"{'SAMPLE (demo, not real) ' if x['is_sample'] else ''}shared record nearby, {x['address']}: listed at {x['list_price']}, " + ", ".join(bits)
        facts.append(row)

    if lv.get("found"):
        at_or_above = sum(1 for v in lv["values"] if v >= offer)
        facts.append({
            "ref": lv["ref"],
            "fact": f"{at_or_above} of {lv['loans']} mortgages made in this tract in 2024 used a home value (usually the appraisal) at or above the offer; this is not the loan amount and says nothing about asking prices",
            "count_at_or_above": at_or_above,
            "loans": lv["loans"],
            "median_value": lv["median"],
        })
    tl = stats["tract_latest"]
    for s in ("P25", "MEDIAN", "P75"):
        if s in tl:
            rec = tl[s]
            facts.append({
                "ref": rec["ref"],
                "fact": f"{rec['label']} in this tract ({rec['purpose'].lower()} appraisals, {rec['year']}): {int(rec['value'])}",
                "offer_minus_value": offer - int(rec["value"]),
            })
    cl = stats["county_latest_purchase"]
    if "PERBEL" in cl:
        rec = cl["PERBEL"]
        facts.append({
            "ref": rec["ref"],
            "fact": f"{rec['label']} across {stats['county_name']} purchase appraisals, {rec['year']}: {rec['value']}",
        })
    if "PERBEL" in tl:
        rec = tl["PERBEL"]
        facts.append({"ref": rec["ref"], "fact": f"{rec['label']} in this tract, {rec['year']}: {rec['value']}"})
    arms = [s for s in prop["sales"] if s["arms_length"]]
    if arms:
        last = arms[0]
        facts.append({
            "ref": last["ref"],
            "fact": f"last arm's-length sale of this home: {last['sale_price']} on {last['date']} (a sale price, not an appraisal)",
            "offer_minus_sale": offer - last["sale_price"],
        })
    for sh in shared[:1]:
        label = "SAMPLE (demo, not real) " if sh["is_sample"] else ""
        chain = {k: sh.get(k) for k in ("list_price", "offer_price", "contract_price", "appraised_value", "close_price") if sh.get(k)}
        text = f"{label}shared record for this home: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in chain.items())
        fact = {"ref": sh["ref"]}
        if sh.get("appraised_value") and sh.get("list_price"):
            fact["appraisal_vs_list_pct"] = _pct(sh["appraised_value"], sh["list_price"])
            text += f"; appraised {fact['appraisal_vs_list_pct']}% vs. its list price"
        fact["fact"] = text
        if sh.get("appraised_value"):
            fact["appraisal_effective"] = sh.get("effective_date")
        facts.append(fact)
    return {"found": True, "offer": offer, "asking": asking, "address": prop["address"], "town": prop["town"], "tract": tract,
            "how_often": how_often(offer, prop, asking), "facts": facts}


def property_panel(address: str, town: str | None = None) -> dict:
    """Everything the lookup page shows, without any model call."""
    prop = find_property(address, town)
    if not prop.get("found"):
        return prop
    tract = prop["tract"]
    lv = lender_values(tract)
    return {
        **prop,
        "appraisals": appraisal_stats(tract, prop["town"]),
        "lender_values": lv,
        "shared": shared_appraisals(address=prop["address"], town=prop["town"]),
        "shared_nearby": [s for s in shared_appraisals(tract_id=tract) if s["address"] != prop["address"]][:5] if tract else [],
        "as_of": date.today().isoformat(),
    }


def meta() -> dict:
    with connect() as con:
        return {r["key"]: r["value"] for r in con.execute("SELECT * FROM meta")}


# ---------------------------------------------------------------- areas

def _median(xs):
    return int(statistics.median(xs)) if xs else None


def _window(con, town: str | None = None) -> tuple[str, str]:
    """Last 12 months of recorded sales for a town. Towns report on different
    schedules (some have no 2025 list year yet), so each town uses its own latest date."""
    sql, args = "SELECT MAX(date) AS d FROM sales", ()
    if town:
        sql, args = sql + " WHERE town = ?", (town,)
    end = con.execute(sql, args).fetchone()["d"]
    y, m, d = (int(x) for x in end.split("-"))
    return f"{y - 1}-{m:02d}-{d:02d}", end


def _town_tracts(con, town: str) -> list[str]:
    """Tracts that really belong to a town. A few geocoded points land just over a
    town line, so a tract needs at least 10 of the town's sales to count."""
    return [r["tract_id"] for r in con.execute(
        "SELECT tract_id FROM sales WHERE town = ? AND tract_id IS NOT NULL GROUP BY tract_id HAVING COUNT(*) >= 10 ORDER BY tract_id",
        (town,))]


_AREAS_CACHE: list[dict] | None = None


def areas() -> list[dict]:
    """One card per town. The public-record figures are computed once per process
    (169 towns); shared-record counts are always live."""
    global _AREAS_CACHE
    if _AREAS_CACHE is None:
        _AREAS_CACHE = _compute_areas()
    with connect() as con:
        # Demo samples never count toward totals; they're reported separately.
        rows = con.execute("SELECT town, SUM(is_sample = 0) AS real, SUM(is_sample = 1) AS samples FROM shared_appraisals GROUP BY town").fetchall()
    counts = {r["town"]: (r["real"] or 0, r["samples"] or 0) for r in rows}
    return [{**a, "shared_records": counts.get(a["town"], (0, 0))[0], "sample_records": counts.get(a["town"], (0, 0))[1]}
            for a in _AREAS_CACHE]


def _compute_areas() -> list[dict]:
    out = []
    tc = _meta_json("town_county")
    with connect() as con:
        for town in towns():
            start, end = _window(con, town)
            sales = [r["sale_amount"] for r in con.execute(
                "SELECT sale_amount FROM sales WHERE town = ? AND non_use_code = '' AND residential_type = 'Single Family'"
                " AND date > ? AND date <= ?", (town, start, end))]
            lv = [r["property_value"] for r in con.execute("SELECT property_value FROM lender_values WHERE town = ?", (town,))]
            tracts = _town_tracts(con, town)
            q = ",".join("?" * len(tracts)) or "''"
            fhfa = con.execute(
                f"SELECT SUM(value) AS n FROM tract_appraisals WHERE series = 'COUNT' AND purpose = 'Both' AND year = 2023 AND tract_id IN ({q})",
                tracts).fetchone()["n"]
            total = con.execute(
                f"SELECT SUM(value) AS n, MIN(year) AS y0, MAX(year) AS y1 FROM tract_appraisals"
                f" WHERE series = 'COUNT' AND purpose = 'Both' AND quarter = 5 AND tract_id IN ({q})", tracts).fetchone()
            meds = [r["value"] for r in con.execute(
                f"SELECT value FROM tract_appraisals WHERE series = 'MEDIAN' AND purpose = 'Both' AND year = 2023 AND tract_id IN ({q})", tracts)]
            out.append({
                "town": town,
                "county": tc.get(town), "county_name": geo_name(tc.get(town, "")) if tc.get(town) else None,
                "sales_12m": len(sales),
                "median_sale_12m": _median(sales),
                "window": [start, end],
                "lender_loans_2024": len(lv),
                "median_lender_value_2024": _median(lv),
                "appraisals_2023": int(fhfa) if fhfa else None,
                # Running total of published appraisal counts (FHFA hides small yearly counts, so this is a floor).
                "appraisals_total": int(total["n"]) if total["n"] else None,
                "appraisal_years": [total["y0"], total["y1"]] if total["n"] else None,
                "tract_median_range_2023": [int(min(meds)), int(max(meds))] if meds else None,
                "tracts": len(tracts),
            })
    return out


_COUNTIES_CACHE: list[dict] | None = None


def counties() -> list[dict]:
    """One card per county: towns, recent single-family sales, mortgage home values, and FHFA appraisal figures."""
    global _COUNTIES_CACHE
    if _COUNTIES_CACHE is None:
        by_county: dict[str, list[dict]] = {}
        for a in areas():
            if a.get("county"):
                by_county.setdefault(a["county"], []).append(a)
        out = []
        with connect() as con:
            for fips, towns_in in sorted(by_county.items(), key=lambda kv: geo_name(kv[0])):
                sales, values = [], []
                for a in towns_in:
                    start, end = a["window"]
                    sales += [r["sale_amount"] for r in con.execute(
                        "SELECT sale_amount FROM sales WHERE town = ? AND non_use_code = '' AND residential_type = 'Single Family'"
                        " AND date > ? AND date <= ?", (a["town"], start, end))]
                    values += [r["property_value"] for r in con.execute("SELECT property_value FROM lender_values WHERE town = ?", (a["town"],))]
                latest = _latest_by_series([r for r in _fhfa(con, fips) if r["purpose"] == "Purchase"])
                out.append({
                    "county": fips, "county_name": geo_name(fips), "towns": len(towns_in),
                    "sales_12m": len(sales), "median_sale_12m": _median(sales),
                    "median_lender_value_2024": _median(values), "lender_loans_2024": len(values),
                    "median_appraisal": latest.get("MEDIAN", {}).get("value"), "median_appraisal_year": latest.get("MEDIAN", {}).get("year"),
                    "share_below": latest.get("PERBEL", {}).get("value"), "share_below_year": latest.get("PERBEL", {}).get("year"),
                })
        _COUNTIES_CACHE = out
    return _COUNTIES_CACHE


def state_appraisal_total() -> dict:
    with connect() as con:
        r = con.execute("SELECT SUM(value) AS n, MIN(year) AS y0, MAX(year) AS y1 FROM tract_appraisals"
                        " WHERE tract_id = 'CT' AND series = 'COUNT' AND purpose = 'Both' AND quarter = 5").fetchone()
    return {"total": int(r["n"] or 0), "years": [r["y0"], r["y1"]]}


def county_gap_trend(geo: str = "CT") -> list[dict]:
    """Share of purchases where the contract price was higher than the appraisal, by year, for the
    whole state ('CT') or one county (5-digit FIPS)."""
    with connect() as con:
        rows = con.execute(
            "SELECT year, value FROM tract_appraisals WHERE tract_id = ? AND purpose = 'Purchase'"
            " AND series = 'PERBEL' AND quarter = 5 ORDER BY year", (geo,)).fetchall()
    return [{"year": r["year"], "share_below": r["value"], "geo": geo_name(geo),
             "ref": f"fhfa:{geo}:Purchase:{r['year']}:PERBEL"} for r in rows]


def tract_sales(town: str, tract: str) -> dict:
    """The arm's-length sales behind a neighborhood's "Sales, 12 mo" count (same window, same filter)."""
    town = match_town(town)
    if not town:
        return {"found": False}
    with connect() as con:
        start, end = _window(con, town)
        rows = [_sale(r) for r in con.execute(
            "SELECT * FROM sales WHERE tract_id = ? AND town = ? AND non_use_code = '' AND date > ? AND date <= ?"
            " ORDER BY date DESC", (tract, town, start, end))]
    return {"found": True, "town": town, "tract": tract, "label": tract_label(tract, town), "window": [start, end], "sales": rows}


def area_detail(town: str) -> dict:
    town = match_town(town)
    if not town:
        return {"found": False}
    card = next(a for a in areas() if a["town"] == town)
    with connect() as con:
        start, end = _window(con, town)
        tracts = []
        for t in _town_tracts(con, town):
            latest = _latest_by_series(_fhfa(con, t))
            lv = [r["property_value"] for r in con.execute("SELECT property_value FROM lender_values WHERE tract_id = ?", (t,))]
            sales = [r["sale_amount"] for r in con.execute(
                "SELECT sale_amount FROM sales WHERE tract_id = ? AND town = ? AND non_use_code = '' AND date > ? AND date <= ?",
                (t, town, start, end))]
            tracts.append({
                "tract": t, "name": tract_name(t), "label": tract_label(t, town),
                "appraisal_median": latest.get("MEDIAN", {}).get("value"),
                "appraisal_year": latest.get("MEDIAN", {}).get("year"),
                "appraisal_p25": latest.get("P25", {}).get("value"),
                "appraisal_p75": latest.get("P75", {}).get("value"),
                "share_below": latest.get("PERBEL", {}).get("value"),
                "share_below_year": latest.get("PERBEL", {}).get("year"),
                "lender_loans": len(lv), "lender_median": _median(lv),
                "sales_12m": len(sales), "sale_median_12m": _median(sales),
            })
        recent = [_sale(r) for r in con.execute(
            "SELECT * FROM sales WHERE town = ? AND non_use_code = '' ORDER BY date DESC LIMIT 24", (town,))]
    return {"found": True, **card, "tract_rows": tracts, "recent_sales": recent,
            "shared": shared_appraisals(town=town), "county_gap_trend": county_gap_trend(card.get("county") or "CT")}
