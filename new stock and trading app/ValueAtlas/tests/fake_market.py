"""Offline stand-in for the parts of yfinance the trading desk uses (tests and UI previews only)."""
from collections import namedtuple
from datetime import datetime, timedelta, time as dtime
from functools import lru_cache
import zlib

import numpy as np
import pandas as pd

NY = "America/New_York"
Chain = namedtuple("Options", ["calls", "puts", "underlying"])
STEP = {"1m": 1, "2m": 2, "5m": 5, "15m": 15, "30m": 30, "60m": 60}
DAYS = {"5d": 5, "1mo": 22, "60d": 60, "1y": 252, "5y": 1260}


def _seed(symbol):
    return zlib.crc32(symbol.encode())


@lru_cache(maxsize=32)
def _minutes(symbol, sessions=130):
    rng = np.random.default_rng(_seed(symbol))
    now = pd.Timestamp.now(tz=NY)
    days = pd.bdate_range(end=now.normalize().tz_localize(None), periods=sessions)
    stamps = []
    for d in days:
        start = pd.Timestamp(datetime.combine(d.date(), dtime(9, 30)), tz=NY)
        mins = 390
        if d.date() == now.date():
            mins = int(max(0, min(390, (now - start).total_seconds() // 60 + 1)))
        stamps.append(pd.date_range(start, periods=mins, freq="1min"))
    idx = stamps[0].append(stamps[1:]) if len(stamps) > 1 else stamps[0]
    n = len(idx)
    drift = rng.uniform(-0.00002, 0.00004)
    regime = np.repeat(rng.normal(0, 0.00012, n // 600 + 1), 600)[:n]
    ret = rng.normal(drift, 0.0009, n) + regime
    close = (50 + _seed(symbol) % 400) * np.exp(np.cumsum(ret))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.0007, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.0007, n))
    vol = rng.integers(2_000, 40_000, n).astype(float)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol}, index=idx)


def _agg(df, rule):
    g = df.groupby(df.index.floor(rule) if rule != "1D" else df.index.normalize())
    return pd.DataFrame({"Open": g["Open"].first(), "High": g["High"].max(), "Low": g["Low"].min(),
                         "Close": g["Close"].last(), "Volume": g["Volume"].sum()})


class Ticker:
    def __init__(self, symbol):
        self.symbol = symbol
        if symbol.startswith("BAD"):
            raise ValueError("unknown symbol")

    @property
    def options(self):
        if "." in self.symbol:
            return ()
        today = pd.Timestamp.now(tz=NY).date()
        fridays = [today + timedelta(days=i) for i in range(0, 70) if (today + timedelta(days=i)).weekday() == 4]
        return tuple(d.isoformat() for d in fridays[:8])

    def history(self, period="1mo", interval="1d", prepost=False, auto_adjust=False, timeout=20):
        if self.symbol.startswith("EMPTY"):
            return pd.DataFrame()
        m = _minutes(self.symbol)
        if interval == "1d":
            intraday = _agg(m, "1D")
            rng = np.random.default_rng(_seed(self.symbol) + 7)
            k = DAYS[period] - len(intraday)
            if k > 0:
                first = intraday["Open"].iloc[0]
                path = first * np.exp(np.cumsum(rng.normal(0.0003, 0.015, k))[::-1] * -1)
                idx = pd.bdate_range(end=intraday.index[0].tz_localize(None) - pd.Timedelta(days=1), periods=k).tz_localize(NY)
                o = np.r_[path[0], path[:-1]]
                old = pd.DataFrame({"Open": o, "High": np.maximum(o, path) * 1.006, "Low": np.minimum(o, path) * 0.994,
                                    "Close": path, "Volume": np.full(k, 5e6)}, index=idx)
                intraday = pd.concat([old, intraday])
            return intraday.tail(DAYS[period])
        step = STEP[interval]
        df = m if step == 1 else m.groupby(m.index.floor(f"{step}min")).agg(
            {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
        if step == 60:  # Yahoo hourly bars start at the session open (9:30)
            off = pd.Timedelta(minutes=30)
            g = (m.index - off).floor("60min") + off
            df = m.groupby(g).agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
        days = sorted(set(df.index.date))[-DAYS[period]:]
        return df[np.isin(df.index.date, days)]

    def get_info(self):
        return {"symbol": self.symbol, "shortName": f"{self.symbol} Demo Corp", "exchange": "NMS",
                "fullExchangeName": "NasdaqGS", "currency": "USD", "quoteType": "EQUITY"}

    def option_chain(self, exp):
        price = float(_minutes(self.symbol)["Close"].iloc[-1])
        rng = np.random.default_rng(_seed(self.symbol + exp))
        strikes = np.round(np.arange(price * 0.7, price * 1.3, max(1, round(price * 0.025))), 0)
        def side(kind):
            centre = price * (1.05 if kind == "call" else 0.95)
            oi = (rng.integers(100, 6000, len(strikes)) * np.exp(-np.abs(strikes - centre) / (price * 0.08))).round()
            vol = (oi * rng.uniform(0.05, 1.6, len(strikes))).round()
            intrinsic = np.maximum(0, (price - strikes) if kind == "call" else (strikes - price))
            mid = intrinsic + price * 0.02 * np.exp(-np.abs(strikes - price) / (price * 0.1))
            return pd.DataFrame({"strike": strikes, "openInterest": oi, "volume": vol, "bid": mid * 0.97,
                                 "ask": mid * 1.03, "lastPrice": mid, "impliedVolatility": rng.uniform(0.25, 0.45, len(strikes)),
                                 "contractSize": "REGULAR"})
        return Chain(side("call"), side("put"), {"regularMarketPrice": price})


class Search:
    def __init__(self, q, **kw):
        q = q.upper()
        universe = [("AAPL", "Apple Inc."), ("AMZN", "Amazon.com"), ("AMD", "Advanced Micro Devices"),
                    ("NVDA", "NVIDIA Corp"), ("TSLA", "Tesla Inc"), ("SAP.DE", "SAP SE")]
        self.quotes = [{"symbol": s, "shortname": n, "exchDisp": "NASDAQ", "quoteType": "EQUITY", "typeDisp": "Equity"}
                       for s, n in universe if s.startswith(q) or q in n.upper()]


class FakeYF:
    Ticker = Ticker
    Search = Search
