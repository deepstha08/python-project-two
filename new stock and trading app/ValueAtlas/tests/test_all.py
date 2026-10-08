import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
TMP = tempfile.mkdtemp(prefix="valueatlas-test-")
os.environ["VALUEATLAS_DATA"] = TMP

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from valueatlas import storage  # noqa: E402
from valueatlas.core import eligible, max_pain, rank_stocks, top_by_region, value_flags, safe_csv  # noqa: E402
from valueatlas.indicators import technical_rating  # noqa: E402
from valueatlas.sentiment import composite  # noqa: E402
from valueatlas.engine import Progress, refresh  # noqa: E402
from fake_provider import FakeProvider  # noqa: E402


def row(sym, pe, fpe, peg, region="US", sector="Tech", **kw):
    return {"symbol": sym, "region": region, "sector": sector, "pe": pe, "forward_pe": fpe, "peg": peg, **kw}


def frame(closes):
    closes = np.asarray(closes, dtype=float)
    idx = pd.bdate_range("2023-01-02", periods=len(closes))
    return pd.DataFrame({"Open": closes, "High": closes * 1.01, "Low": closes * 0.99, "Close": closes,
                         "Volume": np.full(len(closes), 1e6)}, index=idx)


class CoreTests(unittest.TestCase):
    def test_cheapest_ranks_first_and_regions_are_separate(self):
        rows = [row("A", 5, 5, 0.5), row("B", 20, 20, 2), row("C", 10, 10, 1),
                row("X", 50, 50, 5, region="Asia"), row("Y", 30, 30, 3, region="Asia")]
        ranked = rank_stocks(rows, sector_adjust=False)
        top = top_by_region(ranked)
        self.assertEqual([r["symbol"] for r in top["US"]], ["A", "C", "B"])
        self.assertEqual([r["symbol"] for r in top["Asia"]], ["Y", "X"])
        self.assertEqual(top["US"][0]["score"], 100)
        self.assertEqual(top["US"][0]["rank"], 1)

    def test_invalid_metrics_excluded(self):
        for bad in (None, 0, -3, float("nan"), float("inf"), "abc"):
            self.assertFalse(eligible(row("Z", 10, 10, bad)))
        self.assertFalse(eligible(row("Z", 10, 10, 1, error="boom")))
        self.assertFalse(eligible(row("Z", 900, 10, 1), {"pe": 60}))

    def test_sector_relative_needs_five_peers(self):
        rows = [row(f"T{i}", 10 + i, 10 + i, 1 + i / 10) for i in range(5)] + [row("E1", 30, 30, 3, sector="Energy")]
        ranked = {r["symbol"]: r for r in rank_stocks(rows, sector_adjust=True)}
        self.assertEqual(ranked["T0"]["peer_count"], 5)
        self.assertEqual(ranked["E1"]["peer_basis"], "All US")

    def test_weights(self):
        rows = [row("A", 5, 30, 3), row("B", 30, 5, 0.5)]
        only_pe = rank_stocks(rows, sector_adjust=False, weights={"pe": 1, "forward_pe": 0, "peg": 0})
        self.assertEqual(only_pe[0]["symbol"], "A")
        only_peg = rank_stocks(rows, sector_adjust=False, weights={"pe": 0, "forward_pe": 0, "peg": 1})
        self.assertEqual(only_peg[0]["symbol"], "B")

    def test_flags_and_exclusion(self):
        trap = row("T", 5, 9, 0.5, earnings_growth=-0.4, debt_to_equity=400)
        self.assertIn("Earnings falling", value_flags(trap))
        self.assertIn("High debt", value_flags(trap))
        self.assertIn("Earnings expected to drop", value_flags(trap))
        ranked = rank_stocks([trap, row("OK", 10, 9, 1)], sector_adjust=False, exclude_flagged=True)
        self.assertEqual([r["symbol"] for r in ranked], ["OK"])

    def test_max_pain_known_answer(self):
        contracts = [
            {"type": "call", "strike": 90, "open_interest": 100},
            {"type": "call", "strike": 100, "open_interest": 300},
            {"type": "call", "strike": 110, "open_interest": 500},
            {"type": "put", "strike": 90, "open_interest": 500},
            {"type": "put", "strike": 100, "open_interest": 300},
            {"type": "put", "strike": 110, "open_interest": 100},
        ]
        result = max_pain(contracts)
        # payouts: S=90: puts 300*10+100*20=5000 -> *100; S=100: calls 100*10 + puts 100*10 = 2000; S=110: 4000+...
        self.assertEqual(result["max_pain"], 100)
        self.assertEqual(result["payout"], 2000 * 100)
        self.assertAlmostEqual(result["put_call_ratio"], 1.0)

    def test_max_pain_rejects_one_sided(self):
        with self.assertRaises(ValueError):
            max_pain([{"type": "call", "strike": 10, "open_interest": 5}])
        with self.assertRaises(ValueError):
            max_pain([])

    def test_missing_open_interest_counts_as_zero(self):
        r = max_pain([{"type": "call", "strike": 10, "open_interest": None},
                      {"type": "call", "strike": 12, "open_interest": 10},
                      {"type": "put", "strike": 10, "open_interest": 10}])
        self.assertIn(r["max_pain"], (10, 12))

    def test_csv_formula_guard(self):
        self.assertEqual(safe_csv("=HYPERLINK()"), "'=HYPERLINK()")
        self.assertEqual(safe_csv("AAPL"), "AAPL")


