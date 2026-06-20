"""
payments.py — Stripe + Helio checkout
=====================================
Creates checkout sessions and verifies completed payments for two products:
  - one-time all-time report  (PRICE_ONETIME)
  - monthly subscription      (PRICE_SUB)

Wallet address is passed through as metadata so that when a payment
completes (via webhook), we know which wallet to unlock.

Both rails write to the same access store, so the rest of the app never
needs to know which method was used.

NOTE on dependencies:
  - Stripe needs the `stripe` package (in requirements.txt).
  - Helio is called over plain HTTPS (no SDK needed) so it has no extra dep.
    Helio's exact API shape can change — the create/verify helpers below are
    structured so you only adjust the URLs/payload in one place if needed.
"""

import time
import json
import urllib.request
import urllib.error

import config
import access


# ─────────────────────────────────────────────────────────────
# STRIPE
# ─────────────────────────────────────────────────────────────

def _stripe():
    """Lazy import so the app still runs if stripe isn't installed yet."""
    import stripe
    stripe.api_key = config.STRIPE_SECRET_KEY
    return stripe


def create_stripe_checkout(wallet, product):
    """
    Create a Stripe Checkout session. Returns the URL to redirect the user to.
    product: "onetime" or "sub"
    """
    stripe = _stripe()
    if product == "sub":
        line_item = {"price": config.STRIPE_PRICE_SUB, "quantity": 1}
        mode = "subscription"
    else:
        line_item = {"price": config.STRIPE_PRICE_ONETIME, "quantity": 1}
        mode = "payment"

    session = stripe.checkout.Session.create(
        mode=mode,
        line_items=[line_item],
        success_url=config.SITE_URL + "/?paid=1&wallet=" + wallet,
        cancel_url=config.SITE_URL + "/?paid=0",
        metadata={"wallet": wallet, "product": product},
        # for subscriptions, also stamp the subscription's metadata
        subscription_data=({"metadata": {"wallet": wallet}} if mode == "subscription" else None),
    )
    return session.url


def handle_stripe_webhook(payload, sig_header):
    """
    Verify and process a Stripe webhook. Grants access on completed payment.
    Returns (ok: bool, message: str).
    """
    stripe = _stripe()
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, config.STRIPE_WEBHOOK_SECRET
        )
    except Exception as e:
        return False, f"Invalid signature: {e}"

    etype = event["type"]
    obj = event["data"]["object"]

    # One-time + first subscription payment both land here
    if etype == "checkout.session.completed":
        wallet = (obj.get("metadata") or {}).get("wallet")
        product = (obj.get("metadata") or {}).get("product")
        if wallet:
            if product == "sub" or obj.get("mode") == "subscription":
                access.grant_subscription(wallet, days=30)
            else:
                access.grant_one_time(wallet)
        return True, "access granted"

    # Recurring subscription renewals
    if etype == "invoice.payment_succeeded":
        # subscription metadata carries the wallet
        sub_id = obj.get("subscription")
        if sub_id:
            try:
                sub = stripe.Subscription.retrieve(sub_id)
                wallet = (sub.get("metadata") or {}).get("wallet")
                if wallet:
                    access.grant_subscription(wallet, days=30)
            except Exception:
                pass
        return True, "subscription renewed"

    # Cancellation
    if etype == "customer.subscription.deleted":
        wallet = (obj.get("metadata") or {}).get("wallet")
        if wallet:
            access.revoke(wallet)
        return True, "subscription cancelled"

    return True, f"ignored event {etype}"


# ─────────────────────────────────────────────────────────────
# HELIO (Solana payments)
# ─────────────────────────────────────────────────────────────
# Helio is non-custodial Solana checkout. The typical flow:
#   1. You pre-create "pay links" in the Helio dashboard (one for the
#      one-time report, one for the subscription) — each has a paylinkId.
#   2. You send the user to that pay link with their wallet as metadata.
#   3. Helio calls your webhook when payment confirms; you unlock the wallet.
#
# Helio's API/field names have shifted over time, so this is deliberately
# thin — adjust the URL and the field you read the wallet from in ONE place
# if their docs differ when you wire it up.

HELIO_BASE = "https://api.hel.io/v1"


def helio_checkout_url(wallet, product):
    """
    Build the Helio pay-link URL for the chosen product, tagging the wallet
    so the webhook can identify who paid.
    """
    paylink = config.HELIO_PAYLINK_SUB if product == "sub" else config.HELIO_PAYLINK_ONETIME
    # Helio pay links accept additionalJSON / metadata via query string.
    # We pass the wallet so the webhook can read it back.
    return f"https://app.hel.io/pay/{paylink}?customParam={wallet}__{product}"


def handle_helio_webhook(payload_bytes, signature):
    """
    Process a Helio payment webhook. Grants access on confirmed payment.
    Returns (ok, message).

    Helio signs webhooks with a shared secret you set in their dashboard.
    We verify it matches config.HELIO_WEBHOOK_SECRET.
    """
    # Verify shared secret
    if config.HELIO_WEBHOOK_SECRET and signature != config.HELIO_WEBHOOK_SECRET:
        return False, "bad signature"

    try:
        data = json.loads(payload_bytes.decode("utf-8"))
    except Exception as e:
        return False, f"bad payload: {e}"

    # Only act on a successful/confirmed payment
    status = (data.get("transactionStatus") or data.get("status") or "").upper()
    if status not in ("SUCCESS", "CONFIRMED", "PAID", "COMPLETED"):
        return True, f"ignored status {status}"

    # Read the wallet + product back out of the custom param we set
    custom = (
        data.get("customParam")
        or (data.get("meta") or {}).get("customParam")
        or (data.get("metadata") or {}).get("customParam")
        or ""
    )
    wallet, _, product = custom.partition("__")
    if not wallet:
        return False, "no wallet in webhook"

    if product == "sub":
        access.grant_subscription(wallet, days=30)
    else:
        access.grant_one_time(wallet)
    return True, "access granted"
