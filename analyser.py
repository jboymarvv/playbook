"""
analyser.py — Core async wallet analysis engine
=================================================
Fetches wallet trade history + candle data from Solana Tracker,
pairs trades, simulates the strategy, returns a full breakdown.

Async + parallel for speed. Caching built in to cut API costs.
"""

import asyncio
import aiohttp
import csv
import io
import time
from datetime import datetime, timezone
from collections import defaultdict

import config
import cache
import insights as insights_engine

BASE = "https://data.solanatracker.io"
SOL_MINTS = {
    "So11111111111111111111111111111111111111111",
    "So11111111111111111111111111111111111111112",
}

# Strategy TP levels (your confirmed setup)
TP_LEVELS = [(100, 0.50), (500, 0.25), (1000, 0.25)]
SIM_ENTRY = 0.1   # flat entry for fair comparison
CANDLE_HOURS = 12
CATCH_SECS = 45


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def sf(v, d=0.0):
    try:
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):  # NaN/Inf check, no math import needed
            return d
        return f
    except Exception:
        return d

def si(v, d=0):
    try: return int(v)
    except Exception: return d


# ─────────────────────────────────────────────────────────────
# ASYNC API LAYER (with concurrency limit for 60 RPS)
# ─────────────────────────────────────────────────────────────

class SolanaTrackerClient:
    def __init__(self, api_key, max_concurrent=20):
        self.api_key = api_key
        self.sem = asyncio.Semaphore(max_concurrent)
        self.headers = {"x-api-key": api_key, "Accept": "application/json"}

    async def _get(self, session, url, params=None, retries=3):
        async with self.sem:
            for attempt in range(retries):
                try:
                    async with session.get(url, headers=self.headers,
                                           params=params, timeout=20) as r:
                        if r.status == 200:
                            return await r.json()
                        if r.status == 429:
                            await asyncio.sleep(2 * (attempt + 1))
                            continue
                        if r.status in (400, 404):
                            return None
                        await asyncio.sleep(1)
                except (asyncio.TimeoutError, aiohttp.ClientError):
                    await asyncio.sleep(1)
            return None

    async def get_wallet_trades(self, session, wallet, max_pages=5):
        """Fetch swap history for a wallet."""
        all_trades = []
        cursor = None
        for _ in range(max_pages):
            params = {"cursor": cursor} if cursor else {}
            data = await self._get(session, f"{BASE}/wallet/{wallet}/trades", params)
            if not data:
                break
            trades = data.get("trades", []) if isinstance(data, dict) else data
            if not trades:
                break
            all_trades.extend(trades)
            cursor = data.get("nextCursor") if isinstance(data, dict) else None
            if not cursor:
                break
        return all_trades

    async def get_candles(self, session, token, from_ts, to_ts):
        """Fetch candles, using cache first."""
        cached = cache.get_candles(token, from_ts)
        if cached is not None:
            return cached
        params = {
            "from": int(from_ts) - 120,
            "to": int(to_ts) + (CANDLE_HOURS * 3600),
            "resolution": "1",
            "currency": "sol",
        }
        data = await self._get(session, f"{BASE}/chart/{token}", params)
        candles = self._parse_candles(data, from_ts)
        cache.set_candles(token, from_ts, candles)
        return candles

    async def get_token_info(self, session, token):
        cached = cache.get_token_info(token)
        if cached is not None:
            return cached
        data = await self._get(session, f"{BASE}/tokens/{token}")
        info = self._parse_token_info(token, data)
        cache.set_token_info(token, info)
        return info

    @staticmethod
    def _parse_candles(data, from_ts):
        if not data:
            return []
        raw = data if isinstance(data, list) else None
        if raw is None:
            for k in ("oclhv", "data", "candles", "ohlcv", "result"):
                if isinstance(data, dict) and k in data and isinstance(data[k], list):
                    raw = data[k]; break
        if raw is None:
            return []
        out = []
        for ch in raw:
            try:
                if isinstance(ch, dict):
                    t = sf(ch.get("t") or ch.get("time") or ch.get("timestamp"))
                    h = sf(ch.get("h") or ch.get("high"))
                    lo = sf(ch.get("l") or ch.get("low"))
                    cl = sf(ch.get("c") or ch.get("close"))
                elif isinstance(ch, (list, tuple)) and len(ch) >= 4:
                    t = sf(ch[0]); nums = [sf(x) for x in ch[1:5] if sf(x) > 0]
                    h = max(nums) if nums else 0; lo = min(nums) if nums else 0
                    cl = sf(ch[2])
                else:
                    continue
                if t > 0 and h > 0:
                    out.append((int(t), h, lo, cl))
            except Exception:
                continue
        return sorted(out, key=lambda x: x[0])

    @staticmethod
    def _parse_token_info(token, data):
        if not data:
            return {"symbol": "", "created_ts": 0, "market_cap": 0, "is_pump_fun": str(token).endswith("pump")}
        try:
            pools = data.get("pools") or []
            pool = pools[0] if pools else {}
            created = si(data.get("createdAt") or pool.get("createdAt") or 0)
            if created > 2_000_000_000_000:
                created //= 1000
            return {
                "symbol": data.get("symbol") or data.get("token", {}).get("symbol", ""),
                "created_ts": created,
                "market_cap": sf(pool.get("marketCap") or data.get("marketCap") or 0),
                "is_pump_fun": str(token).endswith("pump"),
            }
        except Exception:
            return {"symbol": "", "created_ts": 0, "market_cap": 0, "is_pump_fun": str(token).endswith("pump")}