class IndicatorTests(unittest.TestCase):
    def test_uptrend_rates_buy(self):
        closes = 50 * np.exp(np.linspace(0, 1.0, 400)) * (1 + 0.003 * np.sin(np.arange(400)))
        t = technical_rating(frame(closes))
        self.assertTrue(t["available"])
        self.assertGreaterEqual(t["ma_rating"], 0.8)
        smooth = technical_rating(frame(50 * np.exp(np.linspace(0, 1.0, 400))))
        self.assertEqual(smooth["ma_votes"]["Ichimoku"], 1)
        self.assertEqual(smooth["ma_rating"], 1.0)
        self.assertIn(t["rating_label"], ("Buy", "Strong Buy"))
        self.assertTrue(t["golden_cross"])

    def test_downtrend_rates_sell(self):
        closes = 200 * np.exp(np.linspace(0, -1.0, 400)) * (1 + 0.003 * np.sin(np.arange(400)))
        t = technical_rating(frame(closes))
        self.assertLess(t["ma_rating"], -0.8)
        self.assertIn(t["rating_label"], ("Sell", "Strong Sell"))

    def test_short_history_unavailable(self):
        self.assertFalse(technical_rating(frame(np.linspace(1, 2, 100)))["available"])


class SentimentTests(unittest.TestCase):
    def test_bullish_and_bearish(self):
        tech_up = {"available": True, "rating": 0.7, "rating_label": "Strong Buy", "ma_label": "Strong Buy",
                   "osc_label": "Buy", "above_sma200": True, "golden_cross": True}
        bull = composite({"recommendation_mean": 1.6, "analyst_count": 20, "price": 100, "target_mean": 130},
                         tech_up, {"put_call_ratio": 0.5, "total_oi": 50000})
        self.assertIn("Bullish", bull["verdict"])
        tech_down = {**tech_up, "rating": -0.7, "rating_label": "Strong Sell", "above_sma200": False, "golden_cross": False}
        bear = composite({"recommendation_mean": 4.0, "analyst_count": 20, "price": 100, "target_mean": 80},
                         tech_down, {"put_call_ratio": 1.8, "total_oi": 50000})
        self.assertIn("Bearish", bear["verdict"])

    def test_no_data(self):
        self.assertEqual(composite({}, None, None)["verdict"], "No data")


