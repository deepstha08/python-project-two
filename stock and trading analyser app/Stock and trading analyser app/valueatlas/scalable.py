"""Is a stock tradable on Scalable Capital?

Scalable Capital's main trading venue is gettex (Börse München). gettex publishes free,
delayed pre-trade data for every instrument it quotes, as required by EU regulation:
    https://www.gettex.de/en/trading/delayed-data/pretrade-data/
ValueAtlas downloads a few of those files once a day, collects every ISIN that had a
quote, and checks each stock's ISIN against that list.

A stock's ISIN is looked up once (via yfinance's ISIN lookup), confirmed with Yahoo's
search, and cached locally.
"""
from __future__ import annotations

import gzip
import json
import logging
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from . import storage

log = logging.getLogger("valueatlas")

GETTEX_URL = ("https://erdk.bayerische-boerse.de:8000/delayed-data/MUNC-MUND/pretrade/"
              "pretrade.{day}.{hm}.{mic}.csv.gz")
ISIN_RE = re.compile(rb"(?<![A-Z0-9])([A-Z]{2}[A-Z0-9]{9}[0-9])(?![A-Z0-9])")
MAX_BYTES = 400_000_000

YES, NO, UNKNOWN, UNCHECKED = "yes", "no", "unknown", "unchecked"


def isin_valid(isin):
    """ISO 6166 check digit (Luhn over the letters-as-numbers expansion)."""
    if not isin or not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", isin):
        return False
    digits = "".join(str(int(ch, 36)) for ch in isin[:-1])
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 0:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return (10 - total % 10) % 10 == int(isin[-1])


# --------------------------------------------------------------------------- gettex list

def _cache_path():
    return storage.DATA / "gettex_isins.json"


def load_list():
    """Return (set of ISINs, info dict) from the local cache, or (None, None)."""
    try:
        data = json.loads(_cache_path().read_text(encoding="utf-8"))
        return set(data["isins"]), {k: v for k, v in data.items() if k != "isins"}
    except (OSError, ValueError, KeyError):
        return None, None


def _windows(now=None, wanted=3):
    """Recent 15-minute file times (UTC) inside gettex's busy hours, spaced ≥ 2 hours apart."""
    now = now or datetime.now(timezone.utc)
    t = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 15 + 15)
    picked = []
    for _ in range(4 * 24):
        if t.weekday() < 5 and 7 <= t.hour <= 19 and (not picked or picked[-1] - t >= timedelta(hours=2)):
            picked.append(t)
            if len(picked) >= wanted:
                break
        t -= timedelta(minutes=15)
    return picked


def _download_isins(url, opener=None):
    req = urllib.request.Request(url, headers={"User-Agent": "ValueAtlas/3 (personal research tool)"})
    found = set()
    with (opener or urllib.request.urlopen)(req, timeout=90) as resp:
        with gzip.GzipFile(fileobj=resp) as gz:
            read = 0
            for line in gz:
                read += len(line)
                if read > MAX_BYTES:
                    break
                m = ISIN_RE.search(line[:64])
                if m:
                    found.add(m.group(1).decode())
    return found


def refresh_list(force=False, opener=None, now=None):
    """Download today's gettex instrument list (at most once every 20 hours). Returns (isins, info)."""
    isins, info = load_list()
    if not force and info and time.time() - info.get("downloaded_ts", 0) < 20 * 3600:
        return isins, info
    got, files, errors = set(), [], []
    for t in _windows(now):
        for mic in ("mund", "munc"):
            url = GETTEX_URL.format(day=t.strftime("%Y%m%d"), hm=t.strftime("%H.%M"), mic=mic)
            try:
                part = _download_isins(url, opener)
                got |= part
                files.append(url.rsplit("/", 1)[-1])
            except urllib.error.HTTPError as exc:  # that file does not exist – try another time window
                errors.append(f"{url.rsplit('/', 1)[-1]}: HTTP {exc.code}")
            except Exception as exc:  # server unreachable (firewall, offline) – stop trying
                errors.append(f"gettex server unreachable: {str(exc)[:100]}")
                break
        if errors and errors[-1].startswith("gettex server unreachable"):
            break
    if len(got) >= 1000:
        info = {"downloaded": datetime.now(timezone.utc).isoformat(timespec="seconds"), "downloaded_ts": time.time(),
                "files": files, "count": len(got), "source": "gettex pre-trade data (Börse München)"}
        storage.DATA.mkdir(parents=True, exist_ok=True)
        _cache_path().write_text(json.dumps({**info, "isins": sorted(got)}), encoding="utf-8")
        return got, info
    log.warning("gettex list download failed: %s", "; ".join(errors[:3]))
    if isins:  # keep using yesterday's list
        info = dict(info or {}, stale=True, error="; ".join(errors[:2]))
        return isins, info
    return None, {"error": "; ".join(errors[:2]) or "No gettex files available (weekend or night?)"}


# --------------------------------------------------------------------------- ISIN per stock

def cached_isin(symbol):
    with storage.db_session() as db:
        db.execute("CREATE TABLE IF NOT EXISTS isin_map (symbol TEXT PRIMARY KEY, isin TEXT, verified INTEGER, checked TEXT)")
        row = db.execute("SELECT isin, verified FROM isin_map WHERE symbol=?", (symbol,)).fetchone()
    return row  # None = never looked up; (None, 0) = looked up, nothing found


def save_isin(symbol, isin, verified):
    with storage.db_session() as db:
        db.execute("CREATE TABLE IF NOT EXISTS isin_map (symbol TEXT PRIMARY KEY, isin TEXT, verified INTEGER, checked TEXT)")
        db.execute("INSERT OR REPLACE INTO isin_map VALUES(?,?,?,?)", (symbol, isin, int(verified), storage.now()))


def lookup_isin(yf, ticker_obj, symbol):
    """ISIN for a Yahoo symbol, cached. `ticker_obj` should already have fetched .info."""
    hit = cached_isin(symbol)
    if hit is not None:
        return hit[0], bool(hit[1])
    isin, verified = None, False
    try:
        raw = (ticker_obj.get_isin() or "").strip().upper()
        if isin_valid(raw):
            isin = raw
    except Exception as exc:
        log.info("ISIN lookup failed for %s: %s", symbol, exc)
        return None, False  # do not cache network failures
    if isin:
        try:
            res = yf.Search(isin, max_results=10, news_count=0, lists_count=0, recommended=0, raise_errors=False, timeout=10)
            verified = any((q.get("symbol") or "").upper() == symbol for q in (res.quotes or []))
        except Exception:
            verified = False
    save_isin(symbol, isin, verified)
    return isin, verified


def status(isin, gettex, verified=True):
    """yes / no / unknown / unchecked for one stock.

    "no" is only given when the ISIN was confirmed for this exact symbol; an unconfirmed ISIN
    that is missing from gettex may simply be the wrong ISIN, so it counts as "unknown"."""
    if gettex is None:
        return UNCHECKED
    if not isin:
        return UNKNOWN
    if isin in gettex:
        return YES
    return NO if verified else UNKNOWN


def allowed_statuses(settings):
    """Which statuses may appear in the rankings with the user's settings (None = no filter)."""
    if not settings.get("scalable_only", True):
        return None
    allowed = [YES, UNCHECKED]
    if settings.get("scalable_include_unknown", False):
        allowed.append(UNKNOWN)
    return allowed
