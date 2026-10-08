# ValueAtlas 3.5

ValueAtlas is a trading and research app that runs on your own computer and opens in your web browser.

- **Trade desk**: type any ticker and see whether it is **bullish or bearish on the 1m, 2m, 5m, 15m, 30m, 1h, 3h, 4h and 1D charts**. Each verdict combines 60+ indicators, candlestick and chart patterns, and 10 backtested strategies. You also get a live candle chart, TradingView's own live chart and rating gauge (official widgets), and an options panel: max pain, call/put walls, put/call ratios, implied volatility, expected move and unusual activity.
- **Scanner**: every stock in your watchlist on all nine timeframes at once, refreshed every minute.
- **Undervalued screener**: every day it finds the **25 most undervalued stocks in the US, Europe and Asia** using P/E, forward P/E and PEG, rates each bullish or bearish, and tracks options max pain by date.

## Two sections

- **📊 Stock analyser**: *My stocks* (your portfolio and watchlist), *Top 25 undervalued* (US, Europe, Asia), *Max pain*, *Look up* and *Data quality*.
- **⚡ Trading**: *Trade desk* (bullish/bearish on 9 timeframes, live chart, options) and *Scanner*.

## My stocks

Your Scalable Capital portfolio and watchlist (66 stocks, ETFs and ETCs, shown in pages of 20 – adjustable) are built in and checked automatically every day, and again after each screener update. Each stock is rated **Undervalued**, **Neutral** or **Overvalued**:

1. Its **P/E**, **forward P/E** and **PEG** are compared with companies in the same region and sector from the daily market scan. Each gets a 0–100 cheapness percentile (100 = cheapest).
2. The **PEG** is also judged on its own: below 1 is cheap for the expected growth, above 2 is expensive.
3. Score = 65% peer comparison + 35% PEG. **60+ = Undervalued**, **40 or below = Overvalued**, anything in between is Neutral. A forward P/E far above the trailing P/E (falling earnings) costs points.

Each row shows the reasons in plain words, **max pain for the nearest 4 option expiry dates** (with the distance from today's price; adjustable in Settings; European/Asian stocks with a US listing use that listing's options) with one-click links to the same stock on OptionCharts, Yahoo and Barchart, plus the trend (TradingView-method rating) and the 3-month price move. Ratings that changed since the last check are marked "was …". ETFs and commodity ETCs (gold, silver) have no company earnings, so they are "Not rated" and show only their trend. Loss-making companies are also "Not rated". Add stocks with the search box, choose Portfolio or Watchlist, or remove them with ✕.

## Install (Windows 10/11)

1. Extract the ZIP. Right-click it, choose **Extract All**, then open the extracted folder.
2. Double-click **`Install ValueAtlas.cmd`**.
   - If Windows SmartScreen appears, click **More info → Run anyway**. The scripts are plain text, so you can open them in Notepad to check them first.
   - **You don't need to install Python yourself.** The installer downloads a private copy if needed. No administrator rights are required.
3. ValueAtlas opens in your browser at **http://127.0.0.1:8765** on the trade desk. The undervalued screener runs its first scan in the background (5–15 minutes).

The installer also:

- adds a **ValueAtlas** icon to your desktop and Start menu;
- adds Start menu shortcuts for **Update ValueAtlas** and **Uninstall ValueAtlas**;
- starts ValueAtlas quietly when you sign in, so the daily update happens even if the browser is closed. To turn this off, open Task Manager, go to Startup apps and disable *ValueAtlas (background)*, or install with `Install ValueAtlas.cmd -NoStartup`.

The program is installed in `%LOCALAPPDATA%\Programs\ValueAtlas`. Your data is kept in `%LOCALAPPDATA%\ValueAtlas\data`. After installing you can delete the ZIP and the extracted folder.

**macOS / Linux:** open a terminal in the extracted folder and run `bash installer/install.sh`.

## Trade desk

| | |
|---|---|
| **Switch stocks** | Start typing anywhere on the desk. Suggestions come from Yahoo's symbol search; press Enter. Use Yahoo tickers: `AAPL`, `SAP.DE`, `7203.T`, `0700.HK`, `^GSPC`, `ES=F`, `BTC-USD`. |
| **9 timeframe verdicts** | Strong Bullish to Strong Bearish, with the score, the count of bullish/bearish signals, and a **hit rate**: how often the signal's direction matched the next candle on this stock's history. |
| **Why** | Every indicator's value and vote, by category, with the strategy backtests for the selected timeframe. |
| **Chart** | Candles + volume, EMA 9/21/50/200, VWAP, Bollinger, Supertrend, SAR, pattern markers, options levels. A second tab shows TradingView's own live chart. |
| **Refresh** | 1m every 5 s, 5m every 15 s, longer timeframes less often; the verdict strip every 15 s. |
| **Data** | Yahoo Finance: near real-time for US stocks, usually 15–20 min delayed elsewhere. |

