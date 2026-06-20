"""
insights.py — Templated written insights from the data
======================================================
Pure rule-based: data in → written findings out. No AI, no tokens.
Each insight has a type (good/bad/neutral), title, and body.
"""

from collections import defaultdict
from datetime import datetime, timezone


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
    Derive a personalised, structured trading rule set from the user's data.
    Returns a list of rule groups: {section, icon, rules: [{rule, reason}]}

    Pure rule-based. No AI. Every rule is justified by something in the data,
    so the user sees WHY each rule applies to them specifically.
    """
    n = report_summary["total_trades"]
    if n == 0:
        return []

    pumped      = [p for p in positions if p["peak_pct"] >= 100]
    big_movers  = [p for p in positions if p["peak_pct"] >= 500]
    huge        = [p for p in positions if p["peak_pct"] >= 1000]
    multi_buy   = [p for p in positions if p.get("n_buys", 1) > 1]
    single_sell = [p for p in positions if p.get("n_sells", 1) <= 1]

    # Conditional probabilities from THIS user's data
    hit50  = [p for p in positions if p["peak_pct"] >= 50]
    hit100 = [p for p in positions if p["peak_pct"] >= 100]
    hit200 = [p for p in positions if p["peak_pct"] >= 200]
    hit500 = [p for p in positions if p["peak_pct"] >= 500]

    p50_to_100  = round(len(hit100) / len(hit50)  * 100) if hit50  else 0
    p100_to_500 = round(len(hit500) / len(hit100) * 100) if hit100 else 0
    p500_to_1k  = round(len(huge)   / len(hit500) * 100) if hit500 else 0

    groups = []

    # ── ENTRY ─────────────────────────────────────────────────
    entry_rules = []

    # Market cap signal — only if we have mcap data
    mcaps = [p.get("market_cap", 0) for p in pumped if p.get("market_cap", 0) > 0]
    if mcaps:
        med_mcap = sorted(mcaps)[len(mcaps)//2]
        if med_mcap > 0:
            entry_rules.append({
                "rule": f"Enter under ~${int(med_mcap/1000)}k market cap",
                "reason": "Your winners clustered here. Bigger caps moved less.",
            })

    # Launch timing
    launch_times = [p.get("mins_since_launch") for p in pumped if p.get("mins_since_launch") is not None]
    launch_times = [t for t in launch_times if t and t > 0]
    if launch_times:
        med_launch = sorted(launch_times)[len(launch_times)//2]
        entry_rules.append({
            "rule": f"Buy within ~{int(med_launch)} minutes of launch",
            "reason": "Your runners were caught early.",
        })
    else:
        entry_rules.append({
            "rule": "Buy early — within 15 minutes of launch",
            "reason": "Early entries outperform.",
        })

    entry_rules.append({
        "rule": "Size every trade the same — 0.1 to 0.2 SOL",
        "reason": "Stops one bad trade wiping a good day.",
    })

    if len(multi_buy) >= 3:
        entry_rules.append({
            "rule": "One buy per token. Never average down.",
            "reason": f"You did it on {len(multi_buy)} trades. It rarely recovered.",
        })
    else:
        entry_rules.append({
            "rule": "One buy per token. No adding to losers.",
            "reason": "Adding to a dump almost never recovers.",
        })

    groups.append({"section": "How to enter", "icon": "→", "rules": entry_rules})

    # ── EXITS ─────────────────────────────────────────────────
    exit_rules = [
        {
            "rule": "TP1 — sell 50% at +100%",
            "reason": f"{p50_to_100}% of your +50% tokens reached +100%. Bank half, ride the rest." if p50_to_100 else "Bank half at 2x, ride the rest.",
        },
        {
            "rule": "TP2 — sell 25% at +500%",
            "reason": f"{p100_to_500}% of your +100% tokens reached +500%. The real money." if p100_to_500 else "Ride a quarter to +500%.",
        },
        {
            "rule": "TP3 — sell last 25% at +1000% (moonbag)",
            "reason": f"{p500_to_1k}% of your +500% tokens hit +1000%. Free moonbag upside." if p500_to_1k else "Free moonbag on the rare 10x+.",
        },
        {
            "rule": "Cut dead trades manually at ~−10 to −15%",
            "reason": "Your manual cutting beats a wide auto-stop. Keep it.",
        },
        {
            "rule": "If nothing moves in 15 minutes, get out",
            "reason": "Stalled tokens stay dead. Move on.",
        },
    ]
    groups.append({"section": "How to exit", "icon": "↑", "rules": exit_rules})

    # ── DISCIPLINE / SESSION ──────────────────────────────────
    session_rules = []

    # Best/worst hours from their data
    if timing.get("best_hours_utc"):
        best = timing["best_hours_utc"][0]
        if best["n"] >= 2 and best["net"] > 0:
            session_rules.append({
                "rule": f"Trade your {str(best['hour']).zfill(2)}:00 UTC window",
                "reason": f"+{best['net']} SOL across {best['n']} trades.",
            })
    if timing.get("worst_hours_utc"):
        worst = timing["worst_hours_utc"][0]
        if worst["n"] >= 3 and worst["net"] < 0:
            session_rules.append({
                "rule": f"Avoid {str(worst['hour']).zfill(2)}:00 UTC",
                "reason": f"{worst['net']} SOL across {worst['n']} trades.",
            })

    session_rules.append({
        "rule": "Stop for the day at −1 SOL",
        "reason": "Stops a tilt spiral.",
    })
    session_rules.append({
        "rule": "Max 10 trades per day",
        "reason": "Your high-volume days lost money.",
    })
    session_rules.append({
        "rule": "Never size up after a loss",
        "reason": "Revenge sizing blows up weeks.",
    })

    groups.append({"section": "Discipline & timing", "icon": "★", "rules": session_rules})

    return groups
