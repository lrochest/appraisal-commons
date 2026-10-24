"""Build data/appraisal_commons.db from public raw files, for all of Connecticut.

Only needed to rebuild the bundled database. The app itself just reads the
.db file that ships in the repo.

Raw inputs (not committed):
  raw_data/ct_sales/ct_sales.json        CT OPM Real Estate Sales (data.ct.gov 5mzw-sjtu), residential, recorded 2019+
  raw_data/tiger/tl_2020_09_tract.shp    Census TIGER 2020 tract boundaries for Connecticut
  raw_data/fhfa/ct_all_tract.csv         FHFA UAD Aggregate Statistics v3.3, Enterprise single-family, CT tracts
                                         (unzip -p UADAggs_ent_sf_tract_v3_3.zip | grep ',CT,09,' | grep ',No Characteristic,')
  raw_data/fhfa/ct_all_county.csv        same, county level
  raw_data/fhfa/ct_state.csv             same, statewide (from UADAggs_ent_sf_state_v3_3.zip)
  hmda CSV (--hmda)                      HMDA 2024 Connecticut extract with census_tract + property_value

Tracts are keyed by their 11-digit Census GEOID (2020 vintage, old CT counties),
because a handful of 6-digit tract numbers repeat across Connecticut counties.

Usage:
  python scripts/build_data.py --raw ../raw_data --hmda ~/Downloads/hmda_CT_2024_applications_DATA_ONLY.csv
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import sqlite3
from pathlib import Path

import sys

import shapefile
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from app.tickets import SCHEMA as TICKETS_SCHEMA  # noqa: E402

OUT = REPO / "data" / "appraisal_commons.db"
FIRST_YEAR = 2015  # FHFA years kept (the app shows trends from here)
COUNTIES = {"09001": "Fairfield County", "09003": "Hartford County", "09005": "Litchfield County",
            "09007": "Middlesex County", "09009": "New Haven County", "09011": "New London County",
            "09013": "Tolland County", "09015": "Windham County"}

SCHEMA = """
CREATE TABLE tracts (
  tract_id TEXT PRIMARY KEY,    -- 11-digit Census GEOID, e.g. 09009142800
  name TEXT,                    -- e.g. Census Tract 1428
  county_fips TEXT, county_name TEXT,
  lat REAL, lon REAL
);
CREATE TABLE sales (
  id INTEGER PRIMARY KEY,
  town TEXT,
  date TEXT, address TEXT, residential_type TEXT,
  sale_amount INTEGER, assessed_value INTEGER,
  non_use_code TEXT,            -- non-empty = not an arm's-length sale (family, foreclosure, ...)
  tract_id TEXT
);
CREATE INDEX sales_address ON sales(address, town);
CREATE INDEX sales_town ON sales(town, date);
CREATE INDEX sales_tract ON sales(tract_id);
CREATE TABLE lender_values (
  id INTEGER PRIMARY KEY,
  year INTEGER, town TEXT, tract_id TEXT,
  lender TEXT, loan_type TEXT, product TEXT, units TEXT,
  loan_amount INTEGER, property_value INTEGER, ltv REAL
);
CREATE INDEX lender_tract ON lender_values(tract_id);
CREATE INDEX lender_town ON lender_values(town);
CREATE TABLE tract_appraisals (
  tract_id TEXT,                -- 11-digit tract GEOID, or a 5-digit county FIPS for county-wide figures
  purpose TEXT, year INTEGER,
  quarter INTEGER,              -- 1-4, or 5 = full year
  series TEXT,                  -- COUNT, MEDIAN, P25, P75, RATIO1, PERBEL, PEREQ, PERABV
  value REAL,
  PRIMARY KEY (tract_id, purpose, year, quarter, series)
);
CREATE TABLE shared_appraisals (
  id INTEGER PRIMARY KEY,
  address TEXT, town TEXT, tract_id TEXT,
  effective_date TEXT, appraised_value INTEGER, contract_price INTEGER,
  property_type TEXT, gla_sqft INTEGER, bedrooms INTEGER, bathrooms REAL, year_built INTEGER,
  comps_json TEXT,              -- comparable sales from the report
  appraiser_license TEXT, license_state TEXT,   -- checked, never stored (always NULL)
  document_types TEXT,          -- JSON list: appraisal, listing, purchase_contract, closing_disclosure, owner_entry
  unit TEXT, zip TEXT, neighborhood TEXT, lot_size TEXT,
  list_price INTEGER, list_date TEXT, days_on_market INTEGER,
  offer_price INTEGER, offer_date TEXT, contract_date TEXT,
  close_price INTEGER, close_date TEXT, seller_concessions INTEGER,
  assignment_type TEXT,
  public_sale_ref TEXT,         -- CT sales record the stated closing matched, e.g. sale:259
  verification_json TEXT,       -- what the Verifier checked
  is_sample INTEGER NOT NULL DEFAULT 0,   -- 1 = clearly labeled demo sample, not a real record
  shared_at TEXT,
  removal_hash TEXT             -- sha256 of the owner's removal code
);
CREATE INDEX shared_address ON shared_appraisals(address);
CREATE INDEX shared_tract ON shared_appraisals(tract_id);
CREATE TABLE tract_areas (
  tract_id TEXT, town TEXT,
  area_name TEXT,               -- nearest named neighborhood/village (OpenStreetMap), by majority of sales
  share REAL,                   -- share of the tract's sales whose nearest place is area_name
  PRIMARY KEY (tract_id, town)
);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""
KEEP_SERIES = {"COUNT", "MEDIAN", "P25", "P75", "RATIO1", "PERBEL", "PEREQ", "PERABV"}


