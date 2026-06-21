"""
access.py — Who has paid? (persistent, file-based)
====================================================
Tracks premium access keyed by WALLET ADDRESS, stored in a single SQLite
file on disk. SQLite needs no separate server or account (it's built into
Python) but, unlike a hand-written text file, writes are atomic — a crash
mid-write can't corrupt it, and "is this still valid" is a real query.

This survives server restarts as long as the .db file itself lives on
persistent storage. On Railway specifically, attach a Volume and point
ACCESS_DB_PATH at a path inside it — otherwise the container's local disk
can be wiped on redeploy, same risk as the old in-memory version.

Function signatures are unchanged from before, so nothing else in the app
(main.py, payments.py) needs to change.
"""

import sqlite3
import time
import threading

import config

_lock = threading.Lock()


def _connect():
    conn = sqlite3.connect(config.ACCESS_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS access (
            wallet  TEXT PRIMARY KEY,
            kind    TEXT NOT NULL,      -- 'one-time' or 'sub'
            expires REAL,               -- unix ts; NULL = never expires
            since   REAL NOT NULL,
            updated REAL NOT NULL
        )
    """)
    return conn


def grant_one_time(wallet):
    """Permanent all-time report unlock for this wallet."""
    now = time.time()
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO access (wallet, kind, expires, since, updated) "
            "VALUES (?, 'one-time', NULL, ?, ?) "
            "ON CONFLICT(wallet) DO UPDATE SET kind='one-time', expires=NULL, updated=excluded.updated",
            (wallet, now, now),
        )


def grant_subscription(wallet, days=30):
    """Subscription unlock, valid for `days` from now (extends if still active)."""
    now = time.time()
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT kind, expires FROM access WHERE wallet = ?", (wallet,)
        ).fetchone()
        base = now
        if row and row[0] == "sub" and row[1] and row[1] > now:
            base = row[1]   # extend an already-active subscription
        new_expiry = base + days * 86400
        conn.execute(
            "INSERT INTO access (wallet, kind, expires, since, updated) "
            "VALUES (?, 'sub', ?, ?, ?) "
            "ON CONFLICT(wallet) DO UPDATE SET kind='sub', expires=excluded.expires, updated=excluded.updated",
            (wallet, new_expiry, now, now),
        )


def has_access(wallet):
    """True if this wallet currently has valid premium access."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT kind, expires FROM access WHERE wallet = ?", (wallet,)
        ).fetchone()
    if not row:
        return False
    kind, expires = row
    if kind == "one-time":
        return True
    if kind == "sub":
        return expires is not None and expires > time.time()
    return False


def access_status(wallet):
    """Detailed status for the frontend to show — includes expiry date."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT kind, expires, since FROM access WHERE wallet = ?", (wallet,)
        ).fetchone()
    if not row:
        return {"access": False, "kind": None, "expires": None}
    kind, expires, since = row
    valid = (kind == "one-time") or (kind == "sub" and expires and expires > time.time())
    return {"access": valid, "kind": kind, "expires": expires, "since": since}


def revoke(wallet):
    """Remove access (e.g. subscription cancelled)."""
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM access WHERE wallet = ?", (wallet,))


def all_active():
    """List every wallet with currently-valid access — useful for admin/debugging."""
    now = time.time()
    with _connect() as conn:
        rows = conn.execute("SELECT wallet, kind, expires, since FROM access").fetchall()
    out = []
    for wallet, kind, expires, since in rows:
        valid = (kind == "one-time") or (kind == "sub" and expires and expires > now)
        if valid:
            out.append({"wallet": wallet, "kind": kind, "expires": expires, "since": since})
    return out
