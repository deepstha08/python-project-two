"""Pure calculations: valuation ranking and options max pain. No network access."""
from __future__ import annotations

import math
from datetime import date

REGIONS = ("US", "Europe", "Asia")
METRICS = ("pe", "forward_pe", "peg")
DEFAULT_WEIGHTS = {"pe": 1.0, "forward_pe": 1.0, "peg": 1.0}


def number(value):
    """Return a finite float or None."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def positive(value):
    value = number(value)
    return value if value is not None and value > 0 else None


# --------------------------------------------------------------------------- valuation

def eligible(row, limits=None):
    """A stock is ranked only when all three ratios are real, positive numbers.

    `limits` optionally caps each ratio (e.g. {"pe": 60}) so absurd outliers
    such as a P/E of 4,000 caused by near-zero earnings are left out.
    """
    if row.get("error"):
        return False
    for metric in METRICS:
        value = positive(row.get(metric))
        if value is None:
            return False
        cap = (limits or {}).get(metric)
        if cap and value > cap:
            return False
    return True


def value_flags(row):
    """Warning signs that a cheap-looking stock might be a value trap."""
    flags = []
    growth = number(row.get("earnings_growth"))
    if growth is not None and growth < -0.15:
        flags.append("Earnings falling")
    de = number(row.get("debt_to_equity"))
    if de is not None and de > 250:
        flags.append("High debt")
    fcf = number(row.get("free_cashflow"))
    if fcf is not None and fcf < 0:
        flags.append("Negative free cash flow")
    pe, fpe = positive(row.get("pe")), positive(row.get("forward_pe"))
    if pe and fpe and fpe > pe * 1.35:
        flags.append("Earnings expected to drop")
    margin = number(row.get("profit_margin"))
    if margin is not None and margin < 0:
        flags.append("Unprofitable")
    return flags


def _percentile_scores(peers, metric, value):
    greater = sum(float(r[metric]) > value for r in peers)
    equal = sum(float(r[metric]) == value for r in peers)
    if len(peers) <= 1:
        return 50.0
    return 100.0 * (greater + (equal - 1) / 2) / (len(peers) - 1)


def rank_stocks(rows, sector_adjust=True, weights=None, limits=None, exclude_flagged=False,
                min_sector_peers=5, scalable=None):
    """Rank by equal- (or custom-) weighted cheapness percentiles of P/E, forward P/E and PEG.

    Lower ratio => higher percentile. Ties share the mid-rank. Each stock is compared with
    stocks in the same region (and, when `sector_adjust` is on and there are enough peers,
    the same sector) so a cheap bank is compared with banks, not software companies.
    """
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    total_weight = sum(max(0.0, float(weights[m])) for m in METRICS) or 1.0
    valid = []
    for r in rows:
        if not eligible(r, limits):
            continue
        # `scalable`: allowed broker-availability statuses; rows from older scans count as "unchecked"
        if scalable is not None and (r.get("scalable") or "unchecked") not in scalable:
            continue
        row = dict(r)
        row["flags"] = value_flags(row)
        if exclude_flagged and row["flags"]:
            continue
        valid.append(row)

    by_region = {}
    for row in valid:
        by_region.setdefault(row["region"], []).append(row)

    for region_rows in by_region.values():
        sectors = {}
        for row in region_rows:
            sectors.setdefault(row.get("sector") or "Unknown", []).append(row)
        for row in region_rows:
            sector = row.get("sector") or "Unknown"
            use_sector = (sector_adjust and sector != "Unknown"
                          and len(sectors[sector]) >= min_sector_peers)
            peers = sectors[sector] if use_sector else region_rows
            row["peer_basis"] = f"{row['region']} · {sector}" if use_sector else f"All {row['region']}"
            row["peer_count"] = len(peers)
            parts = {}
            for metric in METRICS:
                parts[metric] = _percentile_scores(peers, metric, float(row[metric]))
            row["metric_scores"] = parts
            row["score"] = sum(parts[m] * max(0.0, float(weights[m])) for m in METRICS) / total_weight

    ranked = sorted(valid, key=lambda r: (-r["score"], r["symbol"]))
    counters = {}
    for row in ranked:
        counters[row["region"]] = counters.get(row["region"], 0) + 1
        row["rank"] = counters[row["region"]]
    return ranked


def top_by_region(ranked, n=25):
    out = {region: [] for region in REGIONS}
    for row in ranked:
        bucket = out.setdefault(row["region"], [])
        if len(bucket) < n:
            bucket.append(row)
    return out


# --------------------------------------------------------------------------- max pain

def max_pain(contracts):
    """Max pain for one expiration, following OptionCharts' published method.

    For every listed strike S treated as the settlement price, sum the intrinsic value
    option holders would receive:
        calls: max(S - strike, 0) * open interest * multiplier
        puts:  max(strike - S, 0) * open interest * multiplier
    Max pain is the strike with the smallest total payout. All tied strikes are returned.
    """
    if not contracts:
        raise ValueError("No option contracts are listed for this expiration.")
    clean = []
    for item in contracts:
        strike = positive(item.get("strike"))
        oi = number(item.get("open_interest"))
        side = item.get("type")
        multiplier = positive(item.get("multiplier", 100))
        if strike is None or side not in ("call", "put") or multiplier is None:
            raise ValueError("Option chain contains an invalid strike, side or contract size.")
        if oi is None:  # Yahoo leaves OI blank for some new strikes; treat as zero contracts.
            oi = 0.0
        if oi < 0:
            raise ValueError("Option chain contains negative open interest.")
        clean.append((strike, oi, side, multiplier))
    call_oi = sum(oi for _, oi, side, _ in clean if side == "call")
    put_oi = sum(oi for _, oi, side, _ in clean if side == "put")
    if call_oi <= 0 or put_oi <= 0:
        raise ValueError("Need open interest on both calls and puts to calculate max pain.")
    strikes = sorted({c[0] for c in clean})
    curve = []
    for settle in strikes:
        call_pay = sum(max(0.0, settle - k) * oi * m for k, oi, side, m in clean if side == "call")
        put_pay = sum(max(0.0, k - settle) * oi * m for k, oi, side, m in clean if side == "put")
        curve.append({"strike": settle, "calls": call_pay, "puts": put_pay, "total": call_pay + put_pay})
    minimum = min(p["total"] for p in curve)
    winners = [p["strike"] for p in curve if math.isclose(p["total"], minimum, rel_tol=1e-10, abs_tol=1e-6)]
    oi_by_strike = {}
    for k, oi, side, _ in clean:
        slot = oi_by_strike.setdefault(k, {"strike": k, "call_oi": 0.0, "put_oi": 0.0})
        slot["call_oi" if side == "call" else "put_oi"] += oi
    return {
        "max_pain": winners[(len(winners) - 1) // 2],  # middle of tied strikes (lower if even)
        "strikes": winners,
        "payout": minimum,
        "curve": curve,
        "open_interest": [oi_by_strike[k] for k in sorted(oi_by_strike)],
        "call_oi": call_oi,
        "put_oi": put_oi,
        "put_call_ratio": put_oi / call_oi,
        "contracts": len(clean),
    }


def trim_curve(result, price=None, keep=60):
    """Keep chart data readable: the strikes closest to max pain / price."""
    centre = price or result["max_pain"]
    for key in ("curve", "open_interest"):
        items = result.get(key) or []
        if len(items) > keep:
            items = sorted(items, key=lambda p: abs(p["strike"] - centre))[:keep]
            result[key] = sorted(items, key=lambda p: p["strike"])
    return result


def valid_date(value):
    parsed = date.fromisoformat(str(value))
    if parsed.isoformat() != value:
        raise ValueError("Use YYYY-MM-DD.")
    return value


def safe_csv(value):
    """Stop spreadsheet apps from interpreting exported text as a formula."""
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


# --------------------------------------------------------------------------- watchlist valuation

# Used only until the first market scan has measured real regional medians.
FALLBACK_BENCHMARKS = {"US": {"pe": 22.0, "forward_pe": 19.0}, "Europe": {"pe": 15.0, "forward_pe": 13.5},
                       "Asia": {"pe": 14.0, "forward_pe": 12.5}}


def _clamp(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


def valuation_verdict(row, benchmark):
    """Undervalued / Neutral / Overvalued for one stock from P/E, forward P/E and PEG.

    P/E and forward P/E are compared with the median of the large companies in the stock's
    region (a ratio 1.6x below the median scores +1, 1.6x above scores -1). PEG uses the
    classic yardstick: below 1 is cheap, 1-2 is fair, above 2 is expensive.
    """
    qtype = (row.get("quote_type") or "EQUITY").upper()
    if qtype != "EQUITY":
        kind = "commodity" if any(w in (row.get("name") or "").lower() for w in ("gold", "silver", "physical", "etc")) else "fund"
        return {"verdict": "Not rated", "score": None, "kind": kind, "parts": [],
                "reason": "Commodity ETCs have no earnings, so P/E, forward P/E and PEG don't exist."
                if kind == "commodity" else "Funds hold many companies; P/E-based single-stock ratings don't apply."}
    t_eps, f_eps = number(row.get("trailing_eps")), number(row.get("forward_eps"))
    pe, fpe, peg = positive(row.get("pe")), positive(row.get("forward_pe")), positive(row.get("peg"))
    if t_eps is not None and t_eps <= 0 and not fpe:
        return {"verdict": "No earnings", "score": None, "kind": "loss", "parts": [],
                "reason": "The company is not profitable, so it can't be valued on earnings ratios."}
    parts = []
    bench_pe, bench_fpe = benchmark.get("pe"), benchmark.get("forward_pe")
    if pe and bench_pe:
        parts.append(("P/E", _clamp(math.log(bench_pe / pe) / math.log(1.6)),
                      f"P/E {pe:.1f} vs regional median {bench_pe:.1f}"))
    elif t_eps is not None and t_eps <= 0:
        parts.append(("P/E", -1.0, "Loss over the last 12 months (no positive P/E)"))
    if fpe and bench_fpe:
        parts.append(("Forward P/E", _clamp(math.log(bench_fpe / fpe) / math.log(1.6)),
                      f"Forward P/E {fpe:.1f} vs regional median {bench_fpe:.1f}"))
    if peg:
        label = "PEG (estimated)" if row.get("peg_field") == "estimated" else "PEG"
        parts.append((label, _clamp(1.5 - peg), f"{label} {peg:.2f} (below 1 cheap, above 2 expensive)"))
    if not parts:
        return {"verdict": "Not rated", "score": None, "kind": "nodata", "parts": [],
                "reason": "Yahoo has no P/E, forward P/E or PEG for this stock."}
    score = sum(p[1] for p in parts) / len(parts)
    verdict = "Undervalued" if score >= 0.2 else "Overvalued" if score <= -0.2 else "Neutral"
    return {"verdict": verdict, "score": round(score, 3), "kind": "stock",
            "parts": [{"name": n, "score": round(v, 3), "detail": d} for n, v, d in parts],
            "reason": "; ".join(d for _, _, d in parts)}


def median(values):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    m = len(vals) // 2
    return vals[m] if len(vals) % 2 else (vals[m - 1] + vals[m]) / 2