class EngineTests(unittest.TestCase):
    def test_full_refresh_and_server(self):
        settings = storage.load_settings()
        settings["candidates_per_region"] = 40
        provider = FakeProvider(fail={"US03"})
        result = refresh(Progress(), provider, settings)
        self.assertEqual(result["failed"], 1)
        self.assertGreater(result["analysed"], 10)
        self.assertTrue(result["top"]["US"])
        self.assertLessEqual(len(result["top"]["Europe"]), 25)
        us = result["top"]["US"][0]
        self.assertIsNotNone(us["sentiment"])
        mp = storage.maxpain_rows(symbol=us["symbol"])
        self.assertTrue(mp, "max pain should be stored for US top stock")

        from valueatlas import server
        server.STATE.run = storage.latest_run()
        server.STATE._provider = provider
        client = server.create_app().test_client()
        r = client.get("/api/rankings").get_json()
        self.assertTrue(r["regions"]["US"])
        first = r["regions"]["US"][0]
        self.assertIn(first["verdict"], ("Strong Bullish", "Bullish", "Neutral", "Bearish", "Strong Bearish"))
        detail = client.get(f"/api/stock/{first['symbol']}").get_json()
        self.assertIn("analysis", detail)
        self.assertTrue(detail["maxpain_history"])
        exp = detail["maxpain_history"][0]["expiration"]
        live = client.get(f"/api/options/{first['symbol']}?expiration={exp}&live=1").get_json()
        self.assertIn("curve", live["result"])
        board = client.get("/api/maxpain-board").get_json()
        self.assertTrue(board["rows"])
        self.assertEqual(client.get("/api/export.csv").status_code, 200)
        self.assertEqual(client.get("/").status_code, 200)
        bad = client.post("/api/settings", json={"refresh_time": "25:00"})
        self.assertEqual(bad.status_code, 400)
        ok = client.post("/api/notes/AAPL", json={"source": "Investing.com", "signal": "Buy"}).get_json()
        self.assertEqual(ok["notes"][0]["source"], "Investing.com")
        self.assertEqual(client.post("/api/watchlist", json={"csv": "symbol,region\nsap.de,europe\n"}).get_json()["rows"],
                         [{"symbol": "SAP.DE", "region": "Europe"}])
        self.assertEqual(client.post("/api/analyze/NEWCO").status_code, 200)

    def test_offline_fails_fast_with_clear_message(self):
        class Offline(FakeProvider):
            def check(self):
                raise ConnectionError("curl: (7) could not connect")
        before = storage.latest_run()
        with self.assertRaisesRegex(RuntimeError, "Can't reach Yahoo Finance"):
            refresh(Progress(), Offline(), storage.load_settings())
        self.assertEqual((before or {}).get("id"), (storage.latest_run() or {}).get("id"))

    def test_mass_failure_aborts(self):
        class Broken(FakeProvider):
            def fundamentals(self, symbol, region):
                raise ConnectionError("blocked")
        with self.assertRaisesRegex(RuntimeError, "all failed"):
            refresh(Progress(), Broken(), storage.load_settings())

    def test_lock_blocks_overlap(self):
        with storage.refresh_lock():
            with self.assertRaises(RuntimeError):
                with storage.refresh_lock():
                    pass

    def test_cancel_keeps_previous(self):
        before = storage.latest_run()
        p = Progress()
        p.cancel.set()
        with self.assertRaises(RuntimeError):
            refresh(p, FakeProvider(), storage.load_settings())
        after = storage.latest_run()
        self.assertEqual((before or {}).get("id"), (after or {}).get("id"))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class YahooAdapterTests(unittest.TestCase):
    """Exercise the yfinance adapter against stub objects shaped like yfinance 1.x results."""

    def setUp(self):
        import yfinance
        from collections import namedtuple
        from valueatlas.provider import YahooProvider
        Chain = namedtuple("Options", ["calls", "puts", "underlying"])

        class StubTicker:
            def __init__(self, symbol):
                self.symbol = symbol
                self.options = ("2030-01-18", "2030-02-15")
                self.recommendations = pd.DataFrame([
                    {"period": "0m", "strongBuy": 5, "buy": 10, "hold": 3, "sell": 0, "strongSell": 0},
                    {"period": "-3m", "strongBuy": 3, "buy": 8, "hold": 6, "sell": 1, "strongSell": 0}])

            def get_info(self):
                return {"symbol": self.symbol, "quoteType": "EQUITY", "shortName": "Stub Inc", "sector": "Technology",
                        "currency": "USD", "financialCurrency": "USD", "country": "United States",
                        "currentPrice": 100.0, "trailingPE": 12.5, "forwardPE": 10.0, "trailingPegRatio": None,
                        "pegRatio": 0.9, "dividendRate": 2.0, "recommendationMean": 1.9, "recommendationKey": "buy",
                        "numberOfAnalystOpinions": 20, "targetMeanPrice": 120.0}

            def option_chain(self, exp):
                calls = pd.DataFrame({"strike": [90.0, 100.0, 110.0], "openInterest": [10, float("nan"), 30],
                                      "contractSize": ["REGULAR", "REGULAR", float("nan")]})
                puts = pd.DataFrame({"strike": [90.0, 100.0, 110.0], "openInterest": [30, 20, 10],
                                     "contractSize": ["REGULAR"] * 3})
                return Chain(calls, puts, {"regularMarketPrice": 101.0})

        class StubYF:
            EquityQuery = yfinance.EquityQuery
            Ticker = StubTicker

            @staticmethod
            def screen(query, offset=0, size=25, sortField=None, sortAsc=None):
                assert query.to_dict()["operator"] == "AND"
                return {"quotes": [{"symbol": "aaa", "quoteType": "EQUITY", "trailingPE": 9, "forwardPE": 8},
                                   {"symbol": "ETF1", "quoteType": "ETF"}]}

        self.p = YahooProvider.__new__(YahooProvider)
        self.p.yf = StubYF

    def test_fundamentals_mapping(self):
        r = self.p.fundamentals("STUB", "US")
        self.assertEqual(r["peg"], 0.9)
        self.assertEqual(r["peg_field"], "pegRatio")
        self.assertAlmostEqual(r["dividend_yield"], 0.02)
        self.assertEqual(r["pe"], 12.5)

    def test_screen_and_trend_and_options(self):
        quotes = self.p.screen("us", ["NMS"], 10)
        self.assertEqual([q["symbol"] for q in quotes], ["AAA"])
        now_t, then_t = self.p.recommendation_trend("STUB")
        self.assertEqual(now_t["buy"], 10)
        self.assertEqual(then_t["hold"], 6)
        contracts, price = self.p.option_contracts("STUB", "2030-01-18")
        self.assertEqual(price, 101.0)
        self.assertEqual(len(contracts), 6)
        result = max_pain(contracts)
        self.assertEqual(result["strikes"], [100.0, 110.0])
        self.assertEqual(result["max_pain"], 100.0)


