"""Build docs/report.html and docs/slides.html as single self-contained files.

Templates live in docs/src/. Placeholders:
  {{img:NAME}}      -> data: URI of docs/img/NAME.jpg (images are embedded, nothing is linked)
  {{chart:trend}}   -> SVG bar chart of Connecticut's FHFA "priced above appraisal" share by year
  {{chart:cost}}    -> SVG bar chart of the cost-cut A/B (output/cost_ab.json)
Numbers in the charts come from the bundled database and the A/B results, not typed in.

Run: ./.venv/bin/python docs/build.py
"""

import base64
import json
import re
import sys
from pathlib import Path

DOCS = Path(__file__).resolve().parent
ROOT = DOCS.parent
sys.path.insert(0, str(ROOT))

from app import records  # noqa: E402

INK, MUTED, GRID, BRAND, BRAND_SOFT = "#1d2a26", "#5d6b66", "#e3e8e5", "#1f5f4a", "#9cc5b3"


def img(name: str) -> str:
    data = (DOCS / "img" / f"{name}.jpg").read_bytes()
    return "data:image/jpeg;base64," + base64.b64encode(data).decode()


def bars(items, fmt, title, highlight=None, w=640, h=250, unit_note="", pad_l=44, axis_fmt=None):
    """Single-series vertical bars: thin marks, 4px rounded tops on the baseline, selective labels."""
    pad_r, pad_t, pad_b = 12, 28, 34
    axis_fmt = axis_fmt or fmt
    top = max(v for _, v in items) * 1.15
    n = len(items)
    slot = (w - pad_l - pad_r) / n
    bw = min(46, slot * 0.62)
    y = lambda v: pad_t + (h - pad_t - pad_b) * (1 - v / top)  # noqa: E731
    out = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{title}" class="chart">']
    for k in range(1, 4):  # recessive grid
        gv = top * k / 4
        out.append(f'<line x1="{pad_l}" x2="{w - pad_r}" y1="{y(gv):.1f}" y2="{y(gv):.1f}" stroke="{GRID}" stroke-width="1"/>'
                   f'<text x="{pad_l - 6}" y="{y(gv) + 4:.1f}" text-anchor="end" font-size="11" fill="{MUTED}">{axis_fmt(gv)}</text>')
    base = y(0)
    out.append(f'<line x1="{pad_l}" x2="{w - pad_r}" y1="{base}" y2="{base}" stroke="{MUTED}" stroke-width="1"/>')
    for i, (label, v) in enumerate(items):
        x = pad_l + slot * i + (slot - bw) / 2
        top_y = y(v)
        hh = base - top_y
        r = min(4, hh)
        color = BRAND if (highlight is None or label in highlight) else BRAND_SOFT
        path = (f"M{x:.1f},{base:.1f} V{top_y + r:.1f} Q{x:.1f},{top_y:.1f} {x + r:.1f},{top_y:.1f} "
                f"H{x + bw - r:.1f} Q{x + bw:.1f},{top_y:.1f} {x + bw:.1f},{top_y + r:.1f} V{base:.1f} Z")
        out.append(f'<g><title>{label}: {fmt(v)}</title><path d="{path}" fill="{color}"/></g>')
        out.append(f'<text x="{x + bw / 2:.1f}" y="{base + 16:.1f}" text-anchor="middle" font-size="11.5" fill="{MUTED}">{label}</text>')
        if highlight is None or label in highlight:
            out.append(f'<text x="{x + bw / 2:.1f}" y="{top_y - 7:.1f}" text-anchor="middle" font-size="12" font-weight="700" fill="{INK}">{fmt(v)}</text>')
    if unit_note:
        out.append(f'<text x="{pad_l}" y="14" font-size="11" fill="{MUTED}">{unit_note}</text>')
    out.append("</svg>")
    return "".join(out)


def chart_trend() -> str:
    t = records.county_gap_trend("CT")
    items = [(str(r["year"]), r["share_below"] * 100) for r in t]
    hl = {items[0][0], "2019", "2021", items[-1][0]}
    return bars(items, lambda v: f"{v:.1f}%", "Share of Connecticut purchase appraisals below the contract price, by year",
                highlight=hl, unit_note="% of purchases priced above the appraisal (FHFA UAD, Connecticut)")


def chart_cost() -> str:
    s = json.loads((ROOT / "output" / "cost_ab.json").read_text())["summary"]
    names = {"A_before": "Before", "B_low_effort": "Low reasoning", "C_current": "Shipped"}
    items = [(names[k], v["avg_cost_usd"] * 1e4) for k, v in s.items()]
    return bars(items, lambda v: f"${v / 1e4:.6f}".rstrip("0"), "Average cost per Offer Check by setup",
                w=520, h=230, unit_note="Average cost per Offer Check (5 runs each)", pad_l=64,
                axis_fmt=lambda v: f"${v / 1e4:.4f}")


def build(name: str) -> None:
    src = (DOCS / "src" / f"{name}.src.html").read_text()
    src = re.sub(r"\{\{img:([\w-]+)\}\}", lambda m: img(m.group(1)), src)
    src = src.replace("{{chart:trend}}", chart_trend()).replace("{{chart:cost}}", chart_cost())
    left = re.findall(r"\{\{[^}]+\}\}", src)
    assert not left, f"unfilled placeholders in {name}: {left}"
    (DOCS / f"{name}.html").write_text(src)
    print(f"wrote docs/{name}.html ({len(src) / 1e6:.1f} MB)")


if __name__ == "__main__":
    for n in sys.argv[1:] or ["report", "slides"]:
        if (DOCS / "src" / f"{n}.src.html").exists():
            build(n)
