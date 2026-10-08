"""The daily refresh pipeline and on-demand analysis."""
from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

from .core import REGIONS, median, positive, rank_stocks, top_by_region, max_pain, trim_curve
from .indicators import technical_rating
from .provider import MARKETS, YahooProvider, FinnhubProvider
from .sentiment import composite
from . import scalable, storage
from .storage import now

log = logging.getLogger("valueatlas")


def ranking_options(settings, **overrides):
    s = {**settings, **{k: v for k, v in overrides.items() if v is not None}}
    return {
        "sector_adjust": bool(s["sector_relative"]),
        "weights": s["weights"],
        "limits": {"pe": s["max_pe"], "forward_pe": s["max_forward_pe"], "peg": s["max_peg"]},
        "exclude_flagged": bool(s["exclude_flagged"]),
        "scalable": scalable.allowed_statuses(s),
    }


class Progress:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = {"running": False, "stage": "", "done": 0, "total": 0, "message": "", "log": []}
        self.cancel = threading.Event()

    def update(self, **kw):
        with self.lock:
            self.state.update(kw)
            if kw.get("message"):
                self.state["log"] = (self.state["log"] + [f"{now()[11:19]} {kw['message']}"])[-60:]

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.state))


def _pool(items, fn, workers, progress, stage, cancel, abort_after=None):
    results = {}
    ok = bad = 0
    progress.update(stage=stage, done=0, total=len(items))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn, item): key for key, item in items.items()}
        for i, fut in enumerate(as_completed(futures), 1):
            if cancel.is_set():
                for f in futures:
                    f.cancel()
                raise RuntimeError("Refresh cancelled. The previous results were kept.")
            key = futures[fut]
            try:
                results[key] = fut.result()
                ok += 1
            except Exception as exc:  # isolate single-stock failures
                results[key] = exc
                bad += 1
                if abort_after and ok == 0 and bad >= abort_after:
                    for f in futures:
                        f.cancel()
                    raise RuntimeError(f"The first {bad} lookups all failed – Yahoo Finance may be blocking requests "
                                       f"or offline. Previous results were kept. Last error: {str(exc)[:160]}")
            progress.update(done=i)
    return results


# --------------------------------------------------------------------------- options

def options_for(provider, symbol, price, limit, asof):
    """Max pain for the next `limit` expirations; saves each to history."""
    try:
        expirations = provider.expirations(symbol)
    except Exception as exc:
        return {"available": False, "reason": f"Could not load option dates: {str(exc)[:160]}"}
    if not expirations:
        return {"available": False, "reason": "No listed options on Yahoo Finance for this stock."}
    rows, errors = [], []
    for exp in expirations[:limit]:
        try:
            result = expiration_max_pain(provider, symbol, exp, price, asof)
            rows.append({k: result[k] for k in ("expiration", "max_pain", "put_call_ratio", "call_oi", "put_oi",
                                               "price", "distance")})
        except Exception as exc:
            errors.append(f"{exp}: {str(exc)[:120]}")
    if not rows:
        return {"available": False, "reason": "; ".join(errors[:3]) or "No usable option data.",
                "all_expirations": expirations}
    near = rows[:3]
    call_oi = sum(r["call_oi"] for r in near)
    put_oi = sum(r["put_oi"] for r in near)
    return {"available": True, "expirations": rows, "all_expirations": expirations, "errors": errors,
            "nearest": rows[0], "put_call_ratio": put_oi / call_oi if call_oi else None,
            "total_oi": call_oi + put_oi}


def expiration_max_pain(provider, symbol, expiration, price=None, asof=None, save=True):
    contracts, chain_price = provider.option_contracts(symbol, expiration)
    result = max_pain(contracts)
    price = chain_price or price
    result.update({"symbol": symbol, "expiration": expiration, "price": price, "fetched": now(),
                   "distance": (result["max_pain"] / price - 1) if price else None,
                   "source": "Calculated with OptionCharts' published max-pain method from Yahoo open interest"})
    trim_curve(result, price)
    if save:
        storage.save_maxpain(symbol, result, asof or date.today().isoformat())
    return result


