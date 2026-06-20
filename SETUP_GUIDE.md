# Playbook — Setup & Deployment Guide

A web tool where Solana memecoin traders see what their exits cost them and
get a personalised rule set built from their own trade history.

**Free** 7-day report · **$10 one-time** all-time report.

---

## THE FILES

```
backend/                     (shared by BOTH frontend versions)
  main.py          FastAPI server — analysis + payments + webhooks
  analyser.py      Analysis engine (parse -> candles -> simulate -> report)
  insights.py      Rule-based findings + personalised rules (no AI)
  cache.py         PERMANENT candle/token archive (SQLite) + rate limiting
  access.py        Who has paid (SQLite, survives restarts)
  payments.py      Stripe + Helio checkout and webhooks
  config.py        All settings via environment variables
  requirements.txt
  Procfile

frontend/
  index.html         CSV-UPLOAD version — USE THIS NOW
  index_wallet.html  WALLET-PASTE version — for later (needs Helius)
```

You only deploy ONE frontend at a time. Start with index.html (CSV).
Switch to index_wallet.html when you add Helius. The backend serves both.

---

## WHAT CHANGED RECENTLY

1. **Payment = single $10 one-time** all-time report. The subscription tier
   is fully built but switched OFF (set SUB_ENABLED=true to bring it back —
   no new code needed).
2. **access.py and cache.py now use SQLite files**, not memory — they
   survive server restarts. Tested directly across separate processes.
3. **Candle/token data is cached permanently** — it's immutable historical
   data, and the archive becomes a valuable dataset as it grows.
4. **Two frontend versions** now exist (CSV now, wallet-paste for later).

---

## STEP 1 — GET YOUR API KEY

1. Sign up at solanatracker.io
2. Upgrade to the **Developer plan ($49/mo)** — 50 RPS, needed for speed.
   (The $1 plan times out on parallel candle fetches.)
3. Copy the key.

This is the only required fixed cost to start.

---

## STEP 2 — RUN LOCALLY (no payments yet)

```bash
cd backend
pip install -r requirements.txt
export SOLANA_TRACKER_API_KEY="your_key_here"
uvicorn main:app --reload
```

Open `frontend/index.html` in your browser. Drop in a Solscan
"Export DeFi Activity" CSV, click Analyse.

With payments OFF (the default), both the 7-day and all-time reports work
freely — good for testing the analysis itself before wiring up money.

---

## STEP 3 — DEPLOY

### Backend -> Railway (~$5/mo)

1. Push `backend/` to a GitHub repo.
2. railway.app -> New Project -> Deploy from GitHub.
3. **Attach a Volume** (Railway dashboard -> your service -> Volumes).
   Mount it at e.g. `/data`. This is IMPORTANT — see "Persistence" below.
4. Add environment variables (Railway -> Variables):
   ```
   SOLANA_TRACKER_API_KEY=your_key
   ACCESS_DB_PATH=/data/access.db
   CACHE_DB_PATH=/data/cache.db
   ```
5. Copy your Railway URL.

### Frontend -> Vercel (free)

1. In `frontend/index.html`, set `API_URL` to your Railway URL.
2. Deploy the `frontend/` folder to Vercel.
3. Point playbook.ie at the Vercel deployment.

### Lock down

In `main.py`, change `allow_origins=["*"]` to your real domain
(`https://playbook.ie`).

---

## PERSISTENCE — DON'T SKIP THIS

`access.py` (who paid) and `cache.py` (the candle archive) write to SQLite
files. They survive a normal process restart — BUT on Railway, the
container's local disk can be wiped on redeploy.

**The fix:** attach a Volume and point ACCESS_DB_PATH and CACHE_DB_PATH at
paths inside it (Step 3 above). Then payment records and the archive
survive redeploys. This is a dashboard setting, not code.

Without the Volume, a redeploy would reset who-has-paid — exactly the
problem we fixed, reintroduced one level up. So do attach it before taking
real payments.

---

## STEP 4 — TURN ON PAYMENTS

Controlled by `PAYMENTS_ENABLED=true`. When on, the all-time report
requires the wallet to have paid. Set up Stripe and/or Helio first.

### Stripe (card)

1. Stripe account, live mode when ready.
2. Create ONE Product + Price:
   - "All-time report" — one-time price $10 -> copy its **Price ID**
   (Skip the subscription price unless you re-enable SUB later.)
3. Webhook -> `https://your-backend/webhook/stripe`, events:
   `checkout.session.completed`. Copy the signing secret.
4. Env vars:
   ```
   STRIPE_SECRET_KEY=sk_live_...
   STRIPE_PRICE_ONETIME=price_...
   STRIPE_WEBHOOK_SECRET=whsec_...
   ```

Stripe EU fee: ~1.5% + EUR0.25 per sale. No monthly fee.

### Helio (Solana)

1. Helio account (hel.io). Non-custodial, no monthly fee, ~1-2% per sale.
2. Create ONE Pay Link for the $10 report. Copy its id.
3. Webhook -> `https://your-backend/webhook/helio` with a shared secret.
4. Env vars:
   ```
   HELIO_PAYLINK_ONETIME=...
   HELIO_WEBHOOK_SECRET=...
   ```
   NOTE: if Solana payments confirm but access isn't granted, check
   `handle_helio_webhook()` in payments.py — Helio's field names can shift,
   and the wallet is read from one place there.

### Then

```
PAYMENTS_ENABLED=true
SITE_URL=https://playbook.ie
```

---

## STEP 5 — TEST THE MONEY FLOW

