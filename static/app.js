// Appraisal Commons front end. Plain JS, no build step. Hash routes:
//   #/  #/areas  #/area/<town>  #/home/<town>/<address>  #/share  #/resources  #/faq  #/about
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const money = (v) => (v == null ? "—" : "$" + Math.round(v).toLocaleString("en-US"));
const kmoney = (v) => (v == null ? "—" : v >= 1e6 ? "$" + (v / 1e6).toFixed(2) + "M" : "$" + Math.round(v / 1000) + "k");
const pct = (v) => (v == null ? "—" : (v * 100).toFixed(1) + "%");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const MAP_PIN = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 22s7-6.2 7-12a7 7 0 0 0-14 0c0 5.8 7 12 7 12z"/><circle cx="12" cy="10" r="2.5"/></svg>';
const title = (s) => String(s || "").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
const app = $("#app");
const state = { areas: null, gap: null, panel: null, offer: null, draft: null };

async function api(path, opts = {}) {
  const res = await fetch(path, { credentials: "same-origin", ...opts });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = body.detail;
    const err = new Error(body.error || (typeof d === "string" ? d : d?.message) || `Request failed (${res.status})`);
    err.body = body;
    throw err;
  }
  return body;
}

async function loadAreas() {
  if (!state.areas) {
    const r = await api("/api/areas");
    state.areas = r.areas;
    state.counties = r.counties;
    state.gap = r.county_gap_trend;
    state.stateAppraisals = r.state_appraisals;
    const counties = [...new Set(state.areas.map((a) => a.county_name).filter(Boolean))].sort();
    $("#footer-towns").innerHTML = counties.map((c) => `<li><a href="#/areas/${encodeURIComponent(c)}">${esc(c)}</a></li>`).join("") + `<li><a href="#/areas">All ${state.areas.length} towns</a></li>`;
  }
  return state.areas;
}