def load_tracts(raw: Path):
    reader = shapefile.Reader(str(raw / "tiger" / "tl_2020_09_tract.shp"))
    polys, geoids, rows = [], [], {}
    for sr in reader.iterShapeRecords():
        rec = sr.record
        polys.append(shape(sr.shape.__geo_interface__))
        geoids.append(rec["GEOID"])
        county = rec["GEOID"][:5]
        rows[rec["GEOID"]] = (rec["NAMELSAD"], county, COUNTIES.get(county, ""), float(rec["INTPTLAT"]), float(rec["INTPTLON"]))
    return STRtree(polys), polys, geoids, rows


def tract_for(tree, polys, geoids, lon, lat):
    pt = Point(lon, lat)
    for i in tree.query(pt):
        if polys[i].contains(pt):
            return geoids[i]
    return None


def _addr(x) -> str:
    return " ".join(x.get("address", "").upper().split())


def build_sales(db, raw, tree, polys, geoids):
    data = json.loads((raw / "ct_sales" / "ct_sales.json").read_text())
    # Some records lack coordinates; borrow them from another sale of the same address.
    known = {(x.get("town"), _addr(x)): x["geo_coordinates"]["coordinates"] for x in data if x.get("geo_coordinates")}
    town_county: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    rows, points = [], []
    for x in data:
        if not x.get("town") or not x.get("saleamount"):
            continue
        coords = (x.get("geo_coordinates") or {}).get("coordinates") or known.get((x["town"], _addr(x)))
        tract = tract_for(tree, polys, geoids, *coords) if coords else None
        if tract:
            town_county[x["town"]][tract[:5]] += 1
            points.append((x["town"], tract, coords[0], coords[1]))
        rows.append((x["town"], x["daterecorded"][:10], _addr(x), x.get("residentialtype"),
                     int(float(x["saleamount"])), int(float(x.get("assessedvalue") or 0)), x.get("nonusecode", ""), tract))
    db.executemany("INSERT INTO sales (town, date, address, residential_type, sale_amount, assessed_value,"
                   " non_use_code, tract_id) VALUES (?,?,?,?,?,?,?,?)", rows)
    towns = sorted({r[0] for r in rows})
    return len(rows), towns, {t: c.most_common(1)[0][0] for t, c in town_county.items()}, points


PLACE_TYPES = {"neighbourhood", "suburb", "quarter", "village"}  # hamlets are too small to name a tract


