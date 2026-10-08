"use strict";
/* Trade desk + scanner. Uses helpers from app.js ($, esc, num, pct, api, toast, store, …). */

const TFS = ["1m", "2m", "5m", "15m", "30m", "1h", "3h", "4h", "1D"];
const TF_NAME = { "1m": "1 min", "2m": "2 min", "5m": "5 min", "15m": "15 min", "30m": "30 min", "1h": "1 hour", "3h": "3 hours", "4h": "4 hours", "1D": "1 day" };
const TF_POLL = { "1m": 5, "2m": 8, "5m": 15, "15m": 30, "30m": 45, "1h": 60, "3h": 90, "4h": 90, "1D": 180 };
const TV_TA = { "1m": "1m", "2m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1h", "3h": "4h", "4h": "4h", "1D": "1D" };
const OVERLAYS = [
  ["ema", "EMA 9/21", "#f59e0b"], ["ema_long", "EMA 50/200", "#a78bfa"], ["vwap", "VWAP", "#38bdf8"], ["bb", "Bollinger", "#94a3b8"],
  ["st", "Supertrend", "#22c55e"], ["sar", "SAR", "#eab308"], ["pat", "Patterns", "#f472b6"], ["opt", "Options levels", "#14b8a6"],
];
const T = {
  sym: (store.get("td-sym") || "AAPL").toUpperCase(), tf: store.get("td-tf") || "5m", prepost: store.get("td-prepost") === "1",
  summary: null, chartData: null, options: null, optExp: "", watch: [], visible: false, timers: {}, chartTab: "signal",
  overlays: new Set(JSON.parse(store.get("td-overlays") || '["ema","vwap","st","pat","opt"]')), tvKey: "", tvTaKey: "",
};
const vcls = (v) => (!v ? "none" : /Bull/.test(v) ? "bull" : /Bear/.test(v) ? "bear" : v === "Neutral" ? "neutral" : "none");
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const SAFE_LOCALE = (() => { try { new Intl.DateTimeFormat(navigator.language); return navigator.language; } catch { return "en-US"; } })();
const tzShift = (t) => t - new Date(t * 1000).getTimezoneOffset() * 60;  // show candles in your local time

/* ------------------------------------------------------------ symbol input & suggestions */
const symInput = $("#tdSymbol"), sug = $("#tdSuggest");
let sugTimer = null, sugItems = [], sugIdx = -1;
symInput.addEventListener("input", () => {
  clearTimeout(sugTimer);
  const q = symInput.value.trim();
  if (!q) { sug.classList.add("hidden"); return; }
  sugTimer = setTimeout(async () => {
    try { sugItems = (await api(`/api/trade/search?q=${encodeURIComponent(q)}`)).results; } catch { sugItems = []; }
    sugIdx = -1; renderSuggest();
  }, 250);
});
function renderSuggest() {
  if (!sugItems.length) { sug.classList.add("hidden"); return; }
  sug.innerHTML = sugItems.map((s, i) => `<button data-sym="${esc(s.symbol)}" class="${i === sugIdx ? "on" : ""}"><b>${esc(s.symbol)}</b><span>${esc(s.name)}</span><em>${esc(s.exchange)} · ${esc(s.type || "")}</em></button>`).join("");
  sug.classList.remove("hidden");
  $$("button", sug).forEach((b) => (b.onmousedown = (e) => { e.preventDefault(); pick(b.dataset.sym); }));
}
symInput.addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown" || e.key === "ArrowUp") { if (!sugItems.length) return; e.preventDefault(); sugIdx = (sugIdx + (e.key === "ArrowDown" ? 1 : -1) + sugItems.length) % sugItems.length; renderSuggest(); }
  else if (e.key === "Enter") {
    e.preventDefault();
    const typed = symInput.value.trim().toUpperCase();
    if (sugIdx >= 0) pick(sugItems[sugIdx].symbol);
    else if (/^\^?[A-Z0-9][A-Z0-9.\-=&]{0,24}$/.test(typed)) pick(typed);
    else if (sugItems[0]) pick(sugItems[0].symbol);
  } else if (e.key === "Escape") sug.classList.add("hidden");
});
symInput.addEventListener("blur", () => setTimeout(() => sug.classList.add("hidden"), 150));
// type anywhere on the desk to start searching
document.addEventListener("keydown", (e) => {
  if (!T.visible || e.ctrlKey || e.metaKey || e.altKey || e.key.length !== 1 || /input|textarea|select/i.test(document.activeElement.tagName)) return;
  if (!$("#drawer").classList.contains("hidden")) return;
  symInput.value = ""; symInput.focus();
});
function pick(sym) {
  sug.classList.add("hidden"); symInput.value = ""; symInput.blur();
  setSymbol(sym);
}