# --------------------------------------------------------------------------- analysis

def analyse(provider, row, settings, finnhub=None, asof=None):
    """Technical rating, analyst trend, options and the combined verdict for one stock."""
    symbol = row["symbol"]
    out = {"symbol": symbol, "analysed": now()}
    try:
        out["technical"] = technical_rating(provider.history(symbol))
    except Exception as exc:
        out["technical"] = {"available": False, "reason": str(exc)[:200]}
    try:
        now_t, then_t = provider.recommendation_trend(symbol)
        out["rec_trend_now"], out["rec_trend_3m"] = now_t, then_t
    except Exception:
        out["rec_trend_now"] = out["rec_trend_3m"] = None
    out["finnhub"] = None
    if finnhub:
        try:
            out["finnhub"] = finnhub.recommendation(symbol)
        except Exception as exc:
            out["finnhub"] = {"error": str(exc)[:120]}
    price = row.get("price") or (out["technical"] or {}).get("price")
    out["options"] = options_for(provider, symbol, price, int(settings["option_expirations"]),
                                 asof or date.today().isoformat())
    merged = {**row, "rec_trend_now": out["rec_trend_now"], "rec_trend_3m": out["rec_trend_3m"]}
    out["sentiment"] = composite(merged, out["technical"], out["options"] if out["options"].get("available") else None,
                                 out["finnhub"] if out["finnhub"] and not out["finnhub"].get("error") else None)
    return out


# --------------------------------------------------------------------------- refresh

def gather_universe(provider, settings, progress, gettex=None):
    """Candidates per region: whole-market screen (preferred) + the user's watchlist.

    Returns the first batch to look up and, per region, an ordered reserve used to top up
    regions where fewer than 25 stocks qualify (e.g. after the Scalable Capital filter)."""
    watch = storage.read_watchlist()
    candidates = {r: {} for r in REGIONS}
    screened_ok = 0
    notes = []
    if settings["use_screener"]:
        jobs = [(region, c, ex, n) for region, markets in MARKETS.items() for c, ex, n in markets]
        progress.update(stage="Scanning markets", done=0, total=len(jobs))
        for i, (region, country, exchanges, count) in enumerate(jobs, 1):
            if progress.cancel.is_set():
                raise RuntimeError("Refresh cancelled. The previous results were kept.")
            if screened_ok == 0 and len(notes) >= 3:
                notes.append("Screener skipped after 3 failures.")
                break
            progress.update(message=f"Screening {country.upper()} ({region})", done=i)
            try:
                for q in provider.screen(country, exchanges, count, max(settings["max_pe"], 1)):
                    q["country_code"] = country
                    candidates[region].setdefault(q["symbol"], q)
                screened_ok += 1
            except Exception as exc:
                notes.append(f"Screen {country.upper()} failed: {str(exc)[:140]}")
                log.warning("screen %s failed: %s", country, exc)
    used_fallback = screened_ok == 0
    if used_fallback:
        if settings["use_screener"]:
            notes.append("Market screener unavailable – used the built-in starter list instead.")
        for item in storage.parse_watchlist((storage.APP_DIR / "starter_universe.csv").read_text(encoding="utf-8-sig")):
            candidates[item["region"]].setdefault(item["symbol"], {"symbol": item["symbol"]})

    # Pre-select the cheapest-looking names on screener P/E + forward P/E before detailed lookups.
    limit = int(settings["candidates_per_region"])
    selected, reserve = {}, {r: [] for r in REGIONS}
    for region, items in candidates.items():
        quotes = list(items.values())
        if gettex is not None:  # skip stocks already known not to trade on gettex / Scalable Capital
            def known_missing(sym):
                hit = scalable.cached_isin(sym)
                return bool(hit and hit[0] and hit[1] and hit[0] not in gettex)
            quotes = [q for q in quotes if not known_missing(q["symbol"])]
        with_ratios = [q for q in quotes if q.get("pe") and q.get("forward_pe")]
        without = [q for q in quotes if not (q.get("pe") and q.get("forward_pe"))]

        def pct(values, v):
            return sum(x > v for x in values) / max(1, len(values) - 1)
        pes = [q["pe"] for q in with_ratios]
        fpes = [q["forward_pe"] for q in with_ratios]
        with_ratios.sort(key=lambda q: -(pct(pes, q["pe"]) + pct(fpes, q["forward_pe"])))
        chosen = with_ratios[:limit] if not used_fallback else quotes
        if not used_fallback and len(chosen) < limit:
            chosen += without[: limit - len(chosen)]
        for q in chosen:
            selected[q["symbol"]] = region
        if not used_fallback:
            chosen_syms = {q["symbol"] for q in chosen}
            reserve[region] = [q["symbol"] for q in with_ratios + without if q["symbol"] not in chosen_syms]
    for item in watch:
        selected.setdefault(item["symbol"], item["region"])
    # Regional valuation yardsticks for the watchlist: medians of the largest screened companies
    benchmarks = {}
    for region, items in candidates.items():
        pes = [q.get("pe") for q in items.values() if q.get("pe")]
        fpes = [q.get("forward_pe") for q in items.values() if q.get("forward_pe")]
        if len(pes) >= 30 and len(fpes) >= 30:
            benchmarks[region] = {"pe": round(median(pes), 2), "forward_pe": round(median(fpes), 2), "count": len(pes)}
    return selected, {"screened_markets": screened_ok, "used_fallback": used_fallback, "notes": notes,
                      "watchlist": len(watch), "reserve": reserve, "benchmarks": benchmarks}