// ------------------------------------------------------------------ router
const routes = [
  [/^#?\/?$/, viewHome, "home"],
  [/^#\/areas(?:\/(.+))?$/, (m) => viewAreas(m[1] ? decodeURIComponent(m[1]) : ""), "areas"],
  [/^#\/area\/(.+)$/, (m) => viewArea(decodeURIComponent(m[1])), "areas"],
  [/^#\/home\/([^/]+)\/(.+)$/, (m) => viewProperty(decodeURIComponent(m[1]), decodeURIComponent(m[2])), "areas"],
  [/^#\/share$/, viewShare, "share"],
  [/^#\/resources$/, viewResources, "resources"],
  [/^#\/faq(?:\/([\w-]+))?$/, (m) => viewFaq(m[1]), "faq"],
  [/^#\/about$/, viewAbout, "about"],
  [/^#\/desk$/, viewDesk, "desk"],
];

async function route() {
  const h = location.hash || "#/";
  $("#nav").classList.remove("open");
  $("#menu-btn").setAttribute("aria-expanded", "false");
  for (const [re, fn, nav] of routes) {
    const m = h.match(re);
    if (m) {
      $$("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.nav === nav));
      window.scrollTo(0, 0);
      state.page = pageContext(h);
      renderStarters();
      try {
        await fn(m);
      } catch (err) {
        app.innerHTML = `<section class="wrap pad"><div class="card"><h2>Something went wrong</h2><p>${esc(err.message)}</p><a class="btn" href="#/">Back home</a></div></section>`;
      }
      app.focus({ preventScroll: true });
      return;
    }
  }
  location.hash = "#/";
}
window.addEventListener("hashchange", route);
$("#menu-btn").addEventListener("click", () => {
  const open = $("#nav").classList.toggle("open");
  $("#menu-btn").setAttribute("aria-expanded", String(open));
});

const propHref = (town, address) => `#/home/${encodeURIComponent(town)}/${encodeURIComponent(address)}`;

// ------------------------------------------------------------------ shared bits
// One address suggestion row (used by every search box).
const addrItem = (a) => `<li data-href="${propHref(a.town, a.address)}"><span class="s-ico">⌂</span><span>${esc(title(a.address))}, ${esc(a.town)}</span><span class="meta">${esc(a.residential_type || "")}${a.last_date ? ` · sold ${esc(a.last_date)}` : ""}</span></li>`;
// Split address matches into the chosen county's and everyone else's.
const countyOf = (town) => (state.areas || []).find((x) => x.town === town)?.county_name;
const byCounty = (addrs, county) => county
  ? [addrs.filter((a) => countyOf(a.town) === county), addrs.filter((a) => countyOf(a.town) !== county)]
  : [addrs, []];

function searchBox(id, placeholder, townsOnly = false, county = "") {
  return `<div class="search" data-search="${id}"${townsOnly ? " data-towns-only" : ""}${county ? ` data-county="${esc(county)}"` : ""}>
    <svg class="search-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7" fill="none" stroke="currentColor" stroke-width="2"/><path d="m20 20-3.5-3.5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
    <input type="text" autocomplete="off" placeholder="${esc(placeholder)}" aria-label="Search a town or address" />
    <ul class="suggest" hidden></ul></div>`;
}

function wireSearch(root) {
  $$("[data-search]", root).forEach((box) => {
    const input = $("input", box), list = $(".suggest", box);
    let timer, idx = -1;
    input.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const q = input.value.trim();
        if (!q) return (list.hidden = true);
        const r = await api("/api/search?q=" + encodeURIComponent(q) + (box.dataset.county ? "&county=" + encodeURIComponent(box.dataset.county) : "")).catch(() => null);
        if (!r || input.value.trim() !== q) return;  // failed, or the user has typed something newer
        idx = -1;
        const areaItems = r.areas.map((t) => `<li data-href="#/area/${encodeURIComponent(t)}"><span class="s-ico area">◎</span><span>${esc(t)}</span><span class="meta">town · see the area</span></li>`);
        const [inC, outC] = box.hasAttribute("data-towns-only") ? [[], []] : byCounty(r.addresses, box.dataset.county);
        const addrItems = [...inC.map(addrItem), ...(outC.length ? [`<li class="group-label">${box.dataset.county ? "In other counties" : ""}</li>`, ...outC.map(addrItem)] : [])].filter((x) => x !== '<li class="group-label"></li>');
        list.innerHTML = [...areaItems, ...addrItems].join("") || `<li class="none">${box.hasAttribute("data-towns-only") ? "No Connecticut town matches that name." : "No town or address matches. We cover all 169 Connecticut towns. Try the town name, or a street address that sold since 2019."}</li>`;
        list.hidden = false;
      }, 140);
    });
    input.addEventListener("keydown", (e) => {
      const items = $$("li[data-href]", list);
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (!items.length) return;
        idx = (idx + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
        items.forEach((li, i) => li.classList.toggle("on", i === idx));
      } else if (e.key === "Enter") {
        e.preventDefault();
        const pick = items[idx] || items[0];
        if (pick) location.hash = pick.dataset.href;
      } else if (e.key === "Escape") list.hidden = true;
    });
    list.addEventListener("click", (e) => {
      const li = e.target.closest("li[data-href]");
      if (li) location.hash = li.dataset.href;
    });
  });
}
document.addEventListener("click", (e) => {
  if (!e.target.closest("[data-search]")) $$(".suggest").forEach((l) => (l.hidden = true));
});

const byName = (a, b) => a.town.localeCompare(b.town);
// The busiest towns (most sales in their latest 12 months), shown alphabetically.
const busiest = (areas, n) => [...areas].sort((a, b) => b.sales_12m - a.sales_12m).slice(0, n).sort(byName);

function townChips(areas, limit = 10) {
  return areas.slice(0, limit).map((a) => `<a class="chip" href="#/area/${encodeURIComponent(a.town)}">${esc(a.town)}</a>`).join("");
}

const STEPS = [
  ["list_price", "Listed", "list_date"],
  ["offer_price", "Offer", "offer_date"],
  ["contract_price", "Contract", "contract_date"],
  ["appraised_value", "Appraised", "effective_date"],
  ["close_price", "Closed", "close_date"],
];

function ladder(r) {
  const vsList = (k) => {
    if (!r.list_price || k === "list_price" || !r[k]) return "";
    const p = ((r[k] - r.list_price) / r.list_price) * 100;
    return `<em class="vs ${p > 0 ? "up" : p < 0 ? "down" : ""}">${p > 0 ? "+" : ""}${p.toFixed(1)}% vs. list</em>`;
  };
  const cells = STEPS.filter(([k]) => r[k]).map(([k, label, d]) =>
    `<div class="rung ${k === "appraised_value" ? "appraisal" : ""}"><span>${label}</span><b class="num">${money(r[k])}</b>${vsList(k)}<small>${esc(r[d] || "")}</small></div>`);
  if (!cells.length) return '<div class="muted small">No prices found.</div>';
  let gap = "";
  // The buyer's question: did the appraisal reach the asking price (or the agreed price if there's no listing)?
  const base = r.list_price || r.contract_price;
  if (r.appraised_value && base) {
    const d = r.appraised_value - base, pctv = (d / base) * 100;
    const what = r.list_price ? "asking price" : "contract price";
    gap = `<div class="pocket ${d >= 0 ? "ok" : "bad"}"><div class="pk-num"><span>Appraisal vs. ${r.list_price ? "asking" : "contract"}</span><b class="num">${d > 0 ? "+" : d < 0 ? "−" : ""}${money(Math.abs(d))}</b></div><p>${d === 0 ? `Appraised exactly at the ${what}.` : `Appraised <b>${Math.abs(pctv).toFixed(1)}% ${d > 0 ? "above" : "below"}</b> the ${what}.`}</p></div>`;
  } else if (!r.appraised_value) {
    gap = '<div class="gap">No appraisal in this record (for example a cash sale or a listing-only document), so there\'s no appraisal-vs-asking comparison to show.</div>';
  }
  return `<div class="ladder">${cells.join('<i class="arrow" aria-hidden="true">→</i>')}</div>${gap}`;
}

function homeLine(r) {
  const bits = [r.property_type, r.bedrooms != null && `${r.bedrooms} bd`, r.bathrooms != null && `${r.bathrooms} ba`,
    r.gla_sqft && `${r.gla_sqft.toLocaleString()} sq ft`, r.lot_size, r.year_built && `built ${r.year_built}`, r.neighborhood].filter(Boolean);
  return bits.map(esc).join(" · ");
}

function sharedItem(s, link = true) {
  const v = s.verification || {};
  const name = `${esc(title(s.address))}${s.town ? ", " + esc(s.town) : ""}`;
  return `<div class="shared-item">
      <div class="shared-top">${s.is_sample ? '<span class="sample">SAMPLE</span>' : ""}
        ${link && s.town ? `<a href="${propHref(s.town, s.address)}"><strong>${name}</strong></a>` : `<strong>${name}</strong>`}
        <span class="muted small">${homeLine(s)}</span>
        <span class="pill ${v.verdict === "verified" ? "pass" : "warn"}">${esc(v.verdict || "—")}</span>
        ${v.source === "self_reported" ? '<span class="pill na" title="Typed in by the owner, not from a document">owner-reported</span>' : ""}
        ${s.public_sale_ref ? `<span class="ref" title="The closing matched the state's sale record">✓ matches state record</span>` : ""}</div>
      ${ladder(s)}
      <details class="report"><summary>Report a problem</summary>
        <form class="report-form" data-record="${s.id}"><textarea maxlength="600" required placeholder="What looks wrong? (Please don't include names or contact details.)"></textarea>
        <button class="btn">Send to the review desk</button><span class="small report-msg"></span></form></details>
    </div>`;
}

// little skyline used on the home page and area cards
function skyline(seed = 1, w = 320, h = 70) {
  let x = 0, out = "", r = seed * 9301;
  const rnd = () => ((r = (r * 9301 + 49297) % 233280) / 233280);
  while (x < w) {
    const bw = 26 + rnd() * 26, bh = 26 + rnd() * 34, roof = rnd() > 0.4;
    const y = h - bh;
    out += roof
      ? `<path d="M${x} ${y + 10} L${x + bw / 2} ${y - 6} L${x + bw} ${y + 10} V${h} H${x} Z" />`
      : `<rect x="${x}" y="${y}" width="${bw}" height="${bh}" rx="2"/>`;
    for (let wy = y + 16; wy < h - 10; wy += 13) out += `<rect class="win" x="${x + bw / 2 - 6}" y="${wy}" width="4" height="6"/><rect class="win" x="${x + bw / 2 + 2}" y="${wy}" width="4" height="6"/>`;
    x += bw + 3;
  }
  return `<svg class="skyline" viewBox="0 0 ${w} ${h}" preserveAspectRatio="xMidYMax slice" aria-hidden="true">${out}</svg>`;
}

function gapChart(trend) {
  if (!trend?.length) return "";
  const W = 520, H = 150, pad = 28, max = Math.max(...trend.map((t) => t.share_below)) * 1.15;
  const bw = (W - 2 * pad) / trend.length;
  const bars = trend.map((t, i) => {
    const bh = (t.share_below / max) * (H - 46);
    const x = pad + i * bw + 4, y = H - 22 - bh;
    return `<g><rect x="${x}" y="${y}" width="${bw - 8}" height="${bh}" rx="4" class="${i === trend.length - 1 ? "hot" : ""}"><title>${t.year}: ${pct(t.share_below)}</title></rect>
      <text x="${x + (bw - 8) / 2}" y="${H - 6}" text-anchor="middle">'${String(t.year).slice(2)}</text>
      ${i === trend.length - 1 || i === 0 ? `<text class="val" x="${x + (bw - 8) / 2}" y="${y - 5}" text-anchor="middle">${pct(t.share_below)}</text>` : ""}</g>`;
  }).join("");
  return `<svg class="gap-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Share of purchases where the price was more than the appraisal, by year">${bars}</svg>`;
}

// ------------------------------------------------------------------ HOME
async function viewHome() {
  const areas = await loadAreas();
  const latest = state.gap?.at(-1);
  const oneIn = latest ? Math.round(1 / latest.share_below) : null;
  const totals = areas.reduce((t, a) => ({ loans: t.loans + a.lender_loans_2024, appr: t.appr + (a.appraisals_2023 || 0), shared: t.shared + a.shared_records, samples: t.samples + (a.sample_records || 0), sales: t.sales + a.sales_12m }), { loans: 0, appr: 0, shared: 0, samples: 0, sales: 0 });
  app.innerHTML = `
  <section class="hero-home">
    <div class="wrap hero-grid">
      <div class="hero-copy">
        <span class="eyebrow">Starting with Connecticut · all ${areas.length} towns</span>
        <h1>See what homes are <em>actually</em> valued at before you offer.</h1>
        <ul class="lede-lines">
          <li><span class="ll-tag">List price</span> is what the seller hopes to get.</li>
          <li><span class="ll-tag">Sale price</span> is what one buyer ended up paying.</li>
          <li><span class="ll-tag appraised">The appraisal</span> is what the home was actually valued at.</li>
          <li>Buyers often offer over asking to win a home.</li>
          <li>If it appraises for less than the price, the buyer is expected to bring the difference in cash.</li>
          <li><strong>We show how often homes nearby have appraised that far over asking, so you have an idea whether you'd need extra cash to close.</strong></li>
        </ul>
        ${searchBox("home", "Search a town or an address…")}
        <div class="chips-row"><span class="muted small">Start with an area:</span>${townChips(busiest(areas, 8), 8)}<a class="chip ghost" href="#/areas">All ${areas.length} towns →</a></div>
      </div>
      <div class="hero-art" aria-hidden="true">
        <div class="ticket">
          <div class="ticket-head"><span class="sample">SAMPLE</span><span>Morris Cove, New Haven</span></div>
          <div class="t-row"><span>Listed</span><b class="num">$449,900</b></div>
          <div class="t-row"><span>Offer</span><b class="num">$470,000</b></div>
          <div class="t-row"><span>Contract</span><b class="num">$470,000</b></div>
          <div class="t-row hidden-row"><span>Appraised</span><b class="num reveal">$462,000</b><i class="lock">usually hidden until after you offer</i></div>
          <div class="t-row"><span>Closed</span><b class="num">$459,000</b></div>
          <div class="t-foot">The offer was <b>4.5% over asking</b>. It appraised <b>2.7% over asking</b>.</div>
        </div>
        ${skyline(3, 460, 96)}
      </div>
    </div>
  </section>

  <section class="band">
    <div class="wrap band-grid">
      <div class="band-stat">
        ${oneIn ? `<div class="big">About <span>1 in ${oneIn}</span></div><p>Connecticut home purchases in ${latest.year} had a <strong>price higher than the appraisal</strong> (${pct(latest.share_below)}).</p><a class="src-note" href="#/faq/one-in-ten">Where this comes from: FHFA's Uniform Appraisal Dataset, Connecticut purchase appraisals, ${latest.year} →</a>` : ""}
      </div>
      <div class="band-chart">${gapChart(state.gap)}<div class="small center chart-cap">Share of purchases priced above the appraisal, Connecticut, ${state.gap?.[0]?.year}–${latest?.year}</div></div>
    </div>
    <div class="wrap counters">
      <div><b class="num">${totals.loans.toLocaleString()}</b><span>appraised values on 2024 mortgages</span></div>
      <div><b class="num">${(state.stateAppraisals?.total || totals.appr).toLocaleString()}</b><span>real appraisals this site is built on</span></div>
      <div><b class="num">${totals.sales.toLocaleString()}</b><span>single-family sales in the latest 12 months</span></div>
      <div><b class="num">${totals.shared}</b><span>owner-shared records${totals.samples ? ` (plus ${totals.samples} labeled demo samples)` : ""}</span></div>
    </div>
  </section>

  <section class="wrap pad">
    <div class="market">
      <div class="market-intro">
        <span class="eyebrow">Why it matters in expensive, low-inventory markets</span>
        <h2>When few homes are for sale, buyers bid over asking. The appraisal decides who pays for it.</h2>
        <p>Your lender lends only on the appraised value, not on what you offered. If the appraisal is lower than your price, you're expected to bring the difference in cash at closing.</p>
        <p>Knowing what homes nearby really appraised for tells you how much of an over-asking offer an appraisal has actually supported.</p>
      </div>
      <div class="market-grid">
        <div class="m-card"><div class="m-ico">↑</div><h3>Offering over asking</h3><p>See what homes like it actually appraised for, so you know when "over asking" is still in line with real values and when it's past them.</p></div>
        <div class="m-card"><div class="m-ico">⚖</div><h3>Appraisal gap coverage</h3><p>Sellers in hot markets often favor offers that promise to cover the difference if the price ends up above the appraisal. Knowing how often that happens nearby helps you size that promise.</p></div>
        <div class="m-card"><div class="m-ico">✎</div><h3>Waiving the contingency</h3><p>Some buyers drop the appraisal contingency to compete. That moves all the risk to you. Look at the records first, and talk it through with your agent or lender.</p></div>
      </div>
    </div>
  </section>

  <section class="wrap pad-sm">
    <div class="compare card">
      <div class="compare-intro"><span class="eyebrow">How we're different</span><h2>Other sites show asking and sold prices. Some estimate a value. None show what homes appraised for.</h2></div>
      <table class="compare-table">
        <thead><tr><th></th><th>Zillow, Redfin, Realtor.com</th><th>Appraisal Commons</th></tr></thead>
        <tbody>
          <tr><td>List &amp; sale prices</td><td>✓</td><td>✓ <span class="muted small">(state sale records)</span></td></tr>
          <tr><td>Home "value"</td><td>An algorithm's estimate (Zestimate, Redfin Estimate)</td><td>Real appraisals and lender-used values. No estimates.</td></tr>
          <tr><td>What homes actually appraised for</td><td>—</td><td>✓ Appraisal stats for every CT neighborhood</td></tr>
          <tr><td>How often prices are higher than the appraisal</td><td>—</td><td>✓ By county and neighborhood</td></tr>
          <tr><td>List → offer → appraisal → close, side by side</td><td>—</td><td>✓ From owners who share their numbers</td></tr>
        </tbody>
      </table>
    </div>
  </section>

  <section class="wrap pad">
    <div class="section-head"><h2>Explore by area</h2><a href="#/areas" class="link">See all ${areas.length} towns →</a></div>
    <div class="area-grid">${busiest(areas, 6).map((a) => areaCard(a)).join("")}</div>
  </section>

  <section class="how">
    <div class="wrap pad">
      <h2 class="center">How it works</h2>
      <div class="how-grid">
        <div class="how-step"><div class="how-num">1</div><h3>Search an area or a home</h3><p>Start with a town, then drill into a neighborhood or any address that sold since 2019. Or just ask the Records Guide.</p></div>
        <div class="how-step"><div class="how-num">2</div><h3>See the real records</h3><p>What lenders actually approved homes at, what appraisers actually concluded, and the list → offer → appraisal → close chain owners have shared.</p></div>
        <div class="how-step"><div class="how-num">3</div><h3>Check your offer</h3><p>Type a price. Our Records Concierge lines it up against those records and cites every one. It never guesses a value, and a code check blocks any number that isn't in a record.</p></div>
      </div>
    </div>
  </section>

  <section class="wrap pad two-up">
    <div class="card feature">
      <span class="eyebrow">Just bought or sold?</span>
      <h3>Your appraisal could save the next buyer thousands.</h3>
      <p>Lenders must give buyers a copy of the appraisal. Sellers usually never see it. Share yours, or your listing sheet and closing disclosure. We remove names and loan numbers first, check the numbers against the state's sale record, and publish only with your OK.</p>
      <a class="btn primary" href="#/share">Share your numbers</a>
    </div>
    <div class="card feature soft">
      <span class="eyebrow">New to buying?</span>
      <h3>Start with the free help that already exists.</h3>
      <ul class="tight">
        <li>Connecticut's CHFA down payment programs</li>
        <li>Local down payment help, like New Haven's assistance program</li>
        <li>What to do when your price is above the appraisal</li>
      </ul>
      <a class="btn" href="#/resources">Browse resources</a>
    </div>
  </section>

  <section class="wrap pad">
    <div class="section-head"><h2>Questions people ask</h2><a href="#/faq" class="link">All FAQs →</a></div>
    <div class="faq-list">${FAQ.slice(0, 3).map((f) => faqItem(f)).join("")}</div>
  </section>`;
  wireSearch(app);
}

function areaCard(a, i = 0, vs = null, mode = "") {
  const tag = typeof vs !== "number" || !isFinite(vs) ? "" : `<span class="vs-tag ${vs > 0 ? "up" : vs < 0 ? "down" : ""}">${vs > 0 ? "+" : ""}${vs.toFixed(0)}% vs. county</span>`;
  return `<a class="area-card" href="#/area/${encodeURIComponent(a.town)}">
    <div class="area-art">${skyline(a.town.length * 7 + (typeof i === "number" ? 0 : 0), 320, 60)}<span class="area-name">${esc(a.town)}</span>${tag}</div>
    <div class="area-body">
      <div class="area-stats">
        <div><b class="num">${kmoney(a.median_sale_12m)}</b><span>median single-family sale, 12 mo to ${esc(a.window[1].slice(0, 7))}</span></div>
        <div><b class="num">${kmoney(a.median_lender_value_2024)}</b><span>median appraised value on 2024 mortgages</span></div>
      </div>
      <div class="area-foot"><span>${a.appraisals_total ? `Built on <b>${a.appraisals_total.toLocaleString()}</b> appraisals` : "No published appraisal figures yet"} · ${a.tracts} neighborhood${a.tracts === 1 ? "" : "s"}</span>${a.shared_records ? `<span class="pill pass">${a.shared_records} shared</span>` : ""}</div>
    </div></a>`;
}

// ------------------------------------------------------------------ AREAS
// Typo-tolerant town matching: "haevn" -> New Haven, "grenwich" -> Greenwich, "hart" -> Hartford.
function editDistance(a, b) {  // Damerau-Levenshtein (adjacent swaps count as one edit)
  const d = Array.from({ length: a.length + 1 }, (_, i) => [i, ...Array(b.length).fill(0)]);
  for (let j = 1; j <= b.length; j++) d[0][j] = j;
  for (let i = 1; i <= a.length; i++)
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost);
      if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) d[i][j] = Math.min(d[i][j], d[i - 2][j - 2] + 1);
    }
  return d[a.length][b.length];
}

// Lower score = better match; null = no match.
function townScore(town, query) {
  const t = town.toLowerCase(), q = query.toLowerCase().trim();
  if (!q) return 0;
  if (t.startsWith(q)) return 0;
  if (t.split(/\s+/).some((w) => w.startsWith(q))) return 1;
  if (q.length >= 3 && t.includes(q)) return 2;  // mid-word matches only once there are a few letters
  const qw = q.split(/\s+/), tw = t.split(/\s+/);
  if (qw.length > 1 && qw.length <= tw.length && qw.every((w, i) => tw[i].startsWith(w))) return 1;
  const allowed = q.length <= 3 ? 0 : q.length <= 6 ? 1 : 2;
  let best = Infinity;
  for (const w of [t, ...t.split(/\s+/)]) {
    best = Math.min(best, editDistance(q, w), editDistance(q, w.slice(0, q.length)));  // whole word or its start
  }
  return best <= allowed ? 3 + best : null;
}

// County + town finder: the county narrows the town list (default: all counties).
function wireTownFinder(areas) {
  const sel = $("#county-pick"), input = $("#town-input"), list = $("#town-suggest");
  let idx = -1;
  const ranked = (pool, q) => pool
    .map((a) => ({ a, score: townScore(a.town, q) }))
    .filter((x) => x.score != null)
    .sort((x, y) => x.score - y.score || byName(x.a, y.a))
    .map((x) => x.a);
  const item = (a) => `<li data-href="#/area/${encodeURIComponent(a.town)}"><span class="s-ico area">◎</span><span>${esc(a.town)}</span><span class="meta">${esc(a.county_name || "")}</span></li>`;
  const draw = () => {
    const q = input.value.trim().toLowerCase(), county = sel.value;
    idx = -1;
    if (!q && !county) return (list.hidden = true);
    let html;
    if (!county) {
      html = ranked(areas, q).map(item).join("");
    } else {
      // The chosen county's towns first; matches elsewhere still show, so a wrong county never hides a town.
      const inside = ranked(areas.filter((a) => a.county_name === county), q);
      const outside = q ? ranked(areas.filter((a) => a.county_name !== county), q) : [];
      html = inside.map(item).join("") +
        (outside.length ? `<li class="group-label">In other counties</li>` + outside.map(item).join("") : "");
    }
    list.innerHTML = html || (addrHtml ? "" : `<li class="none">No Connecticut town or address matches "${esc(input.value.trim())}".</li>`);
    list.insertAdjacentHTML("beforeend", addrHtml);
    list.hidden = !list.innerHTML;
  };
  // Addresses come from the server; towns are matched here as you type.
  let addrHtml = "", timer;
  const fetchAddrs = () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 3 || !/\d/.test(q)) { addrHtml = ""; return draw(); }
    const county = sel.value;
    timer = setTimeout(async () => {
      const r = await api("/api/search?q=" + encodeURIComponent(q) + (county ? "&county=" + encodeURIComponent(county) : "")).catch(() => ({ addresses: [] }));
      if (input.value.trim() !== q || sel.value !== county) return;  // stale: newer text or a different county
      const [inC, outC] = byCounty(r.addresses, county);
      addrHtml = (inC.length ? `<li class="group-label">Addresses${county ? " in " + esc(county) : ""}</li>` + inC.map(addrItem).join("") : "") +
        (outC.length ? `<li class="group-label">${county ? "Addresses in other counties" : "Addresses"}</li>` + outC.map(addrItem).join("") : "");
      draw();
    }, 140);
  };
  input.addEventListener("input", () => { addrHtml = ""; draw(); fetchAddrs(); });
  input.addEventListener("focus", draw);
  sel.addEventListener("change", () => { fetchAddrs(); draw(); input.focus(); });
  input.addEventListener("keydown", (e) => {
    const items = $$("li[data-href]", list);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!items.length) return;
      idx = (idx + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      items.forEach((li, i) => li.classList.toggle("on", i === idx));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const pick = items[idx] || items[0];
      if (pick) location.hash = pick.dataset.href;
    } else if (e.key === "Escape") list.hidden = true;
  });
  list.addEventListener("click", (e) => { const li = e.target.closest("li[data-href]"); if (li) location.hash = li.dataset.href; });
  document.addEventListener("click", (e) => { if (!e.target.closest("#town-finder")) list.hidden = true; });
}

function countyCard(c, i = 0) {
  return `<a class="area-card county-card" href="#/areas/${encodeURIComponent(c.county_name)}">
    <div class="area-art">${skyline(c.county_name.length * 5 + i, 320, 60)}<span class="area-name">${esc(c.county_name.replace(" County", ""))}<small>County · ${c.towns} towns</small></span></div>
    <div class="area-body">
      <div class="area-stats">
        <div><b class="num">${kmoney(c.median_sale_12m)}</b><span>median single-family sale, latest 12 mo</span></div>
        <div><b class="num">${kmoney(c.median_appraisal)}</b><span>median purchase appraisal, ${c.median_appraisal_year || "—"}</span></div>
      </div>
      <div class="area-foot"><span>${c.share_below != null ? `<b>${pct(c.share_below)}</b> of purchases priced above the appraisal (${c.share_below_year})` : "—"}</span></div>
    </div></a>`;
}

async function viewAreas(county) {
  const areas = await loadAreas();
  const counties = state.counties || [];
  const c = counties.find((x) => x.county_name === county);
  if (!c) {
    app.innerHTML = `
    <section class="page-hero"><div class="wrap">
      <span class="eyebrow">Explore Areas</span>
      <h1>Every Connecticut town.<br/>Real appraisal records for each.</h1>
      <p class="lede">Find a town to see its neighborhoods, real appraisal statistics, appraised values on recent mortgages, and recent sales.</p>
      <div class="town-finder" id="town-finder">
        <select id="county-pick" aria-label="County"><option value="">All counties</option>${counties.map((x) => `<option>${esc(x.county_name)}</option>`).join("")}</select>
        <div class="tf-input">
          <svg class="search-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7" fill="none" stroke="currentColor" stroke-width="2"/><path d="m20 20-3.5-3.5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
          <input id="town-input" type="text" autocomplete="off" placeholder="Type a town or an address, e.g. Greenwich or 5 Pope St" aria-label="Town or address" />
        </div>
        <ul class="suggest" id="town-suggest" hidden></ul>
      </div>
    </div></section>
    <section class="wrap pad">
      <div class="section-head"><h2>Connecticut's 8 Counties</h2><span class="muted small">${areas.length} towns in all</span></div>
      <div class="area-grid">${counties.map((c, i) => countyCard(c, i)).join("")}</div>
    </section>`;
    wireTownFinder(areas);
    return;
  }
  const towns = areas.filter((a) => a.county_name === county).sort(byName);
  app.innerHTML = `
  <section class="page-hero"><div class="wrap">
    <nav class="crumbs"><a href="#/">Home</a> › <a href="#/areas">Areas</a> › <span>${esc(county)}</span></nav>
    <h1>${esc(county)}</h1>
    <div class="stat-row">
      <div><b class="num">${money(c.median_sale_12m)}</b><span>median single-family sale<br/>latest 12 months</span></div>
      <div><b class="num">${money(c.median_appraisal)}</b><span>median purchase appraisal<br/>${c.median_appraisal_year || ""} (FHFA)</span></div>
      <div><b class="num">${c.share_below != null ? pct(c.share_below) : "—"}</b><span>of purchases priced above<br/>the appraisal (${c.share_below_year || ""})</span></div>
      <div><b class="num">${money(c.median_lender_value_2024)}</b><span>median appraised value on<br/>${c.lender_loans_2024.toLocaleString()} mortgages, 2024</span></div>
    </div>
    ${searchBox("county", `Go to a town or address in ${county.replace(" County", "")}…`, false, county)}
  </div></section>
  <section class="wrap pad">
    <div class="section-head"><h2>${towns.length} towns</h2>
      <div class="sort-row"><label>Sort by
        <select id="town-sort">
          <option value="az">A–Z</option>
          <option value="lender">Appraised value vs. county</option>
          <option value="sale">Median sale vs. county</option>
          <option value="activity">Most sales, latest 12 months</option>
        </select></label><a class="link" href="#/areas">← All counties</a></div></div>
    <p class="muted small" id="sort-note"></p>
    <div class="area-grid" id="town-grid"></div>
  </section>`;
  const vsCounty = (v, base) => (v && base ? ((v - base) / base) * 100 : null);
  const drawTowns = () => {
    const mode = $("#town-sort").value;
    const key = { lender: "median_lender_value_2024", sale: "median_sale_12m", activity: "sales_12m" }[mode];
    const base = { lender: c.median_lender_value_2024, sale: c.median_sale_12m }[mode];
    const list = [...towns].sort((x, y) => mode === "az" ? byName(x, y) : (y[key] ?? -1) - (x[key] ?? -1) || byName(x, y));
    $("#town-grid").innerHTML = list.map((a, i) => areaCard(a, i, base ? vsCounty(a[key], base) : null, mode)).join("");
    $("#sort-note").textContent = {
      az: "Alphabetical.",
      lender: `Highest first. Each tag compares the town's median appraised value on 2024 mortgages (the value lenders used to approve the loan, usually the appraisal) with ${county}'s (${money(c.median_lender_value_2024)}).`,
      sale: `Highest first. Each tag compares the town's median single-family sale with ${county}'s (${money(c.median_sale_12m)}).`,
      activity: "Busiest markets first: number of single-family sales in each town's latest 12 months of records.",
    }[mode];
  };
  $("#town-sort").addEventListener("change", drawTowns);
  drawTowns();
  wireSearch(app);
}

async function viewArea(town) {
  await loadAreas();
  const a = await api("/api/area?town=" + encodeURIComponent(town));
  const rows = a.tract_rows.filter((t) => t.appraisal_median || t.lender_loans);
  const allMed = rows.flatMap((t) => [t.appraisal_p25, t.appraisal_p75, t.lender_median]).filter(Boolean);
  const lo = Math.min(...allMed) * 0.9, hi = Math.max(...allMed) * 1.05;
  const xs = (v) => Math.max(0, Math.min(100, ((v - lo) / (hi - lo)) * 100));
  app.innerHTML = `
  <section class="page-hero area-hero"><div class="wrap">
    <nav class="crumbs"><a href="#/">Home</a> › <a href="#/areas">Areas</a> › ${a.county_name ? `<a href="#/areas/${encodeURIComponent(a.county_name)}">${esc(a.county_name)}</a> › ` : ""}<span>${esc(a.town)}</span></nav>
    <h1>${esc(a.town)}</h1>
    <div class="stat-row">
      <div><b class="num">${money(a.median_sale_12m)}</b><span>median single-family sale<br/>${esc(a.window[0])} to ${esc(a.window[1])}</span></div>
      <div><b class="num">${money(a.median_lender_value_2024)}</b><span>median appraised value on mortgages<br/>${a.lender_loans_2024} loans in 2024</span></div>
      <div><b class="num">${a.appraisals_total ? a.appraisals_total.toLocaleString() : "—"}</b><span>appraisals ${esc(a.town)}'s numbers are<br/>built on, across ${a.tracts} neighborhoods</span></div>
      <div><b class="num">${a.tract_median_range_2023 ? kmoney(a.tract_median_range_2023[0]) + "–" + kmoney(a.tract_median_range_2023[1]) : "—"}</b><span>range of neighborhood median<br/>appraisals, 2023</span></div>
    </div>
    ${searchBox("area", `Search an address in ${a.town}…`)}
  </div></section>

  <section class="wrap pad">
    <div class="section-head"><div><h2>Neighborhoods</h2><p class="muted small">Each row is a census tract, named by the neighborhood most of its homes are closest to (names from OpenStreetMap).</p></div><span class="legend"><i class="lg-box"></i> middle half of appraisals <i class="lg-med"></i> median appraisal <i class="lg-dot"></i> median appraised value on 2024 mortgages</span></div>
    <div class="card table-card">
      <div class="table-scroll"><table class="tract-table">
        <thead><tr><th><button class="sort" data-sort="name">Neighborhood</button></th><th class="bar-col">Appraised values</th><th><button class="sort" data-sort="appraisal_median">Median appraisal</button></th><th><button class="sort" data-sort="share_below">Priced above appraisal</button></th><th><button class="sort" data-sort="lender_median">Appraised value on mortgages, 2024</button></th><th><button class="sort" data-sort="sales_12m">Sales, 12 mo</button></th></tr></thead>
        <tbody id="tract-body"></tbody>
      </table></div>
      <p class="muted small">Grouped by the year of each neighborhood's latest appraisal figures (newest first), A–Z within each year. Click a column heading to sort within each year; click again to flip the order. FHFA hides a neighborhood's figures when there were too few appraisals to publish. "Priced above appraisal" is the share of purchases where the price was more than the appraisal.</p>
    </div>
  </section>

  <section class="wrap pad two-up wide-left">
    <div>
      <div class="section-head"><h2>Recent sales</h2><span class="muted small">Pick a home for its full record</span></div>
      <div class="card list-card">${a.recent_sales.map((s) => `<a class="sale-row" href="${propHref(s.town, s.address)}"><span>${esc(title(s.address))}</span><span class="muted small">${esc(s.type || "")}</span><span class="num">${money(s.sale_price)}</span><span class="muted small">${esc(s.date)}</span></a>`).join("")}</div>
    </div>
    <div>
      <div class="section-head"><h2>Shared records</h2></div>
      <div class="card">${a.shared.length ? a.shared.map((s) => sharedItem(s)).join("") : `<div class="empty"><span>No one in ${esc(a.town)} has shared their numbers yet.</span><a class="btn" href="#/share">Be the first</a></div>`}</div>
    </div>
  </section>`;
  const rowHtml = (t) => `<tr>
          <td class="nb"><b>${esc(t.label || t.name)}</b><small class="muted">${esc(t.name)}</small></td>
          <td class="bar-col"><div class="mini-range">
            ${t.appraisal_p25 && t.appraisal_p75 ? `<span class="box" style="left:${xs(t.appraisal_p25)}%;width:${xs(t.appraisal_p75) - xs(t.appraisal_p25)}%"></span>` : ""}
            ${t.appraisal_median ? `<span class="med" style="left:${xs(t.appraisal_median)}%"></span>` : ""}
            ${t.lender_median ? `<span class="dot" style="left:${xs(t.lender_median)}%"></span>` : ""}
          </div></td>
          <td class="num">${t.appraisal_median ? money(t.appraisal_median) + ` <small class="muted">${t.appraisal_year}</small>` : '<span class="muted">hidden by FHFA</span>'}</td>
          <td class="num">${t.share_below != null ? pct(t.share_below) + ` <small class="muted">${t.share_below_year}</small>` : '<span class="muted">—</span>'}</td>
          <td class="num">${t.lender_median ? money(t.lender_median) + ` <small class="muted">${t.lender_loans} loans</small>` : "—"}</td>
          <td class="num">${t.sales_12m ? `<button type="button" class="sales-btn" data-tract="${esc(t.tract)}" aria-expanded="false">${t.sales_12m} home${t.sales_12m > 1 ? "s" : ""} <span aria-hidden="true">▾</span></button>` : '<span class="muted">0</span>'}</td></tr>`;
  const sortState = { key: "name", dir: 1 };
  const nameOf = (t) => (t.label || t.name).toLowerCase();
  const drawRows = () => {
    const { key, dir } = sortState;
    const sorted = [...rows].sort((x, y) => {
      if (key === "name") return dir * (nameOf(x).localeCompare(nameOf(y)) || x.name.localeCompare(y.name, undefined, { numeric: true }));
      const vx = x[key], vy = y[key];
      if (vx == null && vy == null) return nameOf(x).localeCompare(nameOf(y));
      if (vx == null) return 1;   // blanks always last
      if (vy == null) return -1;
      return dir * (vx - vy);
    });
    // Group by the year of each tract's latest published appraisal figures, newest first,
    // so values in a group are comparable. Tracts FHFA hides go last.
    const years = [...new Set(sorted.map((t) => t.appraisal_year || 0))].sort((x, y) => y - x);
    $("#tract-body").innerHTML = years.map((y) => {
      const group = sorted.filter((t) => (t.appraisal_year || 0) === y);
      const label = y ? `${y} appraisal figures` : "No published appraisal figures";
      const note = y ? "" : " · FHFA hides neighborhoods with too few appraisals";
      return `<tr class="yr"><td colspan="6"><span>${label}</span><small>${group.length} neighborhood${group.length > 1 ? "s" : ""}${note}</small></td></tr>` + group.map(rowHtml).join("");
    }).join("");
    $$(".tract-table .sort").forEach((b) => {
      b.classList.toggle("on", b.dataset.sort === key);
      b.dataset.dir = b.dataset.sort === key ? (dir > 0 ? "↑" : "↓") : "";
    });
  };
  // Click a neighborhood's sales count to list the homes behind it.
  $("#tract-body").addEventListener("click", async (e) => {
    const b = e.target.closest(".sales-btn");
    if (!b) return;
    const row = b.closest("tr"), open = row.nextElementSibling?.classList.contains("sales-detail");
    $$(".sales-detail", $("#tract-body")).forEach((r) => r.remove());
    $$(".sales-btn", $("#tract-body")).forEach((x) => x.setAttribute("aria-expanded", "false"));
    if (open) return;
    b.setAttribute("aria-expanded", "true");
    row.insertAdjacentHTML("afterend", '<tr class="sales-detail"><td colspan="6"><p class="muted small">Loading…</p></td></tr>');
    const cell = row.nextElementSibling.firstElementChild;
    try {
      const d = await api(`/api/area/sales?town=${encodeURIComponent(a.town)}&tract=${encodeURIComponent(b.dataset.tract)}`);
      cell.innerHTML = `<div class="sd-head">Sales in ${esc(d.label || "this neighborhood")}, ${esc(d.window[0])} to ${esc(d.window[1])}</div>
        <div class="sd-list">${d.sales.map((x) => `<a class="sale-row" href="${propHref(x.town, x.address)}"><span>${esc(title(x.address))}</span><span class="muted small">${esc(x.type || "")}</span><span class="num">${money(x.sale_price)}</span><span class="muted small">${esc(x.date)}</span></a>`).join("")}</div>`;
    } catch (err) {
      cell.innerHTML = `<div class="error">${esc(err.message)}</div>`;
    }
  });
  $$(".tract-table .sort").forEach((b) => b.addEventListener("click", () => {
    const k = b.dataset.sort;
    if (sortState.key === k) sortState.dir *= -1;
    else { sortState.key = k; sortState.dir = k === "name" ? 1 : -1; } // numbers: highest first
    drawRows();
  }));
  drawRows();
  wireSearch(app);
}

// ------------------------------------------------------------------ PROPERTY
async function viewProperty(town, address) {
  let p;
  try {
    p = await api(`/api/property?address=${encodeURIComponent(address)}&town=${encodeURIComponent(town)}`);
  } catch (err) {
    const dym = err.body?.detail?.did_you_mean || [];
    app.innerHTML = `<section class="wrap pad"><div class="card"><h2>${esc(err.message)}</h2>
      ${dym.length ? `<p>Did you mean:</p><div class="chips-row">${dym.map((d) => `<a class="chip" href="${propHref(d.town, d.address)}">${esc(title(d.address))}, ${esc(d.town)}</a>`).join("")}</div>` : ""}
      <p><a href="#/areas">Browse areas instead →</a></p></div></section>`;
    return;
  }
  state.panel = p;
  state.offer = null;
  app.innerHTML = `
  <section class="page-hero prop-hero"><div class="wrap">
    <nav class="crumbs"><a href="#/">Home</a> › <a href="#/area/${encodeURIComponent(p.town)}">${esc(p.town)}</a> › <span>${esc(title(p.address))}</span></nav>
    <div class="prop-head">
      <div><h1>${esc(title(p.address))}</h1><div class="muted">${esc(p.town)}, CT · ${esc(p.type || "Residential")} · ${p.tract_label ? `${esc(p.tract_label)} <span class="tract-id">(${esc(p.tract_name || "")})</span>` : esc(p.tract_name || "")}</div></div>
      <div class="head-side">
        <a class="btn maps-btn" href="https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(`${title(p.address)}, ${p.town}, CT`)}" target="_blank" rel="noopener noreferrer">${MAP_PIN}See it on Google Maps</a>
        <div class="badge-row" id="p-badges"></div>
      </div>
    </div>
  </div></section>

  <section class="wrap pad-sm" id="own-shared" hidden>
    <article class="card own-card">
      <h2 class="h3">This home's real numbers <span class="src">Shared by the owner</span></h2>
      <div id="own-shared-body"></div>
    </article>
  </section>

  <section class="wrap pad-sm">
    <article class="card offer-card">
      <div class="offer-layout">
        <div>
          <h2 class="h3">Check an offer</h2>
          <p class="muted small">See where a price sits next to the actual records. No predictions, only what's been recorded.</p>
          <form id="offer-form" class="offer-form">
            <div class="two-money">
              <label>Asking price<div class="money"><span>$</span><input id="asking" type="text" inputmode="numeric" autocomplete="off" /></div></label>
              <label>Your offer<div class="money"><span>$</span><input id="offer" type="text" inputmode="numeric" autocomplete="off" required /></div></label>
            </div>
            <div class="over-ask" id="over-ask" hidden></div>
            <label>Question (optional)<input id="question" type="text" maxlength="300" placeholder="Will this clear the appraisal?" /></label>
            <div class="starters" aria-label="Example questions">
              <button type="button" class="chip q" data-q="How does this offer compare to appraised values on recent mortgages nearby?">vs. mortgage appraisals</button>
              <button type="button" class="chip q" data-q="How often were prices here higher than the appraisal?">appraisal gaps here</button>
              <button type="button" class="chip q" data-q="What did this home's last appraisal and sale show?">this home's history</button>
            </div>
            <button class="btn primary" id="offer-btn" type="submit">Check against the records</button>
          </form>
        </div>
        <div>
          <div id="offer-empty" class="offer-placeholder">
            <svg viewBox="0 0 120 70" aria-hidden="true"><rect x="6" y="22" width="30" height="40" rx="4"/><rect x="45" y="8" width="30" height="54" rx="4" class="mid"/><rect x="84" y="30" width="30" height="32" rx="4"/><path d="M0 64h120"/></svg>
            <p>Enter the asking price from the listing and the offer you're considering. The <strong>Records Concierge</strong> shows how far over asking you'd be and lines it up against real appraisal statistics, appraised values on recent mortgages, this home's sale history, and any shared record, and cites every one.</p>
          </div>
          <div id="offer-out" hidden>
            <div id="offer-often"></div>
            <div id="offer-answer" class="answer" aria-live="polite"></div>
            <div id="offer-guard" class="guard"></div>
            <details class="trace"><summary>How this answer was made <span id="offer-cost" class="muted small"></span></summary><ol id="offer-trace"></ol></details>
          </div>
        </div>
      </div>
    </article>
  </section>

  <section class="wrap pad-sm grid">
    <article class="card">
      <h3>Appraised values on 2024 mortgages nearby <span class="src">HMDA 2024</span></h3>
      <p class="muted small">Each dot is a 2024 mortgage in this neighborhood, placed at the home value the lender used to approve it: usually the appraisal, sometimes the purchase price.</p>
      <p class="note small">This is <b>not</b> the loan amount, so a bigger or smaller down payment doesn't change it. It also doesn't show the asking price. To see how far over asking homes went, look at <b>shared records</b>.</p>
      <div id="lv-chart" class="chart"></div><div id="lv-summary" class="kv"></div>
    </article>
    <article class="card">
      <h3>Real appraisals in this neighborhood <span class="src">FHFA</span></h3>
      <p class="muted small">Statistics from actual appraisal reports for Fannie Mae and Freddie Mac loans.</p>
      <div id="fhfa-range" class="chart"></div><div id="fhfa-kv" class="kv"></div><div id="fhfa-trend" class="chart small-chart"></div>
    </article>
    <article class="card">
      <h3>Sale history <span class="src">CT records</span></h3>
      <p class="muted small">What it sold for. A sale price is <strong>not</strong> an appraisal: the home may have appraised higher or lower.</p>
      <table class="table" id="sales-table"><thead><tr><th>Date</th><th>Sale price</th><th>Tax assessment</th><th></th></tr></thead><tbody></tbody></table>
    </article>
    <article class="card">
      <h3>Shared records nearby <span class="src">Registry</span></h3>
      <div id="shared"></div>
    </article>
  </section>`;
  renderProperty(p);
  wireOffer();
}

function renderProperty(p) {
  const badges = [];
  if (p.lender_values?.found) badges.push(`<span class="badge green">${p.lender_values.loans} mortgage appraisals nearby</span>`);
  badges.push(Object.keys(p.appraisals?.tract_latest || {}).length ? `<span class="badge green">Neighborhood appraisal stats</span>` : `<span class="badge">County appraisal stats only</span>`);
  badges.push(p.shared.length ? `<span class="badge orange">${p.shared.length} shared record${p.shared.length > 1 ? "s" : ""}</span>` : `<span class="badge">No shared record yet</span>`);
  $("#p-badges").innerHTML = badges.join("");
  renderLender(p.lender_values);
  renderFhfa(p.appraisals);
  $("#sales-table tbody").innerHTML = p.sales.map((s) => `<tr><td>${esc(s.date)}</td><td class="num">${money(s.sale_price)}</td><td class="num">${money(s.assessed_value)}</td><td>${s.arms_length ? "" : `<span class="tag" title="Not an arm's-length sale">${esc(s.non_use_code)}</span>`}</td></tr>`).join("");
  $("#own-shared").hidden = !p.shared.length;
  $("#own-shared-body").innerHTML = p.shared.map((s) => sharedItem(s, false)).join("");
  $("#shared").innerHTML =
    (p.shared.length ? "" : `<div class="empty"><span>No one has shared this home's numbers yet.</span><a class="btn" href="#/share">Bought or sold it? Share yours</a></div>`) +
    (p.shared_nearby.length ? `<h4>Same neighborhood</h4>${p.shared_nearby.map((s) => sharedItem(s)).join("")}` : (p.shared.length ? '<div class="empty">No other shared records in this neighborhood yet.</div>' : ""));
}

function stripChart(values, offer) {
  const W = 520, H = 92, pad = 18;
  const vals = values.filter((v) => typeof v === "number");
  const lo = Math.min(...vals, offer ?? Infinity), hi = Math.max(...vals, offer ?? -Infinity);
  const span = hi - lo || 1;
  const x = (v) => pad + ((v - lo) / span) * (W - 2 * pad);
  const counts = {};
  const dots = vals.map((v) => {
    const k = Math.round(x(v));
    counts[k] = (counts[k] || 0) + 1;
    return `<circle cx="${x(v)}" cy="${62 - (counts[k] - 1) * 9}" r="4.2" fill="#2f8a6b" fill-opacity=".75"><title>${money(v)}</title></circle>`;
  }).join("");
  const offerMark = offer ? `<line x1="${x(offer)}" x2="${x(offer)}" y1="8" y2="74" stroke="#c8702a" stroke-width="2.5"/><text x="${x(offer)}" y="6" text-anchor="middle" font-size="11" fill="#c8702a" font-weight="700">your offer ${money(offer)}</text>` : "";
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Appraised values on 2024 mortgages"><line x1="${pad}" x2="${W - pad}" y1="70" y2="70" stroke="#dfe5e2"/>${dots}${offerMark}
    <text x="${pad}" y="88" font-size="11" fill="#5d6b72">${money(lo)}</text><text x="${W - pad}" y="88" font-size="11" fill="#5d6b72" text-anchor="end">${money(hi)}</text></svg>`;
}

function renderLender(lv) {
  if (!lv?.found) {
    $("#lv-chart").innerHTML = `<div class="empty">No 2024 home loans recorded in this neighborhood.</div>`;
    $("#lv-summary").innerHTML = "";
    return;
  }
  $("#lv-chart").innerHTML = stripChart(lv.values, state.offer);
  const above = state.offer ? lv.values.filter((v) => v >= state.offer).length : null;
  $("#lv-summary").innerHTML = `<div><b>${lv.loans}</b><span>loans in 2024</span></div><div><b>${money(lv.median)}</b><span>median appraised value</span></div>
    <div><b>${kmoney(lv.min)}–${kmoney(lv.max)}</b><span>range</span></div>${above != null ? `<div><b>${above} of ${lv.loans}</b><span>at or above your offer</span></div>` : ""}`;
}

function rangeChart(p25, med, p75, offer) {
  const W = 520, H = 70, pad = 18;
  const lo = Math.min(p25, offer ?? p25) * 0.9, hi = Math.max(p75, offer ?? p75) * 1.06;
  const x = (v) => pad + ((v - lo) / (hi - lo)) * (W - 2 * pad);
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Appraised value range"><line x1="${pad}" x2="${W - pad}" y1="36" y2="36" stroke="#dfe5e2" stroke-width="2"/>
    <rect x="${x(p25)}" y="26" width="${x(p75) - x(p25)}" height="20" rx="6" fill="#e6f1ec" stroke="#2f8a6b"/><line x1="${x(med)}" x2="${x(med)}" y1="22" y2="50" stroke="#1f5f4a" stroke-width="3"/>
    <text x="${x(p25)}" y="64" font-size="11" fill="#5d6b72" text-anchor="middle">${money(p25)}</text><text x="${x(med)}" y="16" font-size="11" fill="#1f5f4a" text-anchor="middle" font-weight="700">median ${money(med)}</text>
    <text x="${x(p75)}" y="64" font-size="11" fill="#5d6b72" text-anchor="middle">${money(p75)}</text>
    ${offer ? `<line x1="${x(offer)}" x2="${x(offer)}" y1="18" y2="54" stroke="#c8702a" stroke-width="2.5"/><text x="${x(offer)}" y="64" font-size="11" fill="#c8702a" text-anchor="middle" font-weight="700">offer</text>` : ""}</svg>`;
}

function trendChart(points) {
  if (points.length < 2) return "";
  const W = 520, H = 90, pad = 26;
  const vals = points.map((p) => p.median);
  const lo = Math.min(...vals) * 0.95, hi = Math.max(...vals) * 1.03;
  const x = (i) => pad + (i / (points.length - 1)) * (W - 2 * pad);
  const y = (v) => 12 + (1 - (v - lo) / (hi - lo)) * (H - 36);
  const path = points.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p.median)}`).join(" ");
  return `<div class="muted small">Median appraised value by year (this neighborhood)</div><svg viewBox="0 0 ${W} ${H}"><path d="${path}" fill="none" stroke="#2f8a6b" stroke-width="2.5"/>
    ${points.map((p, i) => `<circle cx="${x(i)}" cy="${y(p.median)}" r="3" fill="#1f5f4a"><title>${p.year}: ${money(p.median)}</title></circle>`).join("")}
    <text x="${x(0)}" y="${H - 4}" font-size="11" fill="#5d6b72" text-anchor="middle">${points[0].year}</text><text x="${x(points.length - 1)}" y="${H - 4}" font-size="11" fill="#5d6b72" text-anchor="middle">${points.at(-1).year}</text>
    <text x="${x(points.length - 1)}" y="${y(points.at(-1).median) - 8}" font-size="11" fill="#1f5f4a" text-anchor="end" font-weight="700">${money(points.at(-1).median)}</text></svg>`;
}

function renderFhfa(a) {
  const t = a.tract_latest || {}, c = a.county_latest_purchase || {};
  $("#fhfa-range").innerHTML = t.MEDIAN && t.P25 && t.P75
    ? `<div class="muted small">Middle half of appraised values, ${t.MEDIAN.year} (${t.MEDIAN.purpose.toLowerCase()} appraisals)</div>` + rangeChart(t.P25.value, t.MEDIAN.value, t.P75.value, state.offer)
    : `<div class="empty">FHFA hides this neighborhood's figures (too few appraisals to publish). County figures below.</div>`;
  const kv = [];
  if (t.COUNT) kv.push(`<div><b>${t.COUNT.value}</b><span>appraisals in neighborhood, ${t.COUNT.year}</span></div>`);
  if (t.PERBEL) kv.push(`<div><b>${pct(t.PERBEL.value)}</b><span>of purchases priced above the appraisal (neighborhood, ${t.PERBEL.year})</span></div>`);
  if (c.PERBEL) kv.push(`<div><b>${pct(c.PERBEL.value)}</b><span>priced above the appraisal, ${esc(a.county_name || "county")} ${c.PERBEL.year}</span></div>`);
  if (c.MEDIAN) kv.push(`<div><b>${money(c.MEDIAN.value)}</b><span>county median appraisal, ${c.MEDIAN.year}</span></div>`);
  $("#fhfa-kv").innerHTML = kv.join("");
  $("#fhfa-trend").innerHTML = trendChart(a.tract_median_trend || []);
}

function renderAnswer(text) {
  const h = esc(text)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\[number removed: not in the records\]/g, '<span class="removed">[number removed: not in the records]</span>')
    .replace(/\[((?:sale|hmda|fhfa|shared):[^\]]+)\]/g, '<span class="ref">$1</span>');
  let out = "", inList = false;
  for (const ln of h.split(/\n+/)) {
    const m = ln.match(/^\s*[-•*]\s+(.*)/);
    if (m) { if (!inList) { out += "<ul>"; inList = true; } out += `<li>${m[1]}</li>`; }
    else { if (inList) { out += "</ul>"; inList = false; } if (ln.trim()) out += `<p>${ln}</p>`; }
  }
  return out + (inList ? "</ul>" : "");
}

function traceItem(t) {
  if (t.step === "model_call") return `<li><span class="agent">${esc(t.agent)}</span> model call ${t.round != null ? "#" + (t.round + 1) : ""}: ${t.usage.input_tokens} in (${t.usage.cached_tokens} cached) / ${t.usage.output_tokens} out · $${t.cost_usd.toFixed(6)}</li>`;
  if (t.step === "tool_call") return `<li><span class="agent">${esc(t.agent)}</span> → tool <code>${esc(t.tool)}</code>(${esc(JSON.stringify(t.args))})</li>`;
  if (t.step === "answer") return `<li><span class="agent">${esc(t.agent)}</span> wrote its answer</li>`;
  if (t.agent === "grounding_guard") return `<li><span class="agent">grounding guard</span> ${t.ok ? "✓ every $ and % figure traced to a record" : "✗ blocked unsupported figures: " + esc(t.unsupported.join(", "))}</li>`;
  if (t.agent === "privacy_redactor") return `<li><span class="agent">privacy redactor</span> removed ${Object.entries(t.removed).map(([k, v]) => `${v} ${k.replace("_", " ")}`).join(", ") || "nothing"}</li>`;
  if (t.agent === "injection_scan") return `<li><span class="agent">tamper scan</span> ${t.hits ? `⚠ ${t.hits} hidden instruction(s): “${esc(t.excerpts[0])}”` : "no hidden instructions found"}</li>`;
  if (t.agent === "safety_backstop") return `<li><span class="agent">safety backstop</span> ${esc(t.reason)}</li>`;
  if (t.agent === "input_guard") return `<li><span class="agent">input guard</span> ⚠ your question looked like instructions to the AI (“${esc(t.excerpt)}”), so it was not passed on</li>`;
  if (t.agent === "provider_filter") return `<li><span class="agent">provider safety filter</span> blocked the request before any answer was written</li>`;
  if (t.agent === "fallback") return `<li><span class="agent">fallback</span> the AI returned no text, so the plain records are shown instead</li>`;
  if (t.agent === "intake") return t.chars != null ? `<li><span class="agent">intake</span> read ${t.chars.toLocaleString()} characters of text</li>` : `<li><span class="agent">intake</span> numbers ${t.source === "spreadsheet" ? "read from your spreadsheet" : "typed in by you"}</li>`;
  return `<li>${esc(JSON.stringify(t))}</li>`;
}

function usageLine(u) {
  const t = u.total;
  return `· ${t.calls} model calls · ${t.input_tokens.toLocaleString()} in (${t.cached_tokens.toLocaleString()} cached) / ${t.output_tokens.toLocaleString()} out · $${t.cost_usd.toFixed(5)}`;
}

// "5.6% over asking" / "2.2% under asking" / "asking"
const vsAsk = (p) => (p > 0 ? `${p}% over asking` : p < 0 ? `${-p}% under asking` : "asking");

function howOftenPanel(h) {
  if (!h) return "";
  const tiles = [];
  const o = h.over_asking;
  if (o) {
    tiles.push(`<div class="often lead"><span class="o-q">${o.your_over_asking_pct > 0 ? `Appraised ${o.your_over_asking_pct}%+ over asking` : o.your_over_asking_pct < 0 ? `Appraised at ${(100 + o.your_over_asking_pct).toFixed(1)}% of asking or more` : "Appraised at asking or more"}</span>${o.with_appraisal_and_list
      ? `<b class="num">${o.appraised_this_far_over} of ${o.with_appraisal_and_list}</b><small>shared sales in ${o.scope === "neighborhood" ? "this neighborhood" : esc(o.scope)}${o.all_samples ? " · demo samples only" : ""}</small>`
      : `<b class="num">—</b><small>not enough shared sales with asking prices nearby yet</small>`}</div>`);
  }
  if (h.appraisal_vs_price) {
    const a = h.appraisal_vs_price;
    tiles.push(`<div class="often"><span class="o-q">Purchases priced above the appraisal</span><b class="num">${pct(a.below)}</b><small>${a.where === "neighborhood" ? "this neighborhood" : esc(a.where)}, ${a.year}</small></div>`);
  }
  if (h.mortgages) tiles.push(`<div class="often"><span class="o-q">Appraised at ${money(h.offer)} or more</span><b class="num">${h.mortgages.at_or_above} of ${h.mortgages.total}</b><small>2024 mortgages in this neighborhood</small></div>`);
  return tiles.length ? `<div class="often-head">How often homes here appraised high enough</div><div class="often-grid">${tiles.join("")}</div>${similarTable(o)}` : "";
}

function similarTable(o) {
  if (!o || !o.similar || !o.similar.length) return "";
  const sign = (v) => (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(1) + "%";
  const rows = o.similar.map((r) => `<tr>
      <td><a href="#/home/${encodeURIComponent(r.town)}/${encodeURIComponent(r.address)}">${esc(title(r.address))}</a>, ${esc(r.town)}${r.is_sample ? ' <span class="sample">SAMPLE</span>' : ""}</td>
      <td class="num">${sign(r.went_over_asking_pct)}</td>
      <td class="num"><span class="vs ${r.appraised_vs_asking_pct >= 0 ? "ok" : "bad"}">${sign(r.appraised_vs_asking_pct)}</span></td>
      <td>${r.appraised_at_or_above_paid ? "Yes" : "No"}</td></tr>`).join("");
  return `<div class="similar">
    <div class="often-head">${o.similar_is_close ? (o.your_over_asking_pct ? `Homes that went about ${vsAsk(o.your_over_asking_pct)}` : "Homes that went for about asking") : `Closest shared homes to ${o.your_over_asking_pct ? vsAsk(o.your_over_asking_pct) : "asking"}`}</div>
    <p class="muted small">${o.similar_is_close ? `Shared sales within ${o.similar_band_pts} points of your offer, and where each one appraised.` : `None of the shared sales nearby went within ${o.similar_band_pts} points of your offer yet, so these are the nearest.`}</p>
    <table class="sim-table"><thead><tr><th>Home</th><th>Went over asking</th><th>Appraised vs. asking</th><th>Appraised at the price?</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

let offerSeq = 0;  // latest Offer Check submit (see the submit handler)

function wireOffer() {
  const num = (sel) => Number($(sel).value.replace(/\D/g, "")) || 0;
  const overAsk = () => {
    const a = num("#asking"), o = num("#offer"), box = $("#over-ask");
    if (!a || !o) return (box.hidden = true);
    const d = o - a, pctv = (d / a) * 100;
    box.hidden = false;
    box.className = "over-ask " + (d > 0 ? "over" : d < 0 ? "under" : "");
    box.innerHTML = d === 0 ? "Your offer matches the asking price."
      : d < 0 ? `Your offer is ${money(-d)} (${(-pctv).toFixed(1)}%) under asking.`
      : `Your offer is <b>${pctv.toFixed(1)}% over asking</b>. Check the records to see how often homes here have appraised that high.`;
  };
  ["#offer", "#asking"].forEach((sel) => $(sel).addEventListener("input", (e) => {
    const digits = e.target.value.replace(/\D/g, "");
    e.target.value = digits ? Number(digits).toLocaleString("en-US") : "";
    overAsk();
  }));
  $("#offer-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const offer = Number($("#offer").value.replace(/\D/g, ""));
    if (!offer) return;
    state.offer = offer;
    renderLender(state.panel.lender_values);
    renderFhfa(state.panel.appraisals);
    const btn = $("#offer-btn");
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span>Reading the records…';
    $("#offer-out").hidden = false;
    $("#offer-empty").hidden = true;
    $("#offer-often").innerHTML = '<p class="muted loading">Checking the records<span class="dots"><i></i><i></i><i></i></span></p>';
    $("#offer-answer").innerHTML = "";
    $("#offer-guard").textContent = "";
    $("#offer-trace").innerHTML = "";
    const asking = Number($("#asking").value.replace(/\D/g, "")) || null;
    const where = { address: state.panel.address, town: state.panel.town, offer, asking };
    const json = (body) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    // The counts come straight from the records (no AI), so show them first.
    // Each submit gets a number; a response only writes if it is still the latest one and the
    // visitor is still on this home's page (otherwise it would land on another home).
    const my = ++offerSeq, form = e.currentTarget;
    const live = () => my === offerSeq && document.contains(form);
    const often = $("#offer-often"), answer = $("#offer-answer"), guard = $("#offer-guard");
    let shownFast = false;
    api("/api/how-often", json(where)).then((h) => {
      if (!live()) return;
      shownFast = true;
      often.innerHTML = howOftenPanel(h);
      if (!answer.textContent) answer.innerHTML = '<p class="muted loading">Writing a plain-English summary<span class="dots"><i></i><i></i><i></i></span></p>';
    }).catch(() => {});
    try {
      const r = await api("/api/offer-check", json({ ...where, question: $("#question").value }));
      if (!live()) return;
      if (!shownFast) often.innerHTML = howOftenPanel(r.facts?.how_often);
      answer.innerHTML = renderAnswer(r.answer) + (r.blocked && r.facts?.facts ? "<ul>" + r.facts.facts.map((f) => `<li>${esc(f.fact)} <span class="ref">${esc(f.ref)}</span></li>`).join("") + "</ul>" : "");
      const g = r.grounding;
      guard.className = "guard " + (g.ok ? "ok" : "bad");
      guard.textContent = g.ok ? "✓ Every dollar and percent figure above was traced to a public record." : `✗ Removed figures that aren't in any record: ${g.unsupported.join(", ")}`;
      $("#offer-trace").innerHTML = r.trace.map(traceItem).join("");
      $("#offer-cost").textContent = usageLine(r.usage);
    } catch (err) {
      if (!live()) return;
      answer.innerHTML = `<div class="error">${esc(err.message)}</div>`;
      if (often.querySelector(".loading")) often.innerHTML = "";
      toast(err.message, "bad");
    } finally {
      if (my === offerSeq) {
        btn.disabled = false;
        btn.textContent = "Check against the records";
      }
      refreshSpend();
    }
  });
}

