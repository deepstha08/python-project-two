"use strict";
/* ValueAtlas dashboard – plain JavaScript, no build step, works offline. */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const isNum = (v) => typeof v === "number" && isFinite(v);
const num = (v, d = 2) => (isNum(v) ? v.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }) : "—");
const pct = (v, d = 1, sign = true) => (isNum(v) ? (sign && v > 0 ? "+" : "") + (v * 100).toFixed(d) + "%" : "—");
const compact = (v) => (isNum(v) ? Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(v) : "—");
const money = (v, cur) => (isNum(v) ? num(v, v >= 1000 ? 0 : 2) + (cur ? ` <span class="sub">${esc(cur)}</span>` : "") : "—");
const median = (a) => { const s = a.filter(isNum).sort((x, y) => x - y); if (!s.length) return null; const m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
const tone = (v) => (!v ? "none" : /Bull/.test(v) ? "bull" : /Bear/.test(v) ? "bear" : v === "Neutral" ? "neutral" : "none");
const techTone = (v) => (!v ? "none" : /Buy/.test(v) ? "bull" : /Sell/.test(v) ? "bear" : "neutral");
const localTime = (iso) => (iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—");
const store = { get(k) { try { return localStorage.getItem(k); } catch { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch {} } };

async function api(path, opts = {}) {
  const init = { ...opts, headers: { ...(opts.headers || {}) } };
  if (init.body !== undefined) {  // only declare JSON when a body is actually sent
    if (typeof init.body !== "string") init.body = JSON.stringify(init.body);
    init.headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}
function toast(msg, ms = 3200) { const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden"); clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.add("hidden"), ms); }

/* ------------------------------------------------------------ theme & tabs */
function applyTheme(t) { document.documentElement.dataset.theme = t; window.dispatchEvent(new CustomEvent("va:theme", { detail: t })); store.set("va-theme", t); const b = document.getElementById("themeBtn"); if (b) b.textContent = t === "light" ? "☾" : "☀"; }
applyTheme(store.get("va-theme") || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"));
$("#themeBtn").onclick = () => applyTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light");

const SECTION_OF = { mystocks: "analyser", rank: "analyser", pain: "analyser", lookup: "analyser", quality: "analyser",
  trade: "trading", scan: "trading", settings: "other", help: "other" };
const SECTION_HOME = { analyser: "mystocks", trading: "trade" };
function showView(name) {
  const section = SECTION_OF[name] || "analyser";
  $$(".view").forEach((v) => v.classList.toggle("hidden", v.id !== `view-${name}`));
  $$("#tabs [data-section]").forEach((b) => b.classList.toggle("hidden", b.dataset.section !== section));
  $$("#sections button").forEach((b) => b.classList.toggle("active", b.dataset.section === section));
  $$("[data-view]").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  if (section !== "other") store.set(`va-last-${section}`, name);
  if (location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
  store.set("va-view", name);
  ({ pain: loadBoard, quality: loadQuality, settings: loadSettings }[name] || (() => {}))();
  window.dispatchEvent(new CustomEvent("va:view", { detail: name }));
}
$$("button[data-view]").forEach((b) => (b.onclick = () => showView(b.dataset.view)));
$$("#sections button").forEach((b) => (b.onclick = () => showView(store.get(`va-last-${b.dataset.section}`) || SECTION_HOME[b.dataset.section])));

/* ------------------------------------------------------------ status & refresh */
const REGIONS = ["US", "Europe", "Asia"];
const S = { region: store.get("va-region") || "US", data: null, sort: null, asc: false, lastFinished: null, settings: null };

async function pollStatus() {
  let st;
  try { st = await api("/api/status"); } catch { $("#status").textContent = "ValueAtlas server not reachable"; setTimeout(pollStatus, 5000); return; }
  const p = st.progress, run = st.run;
  const bits = [];
  bits.push(run ? `Screener updated <b>${esc(localTime(run.finished))}</b>` : "<b>Screener: no data yet</b>");
  if (st.next_refresh) bits.push(`Next ${esc(localTime(st.next_refresh))}`);
  $("#status").innerHTML = bits.join("<br>");
  $("#refreshBtn").disabled = st.busy;
  $("#refreshBtn").textContent = st.busy ? "Screener updating…" : "Update screener";
  const prog = $("#progress");
  if (st.busy) {
    prog.classList.remove("hidden");
    $("#progStage").textContent = p.stage || "Working";
    $("#progMsg").textContent = (p.log || []).slice(-1)[0] || "";
    $("#progBar").style.width = p.total ? `${Math.round((100 * p.done) / p.total)}%` : "4%";
  } else {
    prog.classList.add("hidden");
    if (run && st.error && st.error !== S.lastError) toast(st.error, 7000);
  }
  S.lastError = st.error;
  if (run && run.finished !== S.lastFinished) { S.lastFinished = run.finished; S.run = run; loadRankings(); }
  if (!run) renderEmptyFirstRun(st.busy, st.error);
  $("#dataDir").textContent = st.data_dir;
  setTimeout(pollStatus, st.busy ? 2000 : 20000);
}
$("#refreshBtn").onclick = async () => { await api("/api/refresh", { method: "POST" }); toast("Refresh started – this takes a few minutes."); setTimeout(pollStatus, 500); };
$("#cancelBtn").onclick = async () => { await api("/api/cancel", { method: "POST" }); toast("Cancelling…"); };

/* ------------------------------------------------------------ rankings */
$$("#regionSeg button").forEach((b) => (b.onclick = () => { S.region = b.dataset.region; store.set("va-region", S.region); $("#search").value = ""; renderSearchPanel(null); renderRankings(); }));
$("#verdictFilter").addEventListener("input", renderRankings);
$("#search").addEventListener("input", () => { renderRankings(); searchOutside(); });
$("#search").addEventListener("keydown", (e) => { if (e.key === "Escape") { $("#search").value = ""; renderRankings(); renderSearchPanel(null); } });
["#sectorToggle", "#flagToggle", "#scalableToggle"].forEach((id) => $(id).addEventListener("change", () => { loadRankings(); if ($("#search").value.trim()) searchOutside(); }));
$$("#rankTable th[data-sort]").forEach((th) => (th.onclick = () => {
  const k = th.dataset.sort;
  if (S.sort === k) S.asc = !S.asc; else { S.sort = k; S.asc = ["pe", "forward_pe", "peg"].includes(k); }
  renderRankings();
}));

async function loadRankings() {
  if (!S.settings) { try { S.settings = (await api("/api/settings")).settings; $("#sectorToggle").checked = S.settings.sector_relative; $("#flagToggle").checked = S.settings.exclude_flagged; $("#scalableToggle").checked = S.settings.scalable_only !== false; } catch {} }
  const q = new URLSearchParams({ sector: $("#sectorToggle").checked, exclude_flagged: $("#flagToggle").checked, scalable: $("#scalableToggle").checked });
  $("#exportBtn").href = `/api/export.csv?${q}`;
  try { S.data = await api(`/api/rankings?${q}`); } catch (e) { toast(e.message); return; }
  renderRankings();
}

function renderEmptyFirstRun(busy, error) {
  $("#rankTable tbody").innerHTML = "";
  $("#kpis").innerHTML = "";
  const e = $("#rankEmpty");
  e.classList.remove("hidden");
  e.innerHTML = busy
    ? "<b>Collecting today's data…</b>The first scan checks several hundred stocks in three regions and usually takes 5–15 minutes. Results appear here automatically."
    : error ? `<b>The first scan could not finish</b>${esc(error)}<br><br>Click <i>Update screener</i> to try again.`
    : "<b>No data yet</b>Click <i>Update screener</i> to run the first scan.";
}

function spark(values, w = 110, h = 30) {
  if (!values || values.length < 2) return '<span class="sub">—</span>';
  const min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
  const pts = values.map((v, i) => `${((i / (values.length - 1)) * w).toFixed(1)},${(h - 2 - ((v - min) / span) * (h - 4)).toFixed(1)}`).join(" ");
  const up = values[values.length - 1] >= values[0];
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><polyline points="${pts}" fill="none" stroke="var(${up ? "--bull" : "--bear"})" stroke-width="1.6" stroke-linejoin="round"/></svg>`;
}

function renderRankings() {
  $$("#regionSeg button").forEach((b) => {
    b.classList.toggle("active", b.dataset.region === S.region);
    const n = S.data?.regions?.[b.dataset.region]?.length;
    b.querySelector("em").textContent = isNum(n) ? `· ${n}` : "";
  });
  $$("#rankTable th[data-sort]").forEach((th) => { th.classList.toggle("sorted", th.dataset.sort === S.sort); th.classList.toggle("asc", th.dataset.sort === S.sort && S.asc); });
  if (!S.data) return;
  const q = $("#search").value.trim().toLowerCase();
  const match = (r) => [r.symbol, r.name, r.sector, r.industry, r.country].join(" ").toLowerCase().includes(q);
  // while searching, look through all three regions' top 25 at once
  let rows = q ? REGIONS.flatMap((reg) => (S.data.regions[reg] || []).filter(match)) : [...(S.data.regions[S.region] || [])];
  const all = rows;
  if (q) $$("#regionSeg button").forEach((b) => {
    b.classList.remove("active");
    b.querySelector("em").textContent = `· ${(S.data.regions[b.dataset.region] || []).filter(match).length} found`;
  });
  $("#kpis").classList.toggle("hidden", !!q);
  const vf = $("#verdictFilter").value;
  if (vf) rows = rows.filter((r) => (r.verdict || "").includes(vf));
  if (S.sort) rows.sort((a, b) => { const x = a[S.sort], y = b[S.sort]; if (!isNum(x)) return 1; if (!isNum(y)) return -1; return S.asc ? x - y : y - x; });

  // KPIs
  const bull = all.filter((r) => /Bull/.test(r.verdict || "")).length, bear = all.filter((r) => /Bear/.test(r.verdict || "")).length;
  const loaded = S.data.loaded?.[S.region];
  $("#kpis").innerHTML = [
    ["Median P/E", num(median(all.map((r) => r.pe)), 1), "trailing 12 months"],
    ["Median forward P/E", num(median(all.map((r) => r.forward_pe)), 1), "next 12 months"],
    ["Median PEG", num(median(all.map((r) => r.peg)), 2), "below 1 is often seen as cheap"],
    ["Outlook", `<span class="pos">${bull}</span> / <span class="neg">${bear}</span>`, "bullish / bearish of the top 25"],
    ["Stocks checked", isNum(loaded) ? loaded : "—", `in ${S.region} · data as of ${esc(S.data.asof || "—")}`],
    ["Scalable Capital", (() => { const y = all.filter((r) => r.scalable === "yes").length; return all.length ? `${y} / ${all.length}` : "—"; })(),
      all.some((r) => r.scalable === "unchecked") ? "not checked yet – runs at the next screener update" : "tradable via gettex"],
  ].map(([l, v, s]) => `<div class="kpi"><div class="label">${l}</div><div class="value">${v}</div><div class="sub">${s}</div></div>`).join("");

  const tbody = $("#rankTable tbody");
  const empty = $("#rankEmpty");
  if (!all.length && !q) {
    tbody.innerHTML = "";
    empty.classList.remove("hidden");
    empty.innerHTML = S.data.finished ? "<b>No stocks qualified</b>None had a positive P/E, forward P/E and PEG within your limits. See <i>Data quality</i>." : "";
    if (!S.data.finished) renderEmptyFirstRun(false);
    return;
  }
  empty.classList.toggle("hidden", rows.length > 0);
  if (!rows.length) empty.innerHTML = q ? `<b>"${esc($("#search").value.trim())}" is not in today's top 25 lists</b>See the results above for its valuation and links to other websites.` : "<b>No matches</b>Try a different filter.";
  tbody.innerHTML = rows.map((r) => {
    let move = "";
    if (r.is_new) move = '<span class="move new">NEW</span>';
    else if (isNum(r.prev_rank) && r.prev_rank !== r.rank) move = r.prev_rank > r.rank ? `<span class="move up">▲${r.prev_rank - r.rank}</span>` : `<span class="move down">▼${r.rank - r.prev_rank}</span>`;
    const flags = (r.flags || []).length ? `<span class="flag" title="${esc(r.flags.join(" · "))}">⚠ ${r.flags.length}</span>` : "";
    const verdict = r.analysed ? `<span class="chip ${tone(r.verdict)}"><span class="dot"></span>${esc(r.verdict || "No data")}</span>${r.agree ? `<div class="sub">${r.agree.bullish}↑ ${r.agree.bearish}↓ of ${r.agree.total} signals</div>` : ""}` : '<span class="chip none">Not analysed</span>';
    const tech = r.tv_label ? `<span class="chip ${techTone(r.tv_label)}">${esc(r.tv_label)}</span>` : '<span class="sub">—</span>';
    const target = isNum(r.upside) ? `<span class="${r.upside >= 0 ? "pos" : "neg"}">${pct(r.upside)}</span><div class="sub">${esc((r.recommendation_key || "").replace("_", " "))}${r.analyst_count ? ` · ${r.analyst_count} analysts` : ""}</div>` : '<span class="sub">—</span>';
    const mp = isNum(r.max_pain) ? `${num(r.max_pain)} <span class="sub">(${pct(r.max_pain_dist)})</span><div class="sub">${esc(r.max_pain_exp)}</div>` : `<span class="sub">${r.analysed ? "No options" : "—"}</span>`;
    return `<tr class="clickable" data-symbol="${esc(r.symbol)}">
      <td class="num"><div class="rank-cell">${r.rank}${move}</div></td>
      <td><div class="stock-cell"><b>${esc(r.symbol)}${flags}${q ? `<span class="region-tag">${esc(r.region)} #${r.rank}</span>` : ""}</b><span title="${esc(r.name)}">${esc(r.name)}</span><span>${esc(r.sector)} ${scalableTag(r.scalable)}</span></div></td>
      <td class="num">${money(r.price, r.currency)}</td>
      <td class="num">${num(r.pe, 1)}</td><td class="num">${num(r.forward_pe, 1)}</td><td class="num">${num(r.peg, 2)}${r.peg_field === "estimated" ? '<div class="sub" title="Estimated: trailing P/E ÷ forecast EPS growth">est.</div>' : ""}</td>
      <td><div class="scorebar"><div class="track"><div class="fill" style="width:${Math.max(2, r.score)}%"></div></div><b>${Math.round(r.score)}</b></div><div class="sub">vs ${esc(r.peer_basis)} (${r.peer_count})</div></td>
      <td>${verdict}</td><td>${tech}</td><td class="num">${target}</td><td class="num">${mp}</td><td>${spark(r.spark)}</td></tr>`;
  }).join("");
  $$("tr[data-symbol]", tbody).forEach((tr) => (tr.onclick = () => openStock(tr.dataset.symbol)));
  $("#rankFoot").innerHTML = `Value score = weighted cheapness percentile of P/E, forward P/E and PEG (100 = cheapest of its peers). Arrows compare with the ranking on ${esc(S.data.previous_day || "the previous day")}. ⚠ marks possible value traps. Click a row for the full analysis.`;
}


/* ------------------------------------------------------------ search beyond the top 25 */
function linksMenu(links, label = "Other websites ▾") {
  if (!links || !links.length) return "";
  const groups = {};
  links.forEach((l) => (groups[l.group || "Links"] = groups[l.group || "Links"] || []).push(l));
  return `<details class="links-menu"><summary class="btn small ghost">${label}</summary><div class="link-groups">${Object.entries(groups).map(([g, ls]) =>
    `<div><div class="lg-name">${esc(g)}</div><div class="lg-items">${ls.map((l) => `<a class="btn small ghost" target="_blank" rel="noopener" href="${esc(l.url)}">${esc(l.name)} ↗</a>`).join("")}</div></div>`).join("")}</div></details>`;
}
function scalableTag(st, big) {
  const map = { yes: ["sc-yes", "✓ Scalable"], no: ["sc-no", "Not on Scalable"], unknown: ["sc-unk", "Scalable ?"] };
  const m = map[st];
  if (!m) return "";
  const title = { yes: "Quoted on gettex – tradable on Scalable Capital", no: "No gettex quote for this ISIN – not tradable on Scalable Capital", unknown: "ISIN not found – availability on Scalable Capital unknown" }[st];
  return `<span class="sc-tag ${m[0]} ${big ? "big" : ""}" title="${title}">${m[1]}</span>`;
}
const ratioLine = (r) => `<div class="ratios"><span>P/E <b>${num(r.pe, 1)}</b></span><span>Fwd P/E <b>${num(r.forward_pe, 1)}</b></span><span>PEG <b>${num(r.peg, 2)}</b></span></div>`;
function standingText(st) {
  if (!st) return "";
  if (st.status === "top25") return `In the top 25: #${st.rank} of ${st.of} in ${esc(st.region)}`;
  if (st.status === "would_top25") return `Would rank #${st.rank} of ${st.of} in ${esc(st.region)} – cheap enough for the top 25, but it was not in today's market scan (add it under Settings → Always include)`;
  if (st.status === "would_rank") return `Would rank #${st.rank} of ${st.of} in ${esc(st.region)} – not cheap enough for the top 25`;
  if (st.status === "ranked") return `Not in the top 25 – ranks #${st.rank} of ${st.of} in ${esc(st.region)} (value score ${Math.round(st.score)})`;
  return esc(st.reason || "Not ranked");
}
let searchTimer = null, searchSeq = 0;
function searchOutside() {
  clearTimeout(searchTimer);
  const q = $("#search").value.trim();
  if (!q) { renderSearchPanel(null); return; }
  $("#searchHint").textContent = "Searching…";
  searchTimer = setTimeout(async () => {
    const seq = ++searchSeq;
    const qs = new URLSearchParams({ q, sector: $("#sectorToggle").checked, exclude_flagged: $("#flagToggle").checked, scalable: $("#scalableToggle").checked });
    try {
      const d = await api(`/api/search?${qs}`);
      if (seq === searchSeq) renderSearchPanel(d, q);
    } catch (e) {
      if (seq === searchSeq) $("#searchPanel").innerHTML = `<p class="neg">Search failed: ${esc(e.message)}</p>`;
    }
    if (seq === searchSeq) $("#searchHint").textContent = "Searches all three regions";
  }, 300);
}
function renderSearchPanel(d, q) {
  const box = $("#searchPanel");
  if (!d) { box.innerHTML = ""; $("#searchHint").textContent = "Searches all three regions"; return; }
  let html = "";
  if (d.top.length) html += `<div class="sp-head"><h3>${d.top.length} match${d.top.length > 1 ? "es" : ""} in today's top 25 lists</h3><span class="sub">shown in the table below – click a row for details</span></div>`;
  if (d.scanned.length) {
    html += `<div class="sp-head"><h3>Scanned today, but not in the top 25</h3><span class="sub">ranked on P/E, forward P/E and PEG against stocks in the same region</span></div><div class="sp-list">`;
    html += d.scanned.map((r) => `<div class="sp-item"><div class="top"><b>${esc(r.symbol)} <span class="region-tag">${esc(r.region)}</span> ${scalableTag(r.scalable)}</b><span class="sub">${esc(r.sector || "")}</span></div>
      <div class="sub">${esc(r.name || "")}</div>${ratioLine(r)}<div class="why">${standingText(r)}</div>
      <div class="acts"><button class="btn small" data-open="${esc(r.symbol)}">Details</button><button class="btn small ghost" data-trade="${esc(r.symbol)}">Trade desk</button><button class="btn small ghost" data-links="${esc(r.symbol)}">Other websites ▾</button></div></div>`).join("");
    html += "</div>";
  }
  const others = d.others.slice();
  if (d.direct) others.unshift({ symbol: d.direct.symbol, name: "Ticker as typed", exchange: "", links: d.direct.links });
  if (others.length) {
    html += `<div class="sp-head"><h3>Other stocks</h3><span class="sub">not in today's scan – check their valuation here or open them on other websites</span></div><div class="sp-list">`;
    html += others.map((r) => `<div class="sp-item"><div class="top"><b>${esc(r.symbol)}</b><span class="sub">${esc(r.exchange || "")} ${r.type ? "· " + esc(r.type) : ""}</span></div>
      <div class="sub">${esc(r.name || "")}</div>
      <div class="acts"><button class="btn small primary" data-check="${esc(r.symbol)}">Check P/E, forward P/E &amp; PEG</button><button class="btn small ghost" data-trade="${esc(r.symbol)}">Trade desk</button>${linksMenu(r.links)}</div></div>`).join("");
    html += "</div>";
  }
  if (!html) html = `<p class="muted">Nothing found for "${esc(q)}". Try a ticker in Yahoo format, e.g. AAPL, SAP.DE, 7203.T, 0700.HK.</p>`;
  box.innerHTML = html;
  $$("[data-open]", box).forEach((b) => (b.onclick = () => openStock(b.dataset.open)));
  $$("[data-trade]", box).forEach((b) => (b.onclick = () => { if (window.openTradeDesk) window.openTradeDesk(b.dataset.trade); }));
  $$("[data-links]", box).forEach((b) => (b.onclick = async () => {
    try { const s = await api(`/api/stock/${encodeURIComponent(b.dataset.links)}`); b.outerHTML = linksMenu(s.links, "Other websites ▴").replace("<details", "<details open"); }
    catch (e) { toast(e.message); }
  }));
  $$("[data-check]", box).forEach((b) => (b.onclick = async () => {
    const sym = b.dataset.check;
    b.disabled = true; b.textContent = "Checking… (10–30 s)";
    try { await api(`/api/analyze/${encodeURIComponent(sym)}`, { method: "POST" }); openStock(sym); b.textContent = "Show valuation"; b.disabled = false; b.onclick = () => openStock(sym); }
    catch (e) { b.textContent = "Check P/E, forward P/E & PEG"; b.disabled = false; toast(e.message, 6000); }
  }));
}

/* ------------------------------------------------------------ stock drawer */
function closeDrawer() { $("#drawer").classList.add("hidden"); $("#scrim").classList.add("hidden"); }
$("#closeDrawer").onclick = closeDrawer; $("#scrim").onclick = closeDrawer;
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

function metric(label, value, sub = "", pctScore = null) {
  return `<div class="metric"><div class="label">${label}</div><div class="value">${value}</div>${sub ? `<div class="sub">${sub}</div>` : ""}${isNum(pctScore) ? `<div class="pct"><i style="width:${pctScore}%"></i></div>` : ""}</div>`;
}

async function openStock(symbol) {
  $("#drawer").classList.remove("hidden"); $("#scrim").classList.remove("hidden");
  $("#dSymbol").textContent = symbol; $("#dName").textContent = "Loading…"; $("#drawerBody").innerHTML = "";
  let d;
  try { d = await api(`/api/stock/${encodeURIComponent(symbol)}`); } catch (e) { $("#dName").textContent = e.message; return; }
  const r = d.row, a = d.analysis || {}, t = a.technical || {}, s = a.sentiment || {}, o = a.options || {};
  $("#dName").textContent = `${r.name || ""} · ${[r.region, r.country, r.sector, r.industry].filter(Boolean).join(" · ")}`;
  const ms = (d.my && d.my.metric_scores) || r.metric_scores || {};
  const verdictCls = tone(s.verdict);
  const comps = (s.components || []).map((c) => {
    const w = Math.abs(c.value) * 50, left = c.value >= 0 ? 50 : 50 - w;
    return `<div class="comp"><div class="name"><b>${esc(c.name)}</b><span>${esc(c.source)}</span></div>
      <div><div class="diverge"><i style="left:${left}%;width:${w}%;background:var(${c.value >= 0 ? "--bull" : "--bear"})"></i></div><div class="detail">${esc(c.detail)}</div></div>
      <div class="num"><span class="chip ${tone(c.tone)}">${c.value > 0 ? "+" : ""}${c.value.toFixed(2)}</span></div></div>`;
  }).join("");
  const votes = (obj) => Object.entries(obj || {}).map(([k, v]) => `<span class="vote ${v > 0 ? "b" : v < 0 ? "s" : ""}">${esc(k)} ${v > 0 ? "Buy" : v < 0 ? "Sell" : "Neutral"}</span>`).join("");
  const expOptions = (o.all_expirations || []).map((e) => `<option>${esc(e)}</option>`).join("");
  const expRows = (o.expirations || []).map((e) => `<tr class="clickable" data-exp="${esc(e.expiration)}"><td>${esc(e.expiration)}</td><td class="num"><b>${num(e.max_pain)}</b></td><td class="num ${e.distance >= 0 ? "pos" : "neg"}">${pct(e.distance)}</td><td class="num">${num(e.put_call_ratio)}</td><td class="num">${compact(e.call_oi + e.put_oi)}</td></tr>`).join("");
  const hist = (d.history || []).slice(0, 30);

  $("#drawerBody").innerHTML = `
    <div class="link-groups" style="margin:0 0 14px">${Object.entries(d.links.reduce((g, l) => ((g[l.group || "Links"] = g[l.group || "Links"] || []).push(l), g), {})).map(([g, ls]) =>
      `<div><div class="lg-name">${esc(g)}</div><div class="lg-items">${ls.map((l) => `<a class="btn small ghost" target="_blank" rel="noopener" href="${esc(l.url)}">${esc(l.name)} ↗</a>`).join("")}</div></div>`).join("")}
      <div><button class="btn small" id="reanalyse">↻ Re-analyse now</button></div></div>

    <div class="card"><h2>Outlook</h2>
      ${s.components ? `<div class="verdict-hero"><div><div class="verdict-big ${verdictCls}">${esc(s.verdict)}</div><div class="sub">Score ${num(s.score)} · ${s.agree.bullish} bullish, ${s.agree.bearish} bearish of ${s.agree.total} signals</div></div>
      <div class="gauge"><i style="left:${(((s.score || 0) + 1) / 2) * 100}%"></i></div></div>${comps}` : `<p class="muted">Not analysed yet. Click <i>Re-analyse now</i>.</p>`}
    </div>

    <div class="card"><h2>Valuation</h2>
      ${d.my ? `<div class="standing"><b>My stocks rating:</b> <span class="vbadge ${({ Undervalued: "under", Overvalued: "over", Neutral: "neutral" })[d.my.verdict] || "none"}">${esc(d.my.verdict)}</span>
        ${isNum(d.my.value_score) ? ` score ${Math.round(d.my.value_score)}/100` : ""}${(d.my.reasons || []).map((x) => `<div class="sub">${esc(x)}</div>`).join("")}</div>` : ""}
      ${d.standing && !(d.my && d.standing.status === "excluded") ? `<div class="standing ${/top25/.test(d.standing.status) ? "top25" : ""}">${standingText(d.standing)}</div>` : ""}
      <div class="standing">${r.scalable && r.scalable !== "unchecked" ? scalableTag(r.scalable, true) : '<span class="sub">Scalable Capital availability not checked yet</span>'}
        ${r.isin ? ` <span class="sub">ISIN <b style="user-select:all">${esc(r.isin)}</b> – search this in the Scalable app</span>` : ""}</div>
      <div class="metrics">
        ${metric("Price", money(r.price, r.currency), r.market_cap ? `Market cap ${compact(r.market_cap)}` : "")}
        ${metric("P/E (trailing)", num(r.pe, 1), isNum(ms.pe) ? `cheaper than ${Math.round(ms.pe)}% of peers` : "", ms.pe)}
        ${metric("Forward P/E", num(r.forward_pe, 1), isNum(ms.forward_pe) ? `cheaper than ${Math.round(ms.forward_pe)}% of peers` : "", ms.forward_pe)}
        ${metric("PEG", num(r.peg, 2), isNum(ms.peg) ? `cheaper than ${Math.round(ms.peg)}% of peers` : esc(r.peg_field || ""), ms.peg)}
        ${d.my && isNum(d.my.value_score) ? metric("Value score", Math.round(d.my.value_score), `My stocks rating · ${esc(d.my.peer_basis || "")}`) : metric("Value score", isNum(r.score) ? Math.round(r.score) : "—", r.peer_basis ? `vs ${esc(r.peer_basis)} (${r.peer_count})` : "not ranked")}
        ${metric("Price / book", num(r.price_to_book, 2))}
        ${metric("EV / EBITDA", num(r.ev_ebitda, 1))}
        ${metric("Dividend yield", pct(r.dividend_yield, 2, false))}
        ${metric("Earnings growth", pct(r.earnings_growth))}
        ${metric("Revenue growth", pct(r.revenue_growth))}
        ${metric("Profit margin", pct(r.profit_margin, 1, false))}
        ${metric("Return on equity", pct(r.roe, 1, false))}
        ${metric("Debt / equity", isNum(r.debt_to_equity) ? num(r.debt_to_equity / 100, 2) : "—")}
        ${metric("Analyst target", money(r.target_mean, r.currency), isNum(r.target_low) ? `range ${num(r.target_low)} – ${num(r.target_high)}` : "")}
      </div>
      ${(r.flags || []).length ? `<p><b>Possible value-trap warnings:</b> ${r.flags.map((f) => `<span class="flag">⚠ ${esc(f)}</span>`).join(" ")}</p>` : ""}
      ${r.currency_note ? `<p class="muted">⚠ ${esc(r.currency_note)}</p>` : ""}
    </div>

    <div class="card"><h2>Technicals <span class="sub">TradingView rating method · data to ${esc(t.price_date || "—")}</span></h2>
      ${t.available ? `<div class="metrics">
        ${metric("Overall rating", `<span class="chip ${techTone(t.rating_label)}">${esc(t.rating_label)}</span>`, `score ${num(t.rating)}`)}
        ${metric("Moving averages", `<span class="chip ${techTone(t.ma_label)}">${esc(t.ma_label)}</span>`, `score ${num(t.ma_rating)}`)}
        ${metric("Oscillators", `<span class="chip ${techTone(t.osc_label)}">${esc(t.osc_label)}</span>`, `score ${num(t.osc_rating)}`)}
        ${metric("RSI (14)", num(t.rsi14, 1), t.rsi14 > 70 ? "overbought" : t.rsi14 < 30 ? "oversold" : "")}
        ${metric("1 / 3 / 12 months", `${pct(t.return_1m, 0)} <span class="sub">/</span> ${pct(t.return_3m, 0)} <span class="sub">/</span> ${pct(t.return_12m, 0)}`)}
        ${metric("52-week range", `${num(t.low_52w)} – ${num(t.high_52w)}`, `${pct(t.from_high_52w)} from high`)}
      </div>
      <div style="margin-top:12px">${spark(t.spark, 780, 90).replace('class="spark"', 'class="spark chart"')}</div>
      <div class="votes">${votes(t.ma_votes)}</div><div class="votes">${votes(t.osc_votes)}</div>` : `<p class="muted">${esc(t.reason || "Not available.")}</p>`}
    </div>

    <div class="card"><div class="card-head"><h2>Max pain by expiration date</h2>
      ${o.all_expirations?.length ? `<div class="filters"><select id="dExp">${expOptions}</select><button class="btn small primary" id="dExpBtn">Calculate</button></div>` : ""}</div>
      ${o.available ? `<div class="table-wrap" style="margin:10px 0"><table class="grid"><thead><tr><th>Expiration</th><th class="num">Max pain</th><th class="num">vs price</th><th class="num">Put/Call OI</th><th class="num">Open interest</th></tr></thead><tbody id="dExpRows">${expRows}</tbody></table></div>`
        : `<p class="muted">${esc(o.reason || "No option data.")}</p>`}
      <div id="dPain"></div>
      <p class="sub">Calculated with OptionCharts' published method from Yahoo open interest. Compare on <a target="_blank" rel="noopener" href="https://optioncharts.io/options/${esc(r.symbol.split(".")[0])}/max-pain">optioncharts.io ↗</a>.</p>
    </div>

    ${hist.length ? `<div class="card"><h2>Ranking history</h2><div class="table-wrap"><table class="grid"><thead><tr><th>Day</th><th class="num">Rank</th><th class="num">Score</th><th class="num">P/E</th><th class="num">Fwd P/E</th><th class="num">PEG</th><th class="num">Price</th><th>Outlook</th></tr></thead><tbody>
      ${hist.map((h) => `<tr><td>${esc(h.day)}</td><td class="num">${h.rank}</td><td class="num">${num(h.score, 0)}</td><td class="num">${num(h.pe, 1)}</td><td class="num">${num(h.forward_pe, 1)}</td><td class="num">${num(h.peg)}</td><td class="num">${num(h.price)}</td><td><span class="chip ${tone(h.verdict)}">${esc(h.verdict || "—")}</span></td></tr>`).join("")}
      </tbody></table></div></div>` : ""}

    <div class="card"><h2>My notes from other sites</h2>
      <p class="muted">Write down what Investing.com, TradingView or other sites say, with the date you checked.</p>
      <div id="notesList"></div>
      <form class="note-form" id="noteForm">
        <input name="source" placeholder="Website (e.g. Investing.com)" required>
        <select name="signal"><option value="">Signal…</option><option>Strong Buy</option><option>Buy</option><option>Neutral</option><option>Sell</option><option>Strong Sell</option></select>
        <input name="value" placeholder="Value (e.g. max pain 185)">
        <input name="asof" type="date" value="${new Date().toISOString().slice(0, 10)}">
        <input name="note" placeholder="Comment (optional)">
        <button class="btn primary" type="submit">Save note</button>
      </form>
    </div>
    ${r.summary ? `<div class="card"><h2>About</h2><p class="muted">${esc(r.summary)}${r.summary.length >= 700 ? "…" : ""}</p></div>` : ""}`;

  renderNotes(symbol, d.notes);
  $("#reanalyse").onclick = async (ev) => { ev.target.disabled = true; ev.target.textContent = "Analysing…"; try { await api(`/api/analyze/${encodeURIComponent(symbol)}`, { method: "POST" }); openStock(symbol); loadRankings(); } catch (e) { toast(e.message); ev.target.disabled = false; } };
  $("#noteForm").onsubmit = async (ev) => {
    ev.preventDefault();
    const body = Object.fromEntries(new FormData(ev.target));
    try { renderNotes(symbol, (await api(`/api/notes/${encodeURIComponent(symbol)}`, { method: "POST", body })).notes); ev.target.reset(); } catch (e) { toast(e.message); }
  };
  const loadPain = async (exp, live) => {
    $("#dPain").innerHTML = '<p class="muted">Calculating…</p>';
    try { const res = await api(`/api/options/${encodeURIComponent(symbol)}?expiration=${exp}${live ? "&live=1" : ""}`); $("#dPain").innerHTML = painView(res.result, res.history); }
    catch (e) { $("#dPain").innerHTML = `<p class="neg">${esc(e.message)}</p>`; }
  };
  if ($("#dExpBtn")) $("#dExpBtn").onclick = () => loadPain($("#dExp").value, true);
  $$("#dExpRows tr").forEach((tr) => (tr.onclick = () => { $("#dExp").value = tr.dataset.exp; loadPain(tr.dataset.exp, false); }));
  if (o.nearest) loadPain(o.nearest.expiration, false);
}

function renderNotes(symbol, notes) {
  $("#notesList").innerHTML = notes.length ? `<div class="table-wrap"><table class="grid"><thead><tr><th>Date</th><th>Source</th><th>Signal</th><th>Value</th><th>Comment</th><th></th></tr></thead><tbody>
    ${notes.map((n) => `<tr><td>${esc(n.asof)}</td><td>${esc(n.source)}</td><td>${n.signal ? `<span class="chip ${techTone(n.signal)}">${esc(n.signal)}</span>` : ""}</td><td>${esc(n.value)}</td><td style="white-space:normal">${esc(n.note)}</td><td><button class="btn small ghost" data-del="${n.id}">Delete</button></td></tr>`).join("")}</tbody></table></div>` : "";
  $$("[data-del]", $("#notesList")).forEach((b) => (b.onclick = async () => renderNotes(symbol, (await api(`/api/notes/${encodeURIComponent(symbol)}/${b.dataset.del}`, { method: "DELETE" })).notes)));
}

/* ------------------------------------------------------------ max pain charts */
function painChart(result) {
  const curve = result.curve || [];
  if (curve.length < 2) return "";
  const W = 780, H = 220, P = { l: 52, r: 10, t: 12, b: 28 };
  const max = Math.max(...curve.map((c) => c.total)) || 1;
  const bw = (W - P.l - P.r) / curve.length;
  const strikes = curve.map((c) => c.strike), lo = strikes[0], hi = strikes[strikes.length - 1];
  const x = (k) => P.l + ((k - lo) / (hi - lo || 1)) * (W - P.l - P.r - bw) + bw / 2;
  const y = (v) => H - P.b - (v / max) * (H - P.t - P.b);
  let svg = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Total option holder payout by settlement price">`;
  for (let i = 0; i <= 4; i++) { const v = (max * i) / 4; svg += `<line x1="${P.l}" x2="${W - P.r}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)"/><text x="${P.l - 6}" y="${y(v) + 3}" text-anchor="end">${compact(v)}</text>`; }
  curve.forEach((c) => {
    const cx = x(c.strike) - bw * 0.4, w = Math.max(1, bw * 0.8);
    svg += `<rect x="${cx}" y="${y(c.calls)}" width="${w}" height="${H - P.b - y(c.calls)}" fill="var(--bull)" opacity=".75"><title>${c.strike}: calls ${compact(c.calls)}, puts ${compact(c.puts)}</title></rect>`;
    svg += `<rect x="${cx}" y="${y(c.calls + c.puts)}" width="${w}" height="${y(c.calls) - y(c.calls + c.puts)}" fill="var(--bear)" opacity=".75"><title>${c.strike}: calls ${compact(c.calls)}, puts ${compact(c.puts)}</title></rect>`;
  });
  const step = Math.ceil(curve.length / 10);
  curve.forEach((c, i) => { if (i % step === 0) svg += `<text x="${x(c.strike)}" y="${H - 10}" text-anchor="middle">${c.strike}</text>`; });
  const mpx = x(result.max_pain);
  svg += `<line x1="${mpx}" x2="${mpx}" y1="${P.t}" y2="${H - P.b}" stroke="var(--accent)" stroke-width="2"/><text x="${mpx + 4}" y="${P.t + 10}" style="fill:var(--accent);font-weight:700">Max pain ${result.max_pain}</text>`;
  if (isNum(result.price) && result.price >= lo && result.price <= hi) {
    const px = x(result.price);
    svg += `<line x1="${px}" x2="${px}" y1="${P.t}" y2="${H - P.b}" stroke="var(--ink)" stroke-dasharray="4 3"/><text x="${px + 4}" y="${P.t + 24}" style="fill:var(--ink)">Price ${num(result.price)}</text>`;
  }
  return svg + "</svg>";
}

function painView(r, history) {
  const hist = (history || []).filter((h) => h.expiration === r.expiration);
  return `<div class="metrics" style="margin-top:6px">
      ${metric(`Max pain · ${esc(r.expiration)}`, `<span style="color:var(--accent)">${num(r.max_pain)}</span>`, r.strikes && r.strikes.length > 1 ? `tied strikes: ${r.strikes.join(", ")}` : "")}
      ${metric("Current price", num(r.price), isNum(r.price) ? `max pain is ${pct(r.max_pain / r.price - 1)} away` : "")}
      ${metric("Put / call OI ratio", num(r.put_call_ratio ?? r.pcr), `${compact(r.put_oi)} puts · ${compact(r.call_oi)} calls`)}
      ${metric("Calculated", esc(localTime(r.fetched)), r.cached ? "saved today" : "live")}
    </div>
    <div style="margin-top:12px">${painChart(r)}</div>
    <div class="legend"><span><i style="background:var(--bull)"></i>Paid to call holders</span><span><i style="background:var(--bear)"></i>Paid to put holders</span><span>Lowest total = max pain</span></div>
    ${hist.length > 1 ? `<h3>How max pain for ${esc(r.expiration)} changed</h3><div class="table-wrap"><table class="grid"><thead><tr><th>Observed on</th><th class="num">Max pain</th><th class="num">Price</th><th class="num">Put/Call</th></tr></thead><tbody>
      ${hist.map((h) => `<tr><td>${esc(h.asof)}</td><td class="num">${num(h.max_pain)}</td><td class="num">${num(h.price)}</td><td class="num">${num(h.pcr)}</td></tr>`).join("")}</tbody></table></div>` : ""}`;
}

/* ------------------------------------------------------------ max pain tab */
$("#painDatesBtn").onclick = async () => {
  const sym = $("#painSymbol").value.trim().toUpperCase();
  if (!sym) return;
  $("#painResult").innerHTML = '<p class="muted">Loading expiration dates…</p>';
  try {
    const { dates } = await api(`/api/options/${encodeURIComponent(sym)}/dates`);
    if (!dates.length) throw new Error(`No listed options for ${sym} on Yahoo Finance.`);
    $("#painDate").innerHTML = dates.map((d) => `<option>${esc(d)}</option>`).join("");
    $("#painDate").disabled = false; $("#painCalcBtn").disabled = false;
    $("#painResult").innerHTML = `<p class="muted">${dates.length} expiration dates available.</p>`;
  } catch (e) { $("#painResult").innerHTML = `<p class="neg">${esc(e.message)}</p>`; }
};
$("#painSymbol").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#painDatesBtn").click(); });
$("#painCalcBtn").onclick = async () => {
  const sym = $("#painSymbol").value.trim().toUpperCase(), exp = $("#painDate").value;
  $("#painResult").innerHTML = '<p class="muted">Calculating…</p>';
  try { const res = await api(`/api/options/${encodeURIComponent(sym)}?expiration=${exp}&live=1`); $("#painResult").innerHTML = painView(res.result, res.history) + `<p><a target="_blank" rel="noopener" href="https://optioncharts.io/options/${esc(sym.split(".")[0])}/max-pain">Compare on optioncharts.io ↗</a></p>`; loadBoard(); }
  catch (e) { $("#painResult").innerHTML = `<p class="neg">${esc(e.message)}</p>`; }
};
["#boardExp", "#boardAsof", "#boardMine"].forEach((id) => $(id).addEventListener("change", loadBoard));

async function loadBoard() {
  const q = new URLSearchParams();
  if ($("#boardExp").options.length) q.set("expiration", $("#boardExp").value || "all");
  if ($("#boardAsof").value) q.set("asof", $("#boardAsof").value);
  if ($("#boardMine").checked) q.set("mine", "1");
  let d; try { d = await api(`/api/maxpain-board?${q}`); } catch (e) { toast(e.message); return; }
  $("#boardExp").innerHTML = `<option value="all">All dates</option>` + d.expirations.map((e) => `<option ${e === d.expiration ? "selected" : ""}>${esc(e)}</option>`).join("");
  $("#boardAsof").innerHTML = d.asofs.map((e) => `<option ${e === d.asof ? "selected" : ""}>${esc(e)}</option>`).join("");
  $("#boardEmpty").classList.toggle("hidden", d.rows.length > 0);
  $("#boardTable tbody").innerHTML = d.rows.map((r) => `<tr class="clickable" data-symbol="${esc(r.symbol)}">
    <td><div class="stock-cell"><b>${esc(r.symbol)}</b><span>${esc(r.name || "")}</span></div></td><td>${esc(r.expiration)}</td>
    <td class="num">${num(r.price)}</td><td class="num"><b>${num(r.max_pain)}</b></td><td class="num ${r.distance >= 0 ? "pos" : "neg"}">${pct(r.distance)}</td>
    <td class="num">${num(r.pcr)}</td><td class="num">${compact(r.call_oi + r.put_oi)}</td>
    <td><a class="btn small ghost" target="_blank" rel="noopener" href="https://optioncharts.io/options/${esc(r.symbol.split(".")[0])}/max-pain" onclick="event.stopPropagation()">OptionCharts ↗</a></td></tr>`).join("");
  $$("#boardTable tr[data-symbol]").forEach((tr) => (tr.onclick = () => openStock(tr.dataset.symbol)));
}

/* ------------------------------------------------------------ lookup */
$("#lookupBtn").onclick = async () => {
  const sym = $("#lookupSymbol").value.trim().toUpperCase();
  if (!sym) return;
  $("#lookupMsg").textContent = `Analysing ${sym}… (about 10–30 seconds)`;
  $("#lookupBtn").disabled = true;
  try { await api(`/api/analyze/${encodeURIComponent(sym)}`, { method: "POST" }); $("#lookupMsg").textContent = ""; openStock(sym); }
  catch (e) { $("#lookupMsg").textContent = e.message; }
  $("#lookupBtn").disabled = false;
};
$("#lookupSymbol").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#lookupBtn").click(); });

/* ------------------------------------------------------------ quality */
async function loadQuality() {
  const [q, st] = await Promise.all([api("/api/quality"), api("/api/status")]);
  const run = st.run;
  $("#runInfo").innerHTML = run ? [
    `Last refresh: <b>${esc(localTime(run.finished))}</b>`, `Status: <b>${esc(run.status)}</b>`, `Stocks looked up: <b>${run.requested}</b>`,
    `Failed: <b>${run.failed}</b>`, `Analysed in depth: <b>${run.analysed}</b>`, `Markets screened: <b>${run.screened_markets}</b>`,
    ...(run.scalable_info && run.scalable_info.count ? [`Scalable/gettex list: <b>${run.scalable_info.count.toLocaleString()}</b> instruments${run.scalable_info.stale ? " (yesterday's list)" : ""}`] : []),
    ...(run.notes || []).map((n) => `<span class="neg">${esc(n)}</span>`)].map((x) => `<span>${x}</span>`).join("") : "No refresh yet.";
  $("#qualityTable tbody").innerHTML = q.rows.map((r) => `<tr><td><b>${esc(r.symbol)}</b></td><td>${esc(r.name || "")}</td><td>${esc(r.region)}</td><td style="white-space:normal">${esc(r.problem)}</td></tr>`).join("") || '<tr><td colspan="4" class="muted">Nothing to report.</td></tr>';
}

/* ------------------------------------------------------------ settings */
async function loadSettings() {
  const [{ settings }, wl, runs] = await Promise.all([api("/api/settings"), api("/api/watchlist"), api("/api/runs")]);
  const f = $("#settingsForm");
  for (const [k, v] of Object.entries(settings)) {
    const el = f.elements[k]; if (!el) continue;
    if (el.type === "checkbox") el.checked = !!v; else el.value = v;
  }
  for (const m of ["pe", "forward_pe", "peg"]) { const el = f.elements[`w_${m}`]; el.value = settings.weights[m]; el.nextElementSibling.textContent = el.value; el.oninput = () => (el.nextElementSibling.textContent = el.value); }
  $("#watchlistBox").value = ["symbol,region", ...wl.rows.map((r) => `${r.symbol},${r.region}`)].join("\n");
  $("#runsTable tbody").innerHTML = runs.runs.map((r) => `<tr><td>${esc(localTime(r.finished))}</td><td>${esc(r.status)}</td><td class="num">${r.requested ?? "—"}</td><td class="num">${r.failed ?? "—"}</td><td class="num">${r.analysed ?? "—"}</td><td style="white-space:normal" class="sub">${esc((r.notes || []).join("; "))}</td></tr>`).join("") || '<tr><td colspan="6" class="muted">No refreshes yet.</td></tr>';
}
$("#settingsForm").onsubmit = async (ev) => {
  ev.preventDefault();
  const f = ev.target, body = {};
  for (const el of f.elements) {
    if (!el.name || el.name.startsWith("w_")) continue;
    body[el.name] = el.type === "checkbox" ? el.checked : el.type === "number" ? Number(el.value) : el.value;
  }
  body.weights = { pe: +f.elements.w_pe.value, forward_pe: +f.elements.w_forward_pe.value, peg: +f.elements.w_peg.value };
  try {
    S.settings = (await api("/api/settings", { method: "POST", body })).settings;
    await api("/api/watchlist", { method: "POST", body: { csv: $("#watchlistBox").value } });
    $("#settingsMsg").textContent = "Saved. Ranking changes apply now; the stock list changes apply at the next refresh.";
    $("#sectorToggle").checked = S.settings.sector_relative; $("#flagToggle").checked = S.settings.exclude_flagged; $("#scalableToggle").checked = S.settings.scalable_only !== false;
    loadRankings(); pollStatus.once?.();
  } catch (e) { $("#settingsMsg").textContent = e.message; }
};

/* ------------------------------------------------------------ boot */
const VIEWS = ["mystocks", "trade", "scan", "rank", "pain", "lookup", "quality", "settings", "help"];
const viewFromHash = () => { const v = (location.hash || "").slice(1); return VIEWS.includes(v) ? v : null; };
window.addEventListener("load", () => showView(viewFromHash() || (VIEWS.includes(store.get("va-view")) ? store.get("va-view") : "mystocks")));
window.addEventListener("hashchange", () => { const v = viewFromHash(); if (v) showView(v); });
api("/api/settings").then(({ settings }) => { S.settings = settings; $("#sectorToggle").checked = settings.sector_relative; $("#flagToggle").checked = settings.exclude_flagged; $("#scalableToggle").checked = settings.scalable_only !== false; }).catch(() => {});
renderRankings();
pollStatus();