def refresh(progress=None, provider=None, settings=None):
    progress = progress or Progress()
    settings = settings or storage.load_settings()
    with storage.refresh_lock():
        provider = provider or YahooProvider()
        workers = int(settings.get("workers", 4))
        cancel = progress.cancel
        started = now()
        asof = date.today().isoformat()
        progress.update(running=True, stage="Starting", message="Refresh started – checking Yahoo Finance",
                        done=0, total=0)
        if hasattr(provider, "check"):
            try:
                provider.check()
            except Exception as exc:
                raise RuntimeError("Can't reach Yahoo Finance. Check your internet connection and try again later "
                                   f"(previous results were kept). Details: {str(exc)[:160]}") from exc

        gettex, gettex_info = None, None
        if settings.get("scalable_only", True):
            progress.update(stage="Scalable Capital list", message="Downloading the gettex instrument list (Scalable Capital's exchange)")
            try:
                gettex, gettex_info = getattr(provider, "gettex_list", scalable.refresh_list)()
            except Exception as exc:
                gettex_info = {"error": str(exc)[:160]}
        selected, meta = gather_universe(provider, settings, progress, gettex)
        reserve = meta.pop("reserve", {})
        if settings.get("scalable_only", True):
            if gettex is None:
                meta["notes"].append("Could not load the gettex list, so Scalable Capital availability was not checked "
                                     f"this time ({(gettex_info or {}).get('error', 'unknown error')}).")
            meta["scalable_info"] = gettex_info
        rows, failed = [], 0

        def fetch(batch, stage):
            nonlocal failed
            results = _pool({s: (s, r) for s, r in batch.items()},
                            lambda item: provider.fundamentals(*item), workers, progress, stage, cancel,
                            abort_after=15 if not rows else None)
            for symbol, region in batch.items():
                res = results.get(symbol)
                if isinstance(res, Exception) or res is None:
                    failed += 1
                    rows.append({"symbol": symbol, "region": region, "error": str(res)[:240], "fetched": now()})
                    continue
                if settings.get("estimate_peg", True) and not positive(res.get("peg")) and positive(res.get("peg_estimate")):
                    res["peg"], res["peg_field"] = res["peg_estimate"], "estimated"
                res["scalable"] = scalable.status(res.get("isin"), gettex, res.get("isin_verified", True))
                if region == "US" and settings.get("us_domestic_only") and res.get("country") not in ("", "United States"):
                    res["error"] = f"Foreign company listed in the US ({res.get('country')}); excluded from the US list."
                if res.get("financial_currency") and res.get("currency") and res["financial_currency"] != res["currency"] \
                        and res["currency"] not in ("GBp", "ILA", "ZAc"):
                    res["currency_note"] = (f"Reports in {res['financial_currency']} but trades in {res['currency']}; "
                                            "Yahoo ratios may be distorted.")
                rows.append(res)

        progress.update(message=f"Fetching valuations for {len(selected)} stocks")
        fetch(selected, "Valuations")
        # Top up regions where fewer than 25 stocks qualify (missing PEG, not on Scalable Capital, …)
        opts0 = ranking_options(settings)
        for _ in range(3):
            counts = {r: 0 for r in REGIONS}
            for r in rank_stocks(rows, **opts0):
                counts[r["region"]] += 1
            batch = {}
            for region in REGIONS:
                if counts[region] < 25 and reserve.get(region):
                    take, reserve[region] = reserve[region][:60], reserve[region][60:]
                    batch.update({sym: region for sym in take if sym not in batch})
            if not batch:
                break
            short = ", ".join(f"{r} ({counts[r]})" for r in REGIONS if counts[r] < 25)
            progress.update(message=f"Fewer than 25 qualifying stocks in {short} – checking {len(batch)} more")
            fetch(batch, "Topping up")
        selected = {r["symbol"]: r["region"] for r in rows}
        if rows and failed / len(rows) > 0.8:
            raise RuntimeError(f"{failed} of {len(rows)} lookups failed – Yahoo may be rate-limiting or offline. "
                               "Previous results were kept.")

        # Analyse everything that could appear in a top 25, under both ranking modes.
        opts = ranking_options(settings)
        focus = set()
        for sector_mode in (True, False):
            ranked = rank_stocks(rows, **{**opts, "sector_adjust": sector_mode})
            for region_rows in top_by_region(ranked, 30).values():
                focus.update(r["symbol"] for r in region_rows)
        focus.update(w["symbol"] for w in storage.read_watchlist())
        by_symbol = {r["symbol"]: r for r in rows if not r.get("error")}
        focus = [s for s in focus if s in by_symbol]
        finnhub = FinnhubProvider(settings["finnhub_key"]) if settings.get("finnhub_key") else None
        progress.update(message=f"Analysing trend, analysts and options for {len(focus)} stocks")
        analysis = _pool({s: by_symbol[s] for s in focus},
                         lambda r: analyse(provider, r, settings, finnhub, asof),
                         max(1, workers - 1), progress, "Sentiment & max pain", cancel)
        analysis_failed = 0
        for symbol, res in analysis.items():
            if isinstance(res, Exception):
                analysis_failed += 1
                res = {"symbol": symbol, "error": str(res)[:200]}
            by_symbol[symbol]["analysis"] = res

        final = top_by_region(rank_stocks(rows, **opts), 25)
        for region_rows in final.values():
            for r in region_rows:
                r["sentiment"] = (r.get("analysis") or {}).get("sentiment")
        payload = {
            "started": started, "finished": now(), "asof": asof,
            "status": "Partial" if failed or analysis_failed or meta["notes"] else "Complete",
            "requested": len(selected), "failed": failed, "analysis_failed": analysis_failed,
            "analysed": len(focus), **meta, "rows": rows,
            "top": {region: [{k: r.get(k) for k in ("symbol", "rank", "score", "pe", "forward_pe", "peg", "price",
                                                    "sentiment")} for r in rr] for region, rr in final.items()},
        }
        storage.save_run(payload)
        progress.update(stage="Done", message=f"Refresh finished: {len(rows) - failed} stocks loaded, "
                                              f"{failed} failed, {len(focus)} analysed.")
        return payload
