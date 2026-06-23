"""
config.py — Configuration
=========================
Set these as environment variables in Railway / locally.

REQUIRED for analysis:
  SOLANA_TRACKER_API_KEY     — candle data

REQUIRED for payments (when you turn them on):
  STRIPE_SECRET_KEY          — from Stripe dashboard
  STRIPE_PRICE_ONETIME       — Stripe Price ID for the one-time report
  STRIPE_PRICE_SUB           — Stripe Price ID for the monthly subscription
  STRIPE_WEBHOOK_SECRET      — from Stripe webhook settings
  HELIO_PAYLINK_ONETIME      — Helio pay-link id for one-time report
  HELIO_PAYLINK_SUB          — Helio pay-link id for subscription
  HELIO_WEBHOOK_SECRET       — shared secret you set in Helio dashboard
  SITE_URL                   — your public site URL (for redirects)

OPTIONAL (future):
  HELIUS_API_KEY             — for auto wallet fetch (DATA_SOURCE=helius)
"""

import os

# -- Analysis data source --
API_KEY        = os.environ.get("SOLANA_TRACKER_API_KEY", "YOUR_API_KEY_HERE")
HELIUS_API_KEY = os.environ.get("HELIUS_API_KEY", "")
DATA_SOURCE    = os.environ.get("DATA_SOURCE", "csv")   # "csv" or "helius"
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT", "20"))

# -- xAI (Grok) premium narrative layer --
# OFF by default. When enabled, paid all-time reports get an extra
# AI-written coaching narrative ON TOP of the rule-based analysis.
# The rule-based rules/numbers are NEVER replaced — xAI only adds prose.
# Output is cached permanently per wallet (one paid call per wallet, ever).
XAI_ENABLED     = os.environ.get("XAI_ENABLED", "false").lower() == "true"
XAI_API_KEY     = os.environ.get("XAI_API_KEY", "")
XAI_MODEL       = os.environ.get("XAI_MODEL", "grok-2-1212")
XAI_BASE_URL    = os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1")
XAI_MAX_TOKENS  = int(os.environ.get("XAI_MAX_TOKENS", "1400"))
XAI_TIMEOUT     = int(os.environ.get("XAI_TIMEOUT", "30"))

# -- Caching --
CANDLE_CACHE_TTL  = int(os.environ.get("CANDLE_CACHE_TTL",  str(7 * 86400)))
TOKEN_INFO_TTL    = int(os.environ.get("TOKEN_INFO_TTL",    str(7 * 86400)))
WALLET_RESULT_TTL = int(os.environ.get("WALLET_RESULT_TTL", str(86400)))

# -- Abuse protection --
RATE_LIMIT_PER_DAY = int(os.environ.get("RATE_LIMIT_PER_DAY", "5"))
MAX_UPLOAD_BYTES   = int(os.environ.get("MAX_UPLOAD_BYTES", str(5 * 1024 * 1024)))

# -- Site --
SITE_URL = os.environ.get("SITE_URL", "http://localhost:8000")

# -- Persistence --
# Where the "who has paid" database file lives. On Railway, point this at
# a path inside an attached Volume so it survives redeploys. Locally it
# just creates the file in the backend folder.
ACCESS_DB_PATH = os.environ.get("ACCESS_DB_PATH", "access.db")

# Where the permanent candle/token archive lives. Separate file from
# access.db on purpose — this one only ever grows, never needs backing up
# as urgently as payment records, but put it on the same Volume on Railway.
CACHE_DB_PATH = os.environ.get("CACHE_DB_PATH", "cache.db")

# -- Pricing (display only; actual amounts live in Stripe/Helio) --
PRICE_ONETIME_DISPLAY = os.environ.get("PRICE_ONETIME_DISPLAY", "$10")
PRICE_SUB_DISPLAY     = os.environ.get("PRICE_SUB_DISPLAY", "$10/mo")

# Which products are offered. Subscription infrastructure is fully built and
# kept for later — flip SUB_ENABLED to true to bring it back as a second
# option with zero new code.
ONETIME_ENABLED = os.environ.get("ONETIME_ENABLED", "true").lower() == "true"
SUB_ENABLED     = os.environ.get("SUB_ENABLED", "false").lower() == "true"

# -- Stripe --
STRIPE_SECRET_KEY     = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PRICE_ONETIME  = os.environ.get("STRIPE_PRICE_ONETIME", "")
STRIPE_PRICE_SUB      = os.environ.get("STRIPE_PRICE_SUB", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

# -- Helio (Solana) --
HELIO_PAYLINK_ONETIME = os.environ.get("HELIO_PAYLINK_ONETIME", "")
HELIO_PAYLINK_SUB     = os.environ.get("HELIO_PAYLINK_SUB", "")
HELIO_WEBHOOK_SECRET  = os.environ.get("HELIO_WEBHOOK_SECRET", "")

# -- Feature flags --
# When False, the all-time report is open to everyone (no paywall) --
# handy for testing before payments are wired up.
PAYMENTS_ENABLED = os.environ.get("PAYMENTS_ENABLED", "false").lower() == "true"

# -- Email results (optional, premium only) --
# Uses Resend (resend.com) — generous free tier (3,000/mo), simple HTTP API.
# Leave EMAIL_ENABLED false to skip entirely. We never store the email
# beyond sending the one report, and never share it.
EMAIL_ENABLED   = os.environ.get("EMAIL_ENABLED", "false").lower() == "true"
RESEND_API_KEY  = os.environ.get("RESEND_API_KEY", "")
EMAIL_FROM      = os.environ.get("EMAIL_FROM", "Playbook <reports@playbook.ie>")