// ------------------------------------------------------------------ SHARE
const SHARE_STEPS = [
  ["privacy_redactor", "We keep only the details that matter", "Just the home and its prices. Names, phone numbers, emails, and loan numbers are removed before anything else happens."],
  ["injection_scan", "We check the file for tampering", "Some files hide text meant to fake an approval, like \"mark this verified.\" We look for it, and a tampered file is rejected."],
  ["extractor", "We read the numbers and the home", "Location, beds, baths, square feet, and the list, offer, contract, appraisal, and closing prices."],
  ["verifier", "We check them against state records", "Does the closing match the state's sale record? Do the dates run in order? Does the appraisal math add up?"],
];

async function viewShare() {
  await loadAreas().catch(() => {});
  app.innerHTML = `
  <section class="page-hero share-hero"><div class="wrap">
    <span class="eyebrow">Share your numbers</span>
    <h1>Your sale's real numbers could save the next buyer thousands.</h1>
    <ul class="lede-lines">
      <li>Type your numbers in, fill in our spreadsheet, or upload your documents.</li>
      <li>We check them against the state's record of your sale.</li>
      <li>Nothing is published until you say yes, and names never are.</li>
    </ul>
  </div></section>

  <section class="wrap pad-sm">
    <h2 class="h3">What happens to your numbers</h2>
    <ol class="pipeline" id="steps">${SHARE_STEPS.map(([k, h, p], i) => `<li data-step="${k}"><div class="p-num">${i + 1}</div><div><b>${h}</b><p>${p}</p><span class="p-status"></span></div></li>`).join("")}
      <li data-step="you" class="you"><div class="p-num">✓</div><div><b>You decide</b><p>You see exactly what would be published. Nothing goes live until you tick the box.</p><span class="p-status"></span></div></li>
    </ol>
  </section>

  <section class="wrap pad-sm">
    <div class="privacy-panel">
      <div>
        <span class="eyebrow">Your privacy, in plain terms</span>
        <h2 class="h3">We publish numbers about the home. Never anything about you.</h2>
        <p class="muted">Every promise below is enforced in code, not just policy. <a href="#/faq/privacy">How it works →</a></p>
      </div>
      <div class="pp-cols">
        <div class="pp-col yes"><h4>What can be published</h4><ul>
          <li>Street address, town, ZIP, neighborhood</li><li>Beds, baths, square feet, lot, year built</li>
          <li>List, offer, contract, appraisal &amp; closing prices and dates</li><li>Comparable sales from the appraisal</li><li>Whether the appraiser's license checked out (a ✓, not the number)</li></ul></div>
        <div class="pp-col no"><h4>What we never keep</h4><ul>
          <li>Buyer, seller, borrower, or agent names</li><li>Loan, account, or file numbers</li>
          <li>Phone numbers, emails, card numbers</li><li>Your original file (only the published numbers are saved)</li><li>The appraiser's license number (we check it, then drop it)</li><li>Anything at all, if you don't tick the consent box</li></ul></div>
        <div class="pp-col how"><h4>Who sees what</h4><ul>
          <li><b>Right away:</b> personal details are removed</li><li><b>Our reader:</b> only fills in a fixed form of home facts and prices, with no place for names</li>
          <li><b>The public:</b> sees only what you approve</li><li><b>You:</b> get a private removal code to take it down any time</li></ul></div>
      </div>
    </div>
  </section>

  <section class="wrap pad-sm">
    <div class="card share-card">
      <div class="mode-tabs" role="tablist">
        <button class="mode active" data-mode="type" role="tab">✎ Type it in</button>
        <button class="mode" data-mode="sheet" role="tab">▦ Use our spreadsheet</button>
        <button class="mode" data-mode="pdf" role="tab">⬆ Upload a PDF</button>
      </div>

      <div class="mode-pane" data-pane="type">
        <p class="muted small">No documents needed. Fill in what you know and leave the rest blank. Typed-in records are labeled <b>owner-reported</b>, and a closing price that matches the state's record gets a ✓.</p>
        <form id="manual-form" class="manual-form">
          <fieldset><legend>The home</legend>
            <label class="span2">Street address<input name="address" required placeholder="19 Shoreham Rd" maxlength="80" /></label>
            <label>Town<select name="city" required>${(state.areas || []).map((a) => `<option>${esc(a.town)}</option>`).join("")}</select></label>
            <label>ZIP<input name="zip" inputmode="numeric" maxlength="5" placeholder="06512" /></label>
            <label>Home type<select name="property_type"><option>Single Family</option><option>Condo</option><option>Two Family</option><option>Three Family</option><option>Four Family</option></select></label>
            <label>Beds<input name="bedrooms" inputmode="numeric" maxlength="2" /></label>
            <label>Baths<input name="bathrooms" inputmode="decimal" maxlength="4" /></label>
            <label>Living area (sq ft)<input name="gla_sqft" inputmode="numeric" maxlength="6" /></label>
            <label>Year built<input name="year_built" inputmode="numeric" maxlength="4" /></label>
            <label>Neighborhood (optional)<input name="neighborhood" maxlength="40" /></label>
          </fieldset>
          <fieldset class="prices"><legend>The numbers</legend>
            ${[["list", "List price", "list_price", "list_date"], ["offer", "Your offer", "offer_price", "offer_date"], ["contract", "Contract price", "contract_price", "contract_date"], ["appraisal", "Appraised value", "appraised_value", "effective_date"], ["close", "Closing price", "close_price", "close_date"]]
              .map(([k, label, price, d]) => `<div class="price-row ${k === "appraisal" ? "appraisal" : ""}"><span>${label}</span><div class="money sm"><span>$</span><input name="${price}" inputmode="numeric" data-money /></div><input name="${d}" type="date" aria-label="${label} date" /></div>`).join("")}
            <div class="price-row"><span>Seller credits</span><div class="money sm"><span>$</span><input name="seller_concessions" inputmode="numeric" data-money /></div><span></span></div>
          </fieldset>
          <button class="btn primary" type="submit">Check my numbers</button>
        </form>
      </div>

      <div class="mode-pane" data-pane="sheet" hidden>
        <div class="sheet-steps">
          <div><b>1.</b> Download the template: <a href="/static/samples/appraisal_commons_template.xlsx" download>Excel (.xlsx)</a> · <a href="/static/samples/appraisal_commons_template.csv" download>CSV</a></div>
          <div><b>2.</b> Fill in the green "Your value" column. Leave anything you don't know blank.</div>
          <div><b>3.</b> Upload it below, just as it is. No need to convert it to a PDF.</div>
        </div>
        <label class="drop" data-drop="sheet">
          <input type="file" accept=".xlsx,.csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,text/csv" hidden />
          <div class="drop-icon">▦</div><div><strong>Choose your filled-in spreadsheet</strong> or drop it here</div>
          <div class="muted small">.xlsx or .csv · try the <a href="/static/samples/sample_spreadsheet_19_shoreham_rd.xlsx" download>filled-in SAMPLE</a></div>
        </label>
      </div>

      <div class="mode-pane" data-pane="pdf" hidden>
        <label class="drop" data-drop="pdf">
          <input type="file" accept="application/pdf" hidden />
          <div class="drop-icon"><svg viewBox="0 0 24 24" width="30" height="30" aria-hidden="true"><path d="M12 16V4m0 0-4 4m4-4 4 4M5 20h14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg></div>
          <div><strong>Choose a PDF</strong> or drop it here</div>
          <div class="muted small">Appraisal (Form 1004), MLS listing, purchase contract, or closing disclosure · text PDFs up to 5 MB</div>
        </label>
        <div class="samples muted small">No document handy? Try a demo file (all marked SAMPLE):
          <a href="/static/samples/sample_packet_5_pope_st.pdf" download>full packet</a> ·
          <a href="/static/samples/sample_listing_closing_103_portland_st.pdf" download>listing + closing, no appraisal</a> ·
          <a href="/static/samples/sample_appraisal_tampered.pdf" download>tampered appraisal (safety test)</a></div>
      </div>

      <div id="share-out" hidden>
        <div id="verdict" class="verdict"></div>
        <div class="two-col">
          <div>
            <h4>The price chain</h4><div id="draft-ladder"></div>
            <h4>The home</h4><div id="draft-kv" class="kv"></div><div id="docs-found" class="muted small"></div>
            <h4 id="comps-h">Comparable sales in the appraisal</h4>
            <table class="table" id="comps-table"><thead><tr><th>Address</th><th>Sold</th><th>Price</th><th>Net adj.</th><th>Adjusted</th></tr></thead><tbody></tbody></table>
          </div>
          <div>
            <h4>Our checks</h4><ul id="checks" class="checks"></ul>
            <h4>Privacy</h4><div id="privacy" class="muted small"></div>
          </div>
        </div>
        <div class="consent" id="consent-box">
          <label><input type="checkbox" id="consent" /> I'm the buyer or seller in this sale (or have their permission) and agree to publish the numbers above. No names or loan details are published, and I'll get a private code to remove it any time.</label>
          <label><input type="checkbox" id="is-sample" /> This is a demo/sample document (it will be labeled SAMPLE).</label>
          <button class="btn primary" id="publish-btn" disabled>Publish to the registry</button>
          <div id="publish-msg" class="small"></div>
        </div>
        <details class="trace"><summary>Behind the scenes <span id="share-cost" class="muted small"></span></summary><ol id="share-trace"></ol></details>
      </div>
    </div>
  </section>

  <section class="wrap pad-sm">
    <details class="card remove-card" id="remove-card">
      <summary><b>Remove a record</b> <span class="muted small">Changed your mind? Use the private code you got when you shared. No account needed.</span></summary>
      <form id="remove-form" class="remove-form">
        <input id="remove-code" type="text" maxlength="40" placeholder="AC-xxxxxxxxxxxx" aria-label="Removal code" required />
        <button class="btn">Remove it now</button>
        <span id="remove-msg" class="small"></span>
      </form>
    </details>
  </section>`;
  wireShare();
}