# ─────────────────────────────────────────────────────────────
# TRADE PARSING
# ─────────────────────────────────────────────────────────────

def pair_trades(raw_trades):
    """Group raw swap trades into per-token positions."""
    tokens = defaultdict(lambda: {"buys": [], "sells": []})
    for t in raw_trades:
        try:
            # Solana Tracker trade schema (adapt field names as needed)
            from_mint = (t.get("from", {}).get("address") or t.get("tokenIn") or "").strip()
            to_mint = (t.get("to", {}).get("address") or t.get("tokenOut") or "").strip()
            ts = si(t.get("time") or t.get("blockTime") or t.get("timestamp"))
            if ts > 2_000_000_000_000:
                ts //= 1000
            from_amt = sf(t.get("from", {}).get("amount") or t.get("amountIn"))
            to_amt = sf(t.get("to", {}).get("amount") or t.get("amountOut"))

            if from_mint in SOL_MINTS and to_mint and to_mint not in SOL_MINTS:
                tokens[to_mint]["buys"].append({"ts": ts, "sol": from_amt})
            elif to_mint in SOL_MINTS and from_mint and from_mint not in SOL_MINTS:
                tokens[from_mint]["sells"].append({"ts": ts, "sol": to_amt})
        except Exception:
            continue
    return tokens


def parse_solscan_csv(csv_bytes):
    """
    Parse a Solscan 'Export DeFi Activity' CSV into the same per-token
    structure that pair_trades() produces.

    Returns: {token_address: {"buys": [{ts, sol}], "sells": [{ts, sol}]}}
    Raises: ValueError if the CSV is unrecognised.
    """
    tokens = defaultdict(lambda: {"buys": [], "sells": []})

    try:
        text = csv_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = csv_bytes.decode("latin-1", errors="ignore")

    reader = csv.DictReader(io.StringIO(text))
    required = {"Token1", "Token2", "Amount1", "Amount2"}
    if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
        raise ValueError(
            "This doesn't look like a Solscan DeFi Activity export. "
            "Make sure you exported from Account → Activities → DeFi Activities → Export."
        )

    row_count = 0
    for row in reader:
        try:
            t1 = (row.get("Token1") or "").strip()
            t2 = (row.get("Token2") or "").strip()

            # Block Time is a unix timestamp; fall back to Human Time if missing.
            raw_ts = (row.get("Block Time") or "").strip()
            if raw_ts.isdigit():
                ts = int(raw_ts)
            else:
                ht = (row.get("Human Time") or "").strip()
                if not ht:
                    continue
                ts = int(datetime.fromisoformat(ht.replace("Z", "+00:00")).timestamp())

            def real_amount(amt_str, decimals_str, fallback_value):
                try:
                    amt = int(amt_str or 0)
                    dec = int(decimals_str or 9)
                    return amt / (10 ** dec)
                except Exception:
                    return sf(fallback_value)

            amt1 = real_amount(row.get("Amount1"), row.get("TokenDecimals1"), row.get("Value"))
            amt2 = real_amount(row.get("Amount2"), row.get("TokenDecimals2"), row.get("Value"))

            # SOL -> Token = buy that token (price paid = amt1 SOL)
            if t1 in SOL_MINTS and t2 and t2 not in SOL_MINTS:
                tokens[t2]["buys"].append({"ts": ts, "sol": amt1})
            # Token -> SOL = sell that token (proceeds = amt2 SOL)
            elif t2 in SOL_MINTS and t1 and t1 not in SOL_MINTS:
                tokens[t1]["sells"].append({"ts": ts, "sol": amt2})

            row_count += 1
        except Exception:
            continue

    if row_count == 0:
        raise ValueError("The CSV had no readable rows. It may be empty or corrupted.")

    return tokens


