"""Local web server: JSON API + the single-page dashboard. Listens on 127.0.0.1 only."""
from __future__ import annotations

import csv
import io
import logging
import threading
import time
from datetime import date, datetime, timedelta

from flask import Flask, Response, jsonify, request, send_from_directory

from . import __version__, storage
from .core import REGIONS, positive, rank_stocks, safe_csv, top_by_region, valid_date
from .engine import Progress, analyse, expiration_max_pain, ranking_options, refresh
from .provider import FinnhubProvider, YahooProvider

log = logging.getLogger("valueatlas")
STATIC = storage.APP_DIR / "valueatlas" / "static"


class State:
    def __init__(self):
        self.progress = Progress()
        self.thread = None
        self.run = storage.latest_run()
        self.rank_cache = {}
        self.extra = {}          # on-demand analyses for stocks outside the stored run
        self.full_rank, self.full_rank_key = [], None
        from .mystocks import Checker
        self.my = Checker()
        self.last_error = None
        self.last_attempt = None
        self._provider = None

    def provider(self):
        if self._provider is None:
            self._provider = YahooProvider()
        return self._provider

    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def start_refresh(self, reason="manual"):
        if self.busy():
            return False
        self.progress = Progress()
        self.last_attempt = time.time()

        def work():
            try:
                log.info("refresh started (%s)", reason)
                refresh(self.progress, self.provider())
                self.run = storage.latest_run()
                self.rank_cache.clear()
                self.last_error = None
                log.info("refresh finished")
                check_my_stocks()  # re-rate My stocks against the fresh peer data
            except Exception as exc:
                self.last_error = str(exc)
                self.progress.update(stage="Failed", message=f"Refresh failed: {exc}")
                log.exception("refresh failed")
            finally:
                self.progress.update(running=False)

        self.thread = threading.Thread(target=work, name="refresh", daemon=True)
        self.thread.start()
        return True


STATE = State()


def scheduled_today(settings):
    hh, mm = (int(x) for x in settings["refresh_time"].split(":"))
    return datetime.now().replace(hour=hh, minute=mm, second=0, microsecond=0)


def last_finished_local():
    run = STATE.run
    if not run:
        return None
    return datetime.fromisoformat(run["finished"]).astimezone().replace(tzinfo=None)


def next_refresh(settings):
    if not settings["auto_refresh"]:
        return None
    target = scheduled_today(settings)
    last = last_finished_local()
    if last and last >= target:
        target += timedelta(days=1)
    return target


def check_my_stocks(symbols=None):
    return STATE.my.start(STATE.provider, lambda: (STATE.run or {}).get("rows") or [], guess_region, symbols)


def scheduler_loop():
    """Refresh once a day at the configured local time; catch up if the PC was off."""
    time.sleep(5)
    while True:
        try:
            settings = storage.load_settings()
            if settings["auto_refresh"] and not STATE.busy():
                due = next_refresh(settings)
                retry_ok = STATE.last_attempt is None or time.time() - STATE.last_attempt > 3600
                if STATE.run is None and STATE.last_attempt is None:
                    STATE.start_refresh("first start")
                elif due and datetime.now() >= due and retry_ok:
                    STATE.start_refresh("daily schedule")
            # My stocks are always checked: at least once per day, independent of the screener
            from .mystocks import load_latest
            first = load_latest().get("day") is None  # never checked: don't wait for the screener
            if (first or not STATE.busy()) and not STATE.my.busy() and load_latest().get("day") != date.today().isoformat() \
                    and time.time() - getattr(STATE, "my_attempt", 0) > 1800:
                STATE.my_attempt = time.time()
                check_my_stocks()
        except Exception:
            log.exception("scheduler error")
        time.sleep(30)


# --------------------------------------------------------------------------- helpers

def ranked(sector=None, exclude_flagged=None, weights=None, scalable_only=None):
    settings = storage.load_settings()
    over = {}
    if scalable_only is not None:
        over["scalable_only"] = scalable_only
    if sector is not None:
        over["sector_relative"] = sector
    if exclude_flagged is not None:
        over["exclude_flagged"] = exclude_flagged
    if weights:
        over["weights"] = weights
    opts = ranking_options(settings, **over)
    key = repr(sorted((k, repr(v)) for k, v in opts.items()))
    if key not in STATE.rank_cache:
        rows = (STATE.run or {}).get("rows") or []
        STATE.rank_cache = {key: top_by_region(rank_stocks(rows, **opts), 25)}
    return STATE.rank_cache[key], opts


