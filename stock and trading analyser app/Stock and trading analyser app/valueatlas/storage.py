"""Settings, paths and the local SQLite database."""
from __future__ import annotations

import contextlib
import csv
import json
import os
import re
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from .core import REGIONS

APP_DIR = Path(__file__).resolve().parent.parent


def _default_data_dir():
    if os.environ.get("VALUEATLAS_DATA"):
        return Path(os.environ["VALUEATLAS_DATA"])
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ValueAtlas" / "data"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ValueAtlas"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "valueatlas"


DATA = _default_data_dir()

DEFAULT_SETTINGS = {
    "auto_refresh": True,
    "refresh_time": "22:30",          # local time; after the US close in Europe
    "use_screener": True,             # scan whole markets, not just the watchlist
    "candidates_per_region": 120,     # stocks per region analysed in detail
    "sector_relative": True,
    "weights": {"pe": 1.0, "forward_pe": 1.0, "peg": 1.0},
    "max_pe": 60.0,
    "max_forward_pe": 60.0,
    "max_peg": 5.0,
    "exclude_flagged": False,
    "us_domestic_only": True,         # keep foreign ADRs out of the US list
    "estimate_peg": True,
    "scalable_only": True,            # only rank stocks quoted on gettex (Scalable Capital's exchange)
    "scalable_include_unknown": False,  # also show stocks whose ISIN could not be found             # estimate PEG from forecast EPS growth when Yahoo has none
    "option_expirations": 8,          # expirations per stock for max pain
    "my_stocks_expirations": 4,       # nearest expiry dates with max pain on the My stocks page
    "finnhub_key": "",
    "workers": 4,
}

_lock = threading.Lock()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_settings():
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / "settings.json"
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
            for key, value in stored.items():
                if key in settings:
                    if isinstance(settings[key], dict) and isinstance(value, dict):
                        settings[key].update(value)
                    else:
                        settings[key] = value
        except (OSError, ValueError):
            pass
    return settings