def build_area_names(db, raw: Path, points) -> int:
    """Name each (tract, town) by the nearest OpenStreetMap neighborhood/village to its sales, by majority vote.

    raw_data/osm/ct_places.json comes from the Overpass API:
      node["place"~"^(neighbourhood|suburb|quarter|village|hamlet)$"](40.95,-73.73,42.06,-71.78); out body;
    Data (c) OpenStreetMap contributors, ODbL.
    """
    path = raw / "osm" / "ct_places.json"
    if not path.exists():
        return 0
    places = [e for e in json.loads(path.read_text())["elements"]
              if e.get("tags", {}).get("name") and e["tags"].get("place") in PLACE_TYPES]
    geoms = [Point(e["lon"], e["lat"]) for e in places]
    tree = STRtree(geoms)
    votes: dict[tuple, collections.Counter] = collections.defaultdict(collections.Counter)
    for town, tract, lon, lat in points:
        i = tree.nearest(Point(lon, lat))
        # ~0.03 degrees is about 3 km; beyond that the nearest name doesn't describe the spot.
        if geoms[i].distance(Point(lon, lat)) < 0.03:
            votes[(tract, town)][places[i]["tags"]["name"]] += 1
    rows = []
    for (tract, town), c in votes.items():
        name, n = c.most_common(1)[0]
        rows.append((tract, town, name, round(n / sum(c.values()), 3)))
    db.executemany("INSERT INTO tract_areas VALUES (?,?,?,?)", rows)
    return len(rows)


def build_hmda(db, hmda_csv: Path, tract_rows: dict, town_county: dict):
    # HMDA 2024 uses CT's new planning-region county codes; tract numbers didn't change,
    # so map the 6-digit tract back to the 2020 GEOID (using the town's county when a number repeats).
    by_tractce: dict[str, list[str]] = collections.defaultdict(list)
    for geoid in tract_rows:
        by_tractce[geoid[5:]].append(geoid)
    n = 0
    with open(hmda_csv, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if r["action_taken"] != "1" or not r["property_value"] or r["property_value"] in ("Exempt", "NA"):
                continue
            if not r["census_tract"]:
                continue
            tractce = str(int(float(r["census_tract"])))[-6:].zfill(6)
            cands = by_tractce.get(tractce, [])
            town = (r["town"] or "").strip()
            geoid = cands[0] if len(cands) == 1 else next((g for g in cands if g[:5] == town_county.get(town)), None)
            if not geoid:
                continue
            db.execute(
                "INSERT INTO lender_values (year, town, tract_id, lender, loan_type, product, units,"
                " loan_amount, property_value, ltv) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (int(r["activity_year"]), town, geoid, r["lender_name"], r["loan_type"], r["derived_loan_product_type"],
                 r["total_units"], int(float(r["loan_amount"])), int(float(r["property_value"])),
                 float(r["loan_to_value_ratio"]) if r["loan_to_value_ratio"] not in ("", "Exempt", "NA") else None),
            )
            n += 1
    return n


def _usable(r) -> bool:
    return (r["CHARACTERISTIC1"] == "No Characteristic" and r["SUPPRESSED"] != "1"
            and r["VALUE"] != "" and r["SERIESID"] in KEEP_SERIES and int(r["YEAR"]) >= FIRST_YEAR)


