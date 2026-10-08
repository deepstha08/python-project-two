"""Deterministic offline stand-in for Yahoo, used only by the automated tests."""
import math
import random
from datetime import date, timedelta

import numpy as np
import pandas as pd

from valueatlas.storage import now

SECTORS = ["Technology", "Financial Services", "Healthcare", "Energy", "Industrials", "Consumer Cyclical"]
COUNTRY_REGION = {"us": "US", "gb": "Europe", "de": "Europe", "fr": "Europe", "jp": "Asia", "hk": "Asia"}
SUFFIX = {"us": "", "gb": ".L", "de": ".DE", "fr": ".PA", "jp": ".T", "hk": ".HK"}


class FakeProvider:
    def __init__(self, fail=()):
        self.fail = set(fail)
        self.calls = 0

    def gettex_list(self):
        """Offline stand-in for the gettex instrument list (set of ISINs, or None = unavailable)."""
        return self._gettex, {"count": len(self._gettex or ())}

    _gettex = None  # None = list unavailable -> availability "unchecked"

    def screen(self, country, exchanges, count, max_pe=60):
        if country not in SUFFIX:
            return []
        rng = random.Random(country)
        out = []
        for i in range(min(count, 60)):
            sym = f"{country.upper()}{i:02d}{SUFFIX[country]}"
            out.append({"symbol": sym, "name": f"{country.upper()} Company {i}", "pe": rng.uniform(4, 40),
                        "forward_pe": rng.uniform(4, 35), "market_cap": 1e9 * (100 - i), "currency": "USD"})
        return out

    def fundamentals(self, symbol, region):
        self.calls += 1
        if symbol in self.fail:
            raise ValueError("simulated failure")
        rng = random.Random(symbol)
        price = rng.uniform(10, 300)
        return {"symbol": symbol, "region": region, "name": f"Name {symbol}", "sector": rng.choice(SECTORS),
                "industry": "Test", "country": "United States" if region == "US" else "Elsewhere",
                "currency": "USD", "financial_currency": "USD", "price": price, "market_cap": 5e10,
                "pe": rng.uniform(4, 40), "forward_pe": rng.uniform(4, 35),
                "peg": rng.choice([None, rng.uniform(0.3, 3)]) if region != "US" else rng.uniform(0.3, 3),
                "peg_field": "trailingPegRatio", "earnings_growth": rng.uniform(-0.3, 0.4),
                "debt_to_equity": rng.uniform(10, 300), "free_cashflow": rng.uniform(-1e8, 1e9),
                "profit_margin": rng.uniform(-0.05, 0.3), "recommendation_mean": rng.uniform(1.5, 3.8),
                "recommendation_key": "buy", "analyst_count": rng.randint(0, 30),
                "target_mean": price * rng.uniform(0.8, 1.4), "fetched": now(), "error": ""}

    def recommendation_trend(self, symbol):
        rng = random.Random(symbol + "t")
        mk = lambda: {k: rng.randint(0, 10) for k in ("strongBuy", "buy", "hold", "sell", "strongSell")}  # noqa: E731
        return mk(), mk()

    def history(self, symbol):
        rng = np.random.default_rng(abs(hash(symbol)) % 2**32)
        n = 500
        drift = rng.uniform(-0.001, 0.0015)
        close = 100 * np.exp(np.cumsum(rng.normal(drift, 0.015, n)))
        idx = pd.bdate_range(end=date.today(), periods=n)
        high = close * (1 + rng.uniform(0, 0.02, n))
        low = close * (1 - rng.uniform(0, 0.02, n))
        return pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close,
                             "Volume": rng.integers(1e5, 1e6, n).astype(float)}, index=idx)

    def expirations(self, symbol):
        if "." in symbol:
            return []
        start = date.today()
        fridays = [start + timedelta(days=d) for d in range(1, 120) if (start + timedelta(days=d)).weekday() == 4]
        return [d.isoformat() for d in fridays[:10]]

    def option_contracts(self, symbol, expiration):
        rng = random.Random(symbol + expiration)
        centre = 100
        contracts = []
        for k in range(60, 145, 5):
            contracts.append({"type": "call", "strike": k, "open_interest": rng.randint(0, 5000) * math.exp(-abs(k - centre - 10) / 30), "multiplier": 100})
            contracts.append({"type": "put", "strike": k, "open_interest": rng.randint(0, 5000) * math.exp(-abs(k - centre + 10) / 30), "multiplier": 100})
        for c in contracts:
            c["open_interest"] = float(round(c["open_interest"]))
        return contracts, None
