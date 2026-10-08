"""Technical analysis.

`technical_rating` re-implements TradingView's *published* "Technical Ratings" method
(https://www.tradingview.com/support/solutions/43000614331-technical-ratings/) from daily
price history, so no TradingView data is downloaded. Results can differ slightly from the
TradingView website because of data-vendor and smoothing differences.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def _sma(s, n):
    return s.rolling(n, min_periods=n).mean()


def _ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def _wma(s, n):
    weights = np.arange(1, n + 1, dtype=float)
    return s.rolling(n, min_periods=n).apply(lambda x: float(np.dot(x, weights) / weights.sum()), raw=True)


def _rma(s, n):  # Wilder smoothing
    return s.ewm(alpha=1 / n, adjust=False).mean()


def _rsi(close, n=14):
    delta = close.diff()
    gain = _rma(delta.clip(lower=0), n)
    loss = _rma((-delta).clip(lower=0), n)
    rsi = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    flat = np.where(gain == 0, 50.0, 100.0)
    return pd.Series(np.where(loss == 0, flat, rsi), index=close.index)


def _vote(buy, sell):
    return 1 if buy else (-1 if sell else 0)


def _ok(*values):
    return all(v is not None and not (isinstance(v, float) and math.isnan(v)) for v in values)


def _label(score):
    if score > 0.5:
        return "Strong Buy"
    if score > 0.1:
        return "Buy"
    if score >= -0.1:
        return "Neutral"
    if score >= -0.5:
        return "Sell"
    return "Strong Sell"


def clean_history(df):
    """Normalise a yfinance history frame to Open/High/Low/Close/Volume floats."""
    if df is None or len(df) == 0:
        return None
    df = df.rename(columns=str.title)
    needed = ["Open", "High", "Low", "Close", "Volume"]
    if any(c not in df.columns for c in needed):
        return None
    df = df[needed].astype(float).dropna(subset=["Close", "High", "Low"])
    df = df[df["Close"] > 0]
    return df


def technical_rating(df):
    """Return TradingView-method MA / oscillator / overall ratings plus trend context."""
    df = clean_history(df)
    if df is None or len(df) < 210:
        return {"available": False, "reason": "Need at least 210 daily bars of price history."}
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
    price = float(c.iloc[-1])
    last = lambda s, i=1: float(s.iloc[-i])  # noqa: E731

    ma_votes = {}
    for n in (10, 20, 30, 50, 100, 200):
        for kind, series in (("SMA", _sma(c, n)), ("EMA", _ema(c, n))):
            val = last(series)
            ma_votes[f"{kind}{n}"] = _vote(val < price, val > price)
    hull = _wma(2 * _wma(c, 4) - _wma(c, 9), 3)  # Hull MA(9): sqrt(9) = 3
    ma_votes["HullMA9"] = _vote(last(hull) < price, last(hull) > price)
    vwma = (c * v).rolling(20).sum() / v.rolling(20).sum()
    vw = last(vwma)
    ma_votes["VWMA20"] = _vote(_ok(vw) and vw < price, _ok(vw) and vw > price)
    donch = lambda n: (h.rolling(n).max() + l.rolling(n).min()) / 2  # noqa: E731
    conv_s, base_s = donch(9), donch(26)
    conv, base = last(conv_s), last(base_s)
    # Leading spans are plotted 26 bars ahead, so today's cloud uses values from 25 bars ago.
    span_a = last(((conv_s + base_s) / 2).shift(25))
    span_b = last(donch(52).shift(25))
    ma_votes["Ichimoku"] = _vote(span_a > span_b and base > span_a and conv > base and price > conv,
                                 span_a < span_b and base < span_a and conv < base and price < conv)

    sma50 = _sma(c, 50)
    uptrend, downtrend = price > last(sma50), price < last(sma50)
    osc = {}
    rsi = _rsi(c, 14)
    osc["RSI14"] = _vote(last(rsi) < 30 and last(rsi) > last(rsi, 2), last(rsi) > 70 and last(rsi) < last(rsi, 2))
    lo14, hi14 = l.rolling(14).min(), h.rolling(14).max()
    k_raw = 100 * (c - lo14) / (hi14 - lo14).replace(0, np.nan)
    k = _sma(k_raw, 3)
    d = _sma(k, 3)
    osc["Stoch"] = _vote(last(k) < 20 and last(d) < 20 and last(k) > last(d),
                         last(k) > 80 and last(d) > 80 and last(k) < last(d))
    tp = (h + l + c) / 3
    md = tp.rolling(20).apply(lambda x: float(np.mean(np.abs(x - x.mean()))), raw=True)
    cci = (tp - _sma(tp, 20)) / (0.015 * md.replace(0, np.nan))
    osc["CCI20"] = _vote(last(cci) < -100 and last(cci) > last(cci, 2), last(cci) > 100 and last(cci) < last(cci, 2))
    up, down = h.diff(), -l.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=c.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=c.index)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = _rma(tr, 14)
    plus_di = 100 * _rma(plus_dm, 14) / atr
    minus_di = 100 * _rma(minus_dm, 14) / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = _rma(dx.fillna(0), 14)
    osc["ADX14"] = _vote(last(plus_di) > last(minus_di) and last(adx) > 20 and last(adx) > last(adx, 2),
                         last(plus_di) < last(minus_di) and last(adx) > 20 and last(adx) < last(adx, 2))
    med = (h + l) / 2
    ao = _sma(med, 5) - _sma(med, 34)
    a0, a1, a2 = last(ao), last(ao, 2), last(ao, 3)
    osc["AO"] = _vote((a1 < 0 <= a0) or (a0 > 0 and a1 > 0 and a0 > a1 and a1 <= a2),
                      (a1 > 0 >= a0) or (a0 < 0 and a1 < 0 and a0 < a1 and a1 >= a2))
    mom = c - c.shift(10)
    osc["Mom10"] = _vote(last(mom) > last(mom, 2), last(mom) < last(mom, 2))
    macd = _ema(c, 12) - _ema(c, 26)
    signal = _ema(macd, 9)
    osc["MACD"] = _vote(last(macd) > last(signal), last(macd) < last(signal))
    rsi_lo, rsi_hi = rsi.rolling(14).min(), rsi.rolling(14).max()
    srsi = 100 * (rsi - rsi_lo) / (rsi_hi - rsi_lo).replace(0, np.nan)
    sk = _sma(srsi, 3)
    sd = _sma(sk, 3)
    osc["StochRSI"] = _vote(downtrend and last(sk) < 20 and last(sd) < 20 and last(sk) > last(sd),
                            uptrend and last(sk) > 80 and last(sd) > 80 and last(sk) < last(sd))
    wr = -100 * (hi14 - c) / (hi14 - lo14).replace(0, np.nan)
    osc["W%R"] = _vote(last(wr) < -80 and last(wr) > last(wr, 2), last(wr) > -20 and last(wr) < last(wr, 2))
    ema13 = _ema(c, 13)
    bull, bear = h - ema13, l - ema13
    osc["BBPower"] = _vote(uptrend and last(bear) < 0 and last(bear) > last(bear, 2),
                           downtrend and last(bull) > 0 and last(bull) < last(bull, 2))
    prev_c = c.shift()
    bp = c - pd.concat([l, prev_c], axis=1).min(axis=1)
    trr = pd.concat([h, prev_c], axis=1).max(axis=1) - pd.concat([l, prev_c], axis=1).min(axis=1)
    avg = lambda n: bp.rolling(n).sum() / trr.rolling(n).sum()  # noqa: E731
    uo = 100 * (4 * avg(7) + 2 * avg(14) + avg(28)) / 7
    osc["UO"] = _vote(last(uo) > 70, last(uo) < 30)

    ma_score = sum(ma_votes.values()) / len(ma_votes)
    osc_score = sum(osc.values()) / len(osc)
    overall = (ma_score + osc_score) / 2
    sma200 = last(_sma(c, 200))
    ret = lambda n: (price / float(c.iloc[-n - 1]) - 1) if len(c) > n else None  # noqa: E731
    hi52 = float(h.iloc[-252:].max())
    lo52 = float(l.iloc[-252:].min())
    return {
        "available": True,
        "price": price,
        "price_date": str(df.index[-1].date()) if hasattr(df.index[-1], "date") else str(df.index[-1]),
        "rating": overall,
        "rating_label": _label(overall),
        "ma_rating": ma_score,
        "ma_label": _label(ma_score),
        "osc_rating": osc_score,
        "osc_label": _label(osc_score),
        "ma_votes": ma_votes,
        "osc_votes": osc,
        "rsi14": last(rsi),
        "sma50": last(sma50),
        "sma200": sma200,
        "golden_cross": last(sma50) > sma200,
        "above_sma200": price > sma200,
        "macd_hist": last(macd) - last(signal),
        "return_1m": ret(21),
        "return_3m": ret(63),
        "return_12m": ret(252),
        "high_52w": hi52,
        "low_52w": lo52,
        "from_high_52w": price / hi52 - 1 if hi52 else None,
        "spark": [round(float(x), 4) for x in c.iloc[-126:].tolist()],
    }
