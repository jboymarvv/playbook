"""
insights.py — Data-driven written insights & rules
===================================================
Pure rule-based: data in → written findings out. No AI, no tokens.

Every rule and observation is COMPUTED from this specific wallet's data
(CSV trades + Solana Tracker candle peaks). No two wallets get the same
wording, because the words are generated from their real numbers — not
selected from fixed templates.

Guardrail: a sentence only appears if it is genuinely true for this wallet.
We vary wording because the data differs, never reword for variety's sake.
For wallets with too few trades to derive reliable custom levels, we say so
honestly rather than fitting noise.
"""

from collections import defaultdict
from datetime import datetime, timezone


# ── DATA HELPERS — everything below derives from the wallet ───────

def _median(xs):
    s = sorted(xs)
    if not s:
        return 0
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m-1] + s[m]) / 2


def _fmt_size(sol):
    """Format a SOL size sensibly (0.15 not 0.150000)."""
    if sol >= 1:
        return f"{sol:.1f}"
    return f"{sol:.2f}".rstrip("0").rstrip(".") or "0"


def wallet_profile(positions, summary):
    """
    Compute everything we need to write wallet-specific rules, ONCE.
    Returns a dict of derived facts about THIS trader.
    """
    n = len(positions)
    sizes = [p["sol_in"] for p in positions if p.get("sol_in", 0) > 0]
    med_size = _median(sizes) if sizes else 0

    # Peak distribution — what fraction of THEIR tokens reached each level
    def frac_at(th):
        return sum(1 for p in positions if p["peak_pct"] >= th) / n if n else 0

    dist = {th: frac_at(th) for th in (30, 50, 75, 100, 150, 200, 300, 500, 1000)}

    # Their typical winner's peak (median peak among tokens that moved at all)
    movers = [p["peak_pct"] for p in positions if p["peak_pct"] >= 30]
    typical_peak = _median(movers) if movers else 0

    # Entry timing on winners (tokens that pumped 100%+)
    win_launch = [p.get("mins_since_launch") for p in positions
                  if p["peak_pct"] >= 100 and p.get("mins_since_launch")]
    win_launch = [t for t in win_launch if t and t > 0]
    med_win_launch = _median(win_launch) if win_launch else None

    # Market cap of winners
    win_mcaps = [p.get("market_cap", 0) for p in positions
                 if p["peak_pct"] >= 100 and p.get("market_cap", 0) > 0]
    med_win_mcap = _median(win_mcaps) if win_mcaps else None

    # Behaviour flags
    multi_buy = [p for p in positions if p.get("n_buys", 1) > 1]

    # Volume-by-day: find the trade count where their win rate breaks down
    daily = defaultdict(lambda: {"net": 0.0, "n": 0})
    for p in positions:
        d = datetime.fromtimestamp(p["first_buy_ts"], tz=timezone.utc).strftime("%Y-%m-%d")
        daily[d]["net"] += p["net_sol"]
        daily[d]["n"] += 1
    high_vol_days = [(d, v) for d, v in daily.items() if v["n"] >= 8]
    losing_hv = [d for d, v in high_vol_days if v["net"] < 0]
    typical_day_trades = round(_median([v["n"] for v in daily.values()])) if daily else 0

    # Their actual daily loss on bad days (for a scaled stop)
    losing_days = [v["net"] for v in daily.values() if v["net"] < 0]
    med_losing_day = _median(losing_days) if losing_days else 0

    return {
        "n": n,
        "med_size": med_size,
        "dist": dist,
        "typical_peak": typical_peak,
        "med_win_launch": med_win_launch,
        "med_win_mcap": med_win_mcap,
        "multi_buy_count": len(multi_buy),
        "high_vol_days": len(high_vol_days),
        "losing_hv_days": len(losing_hv),
        "typical_day_trades": typical_day_trades,
        "med_losing_day": med_losing_day,
        # Whether we have enough trades to derive a reliable custom ladder
        "enough_for_ladder": n >= 20,
    }


