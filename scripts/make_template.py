"""Write the blank 'Share your numbers' template (.xlsx and .csv) and one filled SAMPLE sheet."""

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from openpyxl import Workbook  # noqa: E402
from openpyxl.styles import Alignment, Font, PatternFill  # noqa: E402

from app.sheets import TEMPLATE_ROWS  # noqa: E402

OUT = ROOT / "static" / "samples"
GREEN = PatternFill("solid", fgColor="E6F1EC")


def write_xlsx(path: Path, values: dict | None, title: str):
    wb = Workbook()
    ws = wb.active
    ws.title = "Your numbers"
    ws.append(["Field", "Your value", "Example", "Notes"])
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F5F4A")
    for key, label, example, note in TEMPLATE_ROWS:
        ws.append([label, (values or {}).get(key, ""), example, note])
    for row in ws.iter_rows(min_row=2):
        row[1].fill = GREEN
        row[2].font = Font(color="8A9590", italic=True)
    for col, width in zip("ABCD", (26, 22, 18, 44)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "B2"
    ws.append([])
    ws.append([title])
    ws.append(["Don't add names, loan numbers, or contact details. Leave a row blank if you don't know it."])
    for r in ws.iter_rows(min_row=ws.max_row - 1):
        r[0].alignment = Alignment(wrap_text=False)
        r[0].font = Font(italic=True, color="5D6B72")
    wb.save(path)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    write_xlsx(OUT / "appraisal_commons_template.xlsx", None, "Appraisal Commons: share your numbers")
    with open(OUT / "appraisal_commons_template.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Field", "Your value", "Example", "Notes"])
        for key, label, example, note in TEMPLATE_ROWS:
            w.writerow([label, "", example, note])
    sample = {"address": "19 Shoreham Rd", "city": "New Haven", "zip": "06512", "neighborhood": "Morris Cove",
              "property_type": "Single Family", "bedrooms": 3, "bathrooms": 1.5, "gla_sqft": 1412, "lot_size": "0.11 acres",
              "year_built": 1948, "list_price": 419000, "list_date": "2025-08-04", "days_on_market": 9,
              "contract_price": 425000, "contract_date": "2025-09-02", "appraised_value": 418000,
              "effective_date": "2025-09-18", "close_price": 425000, "close_date": "2025-09-29", "seller_concessions": 0}
    write_xlsx(OUT / "sample_spreadsheet_19_shoreham_rd.xlsx", sample, "SAMPLE - demo values, not a real sale record")
    print("wrote template + sample to", OUT)