function setSteps(done, running, statuses = {}) {
  $$("#steps li").forEach((li, i) => {
    li.classList.toggle("done", i < done);
    li.classList.toggle("run", i === running);
    li.classList.remove("alert");
    const st = $(".p-status", li);
    if (st) st.textContent = statuses[li.dataset.step] || (i === running ? "working…" : "");
  });
}

function wireShare() {
  $$(".mode").forEach((b) => b.addEventListener("click", () => {
    $$(".mode").forEach((x) => x.classList.toggle("active", x === b));
    $$(".mode-pane").forEach((p) => (p.hidden = p.dataset.pane !== b.dataset.mode));
  }));
  $$("[data-drop]").forEach((drop) => {
    const input = $("input", drop);
    drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); if (e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]); });
    input.addEventListener("change", () => { if (input.files[0]) upload(input.files[0]); input.value = ""; });
  });
  $$("[data-money]").forEach((i) => i.addEventListener("input", () => {
    const d = i.value.replace(/\D/g, "");
    i.value = d ? Number(d).toLocaleString("en-US") : "";
  }));
  $("#manual-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const fields = {};
    new FormData(e.target).forEach((v, k) => { if (String(v).trim()) fields[k] = String(v).replace(/,/g, "").trim(); });
    upload(null, fields);
  });
  $("#consent").addEventListener("change", (e) => ($("#publish-btn").disabled = !e.target.checked));
  $("#publish-btn").addEventListener("click", publish);
  $("#remove-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await api("/api/share/remove", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code: $("#remove-code").value.trim() }) });
      state.areas = null;
      $("#remove-msg").innerHTML = `<span class="ok-text">✓ Removed ${esc(title(r.removed.address))}${r.removed.town ? ", " + esc(r.removed.town) : ""}. It's gone from the site.</span>`;
      $("#remove-code").value = "";
    } catch (err) {
      $("#remove-msg").innerHTML = `<span class="error-text">${esc(err.message)}</span>`;
    }
  });
}