class TradingDeskTests(unittest.TestCase):
    def setUp(self):
        from fake_market import FakeYF
        from valueatlas.trading import LiveData
        from valueatlas import trade_api, server
        self.live = LiveData(FakeYF)
        trade_api.LIVE = self.live
        self.client = server.create_app().test_client()

    def test_signals_on_trend(self):
        from valueatlas.signals import analyse
        n = 400
        up = 50 * np.exp(np.linspace(0, 0.8, n)) * (1 + 0.002 * np.sin(np.arange(n)))
        r = analyse(frame(up), intraday=False)
        self.assertIn("Bullish", r["verdict"])
        self.assertGreater(r["counts"]["bullish"], r["counts"]["bearish"])
        down = analyse(frame(up[::-1]), intraday=False)
        self.assertIn("Bearish", down["verdict"])
        names = {s["name"] for s in r["signals"]}
        for must in ("RSI (14)", "MACD (12, 26, 9)", "Supertrend (10, 3)", "Ichimoku cloud", "Candlestick patterns",
                     "Golden / death cross", "On-balance volume", "Bollinger %B"):
            self.assertIn(must, names)
        self.assertGreaterEqual(len(r["strategies"]), 8)
        self.assertIsNotNone(r["hit_rate"]["next_bar"])

    def test_candlestick_patterns(self):
        from valueatlas.signals import candle_patterns
        idx = pd.bdate_range("2024-01-01", periods=30)
        o = np.linspace(110, 100, 30)
        c = o - 0.8
        o[-1], c[-1] = c[-2] - 0.3, o[-2] + 0.6  # big green candle engulfing the previous red one
        df = pd.DataFrame({"Open": o, "High": np.maximum(o, c) + 0.1, "Low": np.minimum(o, c) - 0.1, "Close": c, "Volume": 1.0}, index=idx)
        votes, found = candle_patterns(df)
        self.assertIn("Bullish engulfing", [f[1] for f in found if f[0] == 29])
        self.assertEqual(votes.iloc[-1], 1)

    def test_backtest_math(self):
        from valueatlas.signals import backtest
        close = pd.Series([100, 110, 121, 121, 108.9])
        r = backtest(close, [1, 1, 0, -1, -1], warmup=0)
        # long 100->110->121 (+21%), flat, short 121->108.9 (+10%)
        self.assertEqual(r["trades"], 2)
        self.assertAlmostEqual(r["total_return"], 1.21 * 1.10 - 1, places=6)
        self.assertEqual(r["win_rate"], 1.0)

    def test_options_analytics(self):
        from valueatlas.options_live import analyse_chain
        calls = pd.DataFrame({"strike": [90.0, 100, 110], "openInterest": [100, 300, 900], "volume": [50, 800, 2000],
                              "bid": [10.5, 2.0, 0.4], "ask": [11.0, 2.2, 0.5], "lastPrice": [10.7, 2.1, 0.45],
                              "impliedVolatility": [0.3, 0.3, 0.32]})
        puts = pd.DataFrame({"strike": [90.0, 100, 110], "openInterest": [800, 300, 100], "volume": [100, 300, 50],
                             "bid": [0.3, 1.9, 10.2], "ask": [0.4, 2.1, 10.8], "lastPrice": [0.35, 2.0, 10.5],
                             "impliedVolatility": [0.33, 0.31, 0.3]})
        r = analyse_chain(calls, puts, 100.0, "2030-01-18", today=__import__("datetime").date(2030, 1, 11))
        self.assertEqual(r["call_wall"], 110)
        self.assertEqual(r["put_wall"], 90)
        self.assertEqual(r["atm_strike"], 100)
        self.assertAlmostEqual(r["straddle"], 2.1 + 2.0)
        self.assertAlmostEqual(r["pcr_volume"], 450 / 2850)
        self.assertEqual(r["unusual"][0]["side"], "Call")
        self.assertEqual(r["sentiment"]["verdict"], "Bullish")

    def test_resample_matches_tradingview_sessions(self):
        from valueatlas.trading import resample_session, tradingview_symbol
        idx = pd.date_range("2026-10-01 09:30", periods=7, freq="60min", tz="America/New_York")
        df = pd.DataFrame({"Open": range(7), "High": range(1, 8), "Low": range(7), "Close": range(7), "Volume": 1.0}, index=idx)
        out = resample_session(df.astype(float), 4)
        self.assertEqual([t.strftime("%H:%M") for t in out.index], ["09:30", "13:30"])
        self.assertEqual(out["High"].iloc[0], 4)
        self.assertEqual(tradingview_symbol("0700.HK"), "HKEX:700")
        self.assertEqual(tradingview_symbol("AAPL", "NMS"), "NASDAQ:AAPL")

    def test_api_end_to_end(self):
        c = self.client
        s = c.get("/api/trade/summary/aapl").get_json()
        self.assertEqual(set(s["frames"]), {"1m", "2m", "5m", "15m", "30m", "1h", "3h", "4h", "1D"})
        for f in s["frames"].values():
            self.assertTrue(f["available"], f)
            self.assertIn(f["verdict"], ("Strong Bullish", "Bullish", "Neutral", "Bearish", "Strong Bearish"))
        self.assertIsNotNone(s["quote"]["price"])
        ch = c.get("/api/trade/chart/AAPL/3h").get_json()
        self.assertEqual(len(ch["candles"]["t"]), len(ch["overlays"]["ema9"]))
        o = c.get("/api/trade/options/AAPL").get_json()
        self.assertTrue(o["available"])
        self.assertIn("max_pain", o)
        self.assertFalse(c.get("/api/trade/options/SAP.DE").get_json()["available"])
        self.assertEqual(c.get("/api/trade/summary/EMPTY1").status_code, 404)
        self.assertEqual(c.get("/api/trade/chart/AAPL/7m").status_code, 400)
        self.assertTrue(c.get("/api/trade/search?q=tes").get_json()["results"])
        c.post("/api/trade/watchlist", json={"symbols": ["AAPL", "BADONE"]})
        rows = c.get("/api/trade/scan").get_json()["rows"]
        self.assertIn("error", rows[1])
        self.assertIn("groups", rows[0])