def analysis_for(symbol):
    if symbol in STATE.extra:
        return STATE.extra[symbol]
    for r in (STATE.run or {}).get("rows") or []:
        if r["symbol"] == symbol:
            return r.get("analysis")
    return None


def slim(row, prev):
    a = row.get("analysis") or STATE.extra.get(row["symbol"]) or {}
    tech = a.get("technical") or {}
    opt = a.get("options") or {}
    sent = a.get("sentiment") or {}
    previous = prev.get((row["region"], row["symbol"]))
    return {
        **{k: row.get(k) for k in ("symbol", "name", "region", "sector", "industry", "country", "currency", "price",
                                   "market_cap", "pe", "forward_pe", "peg", "peg_field", "score", "rank",
                                   "metric_scores", "peer_basis", "peer_count", "flags", "recommendation_key",
                                   "recommendation_mean", "analyst_count", "target_mean", "dividend_yield",
                                   "currency_note", "earnings_growth", "isin")},
        "scalable": row.get("scalable") or "unchecked",
        "upside": (row["target_mean"] / row["price"] - 1) if positive(row.get("target_mean")) and positive(row.get("price")) else None,
        "verdict": sent.get("verdict"), "sentiment_score": sent.get("score"), "agree": sent.get("agree"),
        "tv_label": tech.get("rating_label"), "tv_rating": tech.get("rating"), "spark": tech.get("spark"),
        "return_3m": tech.get("return_3m"),
        "max_pain": (opt.get("nearest") or {}).get("max_pain") if opt.get("available") else None,
        "max_pain_exp": (opt.get("nearest") or {}).get("expiration") if opt.get("available") else None,
        "max_pain_dist": (opt.get("nearest") or {}).get("distance") if opt.get("available") else None,
        "has_options": bool(opt.get("available")), "analysed": bool(a),
        "prev_rank": previous, "is_new": prev != {} and previous is None,
    }


def external_links(symbol, name="", exchange=""):
    """Research links for one stock on other websites, grouped for the UI."""
    from urllib.parse import quote
    from .trading import tradingview_symbol
    us = "." not in symbol and not symbol.startswith("^")
    base = symbol.split(".")[0]
    tv = tradingview_symbol(symbol, exchange)
    tv_path = tv.replace(":", "-")
    qs = quote(base)
    links = [
        ("Prices & valuation", "Yahoo Finance", f"https://finance.yahoo.com/quote/{quote(symbol)}/key-statistics"),
        ("Prices & valuation", "Investing.com", f"https://www.investing.com/search/?q={qs}"),
        ("Prices & valuation", "Google Finance", f"https://www.google.com/finance?q={quote(symbol)}"),
        ("Prices & valuation", "Morningstar", f"https://www.morningstar.com/search?query={qs}"),
        ("Prices & valuation", "StockAnalysis", f"https://stockanalysis.com/stocks/{base.lower()}/statistics/" if us else None),
        ("Prices & valuation", "Finviz", f"https://finviz.com/quote.ashx?t={qs}" if us else None),
        ("Analysis & ratings", "TradingView technicals", f"https://www.tradingview.com/symbols/{tv_path}/technicals/" if ":" in tv
         else f"https://www.tradingview.com/search/?query={qs}"),
        ("Analysis & ratings", "Investing.com technicals", f"https://www.investing.com/search/?q={qs}"),
        ("Analysis & ratings", "MarketWatch", f"https://www.marketwatch.com/investing/stock/{base.lower()}" if us
         else f"https://www.marketwatch.com/search?q={qs}"),
        ("Analysis & ratings", "Seeking Alpha", f"https://seekingalpha.com/symbol/{qs}" if us else None),
        ("Analysis & ratings", "Zacks", f"https://www.zacks.com/stock/quote/{qs}" if us else None),
        ("Options", "OptionCharts max pain", f"https://optioncharts.io/options/{qs}/max-pain" if us else None),
        ("Options", "OptionCharts overview", f"https://optioncharts.io/options/{qs}" if us else None),
        ("Options", "Barchart options", f"https://www.barchart.com/stocks/quotes/{qs}/options" if us else None),
        ("Options", "Nasdaq option chain", f"https://www.nasdaq.com/market-activity/stocks/{base.lower()}/option-chain" if us else None),
    ]
    return [{"group": g, "name": n, "url": u} for g, n, u in links if u]