- [ ] Payments OFF: 7-day and all-time both work
- [ ] Payments ON, unpaid wallet: all-time opens the $10 checkout
- [ ] Stripe TEST mode: pay -> webhook grants access -> all-time unlocks
- [ ] Helio test: same via Solana
- [ ] Restart the server: previously-paid wallet STILL has access
      (this proves the Volume + SQLite persistence is working)

---

## HOW PAYMENT IDENTITY WORKS

Access is tied to the **wallet address**, not email or login.

1. User picks all-time -> confirms wallet in the $10 checkout modal
2. Pays via card or Solana (wallet passed as metadata)
3. Webhook fires -> access.grant_one_time(wallet)
4. That wallet permanently unlocks the all-time report

Consistent with "we only ever see your public address."

---

## PRICING (as built)

| Tier | Price | What |
|---|---|---|
| Free | $0 | Last 7 days — full insights + rules |
| All-time | $10 one-time | Complete history, permanent unlock |

$10 one-time chosen over subscription because: recurring billing on Solana
isn't cleanly supported (Stripe can, Helio can't), the product is a
one-shot diagnostic not a daily-habit tool, and $10 has a better fee ratio
than smaller amounts. Subscription infra is kept (SUB_ENABLED flag) if you
build real progress-tracking later.

---

## COSTS

| Item | Cost |
|---|---|
| Solana Tracker Developer | $49/mo (fixed) |
| Railway backend + Volume | ~$5/mo (fixed) |
| Vercel frontend | free |
| Stripe | ~1.5% + EUR0.25 per sale |
| Helio | ~1-2% per sale |
| **Fixed total** | **~$54/mo** |

Processor fees are per-sale, not subscriptions. The permanent candle cache
keeps API cost flat as you grow.

---

## SWITCHING TO WALLET-PASTE LATER (HELIUS)

When you want users to paste a wallet instead of uploading a CSV:

1. Get a Helius API key, set `HELIUS_API_KEY` and `DATA_SOURCE=helius`.
2. In `analyser.py`, point `get_wallet_trades()` at Helius's swap-history
   endpoint, mapping fields into the same `{token: {buys, sells}}` shape.
   (One function — the rest of the engine is unchanged.)
3. Deploy `frontend/index_wallet.html` instead of `index.html`
   (set its API_URL the same way).

The backend already has the `/analyse-wallet` endpoint that index_wallet.html
calls. Everything else — payments, access, insights, rules, the permanent
cache — is identical. The two frontends share one backend.

Cost when you switch: Helius (~$49/mo) on top, OR do wallet-paste on Solana
Tracker alone to avoid a second bill.

---

## ABUSE PROTECTION (built in)

- Rate limit: 5 analyses per IP per day (RATE_LIMIT_PER_DAY)
- Upload cap: 5 MB (MAX_UPLOAD_BYTES)
- CSV / wallet-address validation with clear errors
- Permanent cache means repeat scans of the same token cost nothing

Add Cloudflare (free) in front of Vercel for DDoS/bot protection.

---

## DISCORD TIER (deferred — for later)

When you build it: mark a whole Discord SERVER premium when its admin pays
(the bot knows which server a command runs in), rather than tracking
individual members. The bot becomes another thin "source" calling the same
engine. Per-user + per-server rate limits keyed on Discord IDs stop spam.
Not built yet — individual payment first.

---

## ENDPOINTS REFERENCE

```
GET  /                 health/info
GET  /health           health check
GET  /pricing          prices + which products are enabled
GET  /archive-stats    how big the permanent token archive is
GET  /access/{wallet}  does this wallet have premium access
POST /analyse          CSV upload (multipart: file, timeframe, wallet)
POST /analyse-wallet   wallet paste (json: wallet, timeframe) [Helius mode]
POST /checkout         create Stripe/Helio checkout (json: wallet, product, method)
POST /webhook/stripe   Stripe payment notifications
POST /webhook/helio    Helio payment notifications
```

---

## MONETISATION FEATURES (built into the frontend)

The free 7-day report is now deliberately structured to drive upgrades while
still proving the tool works:

1. **Prominent upgrade banner** — appears right after the verdict (the
   emotional peak where they've just seen their SOL swing). Yellow taped
   notebook card, clear benefits, big unlock button.

2. **Gated rules** — free users see the first 2 rules clearly, the rest are
   blurred (structure visible, content locked) with an "X more rules locked"
   unlock CTA. This is the core conversion lever — the rules are the gold.

3. **Gated insights** — free users see the first 3 stat cards, rest blurred.

4. **Paid users see everything** unblurred, no banner, no CTAs — clean.

All gating is controlled by whether the report is 7-day (free) or all-time
(paid). No config needed — it's automatic based on the tier.

---

## EMAIL RESULTS (optional, premium only)

Paid users can have their report summary emailed to them. Off by default.

To enable:
1. Sign up at resend.com (free tier: 3,000 emails/month).
2. Verify your domain (playbook.ie) in Resend so mail sends from
   reports@playbook.ie.
3. Set env vars:
   ```
   EMAIL_ENABLED=true
   RESEND_API_KEY=re_...
   EMAIL_FROM=Playbook <reports@playbook.ie>
   ```

**Privacy (enforced in code + shown in UI):** the email is used once to send
the report, then dropped. It is never stored, shared, or sold. The UI states
this directly under the email field. If you ever want a mailing list, that
must be a separate, explicitly-consented opt-in — not this feature.

Cost: free at your scale (Resend free tier). Only sends for paid users, so
volume is naturally limited.

The email-results option only appears on paid reports when EMAIL_ENABLED is
true. Leave it off and the feature simply doesn't show.

---

## UPDATED ENDPOINTS

```
POST /email-report     email a paid user's report (json: wallet, email, summary)
```
(All previous endpoints unchanged.)