class SearchFixTests(unittest.TestCase):
    """Regressions for the 2.0 search problems."""

    @classmethod
    def setUpClass(cls):
        from valueatlas import server, trade_api
        from valueatlas.trading import LiveData
        from fake_market import FakeYF
        s = storage.load_settings()
        s["candidates_per_region"] = 40
        refresh(Progress(), FakeProvider(), s)
        server.STATE.run = storage.latest_run()
        server.STATE._provider = FakeProvider()
        server.STATE.rank_cache.clear()
        trade_api.LIVE = LiveData(FakeYF)
        cls.c = server.create_app().test_client()

    def test_lookup_with_browser_json_header_and_no_body(self):
        # Browsers sent Content-Type: application/json with an empty body -> 400 in 2.0
        r = self.c.post("/api/analyze/NEWCO", headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 200, r.data)
        d = self.c.get("/api/stock/NEWCO").get_json()
        self.assertIn(d["standing"]["status"], ("would_top25", "would_rank", "excluded"))
        self.assertTrue(any(l["name"].startswith("Investing.com") for l in d["links"]))
        self.assertTrue(any(l["name"].startswith("OptionCharts") for l in d["links"]))

    def test_search_covers_all_three_regions(self):
        top = self.c.get("/api/rankings").get_json()["regions"]
        for region in ("US", "Europe", "Asia"):
            sym = top[region][0]["symbol"]
            d = self.c.get(f"/api/search?q={sym}").get_json()
            self.assertIn(sym, [r["symbol"] for r in d["top"]], region)

    def test_search_outside_top25(self):
        top = {r["symbol"] for rr in self.c.get("/api/rankings").get_json()["regions"].values() for r in rr}
        outside = next(r for r in storage.latest_run()["rows"] if r["symbol"] not in top and not r.get("error"))
        d = self.c.get(f"/api/search?q={outside['symbol']}").get_json()
        hit = next(r for r in d["scanned"] if r["symbol"] == outside["symbol"])
        self.assertIn(hit["status"], ("ranked", "excluded"))
        d = self.c.get("/api/search?q=tesla").get_json()
        self.assertEqual(d["others"][0]["symbol"], "TSLA")
        self.assertTrue(d["others"][0]["links"])
        self.assertIsNone(d["direct"])
        self.assertEqual(self.c.get("/api/search?q=").get_json()["top"], [])

    def test_peg_estimate(self):
        from valueatlas.provider import YahooProvider

        class T:
            def __init__(self, s): pass
            def get_info(self):
                return {"symbol": "X.DE", "quoteType": "EQUITY", "trailingPE": 12.0, "forwardPE": 10.0,
                        "trailingEps": 2.0, "forwardEps": 2.4, "currentPrice": 24.0}

        class Y:
            Ticker = T
        p = YahooProvider.__new__(YahooProvider)
        p.yf = Y
        r = p.fundamentals("X.DE", "Europe")
        self.assertIsNone(r["peg"])
        self.assertAlmostEqual(r["peg_estimate"], 12.0 / 20.0)