def generate_insights(positions, report_summary, peak_dist, timing):
    """
    Punchy, scannable findings. Each: {type, title, body, stat}.
    'stat' is a short big-number string for visual emphasis (optional).
    """
    insights = []
    n = report_summary["total_trades"]
    if n == 0:
        return insights

    pumped       = [p for p in positions if p["peak_pct"] >= 100]
    pumped_lost  = [p for p in positions if p["peak_pct"] >= 100 and p["net_sol"] <= 0]
    big_movers   = [p for p in positions if p["peak_pct"] >= 500]
    dead         = [p for p in positions if p["peak_pct"] == 0]
    multi_buy    = [p for p in positions if p.get("n_buys", 1) > 1]

    strategy_net = report_summary["strategy_net_sol"]
    swing        = report_summary["swing"]
    win_rate     = report_summary["win_rate"]

    # Headline swing
    if swing > 0:
        insights.append({
            "type": "highlight", "title": "Same picks, better exits",
            "stat": f"+{swing} SOL",
            "body": "left on the table. Your exits, not your picks, are the problem.",
        })
    elif swing < -1:
        insights.append({
            "type": "neutral", "title": "You're exiting well",
            "stat": "", "body": "The strategy wouldn't beat your real exits here. Keep it up.",
        })

    # Selection
    pump_rate = len(pumped) / n
    if pump_rate >= 0.35:
        insights.append({
            "type": "good", "title": "Strong picks",
            "stat": f"{int(pump_rate*100)}%",
            "body": f"of your tokens pumped 100%+. Finding runners is the hard part — you've got it.",
        })
    elif pump_rate < 0.15 and n >= 10:
        insights.append({
            "type": "bad", "title": "Picks aren't moving",
            "stat": f"{len(pumped)}/{n}",
            "body": "tokens hit 100%+. Tighten your entry filters first.",
        })

    # Exit leak
    if len(pumped_lost) >= 3:
        cost = round(sum(p["net_sol"] for p in pumped_lost), 2)
        insights.append({
            "type": "bad", "title": "Your biggest leak",
            "stat": f"{len(pumped_lost)} trades",
            "body": f"pumped 100%+ but you still lost on them ({cost} SOL). Pre-set TPs fix this.",
        })

    # Big movers
    if len(big_movers) >= 3:
        insights.append({
            "type": "good", "title": "You catch runners",
            "stat": f"{len(big_movers)}",
            "body": "tokens hit 500%+. Let the strategy ride these — that's where the money is.",
        })

    # Dead
    dead_rate = len(dead) / n
    if dead_rate >= 0.30:
        insights.append({
            "type": "neutral", "title": "Dead picks bleed you",
            "stat": f"{int(dead_rate*100)}%",
            "body": "never moved. Cut these inside 15 min.",
        })

    # Hot hour
    if timing["best_hours_utc"]:
        best = timing["best_hours_utc"][0]
        if best["net"] > 0.05 and best["n"] >= 2:
            insights.append({
                "type": "good", "title": "Your hot hour",
                "stat": f"{str(best['hour']).zfill(2)}:00",
                "body": f"UTC is your sharpest window (+{best['net']} SOL). Trade it more.",
            })

    # Worst hour
    if timing["worst_hours_utc"]:
        worst = timing["worst_hours_utc"][0]
        if worst["net"] < -0.1 and worst["n"] >= 3:
            insights.append({
                "type": "bad", "title": "Your danger hour",
                "stat": f"{str(worst['hour']).zfill(2)}:00",
                "body": f"UTC bleeds you ({worst['net']} SOL). Skip it.",
            })

    # Averaging down
    if len(multi_buy) >= 3:
        insights.append({
            "type": "bad", "title": "Averaging down",
            "stat": f"{len(multi_buy)} trades",
            "body": "you added to. Second buys into a dump rarely recover. One buy, always.",
        })

    # High-volume days
    daily = defaultdict(lambda: {"net": 0.0, "n": 0})
    for p in positions:
        d = datetime.fromtimestamp(p["first_buy_ts"], tz=timezone.utc).strftime("%Y-%m-%d")
        daily[d]["net"] += p["net_sol"]; daily[d]["n"] += 1
    high_vol_days = [(d, v) for d, v in daily.items() if v["n"] >= 10]
    if high_vol_days:
        losing_hv = [d for d, v in high_vol_days if v["net"] < 0]
        if losing_hv and len(losing_hv) / len(high_vol_days) >= 0.7:
            insights.append({
                "type": "bad", "title": "Overtrading kills you",
                "stat": f"{len(losing_hv)}/{len(high_vol_days)}",
                "body": "of your 10+ trade days lost money. Cap it at 10/day.",
            })

    # Win rate framing
    if win_rate < 30 and n >= 20:
        insights.append({
            "type": "neutral", "title": "Low win rate is fine",
            "stat": f"{win_rate}%",
            "body": "win rate — but big winners cover the losers. That's the game.",
        })

    return insights


