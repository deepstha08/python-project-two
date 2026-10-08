"""Option-chain analytics for one expiration: max pain, walls, put/call ratios, IV, expected move."""
from __future__ import annotations

import math
from datetime import date, datetime, timezone

from .core import max_pain, number, positive


def _rows(frame):
    out = []
    for r in frame.to_dict("records"):
        size = r.get("contractSize")
        if isinstance(size, str) and size.strip() and size.strip().upper() != "REGULAR":
            continue
        k = positive(r.get("strike"))
        if k is None:
            continue
        bid, ask, last = number(r.get("bid")) or 0, number(r.get("ask")) or 0, number(r.get("lastPrice")) or 0
        out.append({"strike": k, "oi": number(r.get("openInterest")) or 0.0, "volume": number(r.get("volume")) or 0.0,
                    "iv": number(r.get("impliedVolatility")), "mid": (bid + ask) / 2 if bid > 0 and ask > 0 else last,
                    "last": last})
    return out


def analyse_chain(calls_df, puts_df, price, expiration, today=None):
    calls, puts = _rows(calls_df), _rows(puts_df)
    if not calls or not puts:
        raise ValueError("This expiration has no usable calls or puts.")
    contracts = ([{"type": "call", "strike": r["strike"], "open_interest": r["oi"]} for r in calls]
                 + [{"type": "put", "strike": r["strike"], "open_interest": r["oi"]} for r in puts])
    mp = max_pain(contracts)
    call_oi, put_oi = sum(r["oi"] for r in calls), sum(r["oi"] for r in puts)
    call_vol, put_vol = sum(r["volume"] for r in calls), sum(r["volume"] for r in puts)
    call_wall = max(calls, key=lambda r: r["oi"])
    put_wall = max(puts, key=lambda r: r["oi"])
    above = [r for r in calls if price and r["strike"] >= price]
    below = [r for r in puts if price and r["strike"] <= price]
    res_wall = max(above, key=lambda r: r["oi"]) if above else None
    sup_wall = max(below, key=lambda r: r["oi"]) if below else None

    today = today or date.today()
    days = max((date.fromisoformat(expiration) - today).days, 0) + 1
    atm = iv = straddle = None
    if price:
        strikes = sorted({r["strike"] for r in calls} & {r["strike"] for r in puts}, key=lambda k: abs(k - price))
        if strikes:
            atm = strikes[0]
            c = next(r for r in calls if r["strike"] == atm)
            p = next(r for r in puts if r["strike"] == atm)
            ivs = [x for x in (c["iv"], p["iv"]) if x and 0.01 < x < 5]
            iv = sum(ivs) / len(ivs) if ivs else None
            straddle = (c["mid"] or 0) + (p["mid"] or 0) or None
    move_iv = price * iv * math.sqrt(days / 365) if price and iv else None
    expected = straddle * 0.85 if straddle else move_iv  # straddle ≈ 1.25 σ; 0.85 × straddle ≈ 1 σ move

    unusual = []
    for side, rows in (("Call", calls), ("Put", puts)):
        for r in rows:
            if r["volume"] >= 500 and r["volume"] > max(r["oi"], 1):
                unusual.append({"side": side, "strike": r["strike"], "volume": r["volume"], "oi": r["oi"],
                                "ratio": r["volume"] / max(r["oi"], 1), "iv": r["iv"]})
    unusual.sort(key=lambda u: -u["volume"])

    # options sentiment: volume and OI put/call ratios, plus where unusual activity is concentrated
    votes = []
    pcr_vol = put_vol / call_vol if call_vol else None
    pcr_oi = put_oi / call_oi if call_oi else None
    if pcr_vol is not None and call_vol + put_vol >= 500:
        votes.append(("Put/call volume", 1 if pcr_vol < 0.7 else -1 if pcr_vol > 1.3 else 0, f"{pcr_vol:.2f}"))
    if pcr_oi is not None:
        votes.append(("Put/call open interest", 1 if pcr_oi < 0.7 else -1 if pcr_oi > 1.3 else 0, f"{pcr_oi:.2f}"))
    if unusual:
        cv = sum(u["volume"] for u in unusual if u["side"] == "Call")
        pv = sum(u["volume"] for u in unusual if u["side"] == "Put")
        votes.append(("Unusual activity", 1 if cv > 1.5 * pv else -1 if pv > 1.5 * cv else 0,
                      f"{int(cv):,} calls vs {int(pv):,} puts"))
    if price and mp["max_pain"]:
        gap = mp["max_pain"] / price - 1
        votes.append(("Max-pain pull", 1 if gap > 0.02 else -1 if gap < -0.02 else 0, f"{gap:+.1%}"))
    score = sum(v[1] for v in votes) / len(votes) if votes else None

    window = [k for k in sorted({r["strike"] for r in calls + puts}) if not price or abs(k / price - 1) <= 0.3]
    by_k = {k: {"strike": k, "call_oi": 0.0, "put_oi": 0.0, "call_vol": 0.0, "put_vol": 0.0} for k in window}
    for r in calls:
        if r["strike"] in by_k:
            by_k[r["strike"]]["call_oi"] += r["oi"]
            by_k[r["strike"]]["call_vol"] += r["volume"]
    for r in puts:
        if r["strike"] in by_k:
            by_k[r["strike"]]["put_oi"] += r["oi"]
            by_k[r["strike"]]["put_vol"] += r["volume"]

    return {
        "expiration": expiration, "days": days, "price": price,
        "max_pain": mp["max_pain"], "max_pain_strikes": mp["strikes"],
        "call_wall": call_wall["strike"], "put_wall": put_wall["strike"],
        "resistance": res_wall["strike"] if res_wall else None, "support": sup_wall["strike"] if sup_wall else None,
        "call_oi": call_oi, "put_oi": put_oi, "call_volume": call_vol, "put_volume": put_vol,
        "pcr_oi": pcr_oi, "pcr_volume": pcr_vol,
        "atm_strike": atm, "atm_iv": iv, "straddle": straddle, "expected_move": expected,
        "expected_low": price - expected if price and expected else None,
        "expected_high": price + expected if price and expected else None,
        "unusual": unusual[:10], "strikes": list(by_k.values()),
        "sentiment": {"score": score, "verdict": ("Bullish" if score > 0.2 else "Bearish" if score < -0.2 else "Neutral")
                      if score is not None else "No data",
                      "votes": [{"name": n, "vote": v, "value": t} for n, v, t in votes]},
        "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