class ScalableTests(unittest.TestCase):
    def test_isin_check_digit(self):
        from valueatlas.scalable import isin_valid
        for good in ("US0378331005", "DE0007164600", "JP3633400001", "KYG875721634"):
            self.assertTrue(isin_valid(good), good)
        for bad in ("US0378331006", "US037833100", "us0378331005", "-", None):
            self.assertFalse(isin_valid(bad), bad)

    def test_gettex_file_parsing_and_cache(self):
        import gzip, io
        from datetime import datetime, timezone
        from valueatlas import scalable
        body = gzip.compress(b"US0378331005,2026-10-06T10:00:00Z,226.1,100,226.3,100,EUR\n"
                             + b"".join(f"DE000{i:06d}0,x,1,1,1,1,EUR\n".encode() for i in range(1500))
                             + b"garbage line\n")

        class Resp(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False
        urls = []

        def opener(req, timeout=0):
            urls.append(req.full_url)
            return Resp(body)
        isins, info = scalable.refresh_list(force=True, opener=opener, now=datetime(2026, 10, 6, 21, 8, tzinfo=timezone.utc))
        self.assertIn("US0378331005", isins)
        self.assertEqual(info["count"], 1501)
        self.assertTrue(urls[0].endswith("pretrade.20261006.19.45.mund.csv.gz"), urls[0])
        self.assertEqual(scalable.load_list()[0], isins)
        # weekend: windows fall back to Friday's trading hours
        sat = scalable._windows(datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc))
        self.assertTrue(all(t.weekday() == 4 for t in sat))

    def test_unreachable_server_stops_quickly(self):
        from valueatlas import scalable
        calls = []

        def opener(req, timeout=0):
            calls.append(1)
            raise ConnectionResetError("reset")
        isins, info = scalable.refresh_list(force=True, opener=opener)
        self.assertEqual(len(calls), 1)

    def test_rankings_only_show_scalable_stocks_and_top_up(self):
        class Prov(FakeProvider):
            def fundamentals(self, symbol, region):
                r = super().fundamentals(symbol, region)
                r["isin"] = f"ISIN-{symbol}"
                return r
        # every second stock is "not on gettex"
        Prov._gettex = {f"ISIN-{c}{i:02d}{suf}" for c, suf in (("US", ""), ("GB", ".L"), ("DE", ".DE"), ("FR", ".PA"),
                                                              ("JP", ".T"), ("HK", ".HK")) for i in range(0, 60, 2)}
        s = storage.load_settings()
        s["candidates_per_region"] = 30
        result = refresh(Progress(), Prov(), s)
        rows = {r["symbol"]: r for r in result["rows"]}
        self.assertGreater(len(rows), 90, "regions short of 25 should be topped up with more candidates")
        for region, top in result["top"].items():
            for r in top:
                self.assertEqual(rows[r["symbol"]]["scalable"], "yes", (region, r["symbol"]))
        from valueatlas import server
        server.STATE.run = storage.latest_run()
        server.STATE.rank_cache.clear()
        c = server.create_app().test_client()
        on = c.get("/api/rankings").get_json()["regions"]["US"]
        off = c.get("/api/rankings?scalable=false").get_json()["regions"]["US"]
        self.assertTrue(all(r["scalable"] == "yes" for r in on))
        self.assertTrue(any(r["scalable"] == "no" for r in off))
        missing = next(sym for sym, r in rows.items() if r.get("scalable") == "no" and not r.get("error"))
        st = c.get(f"/api/stock/{missing}").get_json()["standing"]
        self.assertEqual(st["status"], "excluded")
        self.assertIn("Scalable", st["reason"])


