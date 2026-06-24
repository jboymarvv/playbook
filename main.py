"""
main.py — FastAPI server
========================
- /analyse           : upload a Solscan CSV, get the report
                       (7-day = free; all-time = paywalled when PAYMENTS_ENABLED)
- /access/{wallet}    : check if a wallet has premium access
- /checkout           : create a Stripe or Helio checkout for a wallet
- /webhook/stripe     : Stripe payment notifications
- /webhook/helio      : Helio payment notifications

Run locally:    uvicorn main:app --reload
Run on Railway: uvicorn main:app --host 0.0.0.0 --port $PORT
"""

import re
from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

import analyser
import cache
import config
import access
import payments
import emailer
import xai_coach

app = FastAPI(title="Playbook")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],     # lock to your domain before launch
    allow_methods=["*"],
    allow_headers=["*"],
)

SOLANA_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def _sanitise(obj):
    """Replace NaN/Inf with None so responses always serialise."""
    if isinstance(obj, float):
        if obj != obj or obj in (float("inf"), float("-inf")):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitise(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitise(v) for v in obj]
    return obj


async def _attach_coaching(result, wallet):
    """
    Attach the premium xAI coaching narrative to an all-time report.
    Cache-first: a given wallet is only ever sent to xAI once. Fails safe —
    if anything goes wrong, the report is returned unchanged (rule-based).
    """
    wallet = (wallet or "").strip()
    if not wallet:
        return
    cached = cache.get_llm_summary(wallet)
    if cached:
        result["ai_coaching"] = cached
        return
    coaching = await xai_coach.generate_coaching(result)
    if coaching:
        cache.set_llm_summary(wallet, coaching, model=config.XAI_MODEL)
        result["ai_coaching"] = coaching


# ── INFO ─────────────────────────────────────────────────────
@app.get("/")
def root():
    return {"status": "ok", "service": "playbook", "mode": config.DATA_SOURCE}

@app.get("/health")
def health():
    return {"status": "healthy"}

@app.get("/pricing")
def pricing():
    return {
        "onetime": config.PRICE_ONETIME_DISPLAY,
        "sub": config.PRICE_SUB_DISPLAY,
        "onetime_enabled": config.ONETIME_ENABLED,
        "sub_enabled": config.SUB_ENABLED,
        "payments_enabled": config.PAYMENTS_ENABLED,
        "email_enabled": config.EMAIL_ENABLED,
    }

@app.get("/archive-stats")
def archive_stats():
    """How big is the permanent token/candle dataset so far."""
    return cache.archive_stats()


# ── ACCESS CHECK ─────────────────────────────────────────────
@app.get("/access/{wallet}")
def check_access(wallet: str):
    wallet = wallet.strip()
    if not SOLANA_ADDRESS_RE.match(wallet):
        raise HTTPException(status_code=400, detail="Invalid wallet address.")
    return access.access_status(wallet)


# ── ANALYSIS ─────────────────────────────────────────────────
@app.post("/analyse")
async def analyse(
    request: Request,
    file: UploadFile = File(...),
    timeframe: str = Form("7d"),
    wallet: str = Form(""),
):
    """
    Upload a Solscan CSV and get an analysis.
      timeframe = "7d"  -> free
      timeframe = "all" -> requires premium access for `wallet` (when enabled)
    """
    ip = request.client.host if request.client else "unknown"
    if not cache.check_rate_limit(ip):
        raise HTTPException(status_code=429, detail="Daily limit reached. Try again tomorrow.")

    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a .csv file.")

    csv_bytes = await file.read()
    if len(csv_bytes) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"File too large. Max {config.MAX_UPLOAD_BYTES // (1024*1024)} MB.")
    if len(csv_bytes) < 50:
        raise HTTPException(status_code=400, detail="That file looks empty.")

    days = 7 if timeframe == "7d" else None

    # Paywall the all-time report
    if days is None and config.PAYMENTS_ENABLED:
        w = wallet.strip()
        if not SOLANA_ADDRESS_RE.match(w):
            raise HTTPException(status_code=400, detail="A wallet address is required for the all-time report.")
        if not access.has_access(w):
            raise HTTPException(status_code=402, detail="The all-time report is premium. Unlock it to continue.")

    try:
        result = await analyser.analyse_csv(csv_bytes, days=days)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {e}")

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    # Premium AI coaching narrative (paid all-time only, cached per wallet)
    if days is None and config.XAI_ENABLED:
        await _attach_coaching(result, wallet.strip())

    return _sanitise(result)