async function upload(file, fields = null) {
  $("#share-out").hidden = true;
  let tick = 0;
  setSteps(0, 0);
  $("#steps").scrollIntoView({ behavior: "smooth", block: "start" });
  const timer = setInterval(() => { tick = Math.min(tick + 1, 3); setSteps(tick, tick); }, fields ? 1200 : 2200);
  try {
    let r;
    if (fields) {
      r = await api("/api/share/manual", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ fields }) });
    } else {
      const fd = new FormData();
      fd.append("file", file);
      r = await api("/api/share", { method: "POST", body: fd });
    }
    if (!r.draft) throw new Error(r.message || "We couldn't read that. Please check it and try again.");
    clearInterval(timer);
    const removed = r.trace.find((t) => t.agent === "privacy_redactor")?.removed || {};
    const nRemoved = Object.values(removed).reduce((a, b) => a + b, 0);
    const scan = r.trace.find((t) => t.agent === "injection_scan");
    const v = r.draft.verification;
    setSteps(5, -1, {
      privacy_redactor: `Done: ${nRemoved} personal detail${nRemoved === 1 ? "" : "s"} removed`,
      injection_scan: scan?.hits ? `Stopped: ${scan.hits} hidden instruction${scan.hits > 1 ? "s" : ""} found` : "Done: no tampering found",
      extractor: `Done: ${STEPS.filter(([k]) => r.draft[k]).length} price${STEPS.filter(([k]) => r.draft[k]).length === 1 ? "" : "s"} found`,
      verifier: `Done: ${(v.checks || []).filter((c) => c.result === "pass").length} checks passed`,
      you: v.verdict === "rejected" ? "Can't be published" : "Your call",
    });
    if (scan?.hits) $$("#steps li")[1].classList.add("alert");
    if (v.verdict === "rejected") $$("#steps li").at(-1).classList.add("alert");
    renderDraft(r);
  } catch (err) {
    clearInterval(timer);
    setSteps(0, -1);
    state.draft = null;
    $("#share-out").hidden = false;
    ["#draft-ladder", "#draft-kv", "#docs-found", "#checks", "#privacy", "#share-trace"].forEach((sel) => ($(sel).innerHTML = ""));
    $("#comps-h").hidden = true;
    $("#comps-table").hidden = true;
    $("#consent-box").hidden = true;
    $("#verdict").className = "verdict rejected";
    $("#verdict").innerHTML = esc(err.message);
  } finally {
    refreshSpend();
  }
}