def save_settings(updates):
    settings = load_settings()
    for key, value in updates.items():
        if key not in DEFAULT_SETTINGS:
            continue
        default = DEFAULT_SETTINGS[key]
        if isinstance(default, bool):
            value = bool(value)
        elif isinstance(default, int) and not isinstance(default, bool):
            value = int(value)
        elif isinstance(default, float):
            value = float(value)
        elif isinstance(default, dict):
            value = {k: float(value.get(k, default[k])) for k in default}
        elif key == "refresh_time":
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(value)):
                raise ValueError("Refresh time must be HH:MM (24-hour).")
        else:
            value = str(value).strip()
        settings[key] = value
    settings["candidates_per_region"] = max(25, min(400, settings["candidates_per_region"]))
    settings["workers"] = max(1, min(8, settings["workers"]))
    settings["option_expirations"] = max(1, min(20, settings["option_expirations"]))
    settings["my_stocks_expirations"] = max(1, min(12, settings["my_stocks_expirations"]))
    (DATA / "settings.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return settings


def connect():
    DATA.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATA / "valueatlas.sqlite", timeout=30, check_same_thread=False)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, started TEXT, finished TEXT,
            status TEXT, summary TEXT, payload TEXT);
        CREATE TABLE IF NOT EXISTS rank_history (day TEXT, region TEXT, symbol TEXT, rank INTEGER,
            score REAL, pe REAL, forward_pe REAL, peg REAL, price REAL, verdict TEXT,
            PRIMARY KEY(day, region, symbol));
        CREATE TABLE IF NOT EXISTS maxpain (symbol TEXT, expiration TEXT, asof TEXT, max_pain REAL,
            price REAL, call_oi REAL, put_oi REAL, pcr REAL, data TEXT, fetched TEXT,
            PRIMARY KEY(symbol, expiration, asof));
        CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, symbol TEXT, source TEXT,
            asof TEXT, signal TEXT, value TEXT, note TEXT, created TEXT);
    """)
    return db


@contextlib.contextmanager
def db_session():
    with _lock:
        db = connect()
        try:
            with db:
                yield db
        finally:
            db.close()


def save_run(payload):
    summary = {k: v for k, v in payload.items() if k not in ("rows", "top")}
    with db_session() as db:
        cur = db.execute("INSERT INTO runs(started, finished, status, summary, payload) VALUES(?,?,?,?,?)",
                         (payload["started"], payload["finished"], payload["status"],
                          json.dumps(summary), json.dumps(payload, allow_nan=False, default=str)))
        run_id = cur.lastrowid
        # Keep the 45 most recent full snapshots; the slim rank history is kept forever.
        db.execute("DELETE FROM runs WHERE id NOT IN (SELECT id FROM runs ORDER BY id DESC LIMIT 45)")
        day = payload.get("asof") or payload["finished"][:10]  # local calendar day
        for region, rows in payload.get("top", {}).items():
            db.execute("DELETE FROM rank_history WHERE day=? AND region=?", (day, region))
            for r in rows:
                db.execute("INSERT OR REPLACE INTO rank_history VALUES(?,?,?,?,?,?,?,?,?,?)",
                           (day, region, r["symbol"], r["rank"], r["score"], r["pe"], r["forward_pe"],
                            r["peg"], r.get("price"), (r.get("sentiment") or {}).get("verdict")))
    return run_id


def latest_run():
    with db_session() as db:
        row = db.execute("SELECT id, payload FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return None
    payload = json.loads(row[1])
    payload["id"] = row[0]
    return payload


def run_list(limit=30):
    with db_session() as db:
        rows = db.execute("SELECT id, started, finished, status, summary FROM runs ORDER BY id DESC LIMIT ?",
                          (limit,)).fetchall()
    return [{"id": r[0], "started": r[1], "finished": r[2], "status": r[3], **json.loads(r[4] or "{}")}
            for r in rows]


def previous_ranks(before_day):
    """Ranks from the most recent day before `before_day`, for 'moved up/down' arrows."""
    with db_session() as db:
        day = db.execute("SELECT MAX(day) FROM rank_history WHERE day < ?", (before_day,)).fetchone()[0]
        if not day:
            return None, {}
        rows = db.execute("SELECT region, symbol, rank FROM rank_history WHERE day=?", (day,)).fetchall()
    return day, {(r[0], r[1]): r[2] for r in rows}


def symbol_history(symbol, limit=120):
    with db_session() as db:
        rows = db.execute("SELECT day, region, rank, score, pe, forward_pe, peg, price, verdict FROM rank_history "
                          "WHERE symbol=? ORDER BY day DESC LIMIT ?", (symbol, limit)).fetchall()
    keys = ("day", "region", "rank", "score", "pe", "forward_pe", "peg", "price", "verdict")
    return [dict(zip(keys, r)) for r in rows]


def save_maxpain(symbol, result, asof):
    with db_session() as db:
        db.execute("INSERT OR REPLACE INTO maxpain VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (symbol, result["expiration"], asof, result["max_pain"], result.get("price"),
                    result["call_oi"], result["put_oi"], result["put_call_ratio"],
                    json.dumps({k: result[k] for k in ("curve", "open_interest", "strikes") if k in result}),
                    result.get("fetched") or now()))


def maxpain_rows(symbol=None, expiration=None, asof=None, with_data=False):
    sql = ("SELECT symbol, expiration, asof, max_pain, price, call_oi, put_oi, pcr, fetched"
           + (", data" if with_data else "") + " FROM maxpain WHERE 1=1")
    args = []
    for col, val in (("symbol", symbol), ("expiration", expiration), ("asof", asof)):
        if val:
            sql += f" AND {col}=?"
            args.append(val)
    sql += " ORDER BY symbol, expiration, asof DESC"
    with db_session() as db:
        rows = db.execute(sql, args).fetchall()
    keys = ["symbol", "expiration", "asof", "max_pain", "price", "call_oi", "put_oi", "pcr", "fetched"]
    out = []
    for r in rows:
        item = dict(zip(keys, r[:9]))
        if with_data:
            item.update(json.loads(r[9] or "{}"))
        out.append(item)
    return out


def maxpain_dates():
    with db_session() as db:
        exp = [r[0] for r in db.execute("SELECT DISTINCT expiration FROM maxpain ORDER BY expiration")]
        asof = [r[0] for r in db.execute("SELECT DISTINCT asof FROM maxpain ORDER BY asof DESC")]
    return exp, asof


def add_note(symbol, source, asof, signal, value, note):
    with db_session() as db:
        db.execute("INSERT INTO notes(symbol, source, asof, signal, value, note, created) VALUES(?,?,?,?,?,?,?)",
                   (symbol, source, asof, signal, value, note, now()))


def get_notes(symbol):
    with db_session() as db:
        rows = db.execute("SELECT id, source, asof, signal, value, note FROM notes WHERE symbol=? "
                          "ORDER BY asof DESC, id DESC", (symbol,)).fetchall()
    return [dict(zip(("id", "source", "asof", "signal", "value", "note"), r)) for r in rows]


def delete_note(note_id):
    with db_session() as db:
        db.execute("DELETE FROM notes WHERE id=?", (int(note_id),))


# --------------------------------------------------------------------------- watchlist

SYMBOL_RE = re.compile(r"\^?[A-Z0-9][A-Z0-9.\-=&]{0,24}")


def watchlist_path():
    DATA.mkdir(parents=True, exist_ok=True)
    dest = DATA / "watchlist.csv"
    if not dest.exists():
        dest.write_bytes((APP_DIR / "watchlist.csv").read_bytes())
    return dest


def parse_watchlist(text):
    reader = csv.DictReader(text.splitlines())
    fields = {f.strip().lower() for f in (reader.fieldnames or [])}
    if not {"symbol", "region"} <= fields:
        raise ValueError("The list needs a header row with symbol,region columns.")
    rows, seen = [], set()
    for raw in reader:
        row = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
        symbol, region = row["symbol"].upper(), row["region"].title()
        if region == "Us":
            region = "US"
        if not symbol:
            continue
        if not SYMBOL_RE.fullmatch(symbol) or region not in REGIONS:
            raise ValueError(f"Invalid row: {symbol!r}, {region!r} (region must be US, Europe or Asia)")
        if symbol in seen:
            continue
        seen.add(symbol)
        rows.append({"symbol": symbol, "region": region})
    return rows


def read_watchlist():
    return parse_watchlist(watchlist_path().read_text(encoding="utf-8-sig"))


def write_watchlist(rows):
    lines = ["symbol,region"] + [f"{r['symbol']},{r['region']}" for r in rows]
    watchlist_path().write_text("\n".join(lines) + "\n", encoding="utf-8")


@contextlib.contextmanager
def refresh_lock():
    """Cross-process lock so two refreshes never run at once."""
    DATA.mkdir(parents=True, exist_ok=True)
    handle = open(DATA / "refresh.lock", "a+b")
    try:
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Another refresh is already running.") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        handle.close()