def filter_by_days(tokens, days):
    """Keep only positions whose first buy is within `days` of now."""
    if not days:
        return tokens
    cutoff = time.time() - (days * 86400)
    out = {}
    for tk, pos in tokens.items():
        if pos["buys"] and min(b["ts"] for b in pos["buys"]) >= cutoff:
            out[tk] = pos
    return out


# ─────────────────────────────────────────────────────────────
# CANDLE ANALYSIS
# ─────────────────────────────────────────────────────────────

def analyse_candles(candles, entry_price, entry_ts, exit_ts):
    base = {
        "peak_pct": 0.0, "time_to_peak_secs": 0,
        "catchable_50": False, "catchable_100": False,
        "catchable_200": False, "candle_count": len(candles),
    }
    if not candles or not entry_price or entry_price <= 0:
        return base
    peak_price = entry_price
    peak_ts = entry_ts
    lvl_secs = {50: 0, 100: 0, 200: 0}
    window_end = entry_ts + CANDLE_HOURS * 3600
    for (ts, h, lo, cl) in candles:
        if ts < entry_ts - 120 or ts > window_end:
            continue
        if h > peak_price:
            peak_price = h; peak_ts = ts
        for lv in lvl_secs:
            if h >= entry_price * (1 + lv / 100):
                lvl_secs[lv] += 60
    peak_pct = (peak_price - entry_price) / entry_price * 100
    return {
        "peak_pct": round(peak_pct, 2),
        "time_to_peak_secs": max(peak_ts - entry_ts, 0),
        "catchable_50": lvl_secs[50] >= CATCH_SECS,
        "catchable_100": lvl_secs[100] >= CATCH_SECS,
        "catchable_200": lvl_secs[200] >= CATCH_SECS,
        "candle_count": len(candles),
    }


# ─────────────────────────────────────────────────────────────
# STRATEGY SIMULATION
# ─────────────────────────────────────────────────────────────

def simulate_strategy(positions):
    """Run the confirmed TP strategy at flat 0.1 SOL, using actual exits on losers."""
    total = 0.0
    wins = 0
    for p in positions:
        peak = p["peak_pct"]
        actual = p["actual_pct"]
        sol = SIM_ENTRY
        profit = 0.0
        rem = sol
        if peak >= 100:
            for tp, frac in TP_LEVELS:
                if peak >= tp:
                    sell = sol * frac
                    profit += sell * (tp / 100)
                    rem -= sell
                else:
                    profit += rem * (actual / 100)
                    rem = 0
                    break
            if rem > 0:
                profit += rem * (actual / 100)
        else:
            profit = sol * (actual / 100)
        total += profit
        if profit > 0:
            wins += 1
    return round(total, 4), wins


# ─────────────────────────────────────────────────────────────
# MAIN ANALYSIS ENTRY POINT
# ─────────────────────────────────────────────────────────────

async def _enrich_positions(client, session, tokens):
    """
    Shared pipeline: given a {token: {buys, sells}} dict, fetch candles
    + token info for each in parallel and return enriched position dicts.
    Used by both analyse_csv() and analyse_wallet().
    """
    async def process(tk, pos):
        buys  = sorted(pos["buys"],  key=lambda x: x["ts"])
        sells = sorted(pos["sells"], key=lambda x: x["ts"])
        entry_ts = buys[0]["ts"]
        exit_ts  = sells[-1]["ts"]
        sol_in   = sum(b["sol"] for b in buys)
        sol_out  = sum(s["sol"] for s in sells)
        net      = sol_out - sol_in
        actual_pct = (net / sol_in * 100) if sol_in else 0

        candles, info = await asyncio.gather(
            client.get_candles(session, tk, entry_ts, exit_ts),
            client.get_token_info(session, tk),
        )
        entry_price = None
        for (ts, h, lo, cl) in candles:
            if ts >= entry_ts - 180:
                entry_price = cl if cl > 0 else h
                break
        ca = analyse_candles(candles, entry_price, entry_ts, exit_ts)
        created = info.get("created_ts", 0)
        mins_since_launch = round((entry_ts - created) / 60, 1) if created and entry_ts > created > 0 else None

        return {
            "token": tk,
            "symbol": info.get("symbol", ""),
            "sol_in": round(sol_in, 6),
            "net_sol": round(net, 6),
            "actual_pct": round(actual_pct, 2),
            "n_buys": len(buys),
            "n_sells": len(sells),
            "first_buy_ts": entry_ts,
            "hold_seconds": max(exit_ts - entry_ts, 0),
            "market_cap": info.get("market_cap", 0),
            "mins_since_launch": mins_since_launch,
            "is_pump_fun": info.get("is_pump_fun", False),
            **ca,
        }

    return await asyncio.gather(*[process(tk, p) for tk, p in tokens.items()])