/* ------------------------------------------------------------ watchlist chips */
async function loadWatch() { try { T.watch = (await api("/api/trade/watchlist")).symbols; } catch { T.watch = []; } renderWatch(); }
async function saveWatch(list) { T.watch = (await api("/api/trade/watchlist", { method: "POST", body: { symbols: list } })).symbols; renderWatch(); }
function renderWatch() {
  $("#tdWatch").innerHTML = T.watch.map((s) => `<span class="wchip ${s === T.sym ? "on" : ""}" data-sym="${esc(s)}">${esc(s)}<i data-x="${esc(s)}" title="Remove">×</i></span>`).join("");
  $$(".wchip", $("#tdWatch")).forEach((c) => (c.onclick = (e) => { if (e.target.dataset.x) { saveWatch(T.watch.filter((x) => x !== e.target.dataset.x)); return; } setSymbol(c.dataset.sym); }));
  const inList = T.watch.includes(T.sym);
  $("#tdStar").textContent = inList ? "★ In watchlist" : "☆ Add to watchlist";
}
$("#tdStar").onclick = () => saveWatch(T.watch.includes(T.sym) ? T.watch.filter((x) => x !== T.sym) : [...T.watch, T.sym]);
$("#tdPrepost").checked = T.prepost;
$("#tdPrepost").onchange = () => { T.prepost = $("#tdPrepost").checked; store.set("td-prepost", T.prepost ? "1" : "0"); T.chartData = null; refreshAll(); };

function setSymbol(sym) {
  sym = sym.toUpperCase();
  if (sym === T.sym && T.summary) return;
  T.sym = sym; store.set("td-sym", sym);
  T.summary = null; T.chartData = null; T.options = null; T.optExp = "";
  $("#tdQuote").innerHTML = `<span class="q-sym">${esc(sym)}</span><span class="muted">Loading all 9 timeframes…</span>`;
  $("#tfStrip").innerHTML = TFS.map((tf) => `<div class="tf"><div class="tf-name">${tf}</div><div class="tf-verdict muted">…</div></div>`).join("");
  renderWatch();
  refreshAll();
}
function setTf(tf) {
  if (tf === T.tf) return;
  T.tf = tf; store.set("td-tf", tf); T.chartData = null;
  renderStrip(); loadChart(); renderTv();
}
const q = () => (T.prepost ? "?prepost=1" : "");

