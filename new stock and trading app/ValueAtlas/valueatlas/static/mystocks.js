"use strict";
/* My stocks page: portfolio + watchlist valuation. Uses helpers from app.js. */

const MS = { items: [], list: store.get("ms-list") || "", verdict: "", sort: null, asc: false, timer: null, visible: false,
  page: 1, pageSize: +(store.get("ms-page-size") || 20) };
const vbClass = (v) => ({ Undervalued: "under", Overvalued: "over", Neutral: "neutral" }[v] || "none");

async function msLoad() {
  let d;
  try { d = await api("/api/mystocks"); } catch (e) { toast(e.message); return; }
  MS.items = d.items;
  MS.hasPeers = d.has_peers;
  const c = d.checking || {};
  $("#msProgress").classList.toggle("hidden", !c.running);
  if (c.running) {
    $("#msBar").style.width = c.total ? `${Math.round((100 * c.done) / c.total)}%` : "5%";
    $("#msProgText").textContent = `Checking ${c.done} of ${c.total}…`;
  }
  $("#msCheck").disabled = !!c.running;
  $("#msCheck").textContent = c.running ? "Checking…" : "Check all now";
  $("#msUpdated").textContent = d.updated ? `Last checked ${localTime(d.updated)}` : "Not checked yet";
  if (c.error) toast(c.error, 6000);
  msRender();
  clearTimeout(MS.timer);
  if (MS.visible) MS.timer = setTimeout(msLoad, c.running ? 2500 : 60000);
}