def value_standing(row):
    """Where a stock would rank on P/E, forward P/E and PEG among today's scanned stocks of its region."""
    settings = storage.load_settings()
    opts = ranking_options(settings)
    rows = [r for r in (STATE.run or {}).get("rows") or [] if r["symbol"] != row["symbol"]]
    missing = [label for key, label in (("pe", "P/E"), ("forward_pe", "forward P/E"), ("peg", "PEG"))
               if not positive(row.get(key))]
    if row.get("error"):
        return {"status": "excluded", "reason": row["error"]}
    allowed = opts.get("scalable")
    sc = row.get("scalable") or "unchecked"
    if allowed is not None and sc not in allowed:
        return {"status": "excluded", "scalable": sc,
                "reason": "Not available on Scalable Capital: no gettex quote for its ISIN " + (row.get("isin") or "")
                if sc == "no" else "Not shown: its ISIN could not be found, so Scalable Capital availability is unknown."}
    if missing:
        return {"status": "excluded", "reason": "Not ranked: no positive " + ", ".join(missing) + " (all three are needed)."}
    limits = opts["limits"]
    over = [f"{lbl} {row[k]:.1f} > {limits[k]:g}" for k, lbl in (("pe", "P/E"), ("forward_pe", "forward P/E"), ("peg", "PEG"))
            if limits.get(k) and float(row[k]) > limits[k]]
    if over:
        return {"status": "excluded", "reason": "Not ranked: above your limits (" + ", ".join(over) + ")."}
    in_run = any(r["symbol"] == row["symbol"] for r in (STATE.run or {}).get("rows") or [])
    if in_run:  # reuse one cached full ranking instead of re-ranking for every lookup
        key = ((STATE.run or {}).get("id"), repr(sorted((k, repr(v)) for k, v in opts.items())))
        if STATE.full_rank_key != key:
            STATE.full_rank = rank_stocks((STATE.run or {}).get("rows") or [], **{**opts, "exclude_flagged": False})
            STATE.full_rank_key = key
        ranked_rows = STATE.full_rank
    else:
        ranked_rows = rank_stocks(rows + [dict(row, analysis=None)], **{**opts, "exclude_flagged": False})
    mine = next((r for r in ranked_rows if r["symbol"] == row["symbol"]), None)
    if not mine:
        return {"status": "excluded", "reason": "Not ranked."}
    total = sum(1 for r in ranked_rows if r["region"] == mine["region"])
    status = "top25" if mine["rank"] <= 25 else "ranked"
    if not in_run:  # looked up on demand: it was not part of today's scan
        status = "would_top25" if mine["rank"] <= 25 else "would_rank"
    return {"status": status, "rank": mine["rank"], "of": total, "scalable": sc,
            "score": mine["score"], "metric_scores": mine.get("metric_scores"), "peer_basis": mine.get("peer_basis"),
            "peer_count": mine.get("peer_count"), "flags": mine.get("flags"), "region": mine["region"]}


def bool_arg(name):
    v = request.args.get(name)
    return None if v is None else v.lower() in ("1", "true", "yes", "on")


# --------------------------------------------------------------------------- app