function renderDraft(r) {
  $("#share-out").hidden = false;
  const d = (state.draft = r.draft);
  const v = d.verification;
  const label = { verified: "Looks good", needs_review: "Needs a second look", rejected: "Can't be published" }[v.verdict] || v.verdict;
  $("#verdict").className = "verdict " + v.verdict;
  $("#verdict").innerHTML = `<b>${label}</b> · ${esc(v.summary)}`;
  $("#draft-ladder").innerHTML = ladder(d);
  $("#draft-kv").innerHTML = [
    ["Address", esc(title(d.address)) + (d.unit ? " #" + esc(d.unit) : "")], ["Location", esc([d.neighborhood, d.town, d.zip && "CT " + d.zip].filter(Boolean).join(" · ") || "—")],
    ["Type", esc(d.property_type || "—")], ["Beds / baths", `${d.bedrooms ?? "—"} / ${d.bathrooms ?? "—"}`],
    ["Living area", d.gla_sqft ? d.gla_sqft.toLocaleString() + " sq ft" : "—"], ["Lot", esc(d.lot_size || "—")],
    ["Year built", d.year_built ?? "—"], ["Days on market", d.days_on_market ?? "—"],
    ["Seller concessions", d.seller_concessions != null ? money(d.seller_concessions) : "—"], ["Appraiser license", d.license_checked ? "✓ checked" : (d.document_types || []).includes("appraisal") ? "not verified" : "—"],
  ].map(([k, val]) => `<div><b>${val}</b><span>${k}</span></div>`).join("");
  const names = { appraisal: "appraisal", listing: "listing sheet", purchase_contract: "purchase contract", closing_disclosure: "closing disclosure", other: "other" };
  $("#docs-found").textContent = d.source === "self_reported"
    ? "Typed in by the owner, so it will be labeled owner-reported."
    : "Found in this PDF: " + ((d.document_types || []).map((t) => names[t] || t).join(", ") || "unknown document type");
  const hasComps = (d.comps || []).length > 0;
  $("#comps-h").hidden = !hasComps;
  $("#comps-table").hidden = !hasComps;
  $("#comps-table tbody").innerHTML = (d.comps || []).map((c) => `<tr><td>${esc(title(c.address))}</td><td>${esc(c.sale_date)}</td><td class="num">${money(c.sale_price)}</td><td class="num">${c.net_adjustment == null ? "—" : (c.net_adjustment >= 0 ? "+" : "−") + money(Math.abs(c.net_adjustment))}</td><td class="num">${money(c.adjusted_price)}</td></tr>`).join("");
  const CHECK_NAMES = { check_prices: "Prices", check_address: "Address", check_public_sale: "Matches state sale record", check_timeline: "Dates in order", check_license: "Appraiser license", check_comp_math: "Appraisal math" };
  $("#checks").innerHTML = (v.checks || []).map((c) => `<li><span class="pill ${c.result === "n/a" ? "na" : c.result}">${c.result}</span><span><strong>${esc(CHECK_NAMES[c.check] || c.check)}</strong>: ${esc(c.reason)}</span></li>`).join("");
  const removed = Object.entries(v.pii_removed || {}).map(([k, n]) => `${n} ${k.replace("_", " ")}`).join(", ");
  $("#privacy").textContent = (removed ? `Removed before the file was read: ${removed}. ` : "No personal details detected. ") + "Names, loan numbers, and contact details are never stored.";
  $("#consent-box").hidden = v.verdict === "rejected";
  $("#publish-btn").textContent = v.verdict === "needs_review" ? "Send to the review desk" : "Publish to the registry";
  $("#consent").checked = false;
  $("#publish-btn").disabled = true;
  $("#publish-msg").textContent = "";
  $("#share-trace").innerHTML = r.trace.map(traceItem).join("");
  $("#share-cost").textContent = usageLine(r.usage);
}

async function publish() {
  try {
    const r = await api("/api/share/publish", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ draft_id: state.draft.draft_id, consent: $("#consent").checked, is_sample: $("#is-sample").checked }),
    });
    state.areas = null;
    const head = r.held_for_review
      ? `<b>Sent to our review desk.</b> A person will look at the flagged checks and publish it if everything holds up (ticket #${r.ticket}).`
      : `✓ Published. ${r.town ? `<a href="${propHref(r.town, r.address)}">See it on ${esc(title(r.address))} →</a>` : ""}`;
    $("#publish-msg").innerHTML = `<div class="code-box"><div>${head}</div>
      <div><span class="muted small">Your private removal code. Save it: it's the only way to take this record down, and we can't show it again.</span>
      <div class="code-row"><code id="rm-code">${esc(r.removal_code)}</code><button class="btn" id="copy-code" type="button">Copy</button></div></div></div>`;
    $("#copy-code").addEventListener("click", () => navigator.clipboard?.writeText(r.removal_code).then(() => toast("Removal code copied", "ok")));
    $("#publish-btn").disabled = true;
    $$("#steps li").at(-1).classList.add("done");
    toast(r.held_for_review ? "Sent for review. Thank you!" : "Published to the registry. Thank you!", "ok");
  } catch (err) {
    $("#publish-msg").innerHTML = `<span class="error">${esc(err.message)}</span>`;
  }
}

// ------------------------------------------------------------------ RESOURCES
const RESOURCES = [
  { group: "Buying your first home", icon: "🔑", items: [
    { name: "CHFA homebuyer programs", by: "Connecticut Housing Finance Authority", url: "https://www.chfa.org/", text: "Down payment assistance and the Time To Own forgivable loan for first-time buyers. Most programs require a homebuyer education course." },
    { name: "Down Payment & Closing Cost Assistance", by: "City of New Haven", url: "https://newhavenct.gov/government/departments-divisions/economic-development-administration/housing-administration-and-development/homebuyers/down-payment-closing-cost-assistance-financing", text: "A 0% forgivable loan for first-time buyers purchasing a home they'll live in, inside New Haven." },
    { name: "Buying a House: tools & guides", by: "Consumer Financial Protection Bureau", url: "https://www.consumerfinance.gov/owning-a-home/", text: "Step-by-step help comparing loan estimates, reading your closing disclosure, and checking rates." },
    { name: "Find a housing counselor", by: "CFPB / HUD", url: "https://www.consumerfinance.gov/find-a-housing-counselor/", text: "Search for free or low-cost HUD-approved housing counselors near you." },
  ]},
  { group: "When your price is above the appraisal", icon: "📉", items: [
    { name: "Reconsideration of value guidance (2024)", by: "CFPB and four other federal agencies", url: "https://www.consumerfinance.gov/about-us/newsroom/agencies-finalize-interagency-guidance-on-reconsiderations-of-value-for-residential-real-estate-valuations/", text: "How lenders should handle a borrower's request to re-check a low appraisal, including errors or missed comparable sales." },
    { name: "What is an appraisal gap?", by: "Redfin", url: "https://www.redfin.com/blog/what-is-an-appraisal-gap/", text: "Plain-English explainer of what happens when a home appraises below the contract price, and your options." },
    { name: "Appraisal gaps hit starter homes hardest", by: "Scotsman Guide (CoreLogic analysis)", url: "https://www.scotsmanguide.com/news/why-starter-home-appraisals-can-lead-to-higher-overpayment-risk/", text: "Why first-time buyers are more exposed to low appraisals than the market as a whole." },
  ]},
  { group: "Learn from creators", icon: "🎥", note: "We don't endorse creators. Check any advice against the official sources above.", items: [
    { name: "How to Buy a Home (David Sidoni)", by: "Podcast & YouTube", url: "https://www.youtube.com/results?search_query=David+Sidoni+how+to+buy+a+home", text: "A real estate professional and consumer advocate who breaks buying into steps, from budgeting to making an offer." },
    { name: "Videos: appraisal gap explained", by: "YouTube search", url: "https://www.youtube.com/results?search_query=appraisal+gap+explained+first+time+home+buyer", text: "Short explainers from agents and lenders on appraisal gaps, gap coverage, and contingencies." },
  ]},
  { group: "The data behind this site", icon: "📊", items: [
    { name: "Uniform Appraisal Dataset statistics", by: "Federal Housing Finance Agency", url: "https://www.fhfa.gov/data/uniform-appraisal-dataset-aggregate-statistics", text: "The first public statistics built from millions of real appraisal reports, down to the census tract." },
    { name: "HMDA Data Browser", by: "CFPB / FFIEC", url: "https://ffiec.cfpb.gov/data-browser/", text: "Every mortgage application lenders report, including the property value they relied on." },
    { name: "Real Estate Sales", by: "CT Office of Policy & Management", url: "https://data.ct.gov/Housing-and-Development/Real-Estate-Sales-2001-2023-GL/5mzw-sjtu", text: "Every Connecticut property sale of $2,000 or more, with sale price and tax assessment." },
  ]},
];

