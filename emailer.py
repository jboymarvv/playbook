"""
emailer.py — Optional email-results feature (premium)
=====================================================
Sends a paid user their report summary by email. Uses Resend (resend.com),
which has a generous free tier and a dead-simple HTTP API (no SDK needed).

Privacy stance (reflected in the UI copy):
  - email is used ONCE to send the report, then not retained
  - never shared or sold to anyone

We deliberately do NOT store the email anywhere. It's accepted in the
request, used to send, and dropped. If you later want a mailing list,
that must be a separate, clearly-consented opt-in — not this.
"""

import json
import urllib.request
import urllib.error

import config


def _esc(s):
    """Escape user/data text for safe HTML embedding."""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def send_report_email(to_email, report, wallet_short):
    """
    Send the COMPLETE report by email (verdict, stats, rules, biggest misses,
    deep stats). `report` is the full report dict. Returns (ok, message).
    """
    if not config.EMAIL_ENABLED:
        return False, "email disabled"
    if not config.RESEND_API_KEY:
        return False, "no email key configured"
    if not to_email or "@" not in to_email:
        return False, "invalid email"

    summary = report.get("summary", {}) if isinstance(report, dict) else {}
    swing = summary.get("swing", 0)
    actual = summary.get("actual_net_sol", 0)
    strat = summary.get("strategy_net_sol", 0)
    trades = summary.get("total_trades", 0)
    win_rate = summary.get("win_rate", 0)

    subject = f"Your Playbook report — {swing:+.2f} SOL swing"

    # ---- build the rules section ----
    rules_html = ""
    for group in (report.get("rules") or []):
        section = _esc(group.get("section", ""))
        items = ""
        for r in group.get("rules", []):
            items += (
                f'<tr><td style="padding:8px 0;border-bottom:1px solid #eee;">'
                f'<strong style="color:#1a1a1a;">{_esc(r.get("rule",""))}</strong><br>'
                f'<span style="color:#666;font-size:13px;">{_esc(r.get("reason",""))}</span>'
                f'</td></tr>'
            )
        if items:
            rules_html += (
                f'<h3 style="margin:22px 0 6px;font-size:16px;">{section}</h3>'
                f'<table style="width:100%;border-collapse:collapse;">{items}</table>'
            )

    # ---- biggest misses ----
    misses = report.get("biggest_misses") or []
    misses_html = ""
    if misses:
        rows = ""
        for m in misses[:8]:
            rows += (
                f'<tr>'
                f'<td style="padding:7px 0;border-bottom:1px solid #eee;"><strong>{_esc(m.get("symbol",""))}</strong></td>'
                f'<td style="padding:7px 0;border-bottom:1px solid #eee;color:#666;font-size:13px;">'
                f'peaked +{m.get("peak_pct",0)}% · you exited {m.get("actual_pct",0):+}%</td>'
                f'<td style="padding:7px 0;border-bottom:1px solid #eee;text-align:right;color:#C2410C;font-weight:600;">'
                f'−{m.get("left_sol",0)} SOL</td>'
                f'</tr>'
            )
        misses_html = (
            '<h3 style="margin:26px 0 6px;font-size:16px;">Where you left money</h3>'
            f'<table style="width:100%;border-collapse:collapse;">{rows}</table>'
        )

    # ---- deep stats (paid) ----
    ds = report.get("deep_stats") or {}
    deep_html = ""
    if ds:
        def stat(label, val):
            return (f'<tr><td style="padding:5px 0;color:#444;">{label}</td>'
                    f'<td style="padding:5px 0;text-align:right;font-weight:600;">{val}</td></tr>')
        deep_html = (
            '<h3 style="margin:26px 0 6px;font-size:16px;">The deep dive</h3>'
            '<table style="width:100%;border-collapse:collapse;font-size:14px;">'
            + stat("Typical position", f'{ds.get("median_size_sol","—")} SOL')
            + stat("Hold on wins", f'{ds.get("median_win_hold_mins","—")} min')
            + stat("Hold on losses", f'{ds.get("median_loss_hold_mins","—")} min')
            + stat("Best token peak", f'+{ds.get("best_peak_pct","—")}%')
            + stat("Tokens that ran 2x+", ds.get("tokens_pumped_100", "—"))
            + stat("Tokens that ran 10x+", ds.get("tokens_pumped_1000", "—"))
            + '</table>'
        )

    html = f"""
    <div style="font-family:-apple-system,Arial,sans-serif;max-width:600px;margin:0 auto;color:#1a1a1a;padding:8px;">
      <h2 style="margin-bottom:2px;">Your Playbook report</h2>
      <p style="color:#666;margin-top:0;font-size:14px;">Wallet {wallet_short} · {trades} trades · {win_rate}% win rate</p>

      <div style="background:#eaf7ee;border:1px solid #2D8F47;border-radius:10px;padding:18px;margin:16px 0;">
        <div style="font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:#2D8F47;">With disciplined exits, your trades were worth</div>
        <div style="font-size:36px;font-weight:700;color:#2D8F47;">{strat:+.2f} SOL</div>
        <div style="color:#555;font-size:14px;margin-top:4px;">You actually made {actual:+.2f} SOL across {trades} trades.</div>
      </div>

      {rules_html}
      {misses_html}
      {deep_html}

      <p style="margin-top:26px;font-size:14px;">This is your complete report, yours to keep. Re-run it any time at
        <a href="{config.SITE_URL}" style="color:#3B6AB8;">playbook.ie</a>.</p>
      <hr style="border:none;border-top:1px solid #eee;margin:24px 0;">
      <p style="font-size:12px;color:#999;">We used your email only to send this report once. We don't store it, share it, or sell it.</p>
    </div>
    """

    payload = json.dumps({
        "from": config.EMAIL_FROM,
        "to": [to_email],
        "subject": subject,
        "html": html,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {config.RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status in (200, 201):
                return True, "sent"
            return False, f"status {resp.status}"
    except urllib.error.HTTPError as e:
        return False, f"http error {e.code}"
    except Exception as e:
        return False, f"error {e}"