def build_fhfa(db, raw: Path):
    n = 0
    with open(raw / "fhfa" / "ct_all_tract.csv", encoding="utf-8-sig", newline="") as fh:
        rows = [(r["TRACT"], r["PURPOSE"], int(r["YEAR"]), 5, r["SERIESID"], float(r["VALUE"]))
                for r in csv.DictReader(fh) if r["QUARTER"] == "5" and _usable(r)]
    db.executemany("INSERT OR REPLACE INTO tract_appraisals VALUES (?,?,?,?,?,?)", rows)
    n += len(rows)
    with open(raw / "fhfa" / "ct_all_county.csv", encoding="utf-8-sig", newline="") as fh:
        rows = [(r["FIPS"], r["PURPOSE"], int(r["YEAR"]), int(r["QUARTER"]), r["SERIESID"], float(r["VALUE"]))
                for r in csv.DictReader(fh) if _usable(r)]
    db.executemany("INSERT OR REPLACE INTO tract_appraisals VALUES (?,?,?,?,?,?)", rows)
    n += len(rows)
    # Statewide figures, keyed 'CT'.
    with open(raw / "fhfa" / "ct_state.csv", encoding="utf-8-sig", newline="") as fh:
        rows = [("CT", r["PURPOSE"], int(r["YEAR"]), int(r["QUARTER"]), r["SERIESID"], float(r["VALUE"]))
                for r in csv.DictReader(fh) if r["STATEPOSTAL"] == "CT" and _usable(r)]
    db.executemany("INSERT OR REPLACE INTO tract_appraisals VALUES (?,?,?,?,?,?)", rows)
    return n + len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, default=REPO.parent / "raw_data")
    ap.add_argument("--hmda", type=Path, required=True)
    args = ap.parse_args()

    OUT.parent.mkdir(exist_ok=True)
    keep_shared, keep_tickets = [], []
    if OUT.exists():
        with sqlite3.connect(OUT) as old:
            old.row_factory = sqlite3.Row
            for table, bucket in (("shared_appraisals", keep_shared), ("tickets", keep_tickets)):
                try:
                    bucket.extend(dict(r) for r in old.execute(f"SELECT * FROM {table}"))
                except sqlite3.Error:
                    pass
        OUT.unlink()

    db = sqlite3.connect(OUT)
    db.executescript(SCHEMA)
    tree, polys, geoids, tract_rows = load_tracts(args.raw)
    n_sales, towns, town_county, points = build_sales(db, args.raw, tree, polys, geoids)
    n_areas = build_area_names(db, args.raw, points)
    n_hmda = build_hmda(db, args.hmda, tract_rows, town_county)
    db.executemany("INSERT INTO tracts VALUES (?,?,?,?,?,?)", [(g, *v) for g, v in tract_rows.items()])
    n_fhfa = build_fhfa(db, args.raw)

    # Carry over shared records (upgrading old 6-digit tract ids) and review-desk tickets.
    six_to_geoid = {}
    for g in tract_rows:
        six_to_geoid.setdefault(g[5:], []).append(g)
    for row in keep_shared:
        t = row.get("tract_id") or row.get("tract6")
        row.pop("tract6", None)
        if t and len(t) == 6:
            cands = six_to_geoid.get(t, [])
            row["tract_id"] = cands[0] if len(cands) == 1 else next((g for g in cands if g[:5] == town_county.get(row.get("town"))), None)
        cols = [c for c in row if c in {d[1] for d in db.execute("PRAGMA table_info(shared_appraisals)")}]
        db.execute(f"INSERT INTO shared_appraisals ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [row[c] for c in cols])
    db.executescript(TICKETS_SCHEMA)  # same table the app creates, so ids stay INTEGER PRIMARY KEY
    for t in keep_tickets:
        db.execute(f"INSERT INTO tickets ({','.join(t)}) VALUES ({','.join('?' * len(t))})", list(t.values()))

    db.executemany("INSERT INTO meta VALUES (?,?)", [
        ("towns", json.dumps(towns)),
        ("town_county", json.dumps(town_county)),
        ("sales_source", "CT OPM Real Estate Sales (data.ct.gov 5mzw-sjtu), residential, recorded 2019 - Sept 2025, all Connecticut towns"),
        ("lender_source", "HMDA 2024 (CFPB), originated Connecticut loans; property_value = value relied on in the credit decision, published in $10,000 bands"),
        ("area_names_source", "Neighborhood and village names (c) OpenStreetMap contributors, ODbL"),
        ("appraisal_source", "FHFA UAD Aggregate Statistics v3.3, Enterprise single-family, census tract and county, annual"),
    ])
    db.commit()
    db.execute("VACUUM")
    print(f"sales={n_sales} towns={len(towns)} area_names={n_areas} lender_values={n_hmda} fhfa_rows={n_fhfa} "
          f"shared_kept={len(keep_shared)} tickets_kept={len(keep_tickets)}")
    print("wrote", OUT, f"{OUT.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
