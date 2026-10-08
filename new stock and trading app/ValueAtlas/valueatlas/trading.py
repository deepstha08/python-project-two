"""Live multi-timeframe data: fetching, caching, resampling and analysis per symbol."""
from __future__ import annotations

import json
import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from . import storage
from .provider import retry
from .signals import analyse, label

log = logging.getLogger("valueatlas")

# timeframe key -> (Yahoo interval, period, resample factor in source bars, cache seconds, TradingView interval)
TIMEFRAMES = {
    "1m": ("1m", "5d", None, 10, "1"),
    "2m": ("2m", "5d", None, 20, "1"),
    "5m": ("5m", "1mo", None, 30, "5"),
    "15m": ("15m", "1mo", None, 60, "15"),
    "30m": ("30m", "60d", None, 90, "30"),
    "1h": ("60m", "1y", None, 120, "60"),
    "3h": ("60m", "1y", 3, 120, "180"),
    "4h": ("60m", "1y", 4, 120, "240"),
    "1D": ("1d", "5y", None, 300, "D"),
}
ORDER = list(TIMEFRAMES)
HORIZON_GROUPS = {"Scalping (1–5 min)": ["1m", "2m", "5m"], "Intraday (15 min – 1 h)": ["15m", "30m", "1h"],
                  "Swing (3 h – 1 day)": ["3h", "4h", "1D"]}

DEFAULT_WATCHLIST = ["AAPL", "NVDA", "TSLA", "MSFT", "AMZN", "META", "SPY", "QQQ"]


def resample_session(df, factor):
    """Group consecutive intraday bars into N-bar candles that restart each session (like TradingView)."""
    if df is None or df.empty:
        return df
    day = pd.Index(df.index.date)
    pos = df.groupby(day).cumcount().to_numpy() // factor
    key = [f"{d}-{p}" for d, p in zip(day, pos)]
    g = df.groupby(key, sort=False)
    out = pd.DataFrame({"Open": g["Open"].first(), "High": g["High"].max(), "Low": g["Low"].min(),
                        "Close": g["Close"].last(), "Volume": g["Volume"].sum()})
    first = pd.Series(df.index, index=key).groupby(level=0, sort=False).first()
    out.index = pd.DatetimeIndex(first.loc[out.index])
    return out.sort_index()


class LiveData:
    def __init__(self, yf_module=None):
        self._yf = yf_module
        self.lock = threading.Lock()
        self.raw = {}       # (symbol, interval, period, prepost) -> (fetched_at, DataFrame)
        self.results = {}   # (symbol, tf, detail) -> (fingerprint, result)
        self.meta = {}      # symbol -> info dict
        self.pool = ThreadPoolExecutor(max_workers=6)

    @property
    def yf(self):
        if self._yf is None:
            import yfinance as yf
            self._yf = yf
        return self._yf

    # ------------------------------------------------------------- raw data
    def history(self, symbol, interval, period, ttl, prepost=False):
        key = (symbol, interval, period, prepost)
        with self.lock:
            hit = self.raw.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        df = retry(lambda: self.yf.Ticker(symbol).history(period=period, interval=interval, prepost=prepost,
                                                          auto_adjust=False, timeout=20), tries=2, wait=1.5)
        if df is None or df.empty:
            if hit:
                return hit[1]
            raise ValueError(f"No {interval} price data from Yahoo for {symbol}.")
        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
        with self.lock:
            self.raw[key] = (time.time(), df)
            if len(self.raw) > 400:  # simple size cap
                for k in sorted(self.raw, key=lambda k: self.raw[k][0])[:100]:
                    self.raw.pop(k, None)
        return df

    def frame(self, symbol, tf, prepost=False):
        interval, period, factor, ttl, _ = TIMEFRAMES[tf]
        df = self.history(symbol, interval, period, ttl, prepost and interval != "1d")
        if factor:
            df = resample_session(df, factor)
        return df

    # ------------------------------------------------------------- analysis
    def analyse_tf(self, symbol, tf, detail=False, prepost=False):
        df = self.frame(symbol, tf, prepost)
        fp = (len(df), str(df.index[-1]), float(df["Close"].iloc[-1]), float(df["Volume"].iloc[-1]))
        key = (symbol, tf, detail, prepost)
        with self.lock:
            cached = self.results.get(key)
        if cached and cached[0] == fp:
            return cached[1]
        res = analyse(df, intraday=tf != "1D", detail=detail)
        res["tf"] = tf
        res["tv_interval"] = TIMEFRAMES[tf][4]
        with self.lock:
            self.results[key] = (fp, res)
        return res

    def info(self, symbol):
        hit = self.meta.get(symbol)
        if hit and time.time() - hit["_at"] < 6 * 3600:
            return hit
        try:
            fi = self.yf.Ticker(symbol).get_info() or {}
        except Exception:
            fi = {}
        meta = {"_at": time.time(), "name": fi.get("shortName") or fi.get("longName") or symbol,
                "exchange": fi.get("exchange") or "", "exchange_name": fi.get("fullExchangeName") or "",
                "currency": fi.get("currency") or "", "type": fi.get("quoteType") or "",
                "tz": fi.get("exchangeTimezoneShortName") or ""}
        self.meta[symbol] = meta
        return meta

    def summary(self, symbol, prepost=False):
        """Verdict for all nine timeframes plus a live quote."""
        futures = {tf: self.pool.submit(self.analyse_tf, symbol, tf, False, prepost) for tf in ORDER}
        info_f = self.pool.submit(self.info, symbol)
        frames, errors = {}, {}
        for tf, fut in futures.items():
            try:
                r = fut.result(timeout=60)
                frames[tf] = {k: r.get(k) for k in ("available", "reason", "score", "verdict", "previous_score",
                                                     "counts", "hit_rate", "tradingview", "categories", "last_time",
                                                     "price", "rsi", "bars", "tv_interval")}
            except Exception as exc:
                errors[tf] = str(exc)[:160]
                frames[tf] = {"available": False, "reason": str(exc)[:160], "verdict": "No data"}
        meta = info_f.result(timeout=60)
        quote = self.quote(symbol, frames, prepost)
        groups = {}
        for name, tfs in HORIZON_GROUPS.items():
            vals = [frames[t]["score"] for t in tfs if frames[t].get("available") and frames[t].get("score") is not None]
            s = sum(vals) / len(vals) if vals else None
            groups[name] = {"score": round(s, 3) if s is not None else None, "verdict": label(s) if s is not None else "No data"}
        return {"symbol": symbol, "name": meta.get("name"), "exchange": meta.get("exchange_name") or meta.get("exchange"),
                "currency": meta.get("currency"), "type": meta.get("type"),
                "tv_symbol": tradingview_symbol(symbol, meta.get("exchange")),
                "delay": delay_note(symbol), "quote": quote, "frames": frames, "groups": groups,
                "errors": errors, "generated": int(time.time())}

    def quote(self, symbol, frames, prepost=False):
        price, last_t = None, None
        for tf in ORDER:  # most granular available first
            f = frames.get(tf) or {}
            if f.get("available"):
                price, last_t = f.get("price"), f.get("last_time")
                break
        prev_close = None
        try:
            d = self.history(symbol, "1d", "5y", TIMEFRAMES["1D"][3])
            if len(d) >= 2:
                last_day = d.index[-1].date()
                ref_day = None
                if last_t:
                    ref_day = pd.Timestamp(last_t, unit="s", tz="UTC").tz_convert(d.index.tz).date()
                prev_close = float(d["Close"].iloc[-2] if ref_day is None or last_day >= ref_day else d["Close"].iloc[-1])
        except Exception:
            pass
        live = bool(last_t and time.time() - last_t < 6 * 60)
        change = (price - prev_close) if price is not None and prev_close else None
        return {"price": price, "prev_close": prev_close, "change": change,
                "change_pct": (change / prev_close) if change is not None and prev_close else None,
                "last_time": last_t, "live": live}


