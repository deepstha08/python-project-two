"""HTTP routes for the live trading desk."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, jsonify, request

from . import storage
from .options_live import analyse_chain
from .provider import retry
from .trading import ORDER, LiveData, load_watchlist, save_watchlist

bp = Blueprint("trade", __name__, url_prefix="/api/trade")
LIVE = LiveData()
_opt_cache = {}
_scan_pool = ThreadPoolExecutor(max_workers=3)


def _symbol(raw):
    s = (raw or "").strip().upper()
    if not storage.SYMBOL_RE.fullmatch(s):
        raise ValueError("Type a valid ticker, e.g. AAPL, TSLA, SAP.DE, 7203.T, ^GSPC")
    return s


def _prepost():
    return (request.args.get("prepost") or "").lower() in ("1", "true", "yes")


def _err(exc, code=502):
    return jsonify(error=str(exc)[:300]), code


@bp.get("/summary/<path:symbol>")
def summary(symbol):
    try:
        sym = _symbol(symbol)
    except ValueError as exc:
        return _err(exc, 400)
    try:
        data = LIVE.summary(sym, _prepost())
    except Exception as exc:
        return _err(f"Could not load {sym}: {exc}")
    if not any(f.get("available") for f in data["frames"].values()):
        return _err(f"No price data for {sym}. Either the ticker is wrong (use Yahoo format, e.g. SAP.DE, 7203.T) "
                    "or Yahoo Finance can't be reached right now – check your internet connection.", 404)
    return jsonify(data)


@bp.get("/chart/<path:symbol>/<tf>")
def chart(symbol, tf):
    if tf not in ORDER:
        return _err("Unknown timeframe", 400)
    try:
        sym = _symbol(symbol)
        return jsonify(LIVE.analyse_tf(sym, tf, detail=True, prepost=_prepost()))
    except ValueError as exc:
        return _err(exc, 400)
    except Exception as exc:
        return _err(exc)


@bp.get("/options/<path:symbol>")
def options(symbol):
    try:
        sym = _symbol(symbol)
    except ValueError as exc:
        return _err(exc, 400)
    exp = request.args.get("expiration") or ""
    key = (sym, exp)
    hit = _opt_cache.get(key)
    if hit and time.time() - hit[0] < 60:
        return jsonify(hit[1])
    try:
        ticker = LIVE.yf.Ticker(sym)
        dates = list(retry(lambda: ticker.options) or [])
        if not dates:
            return jsonify(available=False, reason=f"No listed options for {sym} on Yahoo Finance.", expirations=[])
        exp = exp if exp in dates else dates[0]
        chain = retry(ticker.option_chain, exp)
        und = getattr(chain, "underlying", None) or {}
        price = und.get("regularMarketPrice") if isinstance(und, dict) else None
        if not price:
            try:
                price = LIVE.analyse_tf(sym, "1m")["price"]
            except Exception:
                price = None
        result = {"available": True, "expirations": dates, **analyse_chain(chain.calls, chain.puts, price, exp)}
    except Exception as exc:
        return _err(f"Options unavailable: {exc}")
    _opt_cache[key] = (time.time(), result)
    return jsonify(result)


@bp.get("/search")
def search():
    q = (request.args.get("q") or "").strip()
    if len(q) < 1:
        return jsonify(results=[])
    try:
        res = LIVE.yf.Search(q, max_results=8, news_count=0, lists_count=0, recommended=0, raise_errors=False, timeout=8)
        quotes = res.quotes or []
    except Exception as exc:
        return jsonify(results=[], error=str(exc)[:120])
    out = []
    for item in quotes:
        if item.get("quoteType") not in ("EQUITY", "ETF", "INDEX", "FUTURE", "CRYPTOCURRENCY", "CURRENCY", "MUTUALFUND"):
            continue
        out.append({"symbol": item.get("symbol"), "name": item.get("shortname") or item.get("longname") or "",
                    "exchange": item.get("exchDisp") or item.get("exchange") or "", "type": item.get("typeDisp") or item.get("quoteType")})
    return jsonify(results=out)


@bp.route("/watchlist", methods=["GET", "POST"])
def watchlist():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        return jsonify(symbols=save_watchlist(data.get("symbols") or []))
    return jsonify(symbols=load_watchlist())


@bp.get("/scan")
def scan():
    symbols = load_watchlist()
    pre = _prepost()

    def one(sym):
        try:
            s = LIVE.summary(sym, pre)
            if not any(f.get("available") for f in s["frames"].values()):
                return {"symbol": sym, "error": "No price data – check the ticker (Yahoo format, e.g. SAP.DE)."}
            return {"symbol": sym, "name": s["name"], "quote": s["quote"], "groups": s["groups"],
                    "frames": {tf: {"verdict": f.get("verdict"), "score": f.get("score")} for tf, f in s["frames"].items()}}
        except Exception as exc:
            return {"symbol": sym, "error": str(exc)[:160]}

    results = list(_scan_pool.map(one, symbols))
    return jsonify(rows=results, generated=int(time.time()))
