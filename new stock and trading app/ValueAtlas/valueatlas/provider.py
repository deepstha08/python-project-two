"""Market-data adapters.

Yahoo Finance is accessed through the open-source `yfinance` library (unofficial; personal
use). Finnhub is optional and needs a free API key from https://finnhub.io.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

from .core import number, positive, valid_date
from .storage import now

# Country quotas keep one large market (e.g. Japan) from crowding out the rest of a region.
# Market caps are compared only *within* a country, so local currencies never get mixed.
MARKETS = {
    "US": [("us", ["NMS", "NYQ", "NGM", "NCM", "ASE"], 400)],
    "Europe": [("gb", ["LSE"], 70), ("de", ["GER"], 60), ("fr", ["PAR"], 60), ("ch", ["EBS"], 40),
               ("nl", ["AMS"], 30), ("it", ["MIL"], 30), ("es", ["MCE"], 30), ("se", ["STO"], 35),
               ("dk", ["CPH"], 20), ("fi", ["HEL"], 15), ("no", ["OSL"], 20), ("be", ["BRU"], 12),
               ("at", ["VIE"], 8), ("ie", ["ISE"], 6), ("pt", ["LIS"], 6)],
    "Asia": [("jp", ["JPX"], 130), ("hk", ["HKG"], 70), ("cn", ["SHH", "SHZ"], 60), ("kr", ["KSC"], 50),
             ("tw", ["TAI"], 50), ("in", ["NSI"], 70), ("sg", ["SES"], 20)],
}


def retry(fn, *args, tries=3, wait=3.0, **kwargs):
    last = None
    for attempt in range(tries):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # network / rate-limit errors from yfinance vary by version
            last = exc
            text = f"{type(exc).__name__} {exc}".lower()
            if attempt < tries - 1 and any(w in text for w in ("rate", "too many", "timed out", "timeout",
                                                               "connection", "429", "temporar", "curl")):
                time.sleep(wait * (attempt + 1) ** 2)
                continue
            raise
    raise last


class YahooProvider:
    name = "Yahoo Finance"

    def __init__(self):
        import yfinance as yf
        self.yf = yf

    def check(self):
        """Fail fast with a clear message when Yahoo cannot be reached."""
        hist = retry(lambda: self.yf.Ticker("MSFT").history(period="5d", timeout=20), tries=2, wait=2)
        if hist is None or len(hist) == 0:
            raise ValueError("Yahoo Finance returned no data for a test request.")

    # ----------------------------------------------------------------- screener
    def screen(self, country, exchanges, count, max_pe=60):
        EQ = self.yf.EquityQuery
        query = EQ("and", [
            EQ("eq", ["region", country]),
            EQ("is-in", ["exchange", *exchanges]),
            EQ("btwn", ["peratio.lasttwelvemonths", 0.01, float(max_pe)]),
        ])
        quotes, offset = [], 0
        while len(quotes) < count:
            size = min(250, count - len(quotes))
            result = retry(self.yf.screen, query, offset=offset, size=size,
                           sortField="intradaymarketcap", sortAsc=False)
            batch = (result or {}).get("quotes") or []
            quotes.extend(batch)
            if len(batch) < size:
                break
            offset += size
        out = []
        for q in quotes:
            if q.get("quoteType", "EQUITY") != "EQUITY" or not q.get("symbol"):
                continue
            out.append({"symbol": q["symbol"].upper(), "name": q.get("shortName") or q.get("longName"),
                        "pe": positive(q.get("trailingPE")), "forward_pe": positive(q.get("forwardPE")),
                        "market_cap": number(q.get("marketCap")), "currency": q.get("currency")})
        return out

    # ----------------------------------------------------------------- fundamentals
    def fundamentals(self, symbol, region, allow_funds=False):
        ticker = self.yf.Ticker(symbol)
        info = retry(ticker.get_info)
        if not info or not (info.get("symbol") or info.get("shortName")):
            raise ValueError("Yahoo returned no data for this symbol.")
        if info.get("quoteType") not in (None, "EQUITY") and not allow_funds:
            raise ValueError(f"Not a common stock (Yahoo type: {info.get('quoteType')}).")
        peg = positive(info.get("trailingPegRatio"))
        peg_field = "trailingPegRatio"
        if peg is None:
            peg, peg_field = positive(info.get("pegRatio")), "pegRatio"
        # Fallback PEG from analysts' EPS forecast: trailing P/E / (expected EPS growth in %)
        peg_estimate = None
        t_eps, f_eps, t_pe = positive(info.get("trailingEps")), positive(info.get("forwardEps")), positive(info.get("trailingPE"))
        if t_eps and f_eps and t_pe and f_eps > t_eps:
            growth_pct = min((f_eps / t_eps - 1) * 100, 100.0)
            if growth_pct >= 1:
                peg_estimate = t_pe / growth_pct
        price = positive(info.get("currentPrice")) or positive(info.get("regularMarketPrice"))
        div_rate = positive(info.get("dividendRate"))
        return {
            "symbol": symbol, "region": region,
            "name": info.get("shortName") or info.get("longName") or symbol,
            "long_name": info.get("longName"),
            "sector": info.get("sector") or "Unknown", "industry": info.get("industry") or "",
            "country": info.get("country") or "", "exchange": info.get("fullExchangeName") or info.get("exchange"),
            "exchange_code": info.get("exchange") or "",
            "currency": info.get("currency") or "", "financial_currency": info.get("financialCurrency") or "",
            "price": price, "market_cap": number(info.get("marketCap")),
            "pe": positive(info.get("trailingPE")), "forward_pe": positive(info.get("forwardPE")),
            "peg": peg, "peg_field": peg_field if peg else None, "peg_estimate": peg_estimate,
            **self._isin(ticker, symbol),
            "price_to_book": positive(info.get("priceToBook")),
            "ev_ebitda": number(info.get("enterpriseToEbitda")),
            "dividend_yield": (div_rate / price) if (div_rate and price) else None,
            "earnings_growth": number(info.get("earningsGrowth")),
            "revenue_growth": number(info.get("revenueGrowth")),
            "profit_margin": number(info.get("profitMargins")),
            "roe": number(info.get("returnOnEquity")),
            "debt_to_equity": number(info.get("debtToEquity")),
            "free_cashflow": number(info.get("freeCashflow")),
            "beta": number(info.get("beta")),
            "recommendation_mean": positive(info.get("recommendationMean")),
            "recommendation_key": info.get("recommendationKey") if info.get("recommendationKey") != "none" else None,
            "analyst_count": number(info.get("numberOfAnalystOpinions")),
            "target_mean": positive(info.get("targetMeanPrice")),
            "target_high": positive(info.get("targetHighPrice")),
            "target_low": positive(info.get("targetLowPrice")),
            "change_52w": number(info.get("52WeekChange")),
            "quote_type": info.get("quoteType") or "EQUITY",
            "trailing_eps": number(info.get("trailingEps")), "forward_eps": number(info.get("forwardEps")),
            "raw_pe": number(info.get("trailingPE")),
            "previous_close": positive(info.get("regularMarketPreviousClose")) or positive(info.get("previousClose")),
            "category": info.get("category") or "", "fund_family": info.get("fundFamily") or "",
            "summary": (info.get("longBusinessSummary") or "")[:700],
            "website": info.get("website") or "",
            "fetched": now(), "error": "",
        }

    def eur_rate(self, currency):
        """Units of `currency` per 1 EUR (GBp handled as pence)."""
        cur = (currency or "").strip()
        if not cur or cur == "EUR":
            return 1.0
        pence = cur in ("GBp", "GBX")
        base = "GBP" if pence else cur.upper()
        hist = retry(lambda: self.yf.Ticker(f"EUR{base}=X").history(period="5d", timeout=15), tries=2, wait=1)
        if hist is None or len(hist) == 0:
            raise ValueError(f"No EUR/{base} rate")
        rate = float(hist["Close"].iloc[-1])
        return rate * 100 if pence else rate

    def resolve_isin(self, isin):
        """Yahoo symbol for an ISIN, preferring German listings (what Scalable Capital shows)."""
        res = self.yf.Search(isin, max_results=15, news_count=0, lists_count=0, recommended=0, raise_errors=False, timeout=10)
        quotes = [q for q in (res.quotes or []) if q.get("symbol")]
        if not quotes:
            return None, None
        order = [".DE", ".F", ".MU", ".SG", ".DU", ".BE", ".HM", ".L", ".AS", ".PA", ".MI", ".SW", ""]
        def rank(q):
            sym = q["symbol"].upper()
            suffix = "." + sym.rsplit(".", 1)[1] if "." in sym else ""
            return order.index(suffix) if suffix in order else len(order)
        best = sorted(quotes, key=rank)[0]
        return best["symbol"].upper(), best.get("longname") or best.get("shortname")

    def _isin(self, ticker, symbol):
        try:
            from .scalable import lookup_isin
            isin, verified = lookup_isin(self.yf, ticker, symbol)
        except Exception:
            isin, verified = None, False
        return {"isin": isin, "isin_verified": verified}

    def recommendation_trend(self, symbol):
        frame = retry(lambda: self.yf.Ticker(symbol).recommendations)
        if frame is None or len(frame) == 0:
            return None, None
        rows = {str(r.get("period")): r for r in frame.to_dict("records")}
        clean = lambda r: {k: number(r.get(k)) or 0 for k in ("strongBuy", "buy", "hold", "sell", "strongSell")} if r else None  # noqa: E731
        return clean(rows.get("0m")), clean(rows.get("-3m") or rows.get("-2m"))

    def history(self, symbol):
        return retry(lambda: self.yf.Ticker(symbol).history(period="2y", interval="1d", auto_adjust=True, timeout=30))

    # ----------------------------------------------------------------- options
    def expirations(self, symbol):
        return list(retry(lambda: self.yf.Ticker(symbol).options) or [])

    def option_contracts(self, symbol, expiration):
        valid_date(expiration)
        ticker = self.yf.Ticker(symbol)
        chain = retry(ticker.option_chain, expiration)
        contracts = []
        for side, frame in (("call", chain.calls), ("put", chain.puts)):
            for row in frame.to_dict("records"):
                size = row.get("contractSize")
                if isinstance(size, str) and size.strip() and size.strip().upper() != "REGULAR":
                    continue  # adjusted contracts have non-standard deliverables
                contracts.append({"type": side, "strike": row.get("strike"),
                                  "open_interest": row.get("openInterest"), "multiplier": 100})
        underlying = getattr(chain, "underlying", None) or {}
        price = positive(underlying.get("regularMarketPrice")) if isinstance(underlying, dict) else None
        return contracts, price


class FinnhubProvider:
    """Optional second analyst-consensus source (free key at finnhub.io)."""
    name = "Finnhub"

    def __init__(self, key):
        self.key = key.strip()

    def recommendation(self, symbol):
        if "." in symbol:  # free plan covers US listings
            return None
        url = ("https://finnhub.io/api/v1/stock/recommendation?symbol="
               + urllib.parse.quote(symbol) + "&token=" + urllib.parse.quote(self.key))
        with urllib.request.urlopen(url, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not isinstance(data, list) or not data:
            return None
        latest = sorted(data, key=lambda r: r.get("period", ""))[-1]
        return {"trend": latest, "period": latest.get("period")}
