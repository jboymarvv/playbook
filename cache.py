"""
cache.py — Caching layer
=========================
Candles and token info are PERMANENT once fetched — a closed position's
historical price data never changes, so there's no reason to ever expire
it. Stored in SQLite (same file-based approach as access.py) so this
doubles as a growing, permanent archive of memecoin price history that
gets more valuable the more tokens get analysed across all users.

Wallet *results* (the full report for a wallet+timeframe) and rate-limit
counters stay short-lived in memory — those legitimately should go stale
(a user might upload a newer CSV with more trades) or reset (daily limits).

Swap to Postgres at real scale by keeping these same function names.
"""

import time
import json
import sqlite3
import threading
from datetime import datetime, timezone

import config

_lock = threading.Lock()

# Short-lived, in-memory (correct to reset / expire)
_wallet_store = {}     # key -> (expiry_ts, data)
_rate_store = {}       # ip  -> (day, count)


def _connect():
    conn = sqlite3.connect(config.CACHE_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS candles (
            cache_key TEXT PRIMARY KEY,
            data      TEXT NOT NULL,
            cached_at REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS token_info (
            token     TEXT PRIMARY KEY,
            data      TEXT NOT NULL,
            cached_at REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wallet_results (
            wallet     TEXT NOT NULL,
            days       INTEGER,                  -- NULL = all-time, 7 = preview
            data       TEXT NOT NULL,
            cached_at  REAL NOT NULL,
            PRIMARY KEY (wallet, days)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS llm_summaries (
            wallet     TEXT PRIMARY KEY,         -- all-time only; one per wallet
            data       TEXT NOT NULL,
            model      TEXT,
            cached_at  REAL NOT NULL
        )
    """)
    return conn


def _candle_key(token, from_ts):
    # Bucket by day so the same token+day is cached once
    day = datetime.fromtimestamp(from_ts, tz=timezone.utc).strftime("%Y-%m-%d")
    return f"{token}:{day}"


# --- CANDLES (permanent) -------------------------------------
def get_candles(token, from_ts):
    key = _candle_key(token, from_ts)
    with _connect() as conn:
        row = conn.execute("SELECT data FROM candles WHERE cache_key = ?", (key,)).fetchone()
    return json.loads(row[0]) if row else None

def set_candles(token, from_ts, data):
    key = _candle_key(token, from_ts)
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO candles (cache_key, data, cached_at) VALUES (?, ?, ?) "
            "ON CONFLICT(cache_key) DO UPDATE SET data=excluded.data",
            (key, json.dumps(data), time.time()),
        )


# --- TOKEN INFO (permanent) -----------------------------------
def get_token_info(token):
    with _connect() as conn:
        row = conn.execute("SELECT data FROM token_info WHERE token = ?", (token,)).fetchone()
    return json.loads(row[0]) if row else None

def set_token_info(token, data):
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO token_info (token, data, cached_at) VALUES (?, ?, ?) "
            "ON CONFLICT(token) DO UPDATE SET data=excluded.data",
            (token, json.dumps(data), time.time()),
        )


# --- WALLET RESULTS -------------------------------------------
# All-time (days=None) is cached PERMANENTLY in SQLite — the underlying
# history doesn't change, so the report is stable and reusable forever.
# The 7-day preview (days=7) is a ROLLING window ("last 7 days"), so its
# meaning changes daily — that one stays short-lived in memory only.
def get_wallet_result(wallet, days):
    if days is None:
        with _connect() as conn:
            row = conn.execute(
                "SELECT data FROM wallet_results WHERE wallet = ? AND days IS NULL",
                (wallet,),
            ).fetchone()
        return json.loads(row[0]) if row else None
    # rolling preview — transient in-memory with TTL
    key = f"wallet:{wallet}:{days}"
    entry = _wallet_store.get(key)
    if entry and entry[0] > time.time():
        return entry[1]
    return None

def set_wallet_result(wallet, days, data):
    if days is None:
        with _lock, _connect() as conn:
            conn.execute(
                "INSERT INTO wallet_results (wallet, days, data, cached_at) "
                "VALUES (?, NULL, ?, ?) "
                "ON CONFLICT(wallet, days) DO UPDATE SET "
                "data=excluded.data, cached_at=excluded.cached_at",
                (wallet, json.dumps(data), time.time()),
            )
        return
    key = f"wallet:{wallet}:{days}"
    _wallet_store[key] = (time.time() + config.WALLET_RESULT_TTL, data)

def clear_wallet_result(wallet):
    """Manual invalidation — wipes cached all-time result + LLM summary for a wallet."""
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM wallet_results WHERE wallet = ?", (wallet,))
        conn.execute("DELETE FROM llm_summaries WHERE wallet = ?", (wallet,))
    for k in list(_wallet_store.keys()):
        if k.startswith(f"wallet:{wallet}:"):
            _wallet_store.pop(k, None)


# --- LLM SUMMARIES (permanent, all-time only) -----------------
# Cache the xAI output per wallet forever, so we only ever pay for it once.
def get_llm_summary(wallet):
    with _connect() as conn:
        row = conn.execute(
            "SELECT data FROM llm_summaries WHERE wallet = ?", (wallet,)
        ).fetchone()
    return json.loads(row[0]) if row else None

def set_llm_summary(wallet, data, model=""):
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO llm_summaries (wallet, data, model, cached_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(wallet) DO UPDATE SET "
            "data=excluded.data, model=excluded.model, cached_at=excluded.cached_at",
            (wallet, json.dumps(data), model, time.time()),
        )


# --- RATE LIMITING (daily, in-memory by design) ----------------
def check_rate_limit(ip):
    """Returns True if allowed, False if over daily limit."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    entry = _rate_store.get(ip)
    if not entry or entry[0] != today:
        _rate_store[ip] = (today, 1)
        return True
    if entry[1] >= config.RATE_LIMIT_PER_DAY:
        return False
    _rate_store[ip] = (today, entry[1] + 1)
    return True


# --- ARCHIVE STATS (the "valuable dataset" growing in the background) --
def archive_stats():
    """How big is the permanent token/candle archive right now?"""
    with _connect() as conn:
        n_tokens = conn.execute("SELECT COUNT(*) FROM token_info").fetchone()[0]
        n_candle_buckets = conn.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
    return {"unique_tokens_archived": n_tokens, "candle_day_buckets_archived": n_candle_buckets}