function viewResources() {
  app.innerHTML = `
  <section class="page-hero"><div class="wrap">
    <span class="eyebrow">Resources</span>
    <h1>Help that already exists, gathered in one place.</h1>
    <p class="lede">Down payment programs, free counseling, what to do when your price is above the appraisal, and the public data we use. Every link opens the official source.</p>
  </div></section>
  <section class="wrap pad">${RESOURCES.map((g) => `
    <div class="res-group"><h2><span aria-hidden="true">${g.icon}</span> ${esc(g.group)}</h2>${g.note ? `<p class="muted small">${esc(g.note)}</p>` : ""}
      <div class="res-grid">${g.items.map((r) => `<a class="res-card" href="${r.url}" target="_blank" rel="noopener noreferrer">
        <span class="res-by">${esc(r.by)}</span><h3>${esc(r.name)} <span aria-hidden="true">↗</span></h3><p>${esc(r.text)}</p></a>`).join("")}</div></div>`).join("")}
    <p class="muted small">Program terms change. Confirm amounts and eligibility with the program before you rely on them.</p>
  </section>`;
}

// ------------------------------------------------------------------ FAQ
const FAQ = [
  ["Is this an appraisal?", "No. Appraisal Commons shows appraisals and lender decisions that already happened, as public records and owner-shared documents. Only a licensed appraiser can appraise a home, and we never estimate a value."],
  ["Why can't I just look at the sale price?", "A sale price is what someone paid. The appraisal is what the lender's appraiser concluded the home was worth, and the two often differ. A home that sold for $380,000 might have appraised at $400,000 (the buyer got a deal) or at $360,000 (the price was $20,000 above the appraisal). Sale history alone can't tell you which."],
  ["What's an appraisal gap, and why does it matter?", "An appraisal gap is when the price a buyer agrees to pay is more than the appraised value. Lenders lend on the appraised value, not the price, so the buyer is usually expected to bring the difference in cash. In Connecticut, about 1 in 10 home purchases in 2023 had a price higher than the appraisal, per FHFA.", null, [["FHFA Uniform Appraisal Dataset Aggregate Statistics", "https://www.fhfa.gov/data/uniform-appraisal-dataset-aggregate-statistics"]]],
  ["Where does the \"1 in 10\" figure come from?", "From the Federal Housing Finance Agency's Uniform Appraisal Dataset (UAD) Aggregate Statistics, the first public dataset built from real appraisal reports. We use FHFA's series on how often the contract price was higher than the appraisal, for purchases of single-family homes in Connecticut. For 2023, the latest full year published, it is 9.9%, which is about 1 in 10 (1 ÷ 0.099 ≈ 10). It varies by county, from below the state figure to above it (New Haven County was 11.2%), and each town page shows its own county. It covers appraisals for loans sold to Fannie Mae and Freddie Mac, which are most mortgages but not FHA, VA, or cash purchases. The yearly bars on the home page are the same statewide series.", "one-in-ten", [["FHFA Uniform Appraisal Dataset Aggregate Statistics", "https://www.fhfa.gov/data/uniform-appraisal-dataset-aggregate-statistics"]]],
  ["How does this help in a competitive market?", "When several buyers want the same home, offers often go over asking. Your lender still lends only on the appraised value, not on what you offered. Seeing what similar homes actually appraised for, and how often prices nearby were higher than the appraisal, helps you judge how far over asking is reasonable. If the appraisal is lower than the price, the buyer is usually expected to bring the difference in cash at closing, so this gives you an idea of whether you might need that extra cash and how much appraisal-gap coverage you might be promising. It isn't advice: talk your plan through with your agent or lender.", "competitive", [["Interagency guidance on reconsiderations of value (2024)", "https://www.consumerfinance.gov/about-us/newsroom/agencies-finalize-interagency-guidance-on-reconsiderations-of-value-for-residential-real-estate-valuations/"]]],
  ["What is the \"appraised value on 2024 mortgages\"?", "When a lender approves a mortgage, it reports the home's value it relied on to the government (HMDA data). That's usually the appraised value, and sometimes the purchase price. It is not the loan amount, so a buyer's down payment doesn't change it. It also doesn't include the asking price, so it can't tell you whether a home sold over asking. For that, look at shared records, which show list price, offer, contract, appraisal, and closing side by side. Values are published in $10,000 bands.", "mortgage-values", [["CFPB HMDA Data Browser", "https://ffiec.cfpb.gov/data-browser/"], ["CFPB: HMDA reportable data summary", "https://files.consumerfinance.gov/f/201510_cfpb_hmda-summary-of-reportable-data.pdf"]]],
  ["How is this different from Zillow, Redfin, or Realtor.com?", "Those sites show listings, asking prices, and sold prices, and some publish an automated estimate of a home's value (like the Zestimate or Redfin Estimate). An estimate is a model's guess. None of them show what homes actually appraised for, which is the number your lender uses. Appraisal Commons shows real appraisal statistics from the federal government, the home values lenders actually used on recent mortgages, and, from owners who share them, the full list → offer → appraisal → close story, including how far over asking each home went and where its appraisal landed against the asking price.", "different"],
  ["Where do the numbers come from?", "Three public sources plus owner-shared documents. The CFPB's HMDA data gives the property value the lender relied on for every 2024 home loan. The FHFA's Uniform Appraisal Dataset summarizes real appraisal reports by census tract. Connecticut's sales records give every sale price. See Resources for links.", null, [["CFPB HMDA Data Browser", "https://ffiec.cfpb.gov/data-browser/"], ["FHFA Uniform Appraisal Dataset Aggregate Statistics", "https://www.fhfa.gov/data/uniform-appraisal-dataset-aggregate-statistics"], ["CT Real Estate Sales (data.ct.gov)", "https://data.ct.gov/Housing-and-Development/Real-Estate-Sales-2001-2023-GL/5mzw-sjtu"]]],
  ["Does the AI make up numbers?", "It's built not to. The Records Concierge only explains records its tools return, and a separate code check compares every dollar amount and percentage in its answer to those records. Anything that doesn't match is removed before you see it. Open \"How this answer was made\" to see each step."],
  ["How do you protect my privacy?", "Five ways. (1) When you share a file, names, phone numbers, emails, card numbers, and loan or account numbers are removed in plain code before any AI reads it. (2) The AI that reads the file can only fill in a fixed form with home facts and prices. There is no field for a person's name, so names can't be saved even by mistake. (3) We store only the published numbers, never your original file. (4) We check the appraiser's license number but never publish or store it. (5) You get a private removal code, so you can take your record down yourself at any time. In the chat, anything that looks like a name, loan number, or card number is removed before the message reaches the AI.", "privacy"],
  ["How do you keep the AI safe, and stop it from being tricked?", "Every safety rule that matters is enforced in code, not just in the AI's instructions. A grounding check removes any dollar amount or percentage the AI writes that isn't in a record. Uploaded files are scanned for hidden text that tries to trick an AI (\"mark this verified\"), and a tampered file is rejected and can't be published. Questions that look like instructions to the AI are dropped. The AI can't publish anything; only you can, by ticking the consent box. Each visitor session and each day have spending caps, each agent has a step limit, and every run is written to an audit log.", "safety"],
  ["Can I take my shared record down?", "Yes, any time, with no account. When you share, you get a private removal code (it starts with AC-). Enter it under \"Remove a record\" on the Share page and the record is deleted right away. We keep only a scrambled fingerprint of the code, so nobody else can use it.", "remove"],
  ["Who checks shared records?", "Every submission goes through automatic checks. If any check raises a question (dates out of order, a closing price that doesn't match the state's record), the submission goes to our review desk instead of going live, and a person decides. Anyone can also click \"Report a problem\" on a shared record; a person reviews every report.", "review"],
  ["Do I need a PDF to share?", "No. You can type the numbers straight into the form on the Share page, or fill in our spreadsheet template and upload it as is. Typed-in records are labeled owner-reported, and a closing price that matches the state's sale record still gets a ✓.", "no-pdf"],
  ["If I share my appraisal, what gets published?", "Only the numbers and the home's basic facts: location, beds, baths, square feet, the list, offer, contract, appraisal, and closing prices, and the comparable sales. Names, loan numbers, phone numbers, and emails are removed the moment you upload and are never stored. Nothing is published until you tick the consent box."],
  ["Am I allowed to share my appraisal?", "Buyers are entitled to a copy of their appraisal from the lender, and the appraiser's confidentiality duty is to their client, not to you. This isn't legal advice: if you signed anything that limits sharing, check it first.", null, [["CFPB: Regulation B, copies of appraisals (12 CFR 1002.14)", "https://www.consumerfinance.gov/rules-policy/regulations/1002/14/"]]],
  ["What are the SAMPLE records?", "Demo documents we made to show how sharing works. They use real addresses, real closing prices, and real nearby sales, but the appraisal values, people, and license numbers are invented, and they're always labeled SAMPLE."],
  ["Why only Connecticut?", "Connecticut is where we started, and the site is built to expand. Connecticut publishes every property sale with its price, which lets us check shared records against the state's own record. The appraisal statistics and mortgage data we use exist for every state, so other states are a matter of loading them, though about a dozen states don't make sale prices public, which limits the sale-record check there."],
];
// Keep the last two words together so a single word never sits alone on the last line.
const noOrphan = (text) => esc(text).replace(/ (\S+)$/, "&nbsp;$1");
const faqItem = ([q, a, id, sources], open = false) => `<details class="faq"${id ? ` id="faq-${id}"` : ""}${open ? " open" : ""}><summary>${esc(q)}</summary><p>${noOrphan(a)}</p>${sources?.length ? `<div class="faq-src"><span>Sources</span>${sources.map(([label, url]) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${esc(label)} ↗</a>`).join("")}</div>` : ""}</details>`;

function scrollToFaq(openId) {
  const el = openId && document.getElementById("faq-" + openId);
  if (el) setTimeout(() => el.scrollIntoView({ behavior: "smooth", block: "center" }), 60);
}

function viewFaq(openId) {
  setTimeout(() => scrollToFaq(openId), 0);
  app.innerHTML = `
  <section class="page-hero"><div class="wrap"><span class="eyebrow">FAQ</span><h1>Questions, answered plainly.</h1>
    <div class="chips-row"><a class="chip" href="#/faq/privacy">Privacy</a><a class="chip" href="#/faq/safety">AI safety</a><a class="chip" href="#/faq/one-in-ten">The "1 in 10" figure</a><a class="chip" href="#/faq/remove">Removing a record</a></div></div></section>
  <section class="wrap pad narrow-col"><div class="faq-list">${FAQ.map((f) => faqItem(f, f[2] && f[2] === openId)).join("")}</div>
    <div class="card soft center-card"><h3>Still wondering something?</h3><p>Try a search, or ask the Records Guide in the corner. Every number on a home page links to the record it came from.</p><a class="btn primary" href="#/areas">Explore Areas</a></div>
  </section>`;
}

// ------------------------------------------------------------------ ABOUT
const AGENT_ICONS = {
  concierge: '<svg viewBox="0 0 24 24"><path d="M4 19V9m5 10V5m5 14v-7m5 7V3" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>',
  guide: '<svg viewBox="0 0 24 24"><path d="M4 5h16v11H9l-5 4z" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linejoin="round"/></svg>',
  extractor: '<svg viewBox="0 0 24 24"><path d="M6 3h9l4 4v14H6z M15 3v4h4 M9 12h7M9 16h7" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>',
  verifier: '<svg viewBox="0 0 24 24"><path d="M12 3 4 6v6c0 4.5 3.4 8 8 9 4.6-1 8-4.5 8-9V6z M8.5 12l2.5 2.5 4.5-5" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/></svg>',
  human: '<svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="4" fill="none" stroke="currentColor" stroke-width="2.2"/><path d="M4 21c1-4.5 4.5-6.5 8-6.5s7 2 8 6.5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>',
};

async function viewAbout() {
  const u = await api("/api/usage").catch(() => null);
  const k = u?.all_runs?.by_kind || {};
  const names = { offer_check: "Offer Check", share_record: "Share a record" };
  app.innerHTML = `
  <section class="page-hero"><div class="wrap">
    <span class="eyebrow">About us</span>
    <h1>Both sides of a home sale deserve to see the same numbers.</h1>
  </div></section>
  <section class="wrap pad about-grid">
    <div class="prose">
      <h2>Why this exists</h2>
      <p>Buying a home is the biggest purchase most people ever make, and it's made half-blind. You make an offer, sign a contract, and only then does an appraiser tell your lender what the home is worth. If your price is more than that number, you're usually expected to bring the difference in cash. The seller often never sees the report.</p>
      <p>The information isn't secret because it has to be. It's scattered: federal agencies publish appraisal statistics, lenders report the values they relied on, the state records every sale, and every buyer already holds a copy of their own appraisal. Appraisal Commons brings those records together, and lets owners add the piece nobody else has: the full list → offer → appraisal → close story of a real sale.</p>
    </div>
  </section>

  <section class="wrap pad-sm team">
    <div class="section-head"><div><span class="eyebrow">Meet the team</span><h2>Four AI helpers, and a person who has the final say.</h2></div></div>
    <div class="agent-grid">
        <article class="agent-card concierge">
          <div class="agent-head"><span class="agent-av" aria-hidden="true">${AGENT_ICONS.concierge}</span><div><h3>The Records Concierge</h3><span class="agent-role">Checks your offer</span></div></div>
          <p>Lines up the price you're considering, and the asking price, against real appraisals, appraised values on mortgages, the home's sale history, and shared records. Cites every one.</p>
          <p class="agent-limit"><b>Guardrail:</b> Never predicts a value. Any number that isn't in a record is removed before you see it.</p>
          <a class="link" href="#/areas">Try it on any home page →</a>
        </article>
        <article class="agent-card guide">
          <div class="agent-head"><span class="agent-av" aria-hidden="true">${AGENT_ICONS.guide}</span><div><h3>The Records Guide</h3><span class="agent-role">Answers your questions</span></div></div>
          <p>The chat in the corner. Looks up towns and homes, explains what a record means, and points you to the right page or resource.</p>
          <p class="agent-limit"><b>Guardrail:</b> Only talks about Connecticut home-value records. Won't give advice that moves money.</p>
          <a class="link" href="#" data-open-chat>Open the Records Guide →</a>
        </article>
        <article class="agent-card extractor">
          <div class="agent-head"><span class="agent-av" aria-hidden="true">${AGENT_ICONS.extractor}</span><div><h3>The Extractor</h3><span class="agent-role">Reads shared documents</span></div></div>
          <p>Turns an appraisal, listing sheet, contract, or closing disclosure into clean numbers: prices, dates, beds, baths, comparable sales.</p>
          <p class="agent-limit"><b>Guardrail:</b> Only sees text after names and loan numbers are removed, and has no place to write them down.</p>
          <a class="link" href="#/share">Share your numbers →</a>
        </article>
        <article class="agent-card verifier">
          <div class="agent-head"><span class="agent-av" aria-hidden="true">${AGENT_ICONS.verifier}</span><div><h3>The Verifier</h3><span class="agent-role">Double-checks before anything goes live</span></div></div>
          <p>Matches the closing price to the state's sale record, checks the dates run in order, and re-adds the appraisal math.</p>
          <p class="agent-limit"><b>Guardrail:</b> Can't publish anything. Flagged records go to a person on the review desk.</p>
          <a class="link" href="#/share">See it on the Share page →</a>
        </article>
        <article class="agent-card human">
          <div class="agent-head"><span class="agent-av" aria-hidden="true">${AGENT_ICONS.human}</span><div><h3>The Review Desk</h3><span class="agent-role">A real person</span></div></div>
          <p>Looks at anything the Verifier flags and any "Report a problem" from visitors, then approves or rejects it with their name and a reason.</p>
          <p class="agent-limit"><b>Why it matters:</b> the AI can open a ticket but can never close one.</p>
        </article>
    </div>
  </section>

  <section class="wrap pad about-grid">
    <div class="prose">
      <h2>What we promise</h2>
      <ul class="promise">
        <li><b>Real records only.</b> No estimates, no "home value" guesses. If a record doesn't exist, we say so.</li>
        <li><b>Privacy first.</b> Names and loan numbers are removed before any AI reads a shared file, and are never stored.</li>
        <li><b>Owners decide.</b> Nothing is published without the owner's consent.</li>
        <li><b>Open about the AI.</b> Every answer shows its steps, sources, and cost.</li>
      </ul>
      <h2>Who built it</h2>
      <p>Appraisal Commons was built by someone who watched a home sale hinge on a number nobody could see until it was too late. The goal is simple: close the information gap between buyers, sellers, and the appraisal that decides what a home is worth to a lender. It's an early version. The shared records so far are labeled demo samples, and every real one makes the picture clearer for the next buyer.</p>
    </div>
  </section>`;
}

// ------------------------------------------------------------------ REVIEW DESK (staff, hw5 pattern)
const DESK_NAME_KEY = "ac_desk_name";

async function viewDesk() {
  let data;
  try {
    data = await api("/api/desk/tickets" + (state.deskFilter ? "?status=" + state.deskFilter : ""));
  } catch (err) {
    app.innerHTML = `
    <section class="page-hero"><div class="wrap"><span class="eyebrow">Staff only</span><h1>Review desk</h1>
      <p class="lede">Flagged submissions and visitor reports wait here until a person decides. The AI can open tickets but can never close them.</p></div></section>
    <section class="wrap pad"><form class="card desk-login" id="desk-login">
      <label>Staff passcode<input type="password" id="desk-pass" autocomplete="current-password" required /></label>
      <button class="btn primary">Sign in</button><span class="small error-text" id="desk-err"></span>
      <p class="muted small">For the class demo the passcode is in the README. Set <code>DESK_PASSCODE</code> in <code>.env</code> for real use.</p>
    </form></section>`;
    $("#desk-login").addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        await api("/api/desk/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ passcode: $("#desk-pass").value }) });
        viewDesk();
      } catch (er) { $("#desk-err").textContent = er.message; }
    });
    return;
  }
  const c = data.counts || {};
  const name = localStorage.getItem(DESK_NAME_KEY) || "";
  app.innerHTML = `
  <section class="page-hero"><div class="wrap desk-head">
    <div><span class="eyebrow">Staff only</span><h1>Review desk</h1>
      <p class="muted">${c.open || 0} open · ${c.approved || 0} approved · ${c.rejected || 0} rejected</p></div>
    <div class="desk-tools">
      <label>Signing as<input id="desk-name" value="${esc(name)}" placeholder="Your name" maxlength="60" /></label>
      <select id="desk-filter"><option value="">All tickets</option><option value="open">Open</option><option value="approved">Approved</option><option value="rejected">Rejected</option></select>
      <button class="btn" id="desk-out">Sign out</button>
    </div>
  </div></section>
  <section class="wrap pad desk-list">${data.tickets.map(ticketCard).join("") || '<div class="empty">No tickets yet.</div>'}</section>`;
  $("#desk-filter").value = state.deskFilter || "";
  $("#desk-filter").addEventListener("change", (e) => { state.deskFilter = e.target.value; viewDesk(); });
  $("#desk-name").addEventListener("input", (e) => localStorage.setItem(DESK_NAME_KEY, e.target.value));
  $("#desk-out").addEventListener("click", async () => { await api("/api/desk/logout", { method: "POST" }); viewDesk(); });
  $$(".decide").forEach((b) => b.addEventListener("click", async () => {
    const card = b.closest(".tkt"), msg = $(".t-msg", card);
    const staff = $("#desk-name").value.trim();
    if (!staff) { msg.textContent = "Enter your name in \"Signing as\" first."; $("#desk-name").focus(); return; }
    try {
      await api(`/api/desk/tickets/${card.dataset.id}/decide`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision: b.dataset.decision, name: staff, reason: $("textarea", card).value }) });
      state.areas = null;
      toast(`Ticket #${card.dataset.id} ${b.dataset.decision === "approve" ? "approved" : "rejected"}`, "ok");
      viewDesk();
    } catch (err) { msg.textContent = err.message; }
  }));
}