# ── ANALYSIS (WALLET PASTE — for Helius mode) ────────────────
@app.post("/analyse-wallet")
async def analyse_wallet_endpoint(request: Request):
    """
    Analyse straight from a wallet address (no CSV upload).
    Requires DATA_SOURCE=helius and HELIUS_API_KEY to be configured.
    Body: { "wallet": "...", "timeframe": "7d"|"all" }
    """
    ip = request.client.host if request.client else "unknown"
    if not cache.check_rate_limit(ip):
        raise HTTPException(status_code=429, detail="Daily limit reached. Try again tomorrow.")

    body = await request.json()
    wallet = (body.get("wallet") or "").strip()
    timeframe = body.get("timeframe", "7d")

    if not SOLANA_ADDRESS_RE.match(wallet):
        raise HTTPException(status_code=400, detail="Invalid wallet address.")

    days = 7 if timeframe == "7d" else None

    # Paywall the all-time report (same logic as CSV mode)
    if days is None and config.PAYMENTS_ENABLED:
        if not access.has_access(wallet):
            raise HTTPException(status_code=402, detail="The all-time report is premium. Unlock it to continue.")

    try:
        result = await analyser.analyse_wallet(wallet, days=days)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {e}")

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    # Premium AI coaching narrative (paid all-time only, cached per wallet)
    if days is None and config.XAI_ENABLED:
        await _attach_coaching(result, wallet)

    return _sanitise(result)


# ── EMAIL REPORT (premium only) ──────────────────────────────
@app.post("/email-report")
async def email_report(request: Request):
    """
    Email a paid user their report summary.
    Body: { "wallet": "...", "email": "...", "summary": {...} }
    Only works for wallets that have premium access.
    """
    if not config.EMAIL_ENABLED:
        raise HTTPException(status_code=503, detail="Email isn't available right now.")

    body = await request.json()
    wallet = (body.get("wallet") or "").strip()
    email  = (body.get("email") or "").strip()
    report_in = body.get("report") or {"summary": body.get("summary") or {}}

    if not SOLANA_ADDRESS_RE.match(wallet):
        raise HTTPException(status_code=400, detail="Invalid wallet.")
    if "@" not in email or "." not in email:
        raise HTTPException(status_code=400, detail="Please enter a valid email.")

    short = wallet[:4] + "…" + wallet[-4:]
    # Paid wallets: pull the authoritative full all-time report from cache so the
    # email is complete. Free wallets: email the 7-day report the client sent.
    if access.has_access(wallet):
        full = cache.get_wallet_result(wallet, None) or report_in
    else:
        full = report_in
    ok, msg = emailer.send_report_email(email, full, short)
    if not ok:
        raise HTTPException(status_code=500, detail=f"Couldn't send: {msg}")
    return {"sent": True}


# ── CHECKOUT ─────────────────────────────────────────────────
@app.post("/checkout")
async def checkout(request: Request):
    """
    Body: { "wallet": "...", "product": "onetime"|"sub", "method": "stripe"|"helio" }
    Returns: { "url": "<checkout url>" }
    """
    body = await request.json()
    wallet  = (body.get("wallet") or "").strip()
    product = body.get("product", "onetime")
    method  = body.get("method", "stripe")

    if not SOLANA_ADDRESS_RE.match(wallet):
        raise HTTPException(status_code=400, detail="Invalid wallet address.")
    if product not in ("onetime", "sub"):
        raise HTTPException(status_code=400, detail="Unknown product.")

    try:
        if method == "helio":
            url = payments.helio_checkout_url(wallet, product)
        else:
            url = payments.create_stripe_checkout(wallet, product)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Checkout failed: {e}")

    return {"url": url}


# ── WEBHOOKS ─────────────────────────────────────────────────
@app.post("/webhook/stripe")
async def webhook_stripe(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    ok, msg = payments.handle_stripe_webhook(payload, sig)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"received": True, "msg": msg}


@app.post("/webhook/helio")
async def webhook_helio(request: Request):
    payload = await request.body()
    sig = request.headers.get("helio-signature", "") or request.headers.get("x-signature", "")
    ok, msg = payments.handle_helio_webhook(payload, sig)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"received": True, "msg": msg}