/* ------------------------------------------------------------ summary (all timeframes) */
async function loadSummary() {
  const sym = T.sym;
  try {
    const s = await api(`/api/trade/summary/${encodeURIComponent(sym)}${q()}`);
    if (sym !== T.sym) return;
    T.summary = s;
    renderQuote(); renderStrip(); renderGroups(); renderTv();
  } catch (e) {
    if (sym !== T.sym) return;
    $("#tdQuote").innerHTML = `<span class="q-sym">${esc(sym)}</span><span class="neg">${esc(e.message)}</span>`;
    $("#tfStrip").innerHTML = ""; $("#tdGroups").innerHTML = "";
  }
}
function renderQuote() {
  const s = T.summary, qt = s.quote || {};
  const up = (qt.change || 0) >= 0;
  const when = qt.last_time ? new Date(qt.last_time * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—";
  $("#tdQuote").innerHTML = `
    <span class="q-sym">${esc(s.symbol)}</span>
    <span class="q-price">${num(qt.price)}</span>
    <span class="q-chg ${up ? "pos" : "neg"}">${qt.change != null ? (up ? "+" : "") + num(qt.change) + " (" + pct(qt.change_pct, 2) + ")" : ""}</span>
    <span class="live-dot ${qt.live ? "on" : ""}" title="${esc(s.delay)}"><i></i>${qt.live ? `Live · ${when}` : `Market closed · last ${when}`}</span>
    <span class="q-name">${esc(s.name || "")} · ${esc(s.exchange || "")} ${s.currency ? "· " + esc(s.currency) : ""}</span>
    <span id="tdScalable">${T.scalable && T.scalable.symbol === s.symbol ? scalableTag(T.scalable.status, true) : ""}</span>`;
  if (!T.scalable || T.scalable.symbol !== s.symbol) {
    const sym = s.symbol;
    T.scalable = { symbol: sym, status: null };
    api(`/api/scalable/${encodeURIComponent(sym)}`).then((r) => {
      if (T.sym !== sym) return;
      T.scalable = { symbol: sym, status: r.status, isin: r.isin };
      const el = $("#tdScalable");
      if (el) { el.innerHTML = scalableTag(r.status, true); el.title = r.isin ? `ISIN ${r.isin}` : ""; }
    }).catch(() => {});
  }
  document.title = `${s.symbol} ${num(qt.price)} · ValueAtlas`;
}
function bar(score) {
  const w = Math.min(1, Math.abs(score || 0)) * 50, left = (score || 0) >= 0 ? 50 : 50 - w;
  return `<div class="mini-div"><i style="left:${left}%;width:${w}%;background:var(${(score || 0) >= 0 ? "--bull" : "--bear"})"></i></div>`;
}
function renderStrip() {
  const s = T.summary;
  if (!s) return;
  $("#tfStrip").innerHTML = TFS.map((tf) => {
    const f = s.frames[tf] || {};
    if (!f.available) return `<button class="tf ${tf === T.tf ? "on" : ""}" data-tf="${tf}"><div class="tf-name">${tf}</div><div class="tf-verdict muted">No data</div><div class="tf-meta" title="${esc(f.reason || "")}">${esc((f.reason || "").slice(0, 28))}</div></button>`;
    const h = f.hit_rate?.next_bar;
    const trend = f.previous_score != null ? (f.score > f.previous_score + 0.02 ? "↗" : f.score < f.previous_score - 0.02 ? "↘" : "→") : "";
    return `<button class="tf ${vcls(f.verdict)} ${tf === T.tf ? "on" : ""}" data-tf="${tf}" title="${TF_NAME[tf]} chart · score ${num(f.score)} · TradingView method: ${esc(f.tradingview?.label || "")}">
      <div class="tf-name"><span>${tf}</span><span>${trend}</span></div>
      <div class="tf-verdict">${esc(f.verdict)}</div>${bar(f.score)}
      <div class="tf-meta"><span>▲${f.counts.bullish} ▼${f.counts.bearish}</span><span>${h ? `hit ${Math.round(h.accuracy * 100)}%` : ""}</span></div></button>`;
  }).join("");
  $$(".tf", $("#tfStrip")).forEach((b) => (b.onclick = () => setTf(b.dataset.tf)));
}
function renderGroups() {
  const g = T.summary.groups;
  $("#tdGroups").innerHTML = Object.entries(g).map(([name, v]) => `<div class="grp"><div><div class="label">${esc(name)}</div><div class="v ${vcls(v.verdict) === "bull" ? "pos" : vcls(v.verdict) === "bear" ? "neg" : ""}">${esc(v.verdict)}</div></div><div style="width:40%">${bar(v.score)}<div class="sub" style="text-align:right;margin-top:4px">${num(v.score)}</div></div></div>`).join("");
}

/* ------------------------------------------------------------ signal chart */
let chart = null, series = {};
function makeChart() {
  const el = $("#tdChart");
  if (chart) { chart.remove(); chart = null; series = {}; }
  if (!window.LightweightCharts) { el.innerHTML = '<p class="muted">Chart library failed to load.</p>'; return; }
  const LW = window.LightweightCharts;
  chart = LW.createChart(el, {
    autoSize: true,
    layout: { background: { type: "solid", color: "transparent" }, textColor: css("--muted"), fontFamily: getComputedStyle(document.body).fontFamily, fontSize: 11 },
    grid: { vertLines: { color: css("--line") + "66" }, horzLines: { color: css("--line") + "66" } },
    rightPriceScale: { borderColor: css("--line") }, timeScale: { borderColor: css("--line"), timeVisible: true, secondsVisible: false, rightOffset: 6 },
    crosshair: { mode: LW.CrosshairMode.Normal },
    localization: { locale: SAFE_LOCALE },
  });
  const bull = css("--bull"), bear = css("--bear");
  series.candle = chart.addCandlestickSeries({ upColor: bull, downColor: bear, borderUpColor: bull, borderDownColor: bear, wickUpColor: bull, wickDownColor: bear });
  series.vol = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol", lastValueVisible: false, priceLineVisible: false });
  chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  const line = (color, width = 1, style = 0) => chart.addLineSeries({ color, lineWidth: width, lineStyle: style, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
  series.ema9 = line("#f59e0b"); series.ema21 = line("#fb7185");
  series.ema50 = line("#a78bfa"); series.ema200 = line("#6366f1", 2);
  series.vwap = line("#38bdf8", 2);
  series.bbU = line("#94a3b8", 1, 2); series.bbM = line("#94a3b8", 1, 1); series.bbL = line("#94a3b8", 1, 2);
  series.st = line(bull, 2);
  series.sar = chart.addLineSeries({ color: "#eab308", lineVisible: false, pointMarkersVisible: true, pointMarkersRadius: 1.5, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
  series.priceLines = [];
  chart.subscribeCrosshairMove((p) => legend(p));
}
function legend(p) {
  const d = T.chartData;
  if (!d) return;
  const c = d.candles, i = p && p.time != null ? d._times.indexOf(p.time) : c.t.length - 1;
  if (i < 0) return;
  const chg = c.c[i] - c.o[i];
  $("#tdLegend").innerHTML = `<span><b>${esc(T.sym)}</b> · ${TF_NAME[T.tf]}</span><span>O ${num(c.o[i])}</span><span>H ${num(c.h[i])}</span><span>L ${num(c.l[i])}</span>
    <span class="${chg >= 0 ? "pos" : "neg"}">C ${num(c.c[i])}</span><span>Vol ${compact(c.v[i])}</span>
    ${d.score_series ? `<span>Signal score ${num(d.score_series[i])}</span>` : ""}`;
}
function pts(times, arr, filter) {
  if (!arr) return [];
  const out = [];
  for (let i = 0; i < times.length; i++) {
    if (arr[i] != null && (!filter || filter(i))) out.push({ time: times[i], value: arr[i] });
    else if (filter) out.push({ time: times[i] });  // whitespace point = gap in the line
  }
  return out;
}
function drawChart(d, fresh) {
  if (!chart) makeChart();
  if (!chart) return;
  const c = d.candles, times = c.t.map(tzShift);
  d._times = times;
  const ts = chart.timeScale();
  const prevRange = fresh ? null : ts.getVisibleLogicalRange();
  const prevLen = T._len || 0;
  series.candle.setData(times.map((t, i) => ({ time: t, open: c.o[i], high: c.h[i], low: c.l[i], close: c.c[i] })));
  const bullA = css("--bull") + "55", bearA = css("--bear") + "55";
  series.vol.setData(times.map((t, i) => ({ time: t, value: c.v[i] || 0, color: c.c[i] >= c.o[i] ? bullA : bearA })));
  const o = d.overlays, on = (k) => T.overlays.has(k);
  series.ema9.setData(on("ema") ? pts(times, o.ema9) : []); series.ema21.setData(on("ema") ? pts(times, o.ema21) : []);
  series.ema50.setData(on("ema_long") ? pts(times, o.ema50) : []); series.ema200.setData(on("ema_long") ? pts(times, o.ema200) : []);
  series.vwap.setData(on("vwap") && o.vwap ? pts(times, o.vwap) : []);
  series.bbU.setData(on("bb") ? pts(times, o.bb_upper) : []); series.bbM.setData(on("bb") ? pts(times, o.bb_mid) : []); series.bbL.setData(on("bb") ? pts(times, o.bb_lower) : []);
  // Supertrend: draw the line in green below price in uptrends, red above price in downtrends
  const bullC = css("--bull"), bearC = css("--bear");
  const stPts = [];
  if (on("st")) for (let i = 0; i < times.length; i++) {
    const v = o.supertrend[i], dir = o.supertrend_dir[i];
    if (v == null || !dir) continue;
    const flip = i > 0 && o.supertrend_dir[i - 1] && o.supertrend_dir[i - 1] !== dir;
    stPts.push({ time: times[i], value: v, color: flip ? "rgba(0,0,0,0)" : dir > 0 ? bullC : bearC });  // hide the jump at a flip
  }
  series.st.setData(stPts);
  series.sar.setData(on("sar") ? pts(times, o.sar) : []);
  // only the most recent patterns, one per candle, so the chart stays readable
  const seen = new Set();
  const recent = (d.patterns || []).filter((p) => p.dir !== 0).reverse().filter((p) => !seen.has(p.t) && seen.add(p.t)).slice(0, 10);
  const markers = on("pat") ? recent.map((p, k) => ({
    time: tzShift(p.t), position: p.dir > 0 ? "belowBar" : "aboveBar", color: p.dir > 0 ? css("--bull") : css("--bear"),
    shape: p.dir > 0 ? "arrowUp" : "arrowDown", text: k < 4 ? p.name.replace(/^(Bullish|Bearish) /, "") : "",
  })) : [];
  series.candle.setMarkers(markers.sort((a, b) => a.time - b.time));
  drawLevels();
  if (fresh || !prevRange) {
    const n = times.length, show = { "1m": 180, "2m": 180, "5m": 160, "15m": 140, "30m": 140, "1h": 140, "3h": 120, "4h": 120, "1D": 160 }[T.tf] || 150;
    ts.setVisibleLogicalRange({ from: Math.max(0, n - show), to: n + 5 });
  } else {
    const grow = times.length - prevLen;
    if (prevRange.to >= prevLen - 3 && grow > 0) ts.setVisibleLogicalRange({ from: prevRange.from + grow, to: prevRange.to + grow });
    else ts.setVisibleLogicalRange(prevRange);
  }
  T._len = times.length;
  legend(null);
}
function drawLevels() {
  if (!series.candle) return;
  (series.priceLines || []).forEach((l) => series.candle.removePriceLine(l));
  series.priceLines = [];
  const o = T.options;
  if (!T.overlays.has("opt") || !o || !o.available) return;
  const add = (price, title, color, style = 2) => { if (isNum(price)) series.priceLines.push(series.candle.createPriceLine({ price, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title })); };
  add(o.max_pain, `Max pain ${o.expiration}`, css("--accent"), 0);
  add(o.resistance ?? o.call_wall, "Call wall", css("--bear"));
  add(o.support ?? o.put_wall, "Put wall", css("--bull"));
  add(o.expected_high, "Exp. move ↑", "#94a3b8", 3);
  add(o.expected_low, "Exp. move ↓", "#94a3b8", 3);
}
function renderToggles() {
  $("#overlayToggles").innerHTML = OVERLAYS.map(([k, n, c]) => `<button data-k="${k}" class="${T.overlays.has(k) ? "on" : ""}"><i style="background:${c}"></i>${n}</button>`).join("");
  $$("button", $("#overlayToggles")).forEach((b) => (b.onclick = () => {
    T.overlays.has(b.dataset.k) ? T.overlays.delete(b.dataset.k) : T.overlays.add(b.dataset.k);
    store.set("td-overlays", JSON.stringify([...T.overlays]));
    renderToggles(); if (T.chartData) drawChart(T.chartData, false);
  }));
}
async function loadChart() {
  const sym = T.sym, tf = T.tf, fresh = !T.chartData || T.chartData.tf !== tf || T.chartData._sym !== sym;
  if (fresh) { $("#chartMsg").classList.remove("hidden"); $("#chartMsg").textContent = `Loading ${sym} ${TF_NAME[tf]} candles…`; }
  try {
    const d = await api(`/api/trade/chart/${encodeURIComponent(sym)}/${tf}${q()}`);
    if (sym !== T.sym || tf !== T.tf) return;
    if (!d.available) throw new Error(d.reason || "No data");
    d._sym = sym;
    T.chartData = d;
    $("#chartMsg").classList.add("hidden");
    drawChart(d, fresh);
    renderWhy(); renderCats(); renderStrategies();
  } catch (e) {
    if (sym !== T.sym || tf !== T.tf) return;
    if (fresh) { $("#chartMsg").classList.remove("hidden"); $("#chartMsg").textContent = e.message; }
  }
}

/* ------------------------------------------------------------ breakdown */
function renderWhy() {
  const d = T.chartData;
  const h1 = d.hit_rate?.next_bar, h5 = d.hit_rate?.next_5_bars;
  const cats = Object.entries(d.categories).map(([k, v]) => `<div class="cb"><b><span>${esc(k)}</span><span>${num(v)}</span></b>${bar(v)}</div>`).join("");
  $("#tdWhy").innerHTML = `<div class="why-head">
      <div><div class="sub">${esc(T.sym)} on the ${TF_NAME[T.tf]} chart</div><div class="why-verdict ${vcls(d.verdict)}">${esc(d.verdict)} <span class="sub">score ${num(d.score)}</span></div>
      <div class="sub">${d.counts.bullish} bullish · ${d.counts.bearish} bearish · ${d.counts.neutral} neutral signals · TradingView-method rating: <b>${esc(d.tradingview.label)}</b> (MAs ${esc(d.tradingview.ma_label)}, oscillators ${esc(d.tradingview.osc_label)})</div></div>
      <div style="min-width:260px;flex:0 1 380px">${bar(d.score)}</div></div>
    <div class="cat-bars">${cats}</div>
    <div class="hit">${h1 ? `How reliable has this been? On the last <b>${h1.signals}</b> ${TF_NAME[T.tf]} candles with a clear signal, the direction was right <b>${Math.round(h1.accuracy * 100)}%</b> of the time for the next candle${h5 ? ` and <b>${Math.round(h5.accuracy * 100)}%</b> over the next 5 candles` : ""}. For comparison, price rose on ${Math.round(h1.up_rate * 100)}% of those candles. Around 50% means the signal is close to a coin flip on this timeframe.` : "Not enough history to measure reliability yet."}</div>`;
}
function renderCats() {
  const d = T.chartData;
  const by = {};
  d.signals.forEach((s) => (by[s.category] = by[s.category] || []).push(s));
  const label = (s) => s.category === "Strategies" ? (s.vote > 0 ? "LONG" : s.vote < 0 ? "SHORT" : "FLAT") : (s.vote > 0 ? "Bullish" : s.vote < 0 ? "Bearish" : "Neutral");
  $("#tdCats").innerHTML = Object.entries(by).map(([cat, list]) => {
    const b = list.filter((s) => s.vote > 0).length, s_ = list.filter((s) => s.vote < 0).length;
    return `<div class="card"><div class="card-head"><h2>${esc(cat)}</h2><span class="sub"><span class="pos">${b}↑</span> <span class="neg">${s_}↓</span> of ${list.length}</span></div>
      ${list.map((s) => `<div class="sig-row" title="${esc(s.rule)}"><span>${esc(s.name)}</span><span class="sv">${esc(s.value)}</span><span class="sig ${s.vote > 0 ? "b" : s.vote < 0 ? "s" : "n"}">${label(s)}</span></div>`).join("")}</div>`;
  }).join("");
}
function renderStrategies() {
  // best historical performer first – a ranking of the past, not a promise
  const rows = [...(T.chartData.strategies || [])].sort((a, b) => (b.total_return ?? -9) - (a.total_return ?? -9));
  $("#stratTable tbody").innerHTML = rows.map((s, i) => `<tr><td><b>${esc(s.name)}</b>${i === 0 && s.total_return > s.buy_hold ? ' <span class="sc-tag sc-yes" title="Highest return in the backtest on the candles loaded">Best backtest</span>' : ""}</td>
    <td><span class="sig ${s.position > 0 ? "b" : s.position < 0 ? "s" : "n"}">${s.position > 0 ? "LONG" : s.position < 0 ? "SHORT" : "FLAT"}</span></td>
    <td class="num">${s.trades}</td><td class="num">${s.win_rate != null ? Math.round(s.win_rate * 100) + "%" : "—"}</td>
    <td class="num ${s.total_return >= 0 ? "pos" : "neg"}">${pct(s.total_return)}</td><td class="num">${pct(s.buy_hold)}</td>
    <td class="sub" style="white-space:normal">${esc(s.rule)}</td></tr>`).join("");
}

/* ------------------------------------------------------------ TradingView widgets (official embeds) */
function tvEmbed(container, script, config) {
  container.innerHTML = "";
  const wrap = document.createElement("div");
  wrap.className = "tradingview-widget-container"; wrap.style.height = "100%"; wrap.style.width = "100%";
  const inner = document.createElement("div");
  inner.className = "tradingview-widget-container__widget"; inner.style.height = "calc(100% - 22px)"; inner.style.width = "100%";
  const copy = document.createElement("div");
  copy.className = "tradingview-widget-copyright sub";
  copy.innerHTML = `<a href="https://www.tradingview.com/" rel="noopener nofollow" target="_blank">Charts and ratings by TradingView</a>`;
  const s = document.createElement("script");
  s.src = `https://s3.tradingview.com/external-embedding/${script}`; s.async = true; s.type = "text/javascript";
  s.innerHTML = JSON.stringify(config);
  wrap.append(inner, copy, s);
  container.appendChild(wrap);
}
function renderTv(force) {
  if (!T.summary || !T.visible) return;
  const theme = document.documentElement.dataset.theme === "light" ? "light" : "dark";
  const sym = T.summary.tv_symbol;
  const taKey = `${sym}|${TV_TA[T.tf]}|${theme}`;
  if (force || taKey !== T.tvTaKey) {
    T.tvTaKey = taKey;
    tvEmbed($("#tvTech"), "embed-widget-technical-analysis.js", { interval: TV_TA[T.tf], width: "100%", height: "100%", isTransparent: true, symbol: sym, showIntervalTabs: true, displayMode: "single", locale: "en", colorTheme: theme });
  }
  if (T.chartTab === "tv") {
    const tvInterval = T.summary.frames[T.tf]?.tv_interval || "5";
    const key = `${sym}|${tvInterval}|${theme}`;
    if (force || key !== T.tvKey) {
      T.tvKey = key;
      tvEmbed($("#tvChart"), "embed-widget-advanced-chart.js", { autosize: true, symbol: sym, interval: tvInterval, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "Etc/UTC",
        theme, style: "1", locale: "en", allow_symbol_change: false, calendar: false, hide_volume: false, support_host: "https://www.tradingview.com",
        studies: ["STD;Supertrend", "STD;RSI", "STD;MACD"] });
    }
  }
}
$$("#chartTabs button").forEach((b) => (b.onclick = () => {
  T.chartTab = b.dataset.chart;
  $$("#chartTabs button").forEach((x) => x.classList.toggle("active", x === b));
  $("#tdChart").classList.toggle("hidden", T.chartTab !== "signal");
  $("#tvChart").classList.toggle("hidden", T.chartTab !== "tv");
  $("#overlayToggles").classList.toggle("hidden", T.chartTab !== "signal");
  $("#tdLegend").classList.toggle("hidden", T.chartTab !== "signal");
  if (T.chartTab === "tv") renderTv(); else $("#chartMsg").classList.toggle("hidden", !!T.chartData);
}));

/* ------------------------------------------------------------ options */
async function loadOptions() {
  const sym = T.sym;
  try {
    const o = await api(`/api/trade/options/${encodeURIComponent(sym)}${T.optExp ? `?expiration=${T.optExp}` : ""}`);
    if (sym !== T.sym) return;
    T.options = o; renderOptions(); drawLevels();
  } catch (e) {
    if (sym !== T.sym) return;
    T.options = { available: false, reason: e.message }; renderOptions();
  }
}
function oiChart(o) {
  const rows = o.strikes || [];
  if (rows.length < 2) return "";
  const W = 1200, H = 280, P = { l: 48, r: 10, t: 14, b: 26 };
  const max = Math.max(...rows.map((r) => Math.max(r.call_oi, r.put_oi))) || 1;
  const bw = (W - P.l - P.r) / rows.length;
  const mid = (H - P.b + P.t) / 2;
  const y = (v) => (v / max) * (mid - P.t);
  const x = (i) => P.l + i * bw;
  let svg = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Open interest by strike: calls above, puts below">`;
  svg += `<line x1="${P.l}" x2="${W - P.r}" y1="${mid}" y2="${mid}" stroke="var(--line)"/>`;
  svg += `<text x="${P.l - 6}" y="${P.t + 8}" text-anchor="end">${compact(max)}</text><text x="${P.l - 6}" y="${mid + 3}" text-anchor="end">0</text><text x="${P.l - 6}" y="${H - P.b}" text-anchor="end">${compact(max)}</text>`;
  rows.forEach((r, i) => {
    const w = Math.max(1, bw * 0.75), cx = x(i) + (bw - w) / 2;
    svg += `<rect x="${cx}" y="${mid - y(r.call_oi)}" width="${w}" height="${y(r.call_oi)}" fill="var(--bull)" opacity=".8"><title>${r.strike} calls: OI ${compact(r.call_oi)}, vol ${compact(r.call_vol)}</title></rect>`;
    svg += `<rect x="${cx}" y="${mid}" width="${w}" height="${y(r.put_oi)}" fill="var(--bear)" opacity=".8"><title>${r.strike} puts: OI ${compact(r.put_oi)}, vol ${compact(r.put_vol)}</title></rect>`;
  });
  const step = Math.ceil(rows.length / 12);
  rows.forEach((r, i) => { if (i % step === 0) svg += `<text x="${x(i) + bw / 2}" y="${H - 8}" text-anchor="middle">${r.strike}</text>`; });
  const at = (k) => { let best = 0; rows.forEach((r, i) => { if (Math.abs(r.strike - k) < Math.abs(rows[best].strike - k)) best = i; }); return x(best) + bw / 2; };
  if (isNum(o.price)) { const px = at(o.price); svg += `<line x1="${px}" x2="${px}" y1="${P.t}" y2="${H - P.b}" stroke="var(--ink)" stroke-dasharray="4 3"/><text x="${px + 4}" y="${P.t + 8}" style="fill:var(--ink)">Price ${num(o.price)}</text>`; }
  if (isNum(o.max_pain)) { const mx = at(o.max_pain); svg += `<line x1="${mx}" x2="${mx}" y1="${P.t}" y2="${H - P.b}" stroke="var(--accent)" stroke-width="2"/><text x="${mx + 4}" y="${H - P.b - 6}" style="fill:var(--accent);font-weight:700">Max pain ${o.max_pain}</text>`; }
  return svg + "</svg>";
}
function renderOptions() {
  const o = T.options;
  const box = $("#tdOptSummary"), full = $("#tdOptions");
  if (!o || !o.available) {
    const msg = esc(o?.reason || "Loading…");
    box.innerHTML = `<h2>Options</h2><p class="muted">${msg}</p>`;
    full.innerHTML = `<h2>Option chain analysis</h2><p class="muted">${msg}</p>`;
    return;
  }
  const s = o.sentiment;
  box.innerHTML = `<div class="card-head"><h2>Options · ${esc(o.expiration)}</h2><span class="chip ${vcls(s.verdict)}"><span class="dot"></span>${esc(s.verdict)}</span></div>
    <div class="opt-grid">
      ${metric("Max pain", `<span style="color:var(--accent)">${num(o.max_pain)}</span>`, isNum(o.price) ? pct(o.max_pain / o.price - 1) + " vs price" : "")}
      ${metric("Expected move", isNum(o.expected_move) ? "±" + num(o.expected_move) : "—", isNum(o.expected_low) ? `${num(o.expected_low)} – ${num(o.expected_high)}` : "")}
      ${metric("Call wall (resistance)", num(o.resistance ?? o.call_wall), "most call open interest")}
      ${metric("Put wall (support)", num(o.support ?? o.put_wall), "most put open interest")}
      ${metric("Put/call volume", num(o.pcr_volume), `OI ratio ${num(o.pcr_oi)}`)}
      ${metric("ATM implied vol.", isNum(o.atm_iv) ? pct(o.atm_iv, 1, false) : "—", `${o.days} day(s) to expiry`)}
    </div>
    <p class="sub" style="margin-top:8px"><a target="_blank" rel="noopener" href="https://optioncharts.io/options/${esc(T.sym.split(".")[0])}/max-pain">Compare on OptionCharts ↗</a></p>`;
  full.innerHTML = `<div class="card-head"><h2>Option chain analysis</h2>
      <label class="filters sub">Expiration <select id="optExp">${o.expirations.map((e) => `<option ${e === o.expiration ? "selected" : ""}>${esc(e)}</option>`).join("")}</select></label></div>
    <div class="metrics" style="margin:10px 0">
      ${s.votes.map((v) => metric(esc(v.name), `<span class="sig ${v.vote > 0 ? "b" : v.vote < 0 ? "s" : "n"}">${v.vote > 0 ? "Bullish" : v.vote < 0 ? "Bearish" : "Neutral"}</span>`, esc(v.value))).join("")}
      ${metric("Open interest", compact(o.call_oi + o.put_oi), `${compact(o.call_oi)} calls · ${compact(o.put_oi)} puts`)}
      ${metric("Volume today", compact(o.call_volume + o.put_volume), `${compact(o.call_volume)} calls · ${compact(o.put_volume)} puts`)}
    </div>
    ${oiChart(o)}
    <div class="legend"><span><i style="background:var(--bull)"></i>Call open interest (above)</span><span><i style="background:var(--bear)"></i>Put open interest (below)</span><span>Big call clusters often act as resistance, big put clusters as support.</span></div>
    ${o.unusual.length ? `<h3>Unusual activity (volume above open interest)</h3><div class="table-wrap"><table class="grid"><thead><tr><th>Side</th><th class="num">Strike</th><th class="num">Volume</th><th class="num">Open interest</th><th class="num">Vol / OI</th><th class="num">IV</th></tr></thead><tbody>
      ${o.unusual.map((u) => `<tr><td><span class="sig ${u.side === "Call" ? "b" : "s"}">${u.side}</span></td><td class="num">${num(u.strike)}</td><td class="num">${compact(u.volume)}</td><td class="num">${compact(u.oi)}</td><td class="num">${num(u.ratio, 1)}×</td><td class="num">${isNum(u.iv) ? pct(u.iv, 0, false) : "—"}</td></tr>`).join("")}</tbody></table></div>` : ""}
    <p class="sub">From Yahoo option chains, refreshed every minute. Max pain uses OptionCharts' published method. The expected move comes from the at-the-money straddle price.</p>`;
  $("#optExp").onchange = (e) => { T.optExp = e.target.value; loadOptions(); };
}

/* ------------------------------------------------------------ polling */
function clearTimers() { Object.values(T.timers).forEach(clearTimeout); T.timers = {}; }
function loop(name, fn, seconds) {
  const run = async () => {
    if (T.visible && !document.hidden) await fn();
    T.timers[name] = setTimeout(run, (typeof seconds === "function" ? seconds() : seconds) * 1000);
  };
  run();
}
function refreshAll() {
  clearTimers();
  if (!T.visible) return;
  loop("summary", loadSummary, 15);
  loop("chart", loadChart, () => TF_POLL[T.tf] || 30);
  loop("options", loadOptions, 60);
}

window.addEventListener("va:view", (e) => {
  const was = T.visible;
  T.visible = e.detail === "trade";
  if (T.visible && !was) { renderToggles(); loadWatch(); if (!T.summary) setSymbol(T.sym); else refreshAll(); setTimeout(() => chart && chart.timeScale(), 50); }
  if (!T.visible) clearTimers();
  if (e.detail === "scan") startScan(); else stopScan();
});
window.addEventListener("va:theme", () => {
  if (!T.visible) return;
  setTimeout(() => { makeChart(); if (T.chartData) drawChart(T.chartData, true); renderTv(true); }, 0);
});
document.addEventListener("visibilitychange", () => { if (!document.hidden && T.visible) refreshAll(); });

/* ------------------------------------------------------------ scanner */
const S2 = { timer: null, busy: false };
const shortV = (v) => ({ "Strong Bullish": "Strong ▲", Bullish: "Bullish", Neutral: "Neutral", Bearish: "Bearish", "Strong Bearish": "Strong ▼" }[v] || "—");
async function runScan() {
  if (S2.busy) return;
  S2.busy = true; $("#scanBtn").disabled = true; $("#scanBtn").textContent = "Scanning…";
  try {
    const d = await api(`/api/trade/scan${T.prepost ? "?prepost=1" : ""}`);
    $("#scanEmpty").classList.toggle("hidden", d.rows.length > 0);
    $("#scanEmpty").innerHTML = "<b>Your watchlist is empty</b>Add tickers above or from the trade desk.";
    $("#scanTable tbody").innerHTML = d.rows.map((r) => {
      if (r.error) return `<tr><td><b>${esc(r.symbol)}</b></td><td colspan="11" class="neg">${esc(r.error)}</td><td><button class="btn small ghost" data-rm="${esc(r.symbol)}">Remove</button></td></tr>`;
      const qt = r.quote || {};
      return `<tr class="clickable" data-sym="${esc(r.symbol)}"><td><div class="stock-cell"><b>${esc(r.symbol)}</b><span>${esc(r.name || "")}</span></div></td>
        <td class="num">${num(qt.price)}</td><td class="num ${(qt.change || 0) >= 0 ? "pos" : "neg"}">${pct(qt.change_pct, 2)}</td>
        ${TFS.map((tf) => { const f = r.frames[tf] || {}; return `<td><span class="vchip ${vcls(f.verdict)} ${/Strong/.test(f.verdict || "") ? "strong" : ""}" title="${tf}: score ${num(f.score)}">${shortV(f.verdict)}</span></td>`; }).join("")}
        <td><button class="btn small ghost" data-rm="${esc(r.symbol)}">Remove</button></td></tr>`;
    }).join("");
    $$("#scanTable tr[data-sym]").forEach((tr) => (tr.onclick = (e) => { if (e.target.dataset.rm) return; setSymbol(tr.dataset.sym); showView("trade"); }));
    $$("#scanTable [data-rm]").forEach((b) => (b.onclick = async (e) => { e.stopPropagation(); await saveWatch(T.watch.filter((x) => x !== b.dataset.rm)); runScan(); }));
    $("#scanTime").textContent = `Updated ${new Date(d.generated * 1000).toLocaleTimeString()}`;
  } catch (e) { toast(e.message); }
  S2.busy = false; $("#scanBtn").disabled = false; $("#scanBtn").textContent = "Scan now";
}
function startScan() { stopScan(); loadWatch(); runScan(); S2.timer = setInterval(() => { if ($("#scanAuto").checked && !document.hidden) runScan(); }, 60000); }
function stopScan() { if (S2.timer) clearInterval(S2.timer); S2.timer = null; }
$("#scanBtn").onclick = runScan;
const addScan = async () => {
  const s = $("#scanAdd").value.trim().toUpperCase();
  if (!/^\^?[A-Z0-9][A-Z0-9.\-=&]{0,24}$/.test(s)) { toast("Type a ticker like AAPL or SAP.DE"); return; }
  await loadWatch(); await saveWatch([...T.watch, s]); $("#scanAdd").value = ""; runScan();
};
$("#scanAddBtn").onclick = addScan;
$("#scanAdd").addEventListener("keydown", (e) => { if (e.key === "Enter") addScan(); });

window.openTradeDesk = (sym) => { showView("trade"); setTimeout(() => setSymbol(sym), 0); };
