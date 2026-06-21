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


def send_report_email(to_email, summary, wallet_short):
    """
    Send a short report summary email. Returns (ok, message).
    Only sends if EMAIL_ENABLED and a key is configured.
    """
    if not config.EMAIL_ENABLED:
        return False, "email disabled"
    if not config.RESEND_API_KEY:
        return False, "no email key configured"
    if not to_email or "@" not in to_email:
        return False, "invalid email"

    swing = summary.get("swing", 0)
    actual = summary.get("actual_net_sol", 0)
    strat = summary.get("strategy_net_sol", 0)
    trades = summary.get("total_trades", 0)

    subject = f"Your Playbook report — {swing:+.2f} SOL swing"

    # Plain, honest HTML — no tracking pixels, no marketing fluff
    html = f"""
    <div style="font-family: -apple-system, Arial, sans-serif; max-width: 540px; margin: 0 auto; color: #1a1a1a;">
      <h2 style="margin-bottom: 4px;">Your Playbook report</h2>
      <p style="color: #666; margin-top: 0;">Wallet {wallet_short}</p>
      <div style="background: #eaf7ee; border: 1px solid #2D8F47; padding: 18px; margin: 16px 0;">
        <div style="font-size: 13px; text-transform: uppercase; color: #2D8F47;">If you'd used the strategy</div>
        <div style="font-size: 34px; font-weight: 700; color: #2D8F47;">{swing:+.2f} SOL</div>
      </div>
      <table style="width: 100%; border-collapse: collapse; font-size: 15px;">
        <tr><td style="padding: 6px 0;">Trades analysed</td><td style="text-align: right; font-weight: 600;">{trades}</td></tr>
        <tr><td style="padding: 6px 0;">Your actual result</td><td style="text-align: right; font-weight: 600;">{actual:+.2f} SOL</td></tr>
        <tr><td style="padding: 6px 0;">With the strategy</td><td style="text-align: right; font-weight: 600;">{strat:+.2f} SOL</td></tr>
      </table>
      <p style="margin-top: 20px;">Open the full breakdown and your complete rules any time at
        <a href="{config.SITE_URL}" style="color: #3B6AB8;">playbook.ie</a>.</p>
      <hr style="border: none; border-top: 1px solid #eee; margin: 24px 0;">
      <p style="font-size: 12px; color: #999;">We used your email only to send this report. We don't store it, share it, or sell it.</p>
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