const shortDate = (iso) => { try { return new Date(iso + "T12:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" }); } catch { return iso; } };
function maxPainCell(r) {
  const d = r.max_pain_dates || [];
  const os = (r.options_symbol || r.listed_symbol || r.symbol || "").split(".")[0];
  const links = os && d.length ? `<div class="mp-links">
      <a target="_blank" rel="noopener" href="https://optioncharts.io/options/${encodeURIComponent(os)}/max-pain" title="Compare with OptionCharts' own max pain chart">OptionCharts ↗</a>
      <a target="_blank" rel="noopener" href="https://finance.yahoo.com/quote/${encodeURIComponent(os)}/options" title="Option chain on Yahoo Finance">Yahoo ↗</a>
      <a target="_blank" rel="noopener" href="https://www.barchart.com/stocks/quotes/${encodeURIComponent(os)}/options" title="Option chain on Barchart">Barchart ↗</a></div>` : "";
  if (!d.length) return `<span class="sub" title="${esc(r.options_reason || "")}">${r.kind === "fund" || /No listed options/.test(r.options_reason || "") ? "No options" : "—"}</span>`;
  const first = d[0];
  const dist = (x) => (isNum(x.distance) ? `<span class="${x.distance >= 0 ? "pos" : "neg"}">${pct(x.distance, 1)}</span>` : "");
  const via = r.options_symbol && r.options_symbol !== (r.listed_symbol || r.symbol) ? `<div class="sub">via US listing ${esc(r.options_symbol)} (USD)</div>` : "";
  return `<div class="mp-cell" title="Max pain = strike where option holders would collect the least at expiry">
    <div><b>${num(first.max_pain)}</b> ${dist(first)} <span class="sub">${esc(shortDate(first.expiration))}</span></div>
    ${d.slice(1).map((x) => `<div class="sub">${esc(shortDate(x.expiration))}: <b style="color:var(--ink)">${num(x.max_pain)}</b> ${dist(x)}</div>`).join("")}${via}${links}</div>`;
}
function renderPager(pages, total, size) {
  const from = total ? (MS.page - 1) * size + 1 : 0, to = Math.min(total, MS.page * size);
  const btn = (p, label = p, dis = false) => `<button class="pg ${p === MS.page && label === p ? "on" : ""}" data-page="${p}" ${dis ? "disabled" : ""}>${label}</button>`;
  let nums = "";
  for (let p = 1; p <= pages; p++) nums += btn(p);
  const html = `<div class="pager">
      <span class="sub">Showing ${from}–${to} of ${total}</span>
      <div class="pg-btns">${btn(MS.page - 1, "‹ Prev", MS.page <= 1)}${nums}${btn(MS.page + 1, "Next ›", MS.page >= pages)}</div>
      <label class="sub">Per page <select class="pg-size">${[10, 20, 30, 50, 0].map((n) => `<option value="${n}" ${n === MS.pageSize ? "selected" : ""}>${n || "All"}</option>`).join("")}</select></label>
    </div>`;
  $$(".ms-pager").forEach((el) => {
    el.innerHTML = html;
    $$("[data-page]", el).forEach((b) => (b.onclick = () => { MS.page = +b.dataset.page; msRender(); if (el.id === "msPagerBottom") $("#msTable").scrollIntoView({ block: "start" }); }));
    $(".pg-size", el).onchange = (e) => { MS.pageSize = +e.target.value; store.set("ms-page-size", MS.pageSize); MS.page = 1; msRender(); };
  });
}
function msRender() {
  $$("#msListSeg button").forEach((b) => {
    b.classList.toggle("active", b.dataset.list === MS.list);
    const n = MS.items.filter((r) => !b.dataset.list || r.list === b.dataset.list).length;
    b.textContent = `${b.dataset.list || "All"} · ${n}`;
  });
  $$("#msVerdictSeg button").forEach((b) => b.classList.toggle("active", b.dataset.v === MS.verdict));
  const scope = MS.items.filter((r) => !MS.list || r.list === MS.list);
  const count = (v) => scope.filter((r) => r.verdict === v).length;
  $("#msKpis").innerHTML = [
    ["Undervalued", count("Undervalued"), "cheap vs peers and growth", "pos"],
    ["Neutral", count("Neutral"), "fairly valued", ""],
    ["Overvalued", count("Overvalued"), "expensive vs peers and growth", "neg"],
    ["Not rated", count("Not rated") + scope.filter((r) => r.pending).length, "ETFs/ETCs, loss-makers, pending", ""],
    ["Peer comparison", MS.hasPeers ? "On" : "Waiting", MS.hasPeers ? "vs today's market scan" : "rule-of-thumb until the screener's first scan finishes", ""],
  ].map(([l, v, s, cls]) => `<div class="kpi"><div class="label">${l}</div><div class="value ${cls}">${v}</div><div class="sub">${s}</div></div>`).join("");

  let rows = scope.filter((r) => !MS.verdict || r.verdict === MS.verdict);
  if (MS.sort) rows = [...rows].sort((a, b) => { const x = a[MS.sort], y = b[MS.sort]; if (!isNum(x)) return 1; if (!isNum(y)) return -1; return MS.asc ? x - y : y - x; });
  $("#msEmpty").classList.toggle("hidden", rows.length > 0);
  $("#msEmpty").innerHTML = MS.items.length ? "<b>No stocks match this filter</b>" : "<b>No stocks yet</b>Add stocks with the box above.";
  // pages 1, 2, 3 … so long lists stay quick to read
  const size = MS.pageSize || rows.length || 1;
  const pages = Math.max(1, Math.ceil(rows.length / size));
  MS.page = Math.min(Math.max(1, MS.page), pages);
  const total = rows.length;
  rows = rows.slice((MS.page - 1) * size, MS.page * size);
  renderPager(pages, total, size);
  $("#msTable tbody").innerHTML = rows.map((r) => {
    const name = `<div class="stock-cell"><b>${esc(r.name || r.symbol)}<span class="list-tag ${esc(r.list)}">${esc(r.list || "")}</span></b>
      <span>${esc(r.symbol)}${r.listed_symbol && r.listed_symbol !== r.symbol ? ` (data: ${esc(r.listed_symbol)})` : ""} · ${esc(r.kind === "fund" ? (r.quote_type || "Fund") : (r.sector || ""))} ${scalableTag(r.scalable)}</span></div>`;
    if (r.pending) return `<tr><td>${name}</td><td colspan="9" class="muted">Waiting for the first check…</td><td></td></tr>`;
    if (r.error) return `<tr><td>${name}</td><td colspan="9" class="neg" style="white-space:normal">${esc(r.error)}</td><td><button class="btn small ghost" data-rm="${esc(r.symbol)}">Remove</button></td></tr>`;
    const score = isNum(r.value_score) ? `<div class="scorebar" style="margin-top:6px"><div class="track"><div class="fill" style="width:${Math.max(2, Math.min(100, r.value_score))}%;background:var(${r.value_score >= 60 ? "--bull" : r.value_score <= 40 ? "--bear" : "--neutral"})"></div></div><b>${Math.round(r.value_score)}</b></div>` : "";
    const changed = r.previous_verdict && r.previous_verdict !== r.verdict ? `<div class="changed">was ${esc(r.previous_verdict)}</div>` : "";
    const why = (r.reasons || []).slice(0, 4).map((x) => `<div>${esc(x)}</div>`).join("");
    const pegCell = `${num(r.peg, 2)}${r.peg_field === "estimated" ? '<div class="sub" title="Estimated: trailing P/E ÷ forecast EPS growth">est.</div>' : ""}`;
    return `<tr class="clickable" data-sym="${esc(r.symbol)}">
      <td>${name}</td><td class="num">${money(r.price, r.currency)}</td>
      <td class="num">${num(r.pe, 1)}</td><td class="num">${num(r.forward_pe, 1)}</td><td class="num">${pegCell}</td>
      <td><span class="vbadge ${vbClass(r.verdict)}">${esc(r.verdict || "—")}</span>${score}${changed}</td>
      <td class="why-cell">${why}</td>
      <td>${r.trend ? `<span class="chip ${techTone(r.trend)}">${esc(r.trend)}</span>` : '<span class="sub">—</span>'}</td>
      <td>${maxPainCell(r)}</td>
      <td>${spark(r.spark)}<div class="sub ${(r.return_3m || 0) >= 0 ? "pos" : "neg"}">${pct(r.return_3m, 1)}</div></td>
      <td><div class="acts" style="display:flex;gap:4px;flex-wrap:wrap"><button class="btn small ghost" data-trade="${esc(r.listed_symbol || r.symbol)}">Trade</button><button class="btn small ghost" data-rm="${esc(r.symbol)}" title="Remove from My stocks">✕</button></div></td></tr>`;
  }).join("");
  $$("#msTable tr[data-sym]").forEach((tr) => (tr.onclick = (e) => { if (e.target.closest("button")) return; openStock(tr.dataset.sym); }));
  $$("#msTable [data-trade]").forEach((b) => (b.onclick = () => window.openTradeDesk && window.openTradeDesk(b.dataset.trade)));
  $$("#msTable [data-rm]").forEach((b) => (b.onclick = async () => {
    if (!confirm(`Remove ${b.dataset.rm} from My stocks?`)) return;
    await api(`/api/mystocks/${encodeURIComponent(b.dataset.rm)}`, { method: "DELETE" });
    msLoad();
  }));
}