def create_app():
    app = Flask(__name__, static_folder=None)
    app.json.sort_keys = False
    from .trade_api import bp as trade_bp
    app.register_blueprint(trade_bp)

    @app.get("/")
    def index():
        return send_from_directory(STATIC, "index.html")

    @app.get("/static/<path:name>")
    def static_file(name):
        resp = send_from_directory(STATIC, name)
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.get("/api/ping")
    def ping():
        return jsonify(app="ValueAtlas", version=__version__)

    @app.get("/api/status")
    def status():
        settings = storage.load_settings()
        run = STATE.run or {}
        nxt = next_refresh(settings)
        return jsonify(
            version=__version__, progress=STATE.progress.snapshot(), busy=STATE.busy(), error=STATE.last_error,
            run={k: run.get(k) for k in ("id", "started", "finished", "asof", "status", "requested", "failed",
                                         "analysis_failed", "analysed", "screened_markets", "used_fallback", "scalable_info",
                                         "notes", "watchlist")} if run else None,
            next_refresh=nxt.isoformat(timespec="minutes") if nxt else None,
            data_dir=str(storage.DATA))

    @app.post("/api/refresh")
    def start_refresh():
        started = STATE.start_refresh("manual")
        return jsonify(started=started, busy=STATE.busy())

    @app.post("/api/cancel")
    def cancel():
        STATE.progress.cancel.set()
        return jsonify(ok=True)

    @app.get("/api/rankings")
    def rankings():
        weights = None
        if any(request.args.get(f"w_{m}") for m in ("pe", "forward_pe", "peg")):
            settings = storage.load_settings()
            weights = {m: float(request.args.get(f"w_{m}", settings["weights"][m])) for m in ("pe", "forward_pe", "peg")}
        top, opts = ranked(bool_arg("sector"), bool_arg("exclude_flagged"), weights, bool_arg("scalable"))
        run = STATE.run or {}
        prev_day, prev = storage.previous_ranks(run.get("asof") or date.today().isoformat()) if run else (None, {})
        rows = run.get("rows") or []
        eligible_counts = {r: 0 for r in REGIONS}
        for row in rows:
            if not row.get("error"):
                eligible_counts[row["region"]] += 1
        return jsonify(
            regions={region: [slim(r, prev) for r in top.get(region, [])] for region in REGIONS},
            options=opts, previous_day=prev_day, loaded=eligible_counts,
            asof=run.get("asof"), finished=run.get("finished"))

    @app.get("/api/stock/<symbol>")
    def stock(symbol):
        symbol = symbol.upper()
        top, _ = ranked()
        row = next((r for rr in top.values() for r in rr if r["symbol"] == symbol), None)
        if row is None:
            row = next((r for r in (STATE.run or {}).get("rows") or [] if r["symbol"] == symbol), None)
        if row is None and symbol in STATE.extra:
            row = STATE.extra[symbol].get("row")
        if row is None:
            from .mystocks import load_latest
            row = next((r for r in load_latest().get("items", []) if r["symbol"] == symbol and not r.get("error")), None)
        if row is None:
            return jsonify(error="Stock not in the latest data. Use 'Look up any stock'."), 404
        row = {k: v for k, v in row.items() if k != "analysis"}
        standing = value_standing(row)
        if standing.get("rank") and not row.get("rank"):
            row.update({k: standing[k] for k in ("rank", "score", "metric_scores", "peer_basis", "peer_count", "flags")
                        if k in standing})
        from .mystocks import load_latest as _my_latest
        mine = next((r for r in _my_latest().get("items", []) if r["symbol"] == symbol and not r.get("error")), None)
        my = {k: mine.get(k) for k in ("verdict", "value_score", "reasons", "peer_basis", "list", "previous_verdict", "metric_scores")} if mine else None
        analysis = analysis_for(symbol) or ({"technical": mine.get("technical"), "options": mine.get("options") or {}}
                                            if mine and mine.get("technical") else None)
        return jsonify(row=row, standing=standing, my=my, analysis=analysis, history=storage.symbol_history(symbol),
                       notes=storage.get_notes(symbol),
                       links=external_links(symbol, row.get("name"), row.get("exchange_code") or ""),
                       maxpain_history=storage.maxpain_rows(symbol=symbol))

    @app.get("/api/mystocks")
    def mystocks_list():
        from . import mystocks
        latest = mystocks.load_latest()
        entries = mystocks.load_list()
        got = {r["symbol"]: r for r in latest.get("items", [])}
        items = []
        for e in entries:
            r = got.get(e["symbol"]) or {"symbol": e["symbol"], "name": e.get("name"), "verdict": None, "pending": True}
            r = {k: v for k, v in r.items() if k not in ("summary",)}
            r["list"] = e.get("list")
            tech = r.pop("technical", None) or {}
            r["trend"] = tech.get("rating_label") if tech.get("available") else None
            r["trend_score"] = tech.get("rating") if tech.get("available") else None
            r["return_3m"] = tech.get("return_3m")
            r["spark"] = tech.get("spark")
            opt = r.pop("options", None) or {}
            if opt.get("available"):
                r["max_pain_dates"] = [{"expiration": x["expiration"], "max_pain": x["max_pain"], "distance": x.get("distance"),
                                        "pcr": x.get("put_call_ratio")} for x in opt.get("expirations", [])]
            else:
                r["max_pain_dates"] = []
                r["options_reason"] = opt.get("reason")
            items.append(r)
        return jsonify(items=items, updated=latest.get("updated"), checking=STATE.my.state,
                       has_peers=bool((STATE.run or {}).get("rows")))

    @app.post("/api/mystocks/check")
    def mystocks_check():
        return jsonify(started=check_my_stocks(), checking=STATE.my.state)

    @app.post("/api/mystocks/add")
    def mystocks_add():
        from . import mystocks
        data = request.get_json(silent=True) or {}
        try:
            mystocks.add_stock(data.get("symbol", ""), data.get("name", ""), data.get("list", "Watchlist"))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        sym = data["symbol"].strip().upper()
        if STATE.my.busy():
            return jsonify(added=sym, checking=STATE.my.state, note="Will be checked after the current check finishes.")
        try:
            STATE.my.run(STATE.provider(), (STATE.run or {}).get("rows") or [], guess_region, [sym], workers=1)
        except Exception as exc:
            return jsonify(added=sym, error=f"Added, but the check failed: {str(exc)[:160]}")
        return jsonify(added=sym)

    @app.delete("/api/mystocks/<path:symbol>")
    def mystocks_remove(symbol):
        from . import mystocks
        mystocks.remove_stock(symbol)
        return jsonify(ok=True)

    @app.get("/api/mystocks/history/<path:symbol>")
    def mystocks_history(symbol):
        from . import mystocks
        return jsonify(history=mystocks.history(symbol.upper()))

    @app.get("/api/search")
    def search():
        """Search today's three top-25 lists first, then everything scanned, then Yahoo's symbol search."""
        q = (request.args.get("q") or "").strip()
        if not q:
            return jsonify(top=[], scanned=[], others=[])
        ql = q.lower()
        top, _ = ranked(bool_arg("sector"), bool_arg("exclude_flagged"), None, bool_arg("scalable"))

        def hit(r):
            return any(ql in str(r.get(k) or "").lower() for k in ("symbol", "name", "long_name", "sector", "industry", "country"))
        in_top = [slim(r, {}) for region in REGIONS for r in top.get(region, []) if hit(r)]
        top_syms = {r["symbol"] for r in in_top}
        scanned = []
        for r in (STATE.run or {}).get("rows") or []:
            if r["symbol"] in top_syms or not hit(r):
                continue
            st = value_standing(r)
            scanned.append({"symbol": r["symbol"], "name": r.get("name"), "region": r["region"], "sector": r.get("sector"),
                            "isin": r.get("isin"), "scalable": r.get("scalable") or "unchecked",
                            "pe": r.get("pe"), "forward_pe": r.get("forward_pe"), "peg": r.get("peg"), **st})
            if len(scanned) >= 10:
                break
        others = []
        known = top_syms | {r["symbol"] for r in scanned}
        try:
            from .trade_api import LIVE
            res = LIVE.yf.Search(q, max_results=8, news_count=0, lists_count=0, recommended=0, raise_errors=False, timeout=8)
            for item in res.quotes or []:
                sym = (item.get("symbol") or "").upper()
                if not sym or sym in known or item.get("quoteType") not in ("EQUITY", "ETF"):
                    continue
                others.append({"symbol": sym, "name": item.get("shortname") or item.get("longname") or "",
                               "exchange": item.get("exchDisp") or item.get("exchange") or "",
                               "type": item.get("typeDisp") or item.get("quoteType"),
                               "links": external_links(sym, item.get("shortname") or "", item.get("exchange") or "")})
        except Exception as exc:
            log.info("symbol search failed: %s", exc)
        guess = q.upper().replace(" ", "")
        direct = None
        if not others and storage.SYMBOL_RE.fullmatch(guess) and guess not in known:
            direct = {"symbol": guess, "links": external_links(guess)}
        return jsonify(top=in_top, scanned=scanned, others=others, direct=direct)

    @app.post("/api/analyze/<symbol>")
    def analyze(symbol):
        symbol = symbol.upper().strip()
        if not storage.SYMBOL_RE.fullmatch(symbol):
            return jsonify(error="Invalid ticker symbol."), 400
        region = (request.get_json(silent=True) or {}).get("region")
        provider = STATE.provider()
        settings = storage.load_settings()
        existing = next((r for r in (STATE.run or {}).get("rows") or [] if r["symbol"] == symbol and not r.get("error")), None)
        try:
            row = existing or provider.fundamentals(symbol, region if region in REGIONS else guess_region(symbol))
            finnhub = FinnhubProvider(settings["finnhub_key"]) if settings.get("finnhub_key") else None
            result = analyse(provider, row, settings, finnhub)
        except Exception as exc:
            return jsonify(error=f"Could not analyse {symbol}: {str(exc)[:200]}"), 502
        result["row"] = {k: v for k, v in row.items() if k != "analysis"}
        if not existing:
            from . import scalable
            gettex, _ = scalable.load_list()
            result["row"]["scalable"] = scalable.status(row.get("isin"), gettex, row.get("isin_verified", True))
        STATE.extra[symbol] = result
        return jsonify(ok=True)

    @app.get("/api/options/<symbol>/dates")
    def option_dates(symbol):
        try:
            return jsonify(dates=STATE.provider().expirations(symbol.upper()))
        except Exception as exc:
            return jsonify(error=str(exc)[:200]), 502

    @app.get("/api/options/<symbol>")
    def option_maxpain(symbol):
        symbol = symbol.upper()
        expiration = request.args.get("expiration", "")
        live = bool_arg("live")
        try:
            valid_date(expiration)
        except ValueError:
            return jsonify(error="Pick an expiration date (YYYY-MM-DD)."), 400
        if not live:
            stored = storage.maxpain_rows(symbol=symbol, expiration=expiration, with_data=True)
            if stored and stored[0]["asof"] == date.today().isoformat():
                return jsonify(result={**stored[0], "put_call_ratio": stored[0]["pcr"], "cached": True},
                               history=stored)
        known = next((r for r in (STATE.run or {}).get("rows") or [] if r["symbol"] == symbol), None) \
            or (STATE.extra.get(symbol) or {}).get("row") or {}
        try:
            result = expiration_max_pain(STATE.provider(), symbol, expiration, known.get("price"))
        except Exception as exc:
            return jsonify(error=f"Max pain unavailable: {str(exc)[:200]}"), 502
        return jsonify(result=result, history=storage.maxpain_rows(symbol=symbol, expiration=expiration))

    @app.get("/api/maxpain-board")
    def maxpain_board():
        expirations, asofs = storage.maxpain_dates()
        asof = request.args.get("asof") or (asofs[0] if asofs else None)
        expiration = request.args.get("expiration")
        if expiration is None:  # default: the next expiration that has saved data
            today = date.today().isoformat()
            upcoming = [e for e in expirations if e >= today]
            expiration = upcoming[0] if upcoming else None
        elif expiration == "all":
            expiration = None
        rows = storage.maxpain_rows(expiration=expiration, asof=asof) if asof else []
        if bool_arg("mine"):
            from .mystocks import load_latest, load_list
            listed = {r.get("listed_symbol") or r["symbol"] for r in load_latest().get("items", [])}
            mine = listed | {e["symbol"] for e in load_list()}
            rows = [r for r in rows if r["symbol"] in mine]
        names = {r["symbol"]: (r.get("name"), r.get("region")) for r in (STATE.run or {}).get("rows") or []}
        for r in rows:
            r["name"], r["region"] = names.get(r["symbol"], (None, None))
            r["distance"] = (r["max_pain"] / r["price"] - 1) if r.get("price") else None
        return jsonify(rows=rows, expirations=expirations, asofs=asofs, asof=asof, expiration=expiration)

    @app.get("/api/quality")
    def quality():
        out = []
        for r in (STATE.run or {}).get("rows") or []:
            missing = [m for m in ("pe", "forward_pe", "peg") if not positive(r.get(m))]
            if r.get("error") or missing:
                out.append({"symbol": r["symbol"], "region": r["region"], "name": r.get("name"),
                            "problem": r.get("error") or "Missing " + ", ".join(
                                {"pe": "P/E", "forward_pe": "forward P/E", "peg": "PEG"}[m] for m in missing)})
            elif r.get("scalable") in ("no", "unknown") and storage.load_settings().get("scalable_only", True):
                out.append({"symbol": r["symbol"], "region": r["region"], "name": r.get("name"),
                            "problem": f"Not on Scalable Capital (no gettex quote for ISIN {r.get('isin')})"
                            if r["scalable"] == "no" else "Scalable Capital availability unknown (ISIN not found)"})
        return jsonify(rows=out)

    @app.get("/api/scalable/<path:symbol>")
    def scalable_check(symbol):
        """Is this stock on Scalable Capital (gettex)? Looks up and caches its ISIN when needed."""
        from . import scalable
        symbol = symbol.upper().strip()
        if not storage.SYMBOL_RE.fullmatch(symbol):
            return jsonify(error="Invalid ticker symbol."), 400
        gettex, info = scalable.load_list()
        if gettex is None and not getattr(STATE, "gettex_thread", None):
            # first use before any screener update: fetch the gettex list in the background
            import threading
            STATE.gettex_thread = threading.Thread(target=lambda: scalable.refresh_list(), daemon=True)
            STATE.gettex_thread.start()
        hit = scalable.cached_isin(symbol)
        if hit is None:
            try:
                yf = STATE.provider().yf
                t = yf.Ticker(symbol)
                t.get_info()
                isin, verified = scalable.lookup_isin(yf, t, symbol)
            except Exception as exc:
                return jsonify(symbol=symbol, status="unknown", error=str(exc)[:160])
        else:
            isin, verified = hit[0], bool(hit[1])
        return jsonify(symbol=symbol, isin=isin, isin_verified=verified, status=scalable.status(isin, gettex, verified),
                       list_date=(info or {}).get("downloaded"), list_count=(info or {}).get("count"))

    @app.route("/api/settings", methods=["GET", "POST"])
    def settings():
        if request.method == "POST":
            try:
                saved = storage.save_settings(request.get_json(silent=True) or {})
            except (ValueError, TypeError) as exc:
                return jsonify(error=str(exc)), 400
            STATE.rank_cache.clear()
            return jsonify(settings=saved)
        return jsonify(settings=storage.load_settings())

    @app.route("/api/watchlist", methods=["GET", "POST"])
    def watchlist():
        if request.method == "POST":
            try:
                rows = storage.parse_watchlist((request.get_json(silent=True) or {}).get("csv", ""))
            except ValueError as exc:
                return jsonify(error=str(exc)), 400
            storage.write_watchlist(rows)
            return jsonify(rows=rows)
        return jsonify(rows=storage.read_watchlist())

    @app.post("/api/notes/<symbol>")
    def add_note(symbol):
        data = request.get_json(silent=True) or {}
        try:
            asof = valid_date(data.get("asof") or date.today().isoformat())
        except ValueError:
            return jsonify(error="Date must be YYYY-MM-DD."), 400
        source = (data.get("source") or "").strip()[:60]
        if not source:
            return jsonify(error="Name the website or source."), 400
        storage.add_note(symbol.upper(), source, asof, (data.get("signal") or "")[:20],
                         (data.get("value") or "")[:60], (data.get("note") or "")[:500])
        return jsonify(notes=storage.get_notes(symbol.upper()))

    @app.delete("/api/notes/<symbol>/<int:note_id>")
    def del_note(symbol, note_id):
        storage.delete_note(note_id)
        return jsonify(notes=storage.get_notes(symbol.upper()))

    @app.get("/api/runs")
    def runs():
        return jsonify(runs=storage.run_list())

    @app.get("/api/export.csv")
    def export():
        top, _ = ranked(bool_arg("sector"), bool_arg("exclude_flagged"), None, bool_arg("scalable"))
        buf = io.StringIO()
        cols = ["region", "rank", "symbol", "name", "sector", "currency", "price", "pe", "forward_pe", "peg",
                "score", "verdict", "sentiment_score", "tv_label", "recommendation_key", "upside",
                "max_pain", "max_pain_exp", "flags"]
        writer = csv.writer(buf)
        writer.writerow(cols)
        for region in REGIONS:
            for r in top.get(region, []):
                s = slim(r, {})
                s["flags"] = "; ".join(s.get("flags") or [])
                writer.writerow([safe_csv(s.get(c)) for c in cols])
        name = f"valueatlas-{(STATE.run or {}).get('asof', 'export')}.csv"
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename={name}"})

    return app


def guess_region(symbol):
    suffix = symbol.rsplit(".", 1)[-1] if "." in symbol else ""
    asia = {"T", "HK", "SS", "SZ", "KS", "KQ", "TW", "TWO", "NS", "BO", "SI", "JK", "BK", "KL"}
    europe = {"L", "DE", "F", "PA", "AS", "MI", "MC", "SW", "ST", "CO", "HE", "OL", "BR", "VI", "IR", "LS", "WA",
              "SG", "MU", "DU", "BE", "HM", "HA", "IL"}
    if suffix in asia:
        return "Asia"
    if suffix in europe:
        return "Europe"
    return "US"