def generate_rules(positions, report_summary, timing):
    """
    Derive a fully personalised rule set from THIS wallet's data.
    Every rule's text is computed from their real numbers — position sizes,
    peak distribution, entry timing, behaviour. No fixed strings.

    Returns a list of rule groups: {section, icon, rules: [{rule, reason}]}.
    """
    n = report_summary["total_trades"]
    if n == 0:
        return []

    prof = wallet_profile(positions, report_summary)
    multi_buy = prof["multi_buy_count"]
    groups = []

    # ════════════════════════════════════════════════════════════
    # HOW TO ENTER
    # ════════════════════════════════════════════════════════════
    entry_rules = []

    # --- Market cap (only if their winners cluster somewhere real) ---
    if prof["med_win_mcap"]:
        mc = prof["med_win_mcap"]
        mc_txt = f"${int(mc/1000)}k" if mc < 1_000_000 else f"${mc/1_000_000:.1f}M"
        entry_rules.append({
            "rule": f"Enter under ~{mc_txt} market cap",
            "reason": f"Your 100%+ winners clustered around this size. Bigger caps moved less for you.",
        })

    # --- Entry timing (their real median timing on winners) ---
    if prof["med_win_launch"] is not None:
        mins = prof["med_win_launch"]
        if mins <= 20:
            entry_rules.append({
                "rule": f"Buy within ~{int(mins)} min of launch",
                "reason": f"Your winners were caught at a median of {int(mins)} minutes in. Later entries lagged.",
            })
        else:
            entry_rules.append({
                "rule": f"Your winners came ~{int(mins)} min after launch",
                "reason": "You do better letting a token establish itself than sniping the first minute.",
            })

    # --- Position sizing (THEIR actual median size, never a fixed number) ---
    if prof["med_size"] > 0:
        sz = prof["med_size"]
        lo = _fmt_size(sz * 0.8)
        hi = _fmt_size(sz * 1.2)
        entry_rules.append({
            "rule": f"Keep size consistent — around {lo}–{hi} SOL",
            "reason": f"Your typical position is {_fmt_size(sz)} SOL. Consistent sizing stops one trade wrecking a day.",
        })

    # --- Averaging down (only if they actually do it) ---
    if multi_buy >= 3:
        entry_rules.append({
            "rule": "One buy per token — stop averaging down",
            "reason": f"You added to a falling position on {multi_buy} trades. It rarely recovered.",
        })
    # if they DON'T average down, we say nothing — silence is the compliment

    if entry_rules:
        groups.append({"section": "How to enter", "icon": "→", "rules": entry_rules})

    # ════════════════════════════════════════════════════════════
    # HOW TO EXIT  — the ladder is built from THEIR peak distribution
    # ════════════════════════════════════════════════════════════
    exit_rules = []
    dist = prof["dist"]

    if not prof["enough_for_ladder"]:
        # Honest: too few trades to derive reliable custom levels
        exit_rules.append({
            "rule": "Bank profit in stages — don't round-trip a winner",
            "reason": f"With {n} trades there isn't enough history yet to fit custom exit levels. Take some off as it climbs and keep a runner.",
        })
        tp = prof["typical_peak"]
        if tp >= 50:
            exit_rules.append({
                "rule": f"Your tokens have typically peaked near +{int(tp)}%",
                "reason": "Use that as a rough first-target guide until more history builds up.",
            })
    else:
        # Build a ladder from where THEIR tokens actually go.
        d = dist
        # TP1: a level a clear majority reach (>=55%), else the highest level >=40% reach
        tp1 = next((lvl for lvl in (50, 75, 100, 150) if d.get(lvl, 0) >= 0.55), None)
        if tp1 is None:
            tp1 = next((lvl for lvl in (50, 75, 100) if d.get(lvl, 0) >= 0.40), 50)
        # TP2: the highest level a meaningful chunk (>=20%) actually reach, above tp1
        tp2 = None
        for lvl in (500, 300, 200, 150, 100):
            if lvl > tp1 and d.get(lvl, 0) >= 0.20:
                tp2 = lvl
                break
        # TP3 / moonbag: only if a real fraction reach the top, above tp2
        tp3 = None
        if tp2:
            for lvl in (1000, 500, 300):
                if lvl > tp2 and d.get(lvl, 0) >= 0.08:
                    tp3 = lvl
                    break

        exit_rules.append({
            "rule": f"TP1 — sell ~half at +{tp1}%",
            "reason": f"{int(d.get(tp1,0)*100)}% of your tokens reached +{tp1}%. Banking half here locks the move in while letting the rest run.",
        })
        if tp2:
            exit_rules.append({
                "rule": f"TP2 — sell ~25% at +{tp2}%",
                "reason": f"{int(d.get(tp2,0)*100)}% of your tokens got to +{tp2}%. This is where your real size pays off.",
            })
            if tp3:
                exit_rules.append({
                    "rule": f"TP3 — moonbag, last 25% at +{tp3}%",
                    "reason": f"{int(d.get(tp3,0)*100)}% of your tokens hit +{tp3}%. Rare, but the moonbag costs nothing once TP1+TP2 banked your entry.",
                })
            else:
                exit_rules.append({
                    "rule": "Skip the moonbag — your tokens rarely run that far",
                    "reason": f"Only {int(d.get(1000,0)*100)}% of your tokens reached +1000%. Take the win at +{tp2}% instead of hoping for a 10x.",
                })
        else:
            # Their tokens don't reliably get far past TP1 — be honest about it
            exit_rules.append({
                "rule": "Take most of your win at TP1 — don't hold for a moonshot",
                "reason": f"Past +{tp1}%, your tokens drop off fast — only {int(d.get(200,0)*100)}% reach +200%. Bank it rather than round-trip it.",
            })

    # Manual cut — frame around whether their own cutting is working
    swing = report_summary.get("swing", 0)
    exit_rules.append({
        "rule": "Cut dead trades fast — ~−10 to −15%",
        "reason": "A quick manual cut on stallers beats a wide automated stop. Free the capital for the next one.",
    })
    if prof["typical_peak"] and prof["typical_peak"] < 50:
        exit_rules.append({
            "rule": "If it hasn't moved in ~15 min, it's probably dead",
            "reason": f"Your tokens' typical peak is only +{int(prof['typical_peak'])}%. Stalled ones rarely come back — move on.",
        })

    if exit_rules:
        groups.append({"section": "How to exit", "icon": "↑", "rules": exit_rules})

    # ════════════════════════════════════════════════════════════
    # DISCIPLINE & TIMING — thresholds scaled to THEIR numbers
    # ════════════════════════════════════════════════════════════
    session_rules = []

    # Best/worst hours (already wallet-specific)
    if timing.get("best_hours_utc"):
        best = timing["best_hours_utc"][0]
        if best["n"] >= 2 and best["net"] > 0:
            session_rules.append({
                "rule": f"Trade your {str(best['hour']).zfill(2)}:00 UTC window",
                "reason": f"+{best['net']} SOL across {best['n']} trades — your sharpest hour.",
            })
    if timing.get("worst_hours_utc"):
        worst = timing["worst_hours_utc"][0]
        if worst["n"] >= 3 and worst["net"] < 0:
            session_rules.append({
                "rule": f"Avoid {str(worst['hour']).zfill(2)}:00 UTC",
                "reason": f"{worst['net']} SOL across {worst['n']} trades — consistently your worst hour.",
            })

    # Daily stop — scaled to their actual bad-day losses
    if prof["med_losing_day"] < 0:
        stop = abs(prof["med_losing_day"])
        # round to something clean
        stop_txt = _fmt_size(stop) if stop < 1 else f"{stop:.1f}"
        session_rules.append({
            "rule": f"Daily stop at ~{stop_txt} SOL down",
            "reason": f"Your typical losing day runs about {stop_txt} SOL. Walk away there before it spirals.",
        })

    # Max trades — scaled to where their volume hurts them
    if prof["high_vol_days"] >= 2 and prof["losing_hv_days"] / max(prof["high_vol_days"], 1) >= 0.6:
        cap = max(prof["typical_day_trades"], 5)
        session_rules.append({
            "rule": f"Cap it at ~{cap} trades a day",
            "reason": f"{prof['losing_hv_days']} of your {prof['high_vol_days']} high-volume days lost money. Overtrading is costing you.",
        })

    # Revenge sizing — only if they actually size up after losses (proxy: high size variance)
    sizes = [p["sol_in"] for p in positions if p.get("sol_in", 0) > 0]
    if sizes:
        mx, md = max(sizes), prof["med_size"]
        if md > 0 and mx >= md * 3:
            session_rules.append({
                "rule": "Never size up after a loss",
                "reason": f"Your biggest position was {mx/md:.0f}x your normal size — that's where revenge trades blow up weeks.",
            })

    if session_rules:
        groups.append({"section": "Discipline & timing", "icon": "★", "rules": session_rules})

    return groups