$$("#msListSeg button").forEach((b) => (b.onclick = () => { MS.list = b.dataset.list; store.set("ms-list", MS.list); MS.page = 1; msRender(); }));
$$("#msVerdictSeg button").forEach((b) => (b.onclick = () => { MS.verdict = b.dataset.v; MS.page = 1; msRender(); }));
$$("#msTable th[data-sort]").forEach((th) => (th.onclick = () => {
  const k = th.dataset.sort;
  if (MS.sort === k) MS.asc = !MS.asc; else { MS.sort = k; MS.asc = ["pe", "forward_pe", "peg"].includes(k); }
  $$("#msTable th[data-sort]").forEach((x) => { x.classList.toggle("sorted", x.dataset.sort === MS.sort); x.classList.toggle("asc", x.dataset.sort === MS.sort && MS.asc); });
  msRender();
}));
$("#msCheck").onclick = async () => { await api("/api/mystocks/check", { method: "POST" }); toast("Checking all your stocks – about a minute."); setTimeout(msLoad, 800); };

/* add a stock: suggestions from Yahoo's symbol search */
let msSugTimer = null, msSug = [];
const msInput = $("#msAddInput"), msBox = $("#msSuggest");
msInput.addEventListener("input", () => {
  clearTimeout(msSugTimer);
  const q = msInput.value.trim();
  if (!q) { msBox.classList.add("hidden"); return; }
  msSugTimer = setTimeout(async () => {
    try { msSug = (await api(`/api/trade/search?q=${encodeURIComponent(q)}`)).results; } catch { msSug = []; }
    if (!msSug.length) { msBox.classList.add("hidden"); return; }
    msBox.innerHTML = msSug.map((s) => `<button data-sym="${esc(s.symbol)}" data-name="${esc(s.name)}"><b>${esc(s.symbol)}</b><span>${esc(s.name)}</span><em>${esc(s.exchange)} · ${esc(s.type || "")}</em></button>`).join("");
    msBox.classList.remove("hidden");
    $$("button", msBox).forEach((b) => (b.onmousedown = (e) => { e.preventDefault(); msAdd(b.dataset.sym, b.dataset.name); }));
  }, 250);
});
msInput.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); msAdd(msInput.value.trim().toUpperCase()); } if (e.key === "Escape") msBox.classList.add("hidden"); });
msInput.addEventListener("blur", () => setTimeout(() => msBox.classList.add("hidden"), 150));
$("#msAddBtn").onclick = () => msAdd(msInput.value.trim().toUpperCase());
async function msAdd(sym, name = "") {
  if (!sym) return;
  msBox.classList.add("hidden");
  $("#msAddBtn").disabled = true; $("#msAddBtn").textContent = "Adding…";
  try {
    const r = await api("/api/mystocks/add", { method: "POST", body: { symbol: sym, name, list: $("#msAddList").value } });
    msInput.value = "";
    toast(r.error ? r.error : r.note ? `${sym} added. ${r.note}` : `${sym} added and checked.`, r.error ? 6000 : 3000);
  } catch (e) { toast(e.message, 6000); }
  $("#msAddBtn").disabled = false; $("#msAddBtn").textContent = "Add";
  msLoad();
}

window.addEventListener("va:view", (e) => {
  MS.visible = e.detail === "mystocks";
  if (MS.visible) msLoad(); else clearTimeout(MS.timer);
});