const CHECK_LABELS = { check_prices: "Prices", check_address: "Address", check_public_sale: "Matches state sale record", check_timeline: "Dates in order", check_license: "Appraiser license", check_comp_math: "Appraisal math" };

function ticketCard(t) {
  const kind = t.kind === "review" ? "Flagged submission" : "Problem report";
  const rec = t.kind === "review" ? t.draft : t.record;
  const checks = (t.draft?.verification?.checks || []).filter((c) => c.result !== "pass" && c.result !== "n/a");
  const approveLabel = t.kind === "review" ? "Approve & publish" : "Approve & take down";
  const rejectLabel = t.kind === "review" ? "Reject" : "Keep the record";
  return `<article class="card tkt ${t.status}" data-id="${t.id}">
    <div class="t-top"><span class="t-kind ${t.kind}">${kind}</span><b>#${t.id} · ${esc(title(t.address || ""))}${t.town ? ", " + esc(t.town) : ""}</b>
      <span class="t-status ${t.status}">${t.status}</span><span class="muted small">opened by ${esc(t.opened_by || "—")} · ${esc(t.created_at)}</span></div>
    <p>${esc(t.message || "")}</p>
    ${checks.length ? `<ul class="checks">${checks.map((c) => `<li><span class="pill ${c.result}">${c.result}</span><span><strong>${esc(CHECK_LABELS[c.check] || c.check)}</strong>: ${esc(c.reason)}</span></li>`).join("")}</ul>` : ""}
    ${rec ? ladder(rec) : t.kind === "correction" ? '<div class="gap">This record was already removed.</div>' : ""}
    ${t.status === "open" ? `<div class="t-actions"><textarea maxlength="600" placeholder="Reason (required to reject)"></textarea>
      <div><button class="btn primary decide" data-decision="approve">${approveLabel}</button> <button class="btn decide" data-decision="reject">${rejectLabel}</button> <span class="small error-text t-msg"></span></div></div>`
      : `<div class="t-decided">${t.status === "approved" ? "✓ Approved" : "✗ Rejected"} by <b>${esc(t.decided_by)}</b> · ${esc(t.decided_at)}${t.decision_reason ? ` · “${esc(t.decision_reason)}”` : ""}</div>`}
  </article>`;
}

// ------------------------------------------------------------------ GUIDE CHAT (hw4-style assistant)
const CHAT_KEY = "ac_chat_v1", NUDGE_KEY = "ac_nudged";
const chat = { history: JSON.parse(sessionStorage.getItem(CHAT_KEY) || "[]"), busy: false };

function pageContext(h) {
  let m;
  if ((m = h.match(/^#\/home\/([^/]+)\/(.+)$/))) return { kind: "home", town: decodeURIComponent(m[1]), address: decodeURIComponent(m[2]) };
  if ((m = h.match(/^#\/area\/(.+)$/))) return { kind: "area", town: decodeURIComponent(m[1]) };
  if (h.startsWith("#/share")) return { kind: "share" };
  return { kind: "site" };
}

function starterList() {
  const p = state.page || { kind: "site" };
  if (p.kind === "home") return [`What's the sale history of ${title(p.address)}?`, "How would a $450,000 offer compare here?", "Has anyone shared an appraisal for this home?"];
  if (p.kind === "area") return [`What were appraised values on recent mortgages in ${p.town}?`, `Any shared records in ${p.town}?`, "How often do appraisals come in below the contract price?"];
  if (p.kind === "share") return ["What happens to my personal details?", "What documents can I upload?", "Can I remove my record later?"];
  return ["How often do appraisals come in low around here?", "What's the difference between a sale price and an appraisal?", "I'm a first-time buyer. Where do I start?"];
}

function renderStarters() {
  const box = $("#chat-starters");
  if (!box) return;
  box.innerHTML = starterList().map((q) => `<button type="button" class="chip">${esc(q)}</button>`).join("");
}

function renderChat() {
  const log = $("#chat-log");
  const hello = `<div class="msg bot"><div class="bubble"><p>Hi! I'm the Records Guide. I can look up any town or home we cover, explain what a record means, or line up an offer price against the real appraisal records. I never guess values.</p></div></div>`;
  log.innerHTML = hello + chat.history.map((m) => m.role === "user"
    ? `<div class="msg me"><div class="bubble">${esc(m.content)}</div></div>`
    : `<div class="msg bot"><div class="bubble">${renderAnswer(m.content)}${m.meta ? `<div class="checked">${m.meta}</div>` : ""}</div></div>`).join("");
  log.scrollTop = log.scrollHeight;
  $("#chat-starters").hidden = chat.history.length > 0;
}

function openChat(open = true) {
  $("#chat-panel").hidden = !open;
  $("#chat-launcher").setAttribute("aria-expanded", String(open));
  $("#chat").classList.toggle("open", open);
  $("#chat-nudge").hidden = true;
  sessionStorage.setItem(NUDGE_KEY, "1");
  if (open) { renderChat(); setTimeout(() => $("#chat-input").focus(), 50); }
}

async function sendChat(text) {
  text = text.trim();
  if (!text || chat.busy) return;
  chat.busy = true;
  const prior = chat.history.map(({ role, content }) => ({ role, content }));
  chat.history.push({ role: "user", content: text });
  renderChat();
  $("#chat-log").insertAdjacentHTML("beforeend", `<div class="msg bot typing"><div class="bubble"><span class="dots"><i></i><i></i><i></i></span> Checking the records…</div></div>`);
  $("#chat-log").scrollTop = $("#chat-log").scrollHeight;
  $("#chat-input").value = "";
  try {
    const r = await api("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, history: prior.slice(-16), page: state.page || {} }) });
    chat.history.push({ role: "assistant", content: r.answer });
  } catch (err) {
    chat.history.push({ role: "assistant", content: err.message || "Sorry, something went wrong. Please try again." });
  } finally {
    chat.busy = false;
    sessionStorage.setItem(CHAT_KEY, JSON.stringify(chat.history.slice(-20)));
    renderChat();
    refreshSpend();
  }
}

$("#chat-launcher").addEventListener("click", () => openChat($("#chat-panel").hidden));
$("#chat-x").addEventListener("click", () => openChat(false));
$("#nudge-x").addEventListener("click", (e) => { e.stopPropagation(); $("#chat-nudge").hidden = true; sessionStorage.setItem(NUDGE_KEY, "1"); });
$("#chat-reset").addEventListener("click", () => { chat.history = []; sessionStorage.removeItem(CHAT_KEY); renderChat(); });
$("#chat-form").addEventListener("submit", (e) => { e.preventDefault(); sendChat($("#chat-input").value); });
$("#chat-starters").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) sendChat(b.textContent); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#chat-panel").hidden) openChat(false); });
if (!sessionStorage.getItem(NUDGE_KEY)) setTimeout(() => { if ($("#chat-panel").hidden) $("#chat-nudge").hidden = false; }, 6000);

// ------------------------------------------------------------------ helpers
async function refreshSpend() {
  try {
    const u = await api("/api/usage");
    if (!$("#spend-value")) return;  // spend meter removed from the footer; caps still enforced server-side
    $("#spend-value").textContent = "$" + u.session_spent_usd.toFixed(4);
    $("#spend-budget").textContent = u.session_budget_usd.toFixed(2);
  } catch {}
}

let toastTimer;
function toast(msg, kind = "") {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast " + kind;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 3800);
}

document.addEventListener("submit", async (e) => {
  const f = e.target.closest(".report-form");
  if (!f) return;
  e.preventDefault();
  const msg = $(".report-msg", f);
  try {
    const r = await api("/api/tickets/correction", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ record_id: Number(f.dataset.record), message: $("textarea", f).value }) });
    msg.innerHTML = `<span class="ok-text">✓ Thanks. A person will review it (ticket #${r.ticket}).</span>`;
    $("textarea", f).value = "";
  } catch (err) {
    msg.innerHTML = `<span class="error-text">${esc(err.message)}</span>`;
  }
});

document.addEventListener("click", (e) => {
  if (e.target.closest("[data-open-chat]")) { e.preventDefault(); openChat(true); return; }
  const q = e.target.closest(".chip.q");
  if (q) { $("#question").value = q.dataset.q; $("#offer").focus(); }
});

loadAreas().catch(() => {});
refreshSpend();
route();
