"""Combine several independent signals into one Bullish / Neutral / Bearish verdict."""
from __future__ import annotations

from .core import number, positive

WEIGHTS = {
    "technical": 0.30,   # TradingView-method technical rating
    "trend": 0.10,       # long-term trend (SMA200 / golden cross)
    "analysts": 0.25,    # Yahoo Finance analyst consensus
    "target": 0.15,      # analyst price target vs price
    "revisions": 0.08,   # analyst rating changes over ~3 months
    "options": 0.07,     # put/call open-interest ratio
    "finnhub": 0.05,     # optional second analyst source
}


def _clip(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


def _tone(value):
    if value > 0.15:
        return "Bullish"
    if value < -0.15:
        return "Bearish"
    return "Neutral"


def trend_counts(trend):
    """Net bullish share from a recommendation-trend row {strongBuy, buy, hold, sell, strongSell}."""
    if not trend:
        return None
    sb, b, hd, s, ss = (number(trend.get(k)) or 0 for k in ("strongBuy", "buy", "hold", "sell", "strongSell"))
    total = sb + b + hd + s + ss
    if total <= 0:
        return None
    return (2 * sb + b - s - 2 * ss) / (2 * total)


def composite(row, technical=None, options=None, finnhub=None):
    """Return {score -1..1, verdict, components[...]} for one stock."""
    comps = []

    def add(key, name, value, detail, source):
        if value is None:
            return
        comps.append({"key": key, "name": name, "value": round(_clip(value), 3), "tone": _tone(value),
                      "detail": detail, "source": source, "weight": WEIGHTS[key]})

    tech = technical or {}
    if tech.get("available"):
        add("technical", "Technical rating", tech["rating"],
            f"{tech['rating_label']} · MAs {tech['ma_label']}, oscillators {tech['osc_label']}",
            "TradingView Technical Ratings method, calculated from Yahoo prices")
        t = (1 if tech["above_sma200"] else -1) * 0.6 + (1 if tech["golden_cross"] else -1) * 0.4
        add("trend", "Long-term trend", t,
            ("Above" if tech["above_sma200"] else "Below") + " 200-day average · "
            + ("golden cross" if tech["golden_cross"] else "death cross") + " (50 vs 200-day)",
            "Calculated from Yahoo prices")

    mean = positive(row.get("recommendation_mean"))
    count = number(row.get("analyst_count")) or 0
    if mean and count >= 3:
        # Yahoo scale: 1 = Strong Buy ... 3 = Hold ... 5 = Strong Sell
        add("analysts", "Analyst consensus", (3 - mean) / 1.5,
            f"{(row.get('recommendation_key') or '').replace('_', ' ').title() or 'Rating'} "
            f"({mean:.2f} on 1–5 scale, {int(count)} analysts)", "Yahoo Finance")

    price, target = positive(row.get("price")), positive(row.get("target_mean"))
    if price and target and count >= 3:
        upside = target / price - 1
        add("target", "Price-target upside", upside / 0.25,
            f"Average target {target:,.2f} → {upside:+.1%} vs current price", "Yahoo Finance analysts")

    now_t, then_t = trend_counts(row.get("rec_trend_now")), trend_counts(row.get("rec_trend_3m"))
    if now_t is not None and then_t is not None:
        delta = now_t - then_t
        add("revisions", "Analyst revisions", delta / 0.15,
            f"Net rating {'improved' if delta > 0.01 else 'worsened' if delta < -0.01 else 'unchanged'} "
            f"over 3 months ({then_t:+.2f} → {now_t:+.2f})", "Yahoo Finance recommendation trend")

    if options and options.get("put_call_ratio") is not None and (options.get("total_oi") or 0) >= 1000:
        pcr = options["put_call_ratio"]
        # < 0.7 leans bullish, > 1.3 leans bearish (contrarian readings ignored for simplicity)
        add("options", "Options positioning", (1.0 - pcr) / 0.5,
            f"Put/call open-interest ratio {pcr:.2f} across near expirations", "Yahoo option chains")

    if finnhub and finnhub.get("trend"):
        fh = trend_counts(finnhub["trend"])
        if fh is not None:
            add("finnhub", "Finnhub analysts", fh / 0.5,
                f"Net rating {fh:+.2f} ({finnhub.get('period', 'latest')})", "Finnhub.io")

    if not comps:
        return {"score": None, "verdict": "No data", "components": [], "agree": None}
    total_w = sum(c["weight"] for c in comps)
    score = sum(c["value"] * c["weight"] for c in comps) / total_w
    bull = sum(c["tone"] == "Bullish" for c in comps)
    bear = sum(c["tone"] == "Bearish" for c in comps)
    if score > 0.45:
        verdict = "Strong Bullish"
    elif score > 0.12:
        verdict = "Bullish"
    elif score < -0.45:
        verdict = "Strong Bearish"
    elif score < -0.12:
        verdict = "Bearish"
    else:
        verdict = "Neutral"
    return {"score": round(score, 3), "verdict": verdict, "components": comps,
            "agree": {"bullish": bull, "bearish": bear, "total": len(comps)},
            "coverage": round(total_w / sum(WEIGHTS.values()), 2)}
