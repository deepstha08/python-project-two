"""My stocks: the user's own portfolio + watchlist, always checked.

Every stock is rated Undervalued / Neutral / Overvalued from its P/E, forward P/E and PEG:
  1. each ratio is compared with the stock's peers from the daily market scan (same region and,
     when there are at least five, same sector) -> a 0–100 "cheapness" percentile;
  2. the PEG is also judged on its own (below 1 = cheap for its growth, above 2 = expensive).
ETFs and commodity ETCs have no company earnings, so they are reported as "Not rated" with
their trend instead.
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
from datetime import date

from . import storage
from .core import number, positive

log = logging.getLogger("valueatlas")

# The user's Scalable Capital portfolio and watchlist (from their screenshots, 7 Oct 2026).
# alts    = fallback Yahoo symbols (funds listed on several exchanges)
# options = US-listed share/ADR whose options are used for max pain when the home listing has none
def _s(symbol, name, lst, alts=(), options=None):
    return {"symbol": symbol, "name": name, "list": lst, "alts": list(alts), **({"options": options} if options else {})}


DEFAULT_STOCKS = [
    # Portfolio
    _s("AAPL", "Apple", "Portfolio"), _s("AZN.L", "AstraZeneca", "Portfolio", options="AZN"),
    _s("DHL.DE", "DHL Group", "Portfolio"), _s("IBM", "IBM", "Portfolio"),
    _s("SSLN.MI", "iShares Physical Silver ETC", "Portfolio", ["PPFD.SG", "ISLN.L"]),
    _s("MC.PA", "LVMH", "Portfolio"), _s("MU", "Micron Technology", "Portfolio"),
    _s("NOVO-B.CO", "Novo Nordisk B", "Portfolio", options="NVO"), _s("NVDA", "NVIDIA", "Portfolio"),
    _s("CI", "The Cigna Group", "Portfolio"), _s("TTE.PA", "TotalEnergies", "Portfolio", options="TTE"),
    # Watchlist (A–Z as in the Scalable app)
    _s("ADS.DE", "adidas", "Watchlist"), _s("ADBE", "Adobe", "Watchlist"), _s("AMD", "Advanced Micro Devices", "Watchlist"),
    _s("AIR.PA", "Airbus", "Watchlist"), _s("BABA", "Alibaba Group ADR", "Watchlist"), _s("ALV.DE", "Allianz", "Watchlist"),
    _s("GOOGL", "Alphabet A", "Watchlist"), _s("AMZN", "Amazon.com", "Watchlist"),
    _s("GLDA.DE", "Amundi Physical Gold ETC", "Watchlist", ["GLDA.PA"]),
    _s("ASML.AS", "ASML Holding", "Watchlist", options="ASML"), _s("9888.HK", "Baidu A", "Watchlist", options="BIDU"),
    _s("B", "Barrick Mining", "Watchlist"), _s("BRK-B", "Berkshire Hathaway B", "Watchlist"), _s("AVGO", "Broadcom", "Watchlist"),
    _s("CVX", "Chevron", "Watchlist"), _s("KO", "Coca-Cola", "Watchlist"), _s("CBK.DE", "Commerzbank", "Watchlist"),
    _s("QBTS", "D-Wave Quantum", "Watchlist"), _s("DBK.DE", "Deutsche Bank", "Watchlist", options="DB"),
    _s("DTE.DE", "Deutsche Telekom", "Watchlist"), _s("LLY", "Eli Lilly", "Watchlist"),
    _s("REXC.L", "HANetf Sprott Rare Earths ex-China (Acc)", "Watchlist", ["REXC.MI", "REXC.DE"]),
    _s("RMS.PA", "Hermès International", "Watchlist"), _s("INTC", "Intel", "Watchlist"),
    _s("CBUM.DE", "iShares S&P 500 Scored & Screened EUR Hedged (Acc)", "Watchlist"), _s("JPM", "JPMorgan Chase", "Watchlist"),
    _s("OR.PA", "L'Oréal", "Watchlist"), _s("MCD", "McDonald's", "Watchlist"), _s("META", "Meta Platforms A", "Watchlist"),
    _s("MSFT", "Microsoft", "Watchlist"), _s("MP", "MP Materials", "Watchlist"), _s("MUV2.DE", "Münchener Rück", "Watchlist"),
    _s("9999.HK", "NetEase", "Watchlist", options="NTES"), _s("ORCL", "Oracle", "Watchlist"),
    _s("PLTR", "Palantir Technologies", "Watchlist"), _s("PUIG.MC", "Puig Brands", "Watchlist"),
    _s("RHM.DE", "Rheinmetall", "Watchlist"),
    _s("005935.KS", "Samsung Electronics Pref. (GDR SMSD)", "Watchlist", ["SMSD.L"]),
    _s("SNDK", "Sandisk", "Watchlist"), _s("SAP.DE", "SAP", "Watchlist", options="SAP"), _s("SIE.DE", "Siemens", "Watchlist"),
    _s("SPCX", "SpaceX", "Watchlist"), _s("SPOT", "Spotify Technology", "Watchlist"),
    _s("TSM", "Taiwan Semiconductor (TSMC) ADR", "Watchlist"), _s("TTWO", "Take-Two Interactive", "Watchlist"),
    _s("0700.HK", "Tencent Holdings", "Watchlist"), _s("TSLA", "Tesla", "Watchlist"),
    _s("7203.T", "Toyota Motor", "Watchlist", options="TM"), _s("TM", "Toyota Motor ADR", "Watchlist"),
    _s("UCG.MI", "UniCredit", "Watchlist"), _s("UNA.AS", "Unilever", "Watchlist", options="UL"),
    _s("UNH", "UnitedHealth Group", "Watchlist"), _s("WMT", "Walmart", "Watchlist"),
    _s("1810.HK", "Xiaomi", "Watchlist"), _s("ZAL.DE", "Zalando", "Watchlist"),
]
SEED_VERSION = 2

UNDER, NEUTRAL, OVER, NOT_RATED = "Undervalued", "Neutral", "Overvalued", "Not rated"
_lock = threading.Lock()


# --------------------------------------------------------------------------- list storage

def _path():
    return storage.DATA / "mystocks.json"


def load_list():
    items = None
    try:
        items = json.loads(_path().read_text(encoding="utf-8"))
        if not isinstance(items, list):
            items = None
    except (OSError, ValueError):
        pass
    seed_file = storage.DATA / "mystocks_seed.txt"
    try:
        seeded = int(seed_file.read_text().strip())
    except (OSError, ValueError):
        seeded = 1 if items is not None else 0
    if items is None or seeded < SEED_VERSION:
        # first start, or an update shipped new stocks: add any missing default entries (never removes yours)
        items = items or []
        have = {it["symbol"]: it for it in items}
        for d in DEFAULT_STOCKS:
            if d["symbol"] in have:
                for key in ("options", "alts"):
                    if d.get(key) and not have[d["symbol"]].get(key):
                        have[d["symbol"]][key] = d[key]
            else:
                items.append(dict(d))
        save_list(items)
        storage.DATA.mkdir(parents=True, exist_ok=True)
        seed_file.write_text(str(SEED_VERSION))
    return items


def save_list(items):
    storage.DATA.mkdir(parents=True, exist_ok=True)
    _path().write_text(json.dumps(items, indent=1), encoding="utf-8")
    return items


def add_stock(symbol, name="", list_name="Watchlist"):
    symbol = symbol.strip().upper()
    if not storage.SYMBOL_RE.fullmatch(symbol):
        raise ValueError("Use a Yahoo Finance ticker, e.g. AAPL, SAP.DE, MC.PA, 7203.T")
    list_name = "Portfolio" if str(list_name).lower().startswith("p") else "Watchlist"
    with _lock:
        items = load_list()
        for it in items:
            if it["symbol"] == symbol:
                it["list"] = list_name
                if name:
                    it["name"] = name
                break
        else:
            items.append({"symbol": symbol, "name": name or symbol, "list": list_name, "alts": []})
        save_list(items)
    return items


def remove_stock(symbol):
    with _lock:
        items = [it for it in load_list() if it["symbol"] != symbol.upper()]
        save_list(items)
        latest = load_latest()
        latest["items"] = [r for r in latest.get("items", []) if r["symbol"] != symbol.upper()]
        _save_latest(latest)
    return items


def load_latest():
    try:
        return json.loads((storage.DATA / "mystocks_latest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"updated": None, "items": []}


def _save_latest(data):
    (storage.DATA / "mystocks_latest.json").write_text(json.dumps(data, default=str), encoding="utf-8")


def _save_history(items):
    day = date.today().isoformat()
    with storage.db_session() as db:
        db.execute("CREATE TABLE IF NOT EXISTS mystock_history (day TEXT, symbol TEXT, verdict TEXT, score REAL, "
                   "pe REAL, forward_pe REAL, peg REAL, price REAL, PRIMARY KEY(day, symbol))")
        for r in items:
            db.execute("INSERT OR REPLACE INTO mystock_history VALUES(?,?,?,?,?,?,?,?)",
                       (day, r["symbol"], r.get("verdict"), r.get("value_score"), r.get("pe"), r.get("forward_pe"),
                        r.get("peg"), r.get("price")))


def history(symbol, limit=60):
    with storage.db_session() as db:
        db.execute("CREATE TABLE IF NOT EXISTS mystock_history (day TEXT, symbol TEXT, verdict TEXT, score REAL, "
                   "pe REAL, forward_pe REAL, peg REAL, price REAL, PRIMARY KEY(day, symbol))")
        rows = db.execute("SELECT day, verdict, score, pe, forward_pe, peg, price FROM mystock_history WHERE symbol=? "
                          "ORDER BY day DESC LIMIT ?", (symbol, limit)).fetchall()
    return [dict(zip(("day", "verdict", "score", "pe", "forward_pe", "peg", "price"), r)) for r in rows]


# --------------------------------------------------------------------------- valuation

def _cheapness(value, peer_values):
    """Share of peers that are MORE expensive (higher ratio) – 100 = cheapest."""
    vals = [v for v in peer_values if v is not None]
    if value is None or len(vals) < 3:
        return None
    greater = sum(v > value for v in vals)
    equal = sum(v == value for v in vals)
    return 100.0 * (greater + equal / 2) / len(vals)


def value_verdict(row, peers):
    """Return dict(verdict, value_score, metric_scores, peer_basis, peer_count, reasons)."""
    pe, fpe, peg = positive(row.get("pe")), positive(row.get("forward_pe")), positive(row.get("peg"))
    if row.get("kind") == "fund":
        return {"verdict": NOT_RATED, "reasons": ["ETF/ETC: no company earnings, so P/E, forward P/E and PEG do not apply. "
                                                  "See the trend instead."]}
    if pe is None and fpe is None:
        return {"verdict": NOT_RATED, "reasons": ["No positive earnings (loss-making or no forecast) – P/E based valuation "
                                                  "is not meaningful."]}
    region, sector = row.get("region"), row.get("sector")
    same_region = [p for p in peers if p.get("region") == region and not p.get("error")]
    same_sector = [p for p in same_region if p.get("sector") == sector and sector not in (None, "", "Unknown")]
    group = same_sector if len(same_sector) >= 5 else same_region
    basis = f"{region} · {sector}" if group is same_sector and same_sector else f"all {region}"
    scores, reasons = {}, []
    labels = {"pe": "P/E", "forward_pe": "forward P/E", "peg": "PEG"}
    for key, val in (("pe", pe), ("forward_pe", fpe), ("peg", peg)):
        pct = _cheapness(val, [positive(p.get(key)) for p in group if p["symbol"] != row["symbol"]])
        if pct is not None:
            scores[key] = pct
            reasons.append(f"{labels[key]} {val:.1f} is cheaper than {pct:.0f}% of {basis} peers")
    if not scores:  # no peer data yet: fall back to simple absolute rules
        basis = "rule of thumb (no peer data yet)"
        for key, val, cheap, dear in (("pe", pe, 15, 30), ("forward_pe", fpe, 13, 27), ("peg", peg, 1, 2)):
            if val is not None:
                scores[key] = max(0.0, min(100.0, 100 * (dear - val) / (dear - cheap) * 0.6 + 20))
        reasons.append("Compared with simple rules of thumb – run 'Update screener' once for peer comparisons.")
    peer_score = sum(scores.values()) / len(scores)
    peg_score = None
    if peg is not None:
        peg_score = max(0.0, min(100.0, 100 * (2.5 - peg) / 2.0))  # 0.5 -> 100, 1.5 -> 50, 2.5 -> 0
        reasons.append(f"PEG {peg:.2f}: " + ("below 1 – cheap for its expected growth" if peg < 1 else
                                              "above 2 – expensive for its expected growth" if peg > 2 else
                                              "between 1 and 2 – fairly priced for its growth"))
    elif row.get("peg_field") is None:
        reasons.append("No PEG available (no growth forecast).")
    value_score = peer_score if peg_score is None else 0.65 * peer_score + 0.35 * peg_score
    if pe and fpe and fpe > pe * 1.35:
        reasons.append("Forward P/E is well above trailing P/E – analysts expect earnings to fall.")
        value_score -= 8
    verdict = UNDER if value_score >= 60 else OVER if value_score <= 40 else NEUTRAL
    return {"verdict": verdict, "value_score": round(value_score, 1), "metric_scores": scores,
            "peer_basis": basis, "peer_count": len(group), "reasons": reasons}


# --------------------------------------------------------------------------- fetching

def _fund_row(yf, symbol, entry):
    info = yf.Ticker(symbol).get_info() or {}
    if not (info.get("symbol") or info.get("shortName") or info.get("regularMarketPrice")):
        raise ValueError("No data")
    price = positive(info.get("regularMarketPrice")) or positive(info.get("navPrice")) or positive(info.get("previousClose"))
    return {"symbol": symbol, "name": entry.get("name") or info.get("shortName") or symbol, "kind": "fund",
            "quote_type": info.get("quoteType"), "currency": info.get("currency"), "price": price,
            "pe": positive(info.get("trailingPE")), "forward_pe": None, "peg": None,
            "change_52w": number(info.get("52WeekChange")) or number(info.get("ytdReturn")),
            "sector": "Fund", "error": ""}


def check_one(provider, entry, peers, guess_region, gettex=None):
    """Fetch and rate one entry; tries fallback symbols for funds."""
    from .indicators import technical_rating
    from . import scalable
    last_err = None
    for sym in [entry["symbol"], *entry.get("alts", [])]:
        region = guess_region(sym)
        try:
            try:
                row = provider.fundamentals(sym, region)
                row["kind"] = "stock"
            except ValueError as exc:
                if "Not a common stock" not in str(exc):
                    raise
                row = _fund_row(provider.yf, sym, entry)
                row["region"] = region
            row["listed_symbol"] = sym
            if not positive(row.get("peg")) and positive(row.get("peg_estimate")) \
                    and storage.load_settings().get("estimate_peg", True):
                row["peg"], row["peg_field"] = row["peg_estimate"], "estimated"
            break
        except Exception as exc:
            last_err = exc
    else:
        return {"symbol": entry["symbol"], "name": entry.get("name"), "list": entry.get("list"), "verdict": NOT_RATED,
                "error": f"No Yahoo data for {entry['symbol']} – edit the ticker. ({str(last_err)[:120]})",
                "reasons": [], "checked": storage.now()}
    row.update({"symbol": entry["symbol"], "list": entry.get("list"), "name": entry.get("name") or row.get("name")})
    try:
        row["technical"] = technical_rating(provider.history(row["listed_symbol"]))
    except Exception as exc:
        row["technical"] = {"available": False, "reason": str(exc)[:120]}
    # Max pain for the nearest option expiry dates (saved to the Max pain history too)
    try:
        from .engine import options_for
        n = int(storage.load_settings().get("my_stocks_expirations", 4))
        opt_sym = entry.get("options") or row["listed_symbol"]
        row["options_symbol"] = opt_sym
        row["options"] = options_for(provider, opt_sym, row.get("price") if opt_sym == row["listed_symbol"] else None,
                                     n, date.today().isoformat())
    except Exception as exc:
        row["options"] = {"available": False, "reason": f"Options unavailable: {str(exc)[:120]}"}
    if row.get("isin") is not None:
        row["scalable"] = scalable.status(row.get("isin"), gettex, row.get("isin_verified", True))
    row.update(value_verdict(row, peers))
    row["checked"] = storage.now()
    return row


class Checker:
    """Background checker with progress, used by the page and the daily schedule."""

    def __init__(self):
        self.thread = None
        self.state = {"running": False, "done": 0, "total": 0, "error": None}

    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, provider_factory, peers_fn, guess_region, symbols=None):
        if self.busy():
            return False

        def work():
            try:
                self.run(provider_factory(), peers_fn(), guess_region, symbols)
            except Exception as exc:
                log.exception("my stocks check failed")
                self.state["error"] = str(exc)[:200]
            finally:
                self.state["running"] = False

        self.state = {"running": True, "done": 0, "total": 0, "error": None}
        self.thread = threading.Thread(target=work, name="mystocks", daemon=True)
        self.thread.start()
        return True

    def run(self, provider, peers, guess_region, symbols=None, workers=3):
        from concurrent.futures import ThreadPoolExecutor
        from . import scalable
        entries = [e for e in load_list() if not symbols or e["symbol"] in symbols]
        self.state.update(total=len(entries), done=0)
        gettex, _ = scalable.load_list()
        results = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for r in pool.map(lambda e: check_one(provider, e, peers, guess_region, gettex), entries):
                results.append(r)
                self.state["done"] += 1
        with _lock:
            latest = load_latest()
            prev = {r["symbol"]: r for r in latest.get("items", [])}
            for r in results:
                old = prev.get(r["symbol"])
                if old and old.get("verdict") and old.get("verdict") != r.get("verdict"):
                    r["previous_verdict"] = old["verdict"]
                elif old and old.get("previous_verdict") and old.get("verdict") == r.get("verdict"):
                    r["previous_verdict"] = old["previous_verdict"]
                prev[r["symbol"]] = r
            order = [e["symbol"] for e in load_list()]
            items = [prev[s] for s in order if s in prev]
            latest = {"updated": storage.now(), "day": date.today().isoformat(), "items": items}
            _save_latest(latest)
        _save_history([r for r in results if not r.get("error")])
        return results