class MyStocksTests(unittest.TestCase):
    def test_default_list_has_users_stocks(self):
        from valueatlas import mystocks
        mystocks._path().unlink(missing_ok=True)  # fresh install -> seeded with the user's list
        syms = {e["symbol"]: e for e in mystocks.load_list()}
        for s in ("AAPL", "NVDA", "MUV2.DE", "ASML.AS", "NOVO-B.CO", "CBUM.DE", "GLDA.DE", "9888.HK"):
            self.assertIn(s, syms)
        self.assertEqual(syms["CI"]["list"], "Portfolio")
        self.assertEqual(syms["KO"]["list"], "Watchlist")

    def test_verdicts(self):
        from valueatlas.mystocks import value_verdict
        peers = [{"symbol": f"P{i}", "region": "US", "sector": "Tech", "pe": 10 + 2 * i, "forward_pe": 9 + 2 * i,
                  "peg": 0.6 + 0.15 * i} for i in range(12)]
        cheap = value_verdict({"symbol": "C", "region": "US", "sector": "Tech", "pe": 9, "forward_pe": 8, "peg": 0.6}, peers)
        dear = value_verdict({"symbol": "D", "region": "US", "sector": "Tech", "pe": 60, "forward_pe": 50, "peg": 3.5}, peers)
        mid = value_verdict({"symbol": "M", "region": "US", "sector": "Tech", "pe": 21, "forward_pe": 20, "peg": 1.5}, peers)
        self.assertEqual(cheap["verdict"], "Undervalued")
        self.assertEqual(dear["verdict"], "Overvalued")
        self.assertEqual(mid["verdict"], "Neutral")
        self.assertIn("Tech", cheap["peer_basis"])
        self.assertEqual(value_verdict({"symbol": "L", "region": "US", "pe": None, "forward_pe": None}, peers)["verdict"], "Not rated")
        self.assertEqual(value_verdict({"symbol": "E", "kind": "fund"}, peers)["verdict"], "Not rated")
        # no peers yet -> rule of thumb still gives a rating
        self.assertEqual(value_verdict({"symbol": "C", "region": "US", "pe": 8, "forward_pe": 7, "peg": 0.5}, [])["verdict"], "Undervalued")

    def test_check_and_api(self):
        from valueatlas import mystocks, server

        class Fund:
            def __init__(self, s): self.s = s
            def get_info(self):
                return {"symbol": self.s, "quoteType": "ETF", "shortName": "Gold ETC", "regularMarketPrice": 145.9, "currency": "EUR"}

        class Prov(FakeProvider):
            yf = type("Y", (), {"Ticker": Fund})

            def fundamentals(self, symbol, region):
                if symbol in ("GLDA.DE", "CBUM.DE", "SSLN.MI", "REXC.L"):
                    raise ValueError("Not a common stock (Yahoo type: ETF).")
                if symbol == "BROKEN":
                    raise ValueError("Yahoo returned no data for this symbol.")
                return super().fundamentals(symbol, region)
        mystocks.save_list([{"symbol": "AAPL", "name": "Apple", "list": "Portfolio", "alts": []},
                            {"symbol": "GLDA.DE", "name": "Amundi Physical Gold ETC", "list": "Watchlist", "alts": []}])
        (storage.DATA / "mystocks_seed.txt").write_text(str(mystocks.SEED_VERSION))
        server.STATE._provider = Prov()
        server.STATE.run = storage.latest_run()
        res = server.STATE.my.run(Prov(), (server.STATE.run or {}).get("rows") or [], server.guess_region)
        by = {r["symbol"]: r for r in res}
        self.assertIn(by["AAPL"]["verdict"], ("Undervalued", "Neutral", "Overvalued", "Not rated"))
        self.assertEqual(by["GLDA.DE"]["kind"], "fund")
        self.assertEqual(by["GLDA.DE"]["verdict"], "Not rated")
        c = server.create_app().test_client()
        items = c.get("/api/mystocks").get_json()["items"]
        self.assertEqual([i["symbol"] for i in items], ["AAPL", "GLDA.DE"])
        aapl = items[0]
        self.assertGreaterEqual(len(aapl["max_pain_dates"]), 1)
        self.assertLessEqual(len(aapl["max_pain_dates"]), 4)
        dates = [x["expiration"] for x in aapl["max_pain_dates"]]
        self.assertEqual(dates, sorted(dates))  # nearest expiry first
        self.assertEqual(items[1]["max_pain_dates"], [])
        board = c.get("/api/maxpain-board?mine=1&expiration=all").get_json()["rows"]
        self.assertTrue(board and all(r["symbol"] in ("AAPL", "GLDA.DE") for r in board))
        self.assertEqual(c.post("/api/mystocks/add", json={"symbol": "msft", "list": "watchlist"}).status_code, 200)
        items = c.get("/api/mystocks").get_json()["items"]
        self.assertEqual(items[-1]["symbol"], "MSFT")
        self.assertFalse(items[-1].get("pending"))
        r = c.post("/api/mystocks/add", json={"symbol": "BROKEN"}).get_json()
        broken = next(i for i in c.get("/api/mystocks").get_json()["items"] if i["symbol"] == "BROKEN")
        self.assertIn("No Yahoo data", broken["error"])
        self.assertEqual(c.post("/api/mystocks/add", json={"symbol": "bad sym"}).status_code, 400)
        c.delete("/api/mystocks/BROKEN")
        self.assertNotIn("BROKEN", [i["symbol"] for i in c.get("/api/mystocks").get_json()["items"]])
        d = c.get("/api/stock/MSFT").get_json()
        self.assertIsNotNone(d["my"])
        self.assertTrue(c.get("/api/mystocks/history/AAPL").get_json()["history"])


