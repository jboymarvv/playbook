"""
xai_coach.py — Optional premium AI coaching narrative (xAI / Grok)
==================================================================
This is an OPTIONAL layer for paid all-time reports. It does NOT replace
the rule-based engine. The deterministic rules and numbers from analyser.py
and insights.py remain the source of truth and are shown exactly as computed.

xAI's job is narrow and supervised:
  - It receives a CONDENSED, already-computed summary of the wallet
    (key stats + the rules we already generated) — never raw trades or
    candle data. This keeps token cost ~10-14k tokens (≈5-10¢) and, more
    importantly, means the model can only reason over facts we vouch for.
  - It returns a structured coaching narrative: a short read on the trader,
    their key mistakes, what to change, and what they already do well.
  - We instruct it to ground every claim in the supplied numbers and to
    invent nothing. If it returns malformed output, we fail safe (the
    report still renders fully from the rule-based engine).

Output is cached permanently per wallet (see cache.set_llm_summary), so a
given wallet costs at most one xAI call, ever.
"""

import json
import aiohttp
import config


SYSTEM_PROMPT = """You are a disciplined Solana memecoin trading coach reviewing one trader's complete history.

You will be given a JSON object of ALREADY-COMPUTED statistics and rules derived from this trader's real on-chain data (every number was calculated from real candle data — you do not need to recompute anything).

Your job: write a sharp, personal coaching read based ONLY on the numbers provided.

HARD RULES:
- Use ONLY the figures in the supplied data. Never invent a number, token, percentage, or pattern that is not present.
- If the data does not support a claim, do not make it.
- Be specific and reference their actual figures (e.g. their real win rate, their real median size).
- Be direct and honest, like a coach who studied their trades — not a hype man, not generic advice.
- No financial guarantees, no price predictions, no "you will make X".
- Keep it tight. No filler.

Return ONLY valid JSON (no markdown, no backticks) in exactly this shape:
{
  "headline": "one punchy sentence summarising this trader",
  "read": "2-3 sentences on their overall trading style and the single biggest thing costing them, grounded in their numbers",
  "mistakes": ["specific mistake tied to a real figure", "another", "another"],
  "improvements": ["concrete, actionable change", "another", "another"],
  "strengths": ["something they genuinely do well, per the data", "another"]
}"""


def build_summary_payload(report):
    """
    Condense the full report into the minimal, structured facts xAI needs.
    This is the cost AND safety control: we send computed stats + our rules,
    never raw trade lists or candle arrays.
    """
    s = report.get("summary", {})
    ds = report.get("deep_stats", {}) or {}
    misses = report.get("biggest_misses", []) or []
    rules = report.get("rules", []) or []
    timing = report.get("timing", {}) or {}

    # Flatten our generated rules to plain text so the model sees what we
    # already told the user (and can deepen rather than contradict).
    rule_lines = []
    for g in rules:
        for r in g.get("rules", []):
            rule_lines.append(f"[{g.get('section','')}] {r.get('rule','')} — {r.get('reason','')}")

    top_misses = [
        {
            "token": m.get("symbol"),
            "left_sol": m.get("left_sol"),
            "peaked_pct": m.get("peak_pct"),
            "you_exited_pct": m.get("actual_pct"),
        }
        for m in misses[:6]
    ]

    return {
        "totals": {
            "trades": s.get("total_trades"),
            "win_rate_pct": s.get("win_rate"),
            "actual_net_sol": s.get("actual_net_sol"),
            "strategy_net_sol": s.get("strategy_net_sol"),
            "swing_sol": s.get("swing"),
        },
        "sizing_and_timing": {
            "median_size_sol": ds.get("median_size_sol"),
            "largest_size_sol": ds.get("largest_size_sol"),
            "median_hold_mins": ds.get("median_hold_mins"),
            "hold_on_wins_mins": ds.get("median_win_hold_mins"),
            "hold_on_losses_mins": ds.get("median_loss_hold_mins"),
            "avg_buys_per_token": ds.get("avg_buys_per_token"),
            "tokens_averaged_down": ds.get("tokens_averaged_down"),
        },
        "token_behaviour": {
            "median_peak_pct": ds.get("median_peak_pct"),
            "best_peak_pct": ds.get("best_peak_pct"),
            "pumped_100": ds.get("tokens_pumped_100"),
            "pumped_500": ds.get("tokens_pumped_500"),
            "pumped_1000": ds.get("tokens_pumped_1000"),
            "never_moved": ds.get("tokens_never_moved"),
        },
        "total_left_on_table_sol": ds.get("total_left_on_table_sol"),
        "biggest_misses": top_misses,
        "best_hours_utc": timing.get("best_hours_utc"),
        "worst_hours_utc": timing.get("worst_hours_utc"),
        "rules_we_already_generated": rule_lines,
    }


def _validate(obj):
    """Ensure the model returned the expected shape; fail safe if not."""
    if not isinstance(obj, dict):
        return None
    required = ["headline", "read", "mistakes", "improvements", "strengths"]
    if not all(k in obj for k in required):
        return None
    # coerce list fields, clamp lengths to keep UI tidy
    for k in ("mistakes", "improvements", "strengths"):
        if not isinstance(obj[k], list):
            return None
        obj[k] = [str(x).strip() for x in obj[k] if str(x).strip()][:5]
    obj["headline"] = str(obj["headline"]).strip()[:200]
    obj["read"] = str(obj["read"]).strip()[:600]
    return obj


async def generate_coaching(report):
    """
    Call xAI with the condensed summary. Returns a validated dict or None.
    Never raises into the request path — on any failure we return None and
    the report renders fully from the rule-based engine.
    """
    if not config.XAI_ENABLED or not config.XAI_API_KEY:
        return None

    payload = build_summary_payload(report)
    body = {
        "model": config.XAI_MODEL,
        "max_tokens": config.XAI_MAX_TOKENS,
        "temperature": 0.4,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, separators=(",", ":"))},
        ],
    }
    headers = {
        "Authorization": f"Bearer {config.XAI_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        timeout = aiohttp.ClientTimeout(total=config.XAI_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{config.XAI_BASE_URL}/chat/completions",
                json=body, headers=headers,
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
        content = data["choices"][0]["message"]["content"].strip()
        # strip accidental code fences
        if content.startswith("```"):
            content = content.split("```", 2)[1] if "```" in content[3:] else content.strip("`")
            content = content.lstrip("json").strip()
        parsed = json.loads(content)
        return _validate(parsed)
    except Exception:
        return None