async def analyse_csv(csv_bytes, days=None, wallet_label="csv-upload"):
    """
    Entry point for the current launch mode: user uploads a Solscan CSV.
    days=None means analyse the whole CSV (user controls scope by what they export).
    days=7 will filter to the last 7 days.
    """
    try:
        tokens = parse_solscan_csv(csv_bytes)
    except ValueError as e:
        return {"error": str(e)}

    tokens = filter_by_days(tokens, days)
    tokens = {tk: p for tk, p in tokens.items() if p["buys"] and p["sells"]}
    if not tokens:
        return {"error": "No completed trades found in the CSV (need at least one buy and one sell per token)."}

    client = SolanaTrackerClient(config.API_KEY, max_concurrent=config.MAX_CONCURRENT)
    async with aiohttp.ClientSession() as session:
        positions = await _enrich_positions(client, session, tokens)

    return build_report(wallet_label, positions, days)


async def analyse_wallet(wallet, days=7):
    """
    Future entry point: fetch wallet history via API (Helius or Solana Tracker).
    Currently used only when DATA_SOURCE=helius is configured.
    """
    client = SolanaTrackerClient(config.API_KEY, max_concurrent=config.MAX_CONCURRENT)

    async with aiohttp.ClientSession() as session:
        raw_trades = await client.get_wallet_trades(session, wallet)
        if not raw_trades:
            return {"error": "No trades found for this wallet."}

        tokens = pair_trades(raw_trades)
        tokens = filter_by_days(tokens, days)
        tokens = {tk: p for tk, p in tokens.items() if p["buys"] and p["sells"]}

        if not tokens:
            return {"error": "No completed trades in the selected timeframe."}

        positions = await _enrich_positions(client, session, tokens)

    return build_report(wallet, positions, days)


# ─────────────────────────────────────────────────────────────
# REPORT BUILDER
# ─────────────────────────────────────────────────────────────

def build_report(wallet, positions, days):
    n = len(positions)
    winners = [p for p in positions if p["net_sol"] > 0]
    actual_net = round(sum(p["net_sol"] for p in positions), 4)
    sim_net, sim_wins = simulate_strategy(positions)

    # peak distribution
    dist = {
        "never": sum(1 for p in positions if p["peak_pct"] == 0),
        "0_100": sum(1 for p in positions if 0 < p["peak_pct"] < 100),
        "100_500": sum(1 for p in positions if 100 <= p["peak_pct"] < 500),
        "500_plus": sum(1 for p in positions if p["peak_pct"] >= 500),
    }

    # hit 100% but lost (exit problem)
    pumped_lost = sum(1 for p in positions if p["peak_pct"] >= 100 and p["net_sol"] <= 0)

    # best/worst hours
    hours = defaultdict(lambda: {"net": 0.0, "n": 0})
    for p in positions:
        h = datetime.fromtimestamp(p["first_buy_ts"], tz=timezone.utc).hour
        hours[h]["net"] += p["net_sol"]; hours[h]["n"] += 1
    hour_list = sorted(hours.items(), key=lambda x: x[1]["net"])
    best_hours = [{"hour": h, "net": round(v["net"], 3), "n": v["n"]} for h, v in hour_list[-3:][::-1]]
    worst_hours = [{"hour": h, "net": round(v["net"], 3), "n": v["n"]} for h, v in hour_list[:3]]

    summary = {
        "total_trades": n,
        "win_rate": round(len(winners) / n * 100, 1) if n else 0,
        "actual_net_sol": actual_net,
        "strategy_net_sol": sim_net,
        "strategy_win_rate": round(sim_wins / n * 100, 1) if n else 0,
        "swing": round(sim_net - actual_net, 2),
    }

    exit_problem = {
        "pumped_but_lost": pumped_lost,
        "note": "Trades that hit +100% but you still lost on — your biggest opportunity.",
    }

    timing = {
        "best_hours_utc": best_hours,
        "worst_hours_utc": worst_hours,
    }

    written_insights = insights_engine.generate_insights(positions, summary, dist, timing)
    rule_set = insights_engine.generate_rules(positions, summary, timing)

    return {
        "wallet": wallet,
        "timeframe": "7 days" if days else "all-time",
        "summary": summary,
        "peak_distribution": dist,
        "exit_problem": exit_problem,
        "timing": timing,
        "insights": written_insights,
        "rules": rule_set,
        "trades": sorted(positions, key=lambda x: x["net_sol"], reverse=True),
    }