class MyStocksV2Tests(unittest.TestCase):
    def test_full_list_and_merge_into_existing_install(self):
        from valueatlas import mystocks
        mystocks._path().unlink(missing_ok=True)
        (storage.DATA / "mystocks_seed.txt").unlink(missing_ok=True)
        syms = {e["symbol"]: e for e in mystocks.load_list()}
        self.assertEqual(len(syms), 66)
        for s in ("SPCX", "PLTR", "ORCL", "SAP.DE", "SIE.DE", "RHM.DE", "TSLA", "WMT", "ZAL.DE", "1810.HK", "0700.HK", "UNA.AS"):
            self.assertIn(s, syms)
        self.assertEqual(syms["7203.T"]["options"], "TM")
        # an older install (v1 list, user removed KO and added XYZ) gets the new stocks without losing changes
        old = [e for e in mystocks.load_list() if e["symbol"] not in ("SPCX", "PLTR", "KO")] + \
              [{"symbol": "XYZ", "name": "Mine", "list": "Watchlist", "alts": []}]
        for e in old:
            e.pop("options", None)
        mystocks.save_list(old)
        (storage.DATA / "mystocks_seed.txt").write_text("1")
        merged = {e["symbol"]: e for e in mystocks.load_list()}
        self.assertIn("SPCX", merged)
        self.assertIn("PLTR", merged)
        self.assertIn("XYZ", merged)
        self.assertEqual(merged["SAP.DE"]["options"], "SAP")
        self.assertIn("KO", merged)  # part of the shipped list again
        # once merged, later loads don't re-add removed entries
        mystocks.remove_stock("KO")
        self.assertNotIn("KO", {e["symbol"] for e in mystocks.load_list()})

    def test_max_pain_via_us_listing(self):
        from valueatlas import mystocks, server
        mystocks.save_list([{"symbol": "7203.T", "name": "Toyota", "list": "Watchlist", "alts": [], "options": "TM"},
                            {"symbol": "RHM.DE", "name": "Rheinmetall", "list": "Watchlist", "alts": []}])
        (storage.DATA / "mystocks_seed.txt").write_text(str(mystocks.SEED_VERSION))
        res = {r["symbol"]: r for r in server.STATE.my.run(FakeProvider(), [], server.guess_region)}
        self.assertTrue(res["7203.T"]["options"]["available"])
        self.assertEqual(res["7203.T"]["options_symbol"], "TM")
        self.assertFalse(res["RHM.DE"]["options"]["available"])
        c = server.create_app().test_client()
        items = {i["symbol"]: i for i in c.get("/api/mystocks").get_json()["items"]}
        self.assertTrue(items["7203.T"]["max_pain_dates"])
        self.assertEqual(items["7203.T"]["options_symbol"], "TM")
