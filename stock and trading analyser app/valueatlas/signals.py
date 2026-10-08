"""Multi-indicator signal engine for one price series (any timeframe).

Every signal is a vote per bar: +1 bullish, -1 bearish, 0 neutral. Votes are grouped
into categories, averaged, and weighted into one score from -1 (very bearish) to +1
(very bullish). Everything is vectorised over the whole history, so the same score can
be checked against what the price did next (the "hit rate").

The 26 indicators of TradingView's published *Technical Ratings* method are included
with TradingView's own buy/sell rules; the rest are standard textbook definitions.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

CATEGORY_WEIGHTS = {
    "Trend": 0.30,
    "Momentum": 0.25,
    "Volume": 0.12,
    "Volatility": 0.08,
    "Patterns": 0.10,
    "Strategies": 0.15,
}
WARMUP = 60  # bars ignored at the start when measuring hit rates / backtests


# --------------------------------------------------------------------------- primitives

def sma(s, n):
    return s.rolling(n, min_periods=n).mean()


def ema(s, n):
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rma(s, n):
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def wma(s, n):
    w = np.arange(1, n + 1, dtype=float)
    return s.rolling(n, min_periods=n).apply(lambda x: float(np.dot(x, w) / w.sum()), raw=True)


def hma(s, n):
    return wma(2 * wma(s, max(1, n // 2)) - wma(s, n), max(1, int(math.sqrt(n))))


def rsi(c, n=14):
    d = c.diff()
    gain, loss = rma(d.clip(lower=0), n), rma((-d).clip(lower=0), n)
    out = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    return out.where(loss != 0, np.where(gain > 0, 100.0, 50.0))


def true_range(h, l, c):
    pc = c.shift()
    return pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)


def vote(buy, sell):
    buy = np.asarray(buy, dtype=bool)
    sell = np.asarray(sell, dtype=bool)
    return np.where(buy & ~sell, 1, np.where(sell & ~buy, -1, 0)).astype(float)


def supertrend(h, l, c, n=10, mult=3.0):
    atr = rma(true_range(h, l, c), n).to_numpy()
    hl2 = ((h + l) / 2).to_numpy()
    cc = c.to_numpy()
    up, dn = hl2 - mult * atr, hl2 + mult * atr
    line = np.full(len(cc), np.nan)
    direction = np.zeros(len(cc))
    fu, fd, d = np.nan, np.nan, 1
    for i in range(len(cc)):
        if np.isnan(atr[i]):
            continue
        fu = up[i] if np.isnan(fu) or cc[i - 1] < fu else max(up[i], fu)
        fd = dn[i] if np.isnan(fd) or cc[i - 1] > fd else min(dn[i], fd)
        if d == -1 and cc[i] > fd:
            d = 1
        elif d == 1 and cc[i] < fu:
            d = -1
        direction[i] = d
        line[i] = fu if d == 1 else fd
    return pd.Series(line, index=c.index), pd.Series(direction, index=c.index)


def parabolic_sar(h, l, step=0.02, max_step=0.2):
    hh, ll = h.to_numpy(), l.to_numpy()
    n = len(hh)
    sar = np.full(n, np.nan)
    direction = np.zeros(n)
    if n < 3:
        return pd.Series(sar, index=h.index), pd.Series(direction, index=h.index)
    up = hh[1] >= hh[0]
    af, ep = step, (hh[1] if up else ll[1])
    s = ll[0] if up else hh[0]
    for i in range(2, n):
        s = s + af * (ep - s)
        if up:
            s = min(s, ll[i - 1], ll[i - 2])
            if ll[i] < s:
                up, s, ep, af = False, ep, ll[i], step
            elif hh[i] > ep:
                ep, af = hh[i], min(af + step, max_step)
        else:
            s = max(s, hh[i - 1], hh[i - 2])
            if hh[i] > s:
                up, s, ep, af = True, ep, hh[i], step
            elif ll[i] < ep:
                ep, af = ll[i], min(af + step, max_step)
        sar[i] = s
        direction[i] = 1 if up else -1
    return pd.Series(sar, index=h.index), pd.Series(direction, index=h.index)


def session_vwap(df):
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    v = df["Volume"]
    day = pd.Index(df.index.date)
    num = (tp * v).groupby(day).cumsum().to_numpy()
    den = v.groupby(day).cumsum().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        return pd.Series(np.where(den > 0, num / den, np.nan), index=df.index)


def swings(h, l, k=2):
    """Swing highs/lows (k bars each side). Returned at the bar they become *known* (k bars later)."""
    win = 2 * k + 1
    ph = h.where(h == h.rolling(win, center=True).max())
    pl = l.where(l == l.rolling(win, center=True).min())
    return ph, pl


# --------------------------------------------------------------------------- candlestick patterns

def candle_patterns(df):
    """Return (votes Series, list of (index position, name, +1/-1/0))."""
    o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]
    body = (c - o).abs()
    rng = (h - l).replace(0, np.nan)
    upper = h - pd.concat([o, c], axis=1).max(axis=1)
    lower = pd.concat([o, c], axis=1).min(axis=1) - l
    avg = body.rolling(14, min_periods=5).mean()
    green, red = c > o, c < o
    trend = sma(c, 10)
    prior_down = c.shift(1) < trend.shift(1)
    prior_up = c.shift(1) > trend.shift(1)
    o1, c1, b1 = o.shift(1), c.shift(1), body.shift(1)
    o2, c2, b2 = o.shift(2), c.shift(2), body.shift(2)
    mid1 = (o1 + c1) / 2
    small = body <= 0.35 * rng
    pats = [
        ("Bullish engulfing", 1, red.shift(1) & green & (o <= c1) & (c >= o1) & (body > b1)),
        ("Bearish engulfing", -1, green.shift(1) & red & (o >= c1) & (c <= o1) & (body > b1)),
        ("Hammer", 1, prior_down & small & (lower >= 2 * body) & (upper <= 0.25 * rng)),
        ("Hanging man", -1, prior_up & small & (lower >= 2 * body) & (upper <= 0.25 * rng)),
        ("Inverted hammer", 1, prior_down & small & (upper >= 2 * body) & (lower <= 0.25 * rng)),
        ("Shooting star", -1, prior_up & small & (upper >= 2 * body) & (lower <= 0.25 * rng)),
        ("Morning star", 1, red.shift(2) & (b2 > avg) & (b1 < 0.5 * avg) & green & (c > (o2 + c2) / 2)),
        ("Evening star", -1, green.shift(2) & (b2 > avg) & (b1 < 0.5 * avg) & red & (c < (o2 + c2) / 2)),
        ("Three white soldiers", 1, green & green.shift(1) & green.shift(2) & (c > c1) & (c1 > c2)
         & (body > 0.6 * avg) & (b1 > 0.6 * avg) & (b2 > 0.6 * avg)),
        ("Three black crows", -1, red & red.shift(1) & red.shift(2) & (c < c1) & (c1 < c2)
         & (body > 0.6 * avg) & (b1 > 0.6 * avg) & (b2 > 0.6 * avg)),
        ("Piercing line", 1, red.shift(1) & (b1 > avg) & green & (o < c1) & (c > mid1) & (c < o1)),
        ("Dark cloud cover", -1, green.shift(1) & (b1 > avg) & red & (o > c1) & (c < mid1) & (c > o1)),
        ("Bullish harami", 1, prior_down & red.shift(1) & (b1 > avg) & green & (o > c1) & (c < o1)),
        ("Bearish harami", -1, prior_up & green.shift(1) & (b1 > avg) & red & (o < c1) & (c > o1)),
        ("Bullish marubozu", 1, green & (body >= 0.92 * rng) & (body > 1.3 * avg)),
        ("Bearish marubozu", -1, red & (body >= 0.92 * rng) & (body > 1.3 * avg)),
        ("Doji", 0, body <= 0.08 * rng),
    ]
    sig = pd.Series(np.nan, index=df.index)
    found = []
    for name, direction, mask in pats:
        mask = mask.fillna(False).to_numpy(dtype=bool)
        for i in np.flatnonzero(mask):
            found.append((int(i), name, direction))
        if direction:
            sig[mask] = direction  # later (stronger multi-bar) patterns overwrite single-bar ones
    votes = sig.ffill(limit=2).fillna(0)  # a pattern counts for the bar it forms and the next two
    found.sort()
    return votes, found


# --------------------------------------------------------------------------- strategies

def _position_loop(n, enter_long, exit_long, enter_short, exit_short):
    pos = np.zeros(n)
    p = 0
    for i in range(n):
        if p == 1 and exit_long[i]:
            p = 0
        elif p == -1 and exit_short[i]:
            p = 0
        if p <= 0 and enter_long[i]:
            p = 1
        elif p >= 0 and enter_short[i]:
            p = -1
        pos[i] = p
    return pos


def backtest(close, pos, warmup=WARMUP):
    """Simple close-to-close backtest, no costs. Position is applied from the next bar."""
    ret = close.pct_change().fillna(0).to_numpy()
    p = np.nan_to_num(np.asarray(pos, dtype=float))
    start = min(warmup, max(0, len(p) - 2))
    strat = np.zeros(len(p))
    strat[1:] = p[:-1] * ret[1:]
    strat, rr, pp = strat[start:], ret[start:], p[start:]
    trades, cur, cur_ret = [], 0, 1.0
    for i in range(len(pp)):
        if i > 0:
            cur_ret *= 1 + pp[i - 1] * rr[i]
        if pp[i] != cur:
            if cur != 0:
                trades.append(cur_ret - 1)
            cur, cur_ret = pp[i], 1.0
    if cur != 0:
        trades.append(cur_ret - 1)
    total = float(np.prod(1 + strat) - 1)
    hold = float(np.prod(1 + rr) - 1)
    return {"trades": len(trades), "win_rate": (sum(t > 0 for t in trades) / len(trades)) if trades else None,
            "total_return": total, "buy_hold": hold, "exposure": float(np.mean(pp != 0)) if len(pp) else 0,
            "bars": int(len(pp))}


# --------------------------------------------------------------------------- the engine

def _f(x, d=2):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(x):
        return "—"
    return f"{x:,.{d}f}"


def label(score, strong=0.4, weak=0.1):
    if score is None or not math.isfinite(score):
        return "No data"
    if score > strong:
        return "Strong Bullish"
    if score > weak:
        return "Bullish"
    if score >= -weak:
        return "Neutral"
    if score >= -strong:
        return "Bearish"
    return "Strong Bearish"


def tv_label(score):
    if score is None or not math.isfinite(score):
        return "No data"
    return ("Strong Buy" if score > 0.5 else "Buy" if score > 0.1 else "Neutral" if score >= -0.1
            else "Sell" if score >= -0.5 else "Strong Sell")


def analyse(df, intraday=True, detail=True):
    """Run every signal on an OHLCV frame. Returns a JSON-friendly dict."""
    df = df[["Open", "High", "Low", "Close", "Volume"]].astype(float).dropna(subset=["Open", "High", "Low", "Close"])
    df = df[df["Close"] > 0]
    n = len(df)
    if n < 60:
        return {"available": False, "reason": f"Only {n} candles available – need at least 60."}
    o, h, l, c, v = (df[k] for k in ("Open", "High", "Low", "Close", "Volume"))
    has_volume = bool(v.tail(50).sum() > 0)
    sigs = []

    def add(name, cat, votes, value="", rule="", tv=None):
        sigs.append({"name": name, "category": cat, "votes": pd.Series(np.asarray(votes, dtype=float), index=df.index),
                     "value": value, "rule": rule, "tv": tv})

    price = c
    # ---------------- Trend: TradingView's 15 moving-average votes
    mas = {}
    for p_ in (10, 20, 30, 50, 100, 200):
        for kind, fn in (("SMA", sma), ("EMA", ema)):
            s = fn(c, p_)
            mas[f"{kind}{p_}"] = s
            add(f"{kind} {p_}", "Trend", vote(s < price, s > price), _f(s.iloc[-1]),
                f"Price above the {p_}-bar {'simple' if kind == 'SMA' else 'exponential'} average = buy", "ma")
    hull = hma(c, 9)
    add("Hull MA 9", "Trend", vote(hull < price, hull > price), _f(hull.iloc[-1]), "Price above Hull MA = buy", "ma")
    vwma = (c * v).rolling(20).sum() / v.rolling(20).sum().replace(0, np.nan) if has_volume else sma(c, 20)
    add("VWMA 20", "Trend", vote(vwma < price, vwma > price), _f(vwma.iloc[-1]), "Price above volume-weighted MA = buy", "ma")
    don = lambda p_: (h.rolling(p_).max() + l.rolling(p_).min()) / 2  # noqa: E731
    conv, base = don(9), don(26)
    span_a, span_b = ((conv + base) / 2).shift(25), don(52).shift(25)
    add("Ichimoku cloud", "Trend",
        vote((span_a > span_b) & (base > span_a) & (conv > base) & (c > conv),
             (span_a < span_b) & (base < span_a) & (conv < base) & (c < conv)),
        f"Conv {_f(conv.iloc[-1])} / Base {_f(base.iloc[-1])}", "TradingView Ichimoku rule (all four conditions)", "ma")

    # ---------------- Trend: extras
    st_line, st_dir = supertrend(h, l, c)
    add("Supertrend (10, 3)", "Trend", st_dir, _f(st_line.iloc[-1]), "Price above the Supertrend line = bullish")
    sar, sar_dir = parabolic_sar(h, l)
    add("Parabolic SAR", "Trend", sar_dir, _f(sar.iloc[-1]), "SAR dots below price = bullish")
    e9, e21 = ema(c, 9), ema(c, 21)
    add("EMA 9 / 21", "Trend", vote(e9 > e21, e9 < e21), f"{_f(e9.iloc[-1])} / {_f(e21.iloc[-1])}", "Fast EMA above slow EMA = bullish")
    tr = true_range(h, l, c)
    atr14 = rma(tr, 14)
    up_m, dn_m = h.diff(), -l.diff()
    plus_dm = pd.Series(np.where((up_m > dn_m) & (up_m > 0), up_m, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((dn_m > up_m) & (dn_m > 0), dn_m, 0.0), index=df.index)
    pdi, mdi = 100 * rma(plus_dm, 14) / atr14, 100 * rma(minus_dm, 14) / atr14
    adx = rma((100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)).fillna(0), 14)
    add("DMI trend (ADX > 20)", "Trend", vote((adx > 20) & (pdi > mdi), (adx > 20) & (pdi < mdi)),
        f"ADX {_f(adx.iloc[-1], 1)} · +DI {_f(pdi.iloc[-1], 1)} / −DI {_f(mdi.iloc[-1], 1)}", "Strong trend in the direction of the larger DI")
    ar_up = 100 * h.rolling(26).apply(lambda x: float(np.argmax(x)), raw=True) / 25
    ar_dn = 100 * l.rolling(26).apply(lambda x: float(np.argmin(x)), raw=True) / 25
    add("Aroon (25)", "Trend", vote((ar_up > 70) & (ar_dn < 30), (ar_dn > 70) & (ar_up < 30)),
        f"Up {_f(ar_up.iloc[-1], 0)} / Down {_f(ar_dn.iloc[-1], 0)}", "Recent new highs (Up > 70, Down < 30) = bullish")
    idx = pd.Series(np.arange(n, dtype=float), index=df.index)
    cov = c.rolling(20).cov(idx)
    slope = cov / idx.rolling(20).var()
    corr = c.rolling(20).corr(idx)
    add("Linear regression (20)", "Trend", vote((slope > 0) & (corr > 0.5), (slope < 0) & (corr < -0.5)),
        f"slope {_f(slope.iloc[-1], 3)}/bar, R {_f(corr.iloc[-1])}", "Clear up- or down-sloping regression line")
    ha_c = (o + h + l + c) / 4
    ov, cv = o.to_numpy(), c.to_numpy()
    hcv = ha_c.to_numpy()
    hv = np.empty(n)
    hv[0] = (ov[0] + cv[0]) / 2
    for i in range(1, n):
        hv[i] = (hv[i - 1] + hcv[i - 1]) / 2
    ha_o = pd.Series(hv, index=df.index)
    ha_green = ha_c > ha_o
    add("Heikin-Ashi", "Trend", vote(ha_green & ha_green.shift(1, fill_value=False), ~ha_green & ~ha_green.shift(1, fill_value=True)),
        "green" if ha_green.iloc[-1] else "red", "Two Heikin-Ashi candles of the same colour")
    ph, pl = swings(h, l)
    ph_vals, pl_vals = ph.to_numpy(), pl.to_numpy()
    struct = np.zeros(n)
    highs, lows = [], []
    for i in range(n):
        j = i - 2  # a swing at bar j is confirmed at bar j + 2
        if j >= 0:
            if not np.isnan(ph_vals[j]):
                highs.append(ph_vals[j])
            if not np.isnan(pl_vals[j]):
                lows.append(pl_vals[j])
        if len(highs) >= 2 and len(lows) >= 2:
            if highs[-1] > highs[-2] and lows[-1] > lows[-2]:
                struct[i] = 1
            elif highs[-1] < highs[-2] and lows[-1] < lows[-2]:
                struct[i] = -1
    add("Market structure", "Trend", struct,
        {1: "higher highs & higher lows", -1: "lower highs & lower lows", 0: "mixed"}[int(struct[-1])],
        "Last two swing highs and lows both rising = bullish")

    # ---------------- Momentum: TradingView's 11 oscillators
    r14 = rsi(c, 14)
    add("RSI (14)", "Momentum", vote((r14 < 30) & (r14 > r14.shift()), (r14 > 70) & (r14 < r14.shift())),
        _f(r14.iloc[-1], 1), "TradingView: below 30 and rising = buy; above 70 and falling = sell", "osc")
    lo14, hi14 = l.rolling(14).min(), h.rolling(14).max()
    k = sma(100 * (c - lo14) / (hi14 - lo14).replace(0, np.nan), 3)
    d = sma(k, 3)
    add("Stochastic (14, 3, 3)", "Momentum", vote((k < 20) & (d < 20) & (k > d), (k > 80) & (d > 80) & (k < d)),
        f"%K {_f(k.iloc[-1], 1)} / %D {_f(d.iloc[-1], 1)}", "Oversold and %K crossing up = buy", "osc")
    tp = (h + l + c) / 3
    md = tp.rolling(20).apply(lambda x: float(np.mean(np.abs(x - x.mean()))), raw=True)
    cci = (tp - sma(tp, 20)) / (0.015 * md.replace(0, np.nan))
    add("CCI (20)", "Momentum", vote((cci < -100) & (cci > cci.shift()), (cci > 100) & (cci < cci.shift())),
        _f(cci.iloc[-1], 0), "Below −100 and rising = buy", "osc")
    add("ADX (14)", "Momentum", vote((pdi > mdi) & (adx > 20) & (adx > adx.shift()), (pdi < mdi) & (adx > 20) & (adx < adx.shift())),
        _f(adx.iloc[-1], 1), "TradingView ADX rule", "osc")
    med = (h + l) / 2
    ao = sma(med, 5) - sma(med, 34)
    a1, a2 = ao.shift(1), ao.shift(2)
    add("Awesome Oscillator", "Momentum",
        vote(((a1 < 0) & (ao >= 0)) | ((ao > 0) & (a1 > 0) & (ao > a1) & (a1 <= a2)),
             ((a1 > 0) & (ao <= 0)) | ((ao < 0) & (a1 < 0) & (ao < a1) & (a1 >= a2))),
        _f(ao.iloc[-1]), "Zero-line cross or saucer", "osc")
    mom = c - c.shift(10)
    add("Momentum (10)", "Momentum", vote(mom > mom.shift(), mom < mom.shift()), _f(mom.iloc[-1]), "Momentum rising = buy", "osc")
    macd = ema(c, 12) - ema(c, 26)
    sig_line = ema(macd, 9)
    hist = macd - sig_line
    add("MACD (12, 26, 9)", "Momentum", vote(macd > sig_line, macd < sig_line),
        f"{_f(macd.iloc[-1], 3)} vs signal {_f(sig_line.iloc[-1], 3)}", "MACD above its signal line = buy", "osc")
    rlo, rhi = r14.rolling(14).min(), r14.rolling(14).max()
    sk = sma(100 * (r14 - rlo) / (rhi - rlo).replace(0, np.nan), 3)
    sd = sma(sk, 3)
    s50 = sma(c, 50)
    add("Stochastic RSI", "Momentum", vote((c < s50) & (sk < 20) & (sd < 20) & (sk > sd), (c > s50) & (sk > 80) & (sd > 80) & (sk < sd)),
        f"K {_f(sk.iloc[-1], 1)} / D {_f(sd.iloc[-1], 1)}", "TradingView Stoch RSI rule", "osc")
    wr = -100 * (hi14 - c) / (hi14 - lo14).replace(0, np.nan)
    add("Williams %R (14)", "Momentum", vote((wr < -80) & (wr > wr.shift()), (wr > -20) & (wr < wr.shift())),
        _f(wr.iloc[-1], 1), "Below −80 and rising = buy", "osc")
    e13 = ema(c, 13)
    bull_p, bear_p = h - e13, l - e13
    add("Bull Bear Power", "Momentum", vote((c > s50) & (bear_p < 0) & (bear_p > bear_p.shift()), (c < s50) & (bull_p > 0) & (bull_p < bull_p.shift())),
        _f((bull_p + bear_p).iloc[-1]), "TradingView Bull Bear Power rule", "osc")
    pc = c.shift()
    bp = c - pd.concat([l, pc], axis=1).min(axis=1)
    trr = pd.concat([h, pc], axis=1).max(axis=1) - pd.concat([l, pc], axis=1).min(axis=1)
    avg_ = lambda p_: bp.rolling(p_).sum() / trr.rolling(p_).sum().replace(0, np.nan)  # noqa: E731
    uo = 100 * (4 * avg_(7) + 2 * avg_(14) + avg_(28)) / 7
    add("Ultimate Oscillator", "Momentum", vote(uo > 70, uo < 30), _f(uo.iloc[-1], 1), "Above 70 = buy, below 30 = sell", "osc")
    # extras
    add("RSI bias", "Momentum", vote(r14 > 55, r14 < 45), _f(r14.iloc[-1], 1), "RSI above 55 = bullish momentum, below 45 = bearish")
    add("MACD histogram", "Momentum", vote((hist > 0) & (hist > hist.shift()), (hist < 0) & (hist < hist.shift())),
        _f(hist.iloc[-1], 3), "Histogram growing above zero = bullish")
    roc = 100 * (c / c.shift(12) - 1)
    add("Rate of change (12)", "Momentum", vote(roc > 0, roc < 0), f"{_f(roc.iloc[-1])}%", "Price higher than 12 bars ago")
    trix = 100 * ema(ema(ema(c, 15), 15), 15).pct_change()
    add("TRIX (15)", "Momentum", vote(trix > 0, trix < 0), _f(trix.iloc[-1], 4), "TRIX above zero = bullish")
    div = np.zeros(n)
    lows_i, highs_i = [], []
    rv = r14.to_numpy()
    last_div, last_at = 0, -999
    for i in range(n):
        j = i - 2
        if j >= 0:
            if not np.isnan(pl_vals[j]):
                lows_i.append(j)
                if len(lows_i) >= 2:
                    a, b = lows_i[-2], lows_i[-1]
                    if pl_vals[b] < pl_vals[a] and rv[b] > rv[a]:
                        last_div, last_at = 1, i
            if not np.isnan(ph_vals[j]):
                highs_i.append(j)
                if len(highs_i) >= 2:
                    a, b = highs_i[-2], highs_i[-1]
                    if ph_vals[b] > ph_vals[a] and rv[b] < rv[a]:
                        last_div, last_at = -1, i
        if i - last_at <= 10:
            div[i] = last_div
    add("RSI divergence", "Momentum", div,
        {1: "bullish divergence", -1: "bearish divergence", 0: "none"}[int(div[-1])],
        "Price makes a lower low while RSI makes a higher low (or the reverse)")

    # ---------------- Volume
    if has_volume:
        obv = (np.sign(c.diff()).fillna(0) * v).cumsum()
        obv_e = ema(obv, 20)
        add("On-balance volume", "Volume", vote(obv > obv_e, obv < obv_e), "above its 20-EMA" if obv.iloc[-1] > obv_e.iloc[-1] else "below its 20-EMA",
            "OBV above its average = buyers in control")
        mfm = ((c - l) - (h - c)) / (h - l).replace(0, np.nan)
        cmf = (mfm * v).rolling(20).sum() / v.rolling(20).sum().replace(0, np.nan)
        add("Chaikin Money Flow (20)", "Volume", vote(cmf > 0.05, cmf < -0.05), _f(cmf.iloc[-1], 3), "Above +0.05 = accumulation")
        raw = tp * v
        pos_f = raw.where(tp > tp.shift(), 0).rolling(14).sum()
        neg_f = raw.where(tp < tp.shift(), 0).rolling(14).sum()
        mfi = 100 - 100 / (1 + pos_f / neg_f.replace(0, np.nan))
        add("Money Flow Index (14)", "Volume",
            vote(((mfi < 20) & (mfi > mfi.shift())) | ((mfi > 50) & (mfi < 80)), ((mfi > 80) & (mfi < mfi.shift())) | ((mfi < 50) & (mfi > 20))),
            _f(mfi.iloc[-1], 1), "Volume-weighted RSI: above 50 bullish, extremes reverse")
        vw = session_vwap(df) if intraday else (tp * v).rolling(20).sum() / v.rolling(20).sum().replace(0, np.nan)
        add("VWAP" if intraday else "VWAP (20 bars)", "Volume", vote(c > vw, c < vw), _f(vw.iloc[-1]),
            "Price above the volume-weighted average price = bullish")
        vavg = sma(v, 20)
        spike = v > 1.8 * vavg
        add("Volume spike", "Volume", pd.Series(vote(spike & (c > o), spike & (c < o)), index=df.index).replace(0, np.nan).ffill(limit=3).fillna(0),
            f"{_f(v.iloc[-1] / vavg.iloc[-1] if vavg.iloc[-1] else np.nan, 1)}× avg", "Big volume on a green (red) candle in the last 3 bars")
        fi = ema(c.diff() * v, 13)
        add("Force Index (13)", "Volume", vote(fi > 0, fi < 0), _f(fi.iloc[-1], 0), "Positive force = buyers")
        ad = (mfm.fillna(0) * v).cumsum()
        add("Accumulation/Distribution", "Volume", vote(ad > ad.shift(5), ad < ad.shift(5)), "rising" if ad.iloc[-1] > ad.iloc[-6] else "falling",
            "A/D line higher than 5 bars ago")
    else:
        vw = pd.Series(np.nan, index=df.index)

    # ---------------- Volatility / breakouts
    mid = sma(c, 20)
    sd20 = c.rolling(20).std()
    bb_u, bb_l = mid + 2 * sd20, mid - 2 * sd20
    pb = (c - bb_l) / (bb_u - bb_l).replace(0, np.nan)
    add("Bollinger %B", "Volatility", vote(pb > 0.8, pb < 0.2), _f(pb.iloc[-1]), "Riding the upper band = strong; the lower band = weak")
    bw = (bb_u - bb_l) / mid
    squeeze = bw <= bw.rolling(120, min_periods=40).quantile(0.2)
    sq_recent = squeeze.rolling(5, min_periods=1).max().astype(bool)
    add("Bollinger squeeze breakout", "Volatility", vote(sq_recent & (c > bb_u), sq_recent & (c < bb_l)),
        "squeeze" if squeeze.iloc[-1] else "normal", "Break out of a tight Bollinger squeeze")
    kc_mid = ema(c, 20)
    kc_u, kc_l = kc_mid + 2 * atr14, kc_mid - 2 * atr14
    add("Keltner channel", "Volatility", vote(c > kc_u, c < kc_l), f"{_f(kc_l.iloc[-1])} – {_f(kc_u.iloc[-1])}", "Close outside the Keltner channel")
    hh20, ll20 = h.rolling(20).max().shift(1), l.rolling(20).min().shift(1)
    add("Donchian 20 breakout", "Volatility", pd.Series(vote(c > hh20, c < ll20), index=df.index).replace(0, np.nan).ffill(limit=5).fillna(0),
        f"{_f(ll20.iloc[-1])} – {_f(hh20.iloc[-1])}", "New 20-bar high (low) in the last 5 bars")
    if intraday:
        day = pd.Index(df.index.date)
        dfd = df.groupby(day).agg({"High": "max", "Low": "min", "Close": "last"})
        prev = dfd.shift(1)
        piv = ((prev["High"] + prev["Low"] + prev["Close"]) / 3).reindex(day).to_numpy()
        piv = pd.Series(piv, index=df.index)
        add("Pivot point (prev. session)", "Volatility", vote(c > piv, c < piv), _f(piv.iloc[-1]), "Price above yesterday's classic pivot")

    # ---------------- Patterns
    pat_votes, pat_found = candle_patterns(df)
    last_pats = [p for p in pat_found if p[0] >= n - 3]
    add("Candlestick patterns", "Patterns", pat_votes,
        ", ".join(sorted({p[1] for p in last_pats})) or "none in last 3 candles",
        "17 classic patterns (engulfing, hammer, stars, soldiers/crows, harami, …)")
    # breakout vs recent range as a chart pattern
    hi50, lo50 = h.rolling(50).max().shift(1), l.rolling(50).min().shift(1)
    rng50 = (hi50 - lo50) / c
    tight = rng50 < rng50.rolling(200, min_periods=50).quantile(0.3)
    add("Range breakout (50 bars)", "Patterns", pd.Series(vote(tight & (c > hi50), tight & (c < lo50)), index=df.index).replace(0, np.nan).ffill(limit=5).fillna(0),
        f"{_f(lo50.iloc[-1])} – {_f(hi50.iloc[-1])}", "Break out of a tight 50-bar consolidation")
    # double top / bottom from the last swings
    dt = np.zeros(n)
    tol = (atr14 * 0.6).to_numpy()
    sh, sl = [], []
    state, at = 0, -999
    for i in range(n):
        j = i - 2
        if j >= 0:
            if not np.isnan(ph_vals[j]):
                sh.append(ph_vals[j])
            if not np.isnan(pl_vals[j]):
                sl.append(pl_vals[j])
        if len(sh) >= 2 and len(sl) >= 1 and not np.isnan(tol[i]) and abs(sh[-1] - sh[-2]) < tol[i] and cv[i] < sl[-1]:
            state, at = -1, i
        if len(sl) >= 2 and len(sh) >= 1 and not np.isnan(tol[i]) and abs(sl[-1] - sl[-2]) < tol[i] and cv[i] > sh[-1]:
            state, at = 1, i
        if i - at <= 10:
            dt[i] = state
    add("Double top / bottom", "Patterns", dt, {1: "double bottom confirmed", -1: "double top confirmed", 0: "none"}[int(dt[-1])],
        "Two equal swing lows (highs) and a break of the middle swing")

    # ---------------- Strategies (each holds a position until its exit rule)
    strategies = []

    def strat(name, pos, rule):
        pos = np.asarray(pos, dtype=float)
        strategies.append({"name": name, "rule": rule, "position": int(pos[-1]), **backtest(c, pos)})
        add(name, "Strategies", pos, {1: "LONG", -1: "SHORT", 0: "flat"}[int(pos[-1])], rule)

    strat("EMA 9/21 crossover", np.sign((e9 - e21).fillna(0)), "Long when EMA 9 > EMA 21, short when below")
    strat("MACD crossover", np.sign((macd - sig_line).fillna(0)), "Long when MACD > signal, short when below")
    strat("Supertrend", st_dir.to_numpy(), "Follow the Supertrend (10, 3) direction")
    strat("Parabolic SAR", sar_dir.to_numpy(), "Follow the SAR flip")
    hh10, ll10 = h.rolling(10).max().shift(1), l.rolling(10).min().shift(1)
    strat("Turtle breakout 20/10", _position_loop(n, (c > hh20).to_numpy(), (c < ll10).to_numpy(), (c < ll20).to_numpy(), (c > hh10).to_numpy()),
          "Enter on a 20-bar breakout, exit on a 10-bar opposite break")
    r2 = rsi(c, 2)
    s200 = sma(c, 200)
    s200f = s200.fillna(sma(c, 50)).fillna(c)
    s5 = sma(c, 5)
    strat("RSI-2 mean reversion", _position_loop(n, ((r2 < 10) & (c > s200f)).to_numpy(), (c > s5).to_numpy(),
                                                  ((r2 > 90) & (c < s200f)).to_numpy(), (c < s5).to_numpy()),
          "Buy dips (RSI-2 < 10) in an uptrend, exit above the 5-bar average")
    strat("Bollinger reversion", _position_loop(n, (c < bb_l).to_numpy(), (c >= mid).to_numpy(), (c > bb_u).to_numpy(), (c <= mid).to_numpy()),
          "Fade moves outside the bands, exit at the middle band")
    s50f, s200x = sma(c, 50), sma(c, 200)
    strat("Golden / death cross", np.sign((s50f - s200x).fillna(0)), "Long when SMA 50 > SMA 200")
    if intraday and has_volume:
        strat("VWAP trend", np.sign((c - vw).fillna(0)), "Long above VWAP, short below")
        day_arr = np.array(df.index.date)
        first_ts = pd.Series(df.index, index=df.index).groupby(day_arr).transform("first")
        in_or = (pd.Series(df.index, index=df.index) - first_ts) < pd.Timedelta(minutes=30)
        or_h = h.where(in_or).groupby(day_arr).transform("max")
        or_l = l.where(in_or).groupby(day_arr).transform("min")
        pos = np.zeros(n)
        p = 0
        last_day = None
        inr, orh, orl, cc_ = in_or.to_numpy(), or_h.to_numpy(), or_l.to_numpy(), cv
        for i in range(n):
            if day_arr[i] != last_day:
                p, last_day = 0, day_arr[i]
            if not inr[i] and p == 0:
                if cc_[i] > orh[i]:
                    p = 1
                elif cc_[i] < orl[i]:
                    p = -1
            pos[i] = p
        strat("Opening-range breakout (30 min)", pos, "Trade the break of the first 30 minutes' range; flat at day end")

    # ---------------- combine
    votes = pd.DataFrame({i: s["votes"] for i, s in enumerate(sigs)})
    cat_names = [s["category"] for s in sigs]
    cat_scores = {}
    for cat in CATEGORY_WEIGHTS:
        cols = [i for i, cn in enumerate(cat_names) if cn == cat]
        if cols:
            cat_scores[cat] = votes[cols].mean(axis=1)
    wsum = sum(CATEGORY_WEIGHTS[k] for k in cat_scores)
    score = sum(cat_scores[k] * CATEGORY_WEIGHTS[k] for k in cat_scores) / wsum
    ma_cols = [i for i, s in enumerate(sigs) if s["tv"] == "ma"]
    osc_cols = [i for i, s in enumerate(sigs) if s["tv"] == "osc"]
    tv_ma, tv_osc = votes[ma_cols].mean(axis=1), votes[osc_cols].mean(axis=1)
    tv_all = (tv_ma + tv_osc) / 2

    # how often did the score's direction match the next 1 and 5 bars?
    def hit(hz):
        fut = np.sign(c.shift(-hz) - c)
        s_ = score.iloc[WARMUP:-hz] if n > WARMUP + hz else score.iloc[:0]
        f_ = fut.loc[s_.index]
        m = (s_.abs() > 0.1) & (f_ != 0) & f_.notna()
        s_, f_ = s_[m].tail(600), f_[m].tail(600)
        if len(s_) < 20:
            return None
        return {"accuracy": float((np.sign(s_) == f_).mean()), "signals": int(len(s_)),
                "up_rate": float((f_ > 0).mean())}

    last = float(score.iloc[-1])
    latest = []
    for s in sigs:
        vv = s["votes"].iloc[-1]
        latest.append({"name": s["name"], "category": s["category"], "vote": int(vv) if math.isfinite(vv) else 0,
                       "value": s["value"], "rule": s["rule"]})
    counts = {"bullish": sum(x["vote"] > 0 for x in latest), "bearish": sum(x["vote"] < 0 for x in latest),
              "neutral": sum(x["vote"] == 0 for x in latest)}
    out = {
        "available": True, "bars": n, "score": round(last, 3), "verdict": label(last),
        "previous_score": round(float(score.iloc[-2]), 3) if n > 1 else None,
        "categories": {k: round(float(vv.iloc[-1]), 3) for k, vv in cat_scores.items()},
        "tradingview": {"overall": round(float(tv_all.iloc[-1]), 3), "label": tv_label(float(tv_all.iloc[-1])),
                        "ma": round(float(tv_ma.iloc[-1]), 3), "ma_label": tv_label(float(tv_ma.iloc[-1])),
                        "osc": round(float(tv_osc.iloc[-1]), 3), "osc_label": tv_label(float(tv_osc.iloc[-1]))},
        "counts": counts, "hit_rate": {"next_bar": hit(1), "next_5_bars": hit(5)},
        "price": float(c.iloc[-1]), "last_time": int(df.index[-1].timestamp()),
        "atr": float(atr14.iloc[-1]) if math.isfinite(atr14.iloc[-1]) else None,
        "rsi": float(r14.iloc[-1]) if math.isfinite(r14.iloc[-1]) else None,
    }
    if detail:
        out["signals"] = latest
        out["strategies"] = strategies
        out["patterns"] = [{"i": i, "name": nm, "dir": dr} for i, nm, dr in pat_found if i >= n - 400]
        keep = min(n, 800)
        tail = df.tail(keep)
        ts = [int(t.timestamp()) for t in tail.index]
        def ser(s_):
            arr = s_.tail(keep).to_numpy()
            return [None if not math.isfinite(x) else round(float(x), 6) for x in arr]
        out["candles"] = {"t": ts, "o": ser(o), "h": ser(h), "l": ser(l), "c": ser(c), "v": ser(v)}
        out["overlays"] = {"ema9": ser(e9), "ema21": ser(e21), "ema50": ser(ema(c, 50)), "ema200": ser(ema(c, 200)),
                           "bb_upper": ser(bb_u), "bb_mid": ser(mid), "bb_lower": ser(bb_l),
                           "supertrend": ser(st_line), "supertrend_dir": ser(st_dir), "sar": ser(sar),
                           "vwap": ser(vw) if has_volume else None}
        out["score_series"] = ser(score.round(3))
        offset = n - keep
        out["patterns"] = [{"t": ts[p["i"] - offset], "name": p["name"], "dir": p["dir"]} for p in out["patterns"] if p["i"] >= offset]
    return out
