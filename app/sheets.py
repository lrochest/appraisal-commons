"""The 'Share your numbers' spreadsheet: template rows and a parser for .xlsx / .csv.

The template is a two-column sheet (Field | Your value) with an example and a
note on each row, so every upload has the same shape no matter who fills it in.
"""

from __future__ import annotations

import csv
import io

# (key, label shown in the sheet, example value, note)
TEMPLATE_ROWS = [
    ("address", "Street address", "5 Pope St", "Number and street only"),
    ("unit", "Unit (if any)", "", ""),
    ("city", "Town", "New Haven", "Any Connecticut town"),
    ("zip", "ZIP code", "06512", ""),
    ("neighborhood", "Neighborhood (optional)", "Morris Cove", ""),
    ("property_type", "Home type", "Single Family", "Single Family, Condo, Two Family..."),
    ("bedrooms", "Bedrooms", "3", ""),
    ("bathrooms", "Bathrooms", "2", "Use .5 for a half bath"),
    ("gla_sqft", "Living area (sq ft)", "1586", ""),
    ("lot_size", "Lot size", "0.14 acres", ""),
    ("year_built", "Year built", "1953", ""),
    ("list_price", "List price", "449900", "Asking price on the listing"),
    ("list_date", "List date", "2025-07-24", "YYYY-MM-DD"),
    ("days_on_market", "Days on market", "17", ""),
    ("offer_price", "Your offer", "470000", "Only if different from the contract price"),
    ("offer_date", "Offer date", "2025-08-10", ""),
    ("contract_price", "Contract price", "470000", "The price you agreed to"),
    ("contract_date", "Contract date", "2025-08-14", ""),
    ("appraised_value", "Appraised value", "462000", "The final value on the appraisal"),
    ("effective_date", "Appraisal date", "2025-09-08", "Effective date on the appraisal"),
    ("close_price", "Closing price", "459000", "Sale price on the closing disclosure"),
    ("close_date", "Closing date", "2025-09-30", ""),
    ("seller_concessions", "Seller credits", "0", "Credits from the seller at closing"),
]
LABEL_TO_KEY = {label.lower(): key for key, label, _, _ in TEMPLATE_ROWS} | {key: key for key, *_ in TEMPLATE_ROWS}
MAX_ROWS = 80


def _pairs_to_fields(rows) -> dict:
    out = {}
    for n, row in enumerate(rows):
        if n > MAX_ROWS:
            break
        if not row or row[0] is None:
            continue
        key = LABEL_TO_KEY.get(str(row[0]).strip().lower())
        if key and len(row) > 1 and row[1] not in (None, ""):
            out[key] = row[1]
    return out


def parse(data: bytes, filename: str) -> dict:
    name = (filename or "").lower()
    if data[:2] == b"PK" or name.endswith(".xlsx"):
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)  # data_only: values, never formulas
        ws = wb.worksheets[0]
        return _pairs_to_fields(ws.iter_rows(min_row=1, max_row=MAX_ROWS, max_col=2, values_only=True))
    text = data.decode("utf-8-sig", errors="replace")
    return _pairs_to_fields(csv.reader(io.StringIO(text)))