# ------------------------------------------------------------------- helpers

TV_EXCHANGES = {
    "NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ", "NAS": "NASDAQ", "NYQ": "NYSE", "NYS": "NYSE",
    "ASE": "AMEX", "PCX": "AMEX", "BTS": "CBOE",
}
TV_SUFFIX = {
    "DE": "XETR", "F": "FWB", "L": "LSE", "PA": "EURONEXT", "AS": "EURONEXT", "BR": "EURONEXT", "LS": "EURONEXT",
    "IR": "EURONEXT", "MI": "MIL", "MC": "BME", "SW": "SIX", "ST": "OMXSTO", "CO": "OMXCOP", "HE": "OMXHEX",
    "OL": "OSL", "VI": "VIE", "WA": "GPW", "T": "TSE", "HK": "HKEX", "SS": "SSE", "SZ": "SZSE", "KS": "KRX",
    "KQ": "KRX", "TW": "TWSE", "TWO": "TPEX", "NS": "NSE", "BO": "BSE", "SI": "SGX", "AX": "ASX", "TO": "TSX",
    "V": "TSXV", "SA": "BMFBOVESPA", "MX": "BMV", "JK": "IDX", "BK": "SET", "KL": "MYX",
}


def tradingview_symbol(symbol, exchange=""):
    if symbol.startswith("^"):
        return {"^GSPC": "SP:SPX", "^NDX": "NASDAQ:NDX", "^DJI": "DJ:DJI", "^IXIC": "NASDAQ:IXIC",
                "^GDAXI": "XETR:DAX", "^N225": "TVC:NI225", "^FTSE": "TVC:UKX", "^HSI": "TVC:HSI"}.get(symbol, symbol[1:])
    if "." in symbol:
        base, suf = symbol.rsplit(".", 1)
        ex = TV_SUFFIX.get(suf.upper())
        if suf.upper() == "HK":
            base = base.lstrip("0") or "0"
        return f"{ex}:{base.replace('-', '_')}" if ex else base
    ex = TV_EXCHANGES.get((exchange or "").upper())
    return f"{ex}:{symbol.replace('-', '.')}" if ex else symbol.replace("-", ".")


def delay_note(symbol):
    if "." in symbol or symbol.startswith("^"):
        return "Yahoo data – usually delayed 15–20 minutes on this exchange"
    return "Yahoo data – near real-time for US stocks (a few seconds to ~1 minute)"


def load_watchlist():
    path = storage.DATA / "trade_watchlist.json"
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(items, list) and all(isinstance(x, str) for x in items):
            return items
    except (OSError, ValueError):
        pass
    try:  # start with the user's own portfolio from "My stocks"
        from .mystocks import load_list
        mine = [e["symbol"] for e in load_list() if e.get("list") == "Portfolio"]
        if mine:
            return mine
    except Exception:
        pass
    return list(DEFAULT_WATCHLIST)


def save_watchlist(items):
    clean = []
    for s in items:
        s = str(s).strip().upper()
        if s and storage.SYMBOL_RE.fullmatch(s) and s not in clean:
            clean.append(s)
    storage.DATA.mkdir(parents=True, exist_ok=True)
    (storage.DATA / "trade_watchlist.json").write_text(json.dumps(clean[:60]), encoding="utf-8")
    return clean[:60]


def finite(x):
    return x is not None and isinstance(x, (int, float)) and math.isfinite(x)