A verdict describes how that timeframe's chart looks right now. It is **not a promise** about the next move. The hit rate shows how close to a coin flip the signal has been for that stock, which is often the case on 1–5 minute charts. Trading on short timeframes is risky; this is a research tool, not investment advice.

## Undervalued screener

**Search bar:** type a company name or ticker. It searches the US, European and Asian top 25 lists at once. A stock that isn't in a top 25 shows where it ranks on P/E, forward P/E and PEG, or why it couldn't be ranked. Any other stock can be checked on the spot, and every result links to Investing.com, OptionCharts, TradingView, Yahoo, Morningstar, Seeking Alpha, Barchart options, the Nasdaq option chain and more.

**Scalable Capital only (on by default):** the top 25 lists show only stocks you can buy on Scalable Capital. Once a day the app downloads the free instrument list of gettex (Börse München), Scalable's main exchange, and checks each stock's ISIN against it. Stocks get a "✓ Scalable" badge, and the details panel shows the ISIN so you can find the stock in the Scalable app. A stock whose ISIN can't be found is hidden unless you allow it in Settings. Regions that drop below 25 are automatically topped up with the next-cheapest candidates. Turn it off with the "Only on Scalable Capital" switch.

**PEG:** Yahoo often has no PEG ratio for European and Asian companies. ValueAtlas then estimates it as trailing P/E ÷ analysts' expected EPS growth and marks it "est.". You can turn this off in Settings.

| Feature | How |
|---|---|
| **Top 25 undervalued per region** | Scans the largest companies in 23 markets with the Yahoo Finance screener. The cheapest 120 per region (adjustable) are looked up in detail. Each stock is ranked on a 0–100 score: its average cheapness percentile for P/E, forward P/E and PEG, compared with stocks in the same region or sector. |
| **Value-trap warnings** | ⚠ marks falling earnings, high debt, negative free cash flow, losses, or a forward P/E well above the trailing P/E. These stocks can be hidden with one switch. |
| **Bullish / bearish outlook** | Combines TradingView's published Technical Rating formula (calculated from Yahoo prices), the long-term trend, Yahoo analyst consensus, analyst price-target upside, analyst rating changes, the options put/call ratio and, optionally, Finnhub analyst ratings. The details panel shows each signal and its source. |
| **Max pain by date** | Calculated for the next 8 expiration dates of every analysed stock that has options, using the method published by OptionCharts. It's saved each day, so you can look up past observation dates on the *Max pain* board. The calculator works for any ticker and any listed expiration. |
| **Daily updates** | Runs automatically at 22:30 your local time, after the US close. You can change the time in Settings. If the PC was off, it catches up on the next start. |
| **Ranking movement** | ▲/▼ arrows and NEW badges compare with the previous day's ranking. Each stock keeps a ranking history. |
| **Links to other sites** | One-click links to Yahoo Finance, TradingView, Investing.com, OptionCharts, Finviz, MarketWatch and Google Finance for each stock. You can save notes about what those sites say. |
| **Export** | CSV of all three top-25 lists. |

### Why OptionCharts, TradingView and Investing.com are links only

Their terms of service forbid automated downloading or scraping. ValueAtlas uses the published methods instead: max pain from OptionCharts' documentation, and TradingView's Technical Ratings formula. Each stock has one-click links to those sites so you can compare.

Max pain values can differ slightly from optioncharts.io because the open-interest data comes from Yahoo, not the OCC. Most European and Asian stocks have no options on Yahoo.

## Things to know

- Yahoo data comes through the open-source `yfinance` library. It is free and unofficial, and it sometimes breaks when Yahoo changes its site. If refreshes start failing, run **Update ValueAtlas** from the Start menu.
- If Yahoo rate-limits you, lower *Parallel downloads* or *Stocks analysed in detail per region* in Settings.
- Rankings depend on PEG, which is often missing for non-US companies. That can leave Europe or Asia with fewer than 25 stocks. *Data quality* lists every stock that couldn't be ranked and why.
- Low ratios can reflect real problems. ValueAtlas is a research tool, not investment advice.

## Developers

```bash
python -m unittest discover -s tests -v          # offline tests (no internet needed)
python run.py                                     # start the server + open browser
python run.py --refresh                           # one refresh in the console
```

Environment variables: `VALUEATLAS_DATA` (data folder), `VALUEATLAS_PORT` (default 8765).
