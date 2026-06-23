Playbook
Personalized trading insights from your own data.
Playbook analyzes your Solana memecoin trade history against real historical candle data and generates clear, personalized rules and insights tailored to how you actually trade.
---
What is Playbook?
Most traders know what they bought. Very few truly understand how they exited.
Playbook helps you see the gap between what your trades could have made and what they actually made. It turns your raw trade data into actionable, personalized trading rules — not generic advice.
Free Tier (7-Day Preview)
Full analysis of your last 7 days of trading
Personalized insights based on your actual data
Peak distribution, timing analysis, and basic rules
Premium Tier (All-Time Report)
Complete analysis of your entire trading history
Custom AI-powered insights and refined trading rules
Deeper breakdowns and full personalized playbook
---
Features
Real candle data analysis — Replays your trades against actual historical price movement
Personalized rules — Rules are generated from your trading patterns, not generic templates
Exit efficiency tracking — See exactly where you left money on the table
Timing insights — Discover your best and worst trading hours/days
Privacy-first — We never see your wallet directly. Only the trades you choose to upload.
---
Live Site
https://www.playbook.ie 
---
Tech Stack
Backend
FastAPI (Python)
SQLite (persistent caching for candles & results)
Railway (hosting + volumes)
Frontend
Vanilla HTML + JavaScript + Chart.js
Hosted on Cloudflare Pages
Data Sources
Solscan (trade history export)
Solana Tracker (historical candle data)
---
Getting Started
If you want to run the backend locally or deploy it yourself, see the full setup guide:
→ SETUP_GUIDE.md
---
Project Structure
```
backend/
├── main.py              # FastAPI entrypoint
├── analyser.py          # Core analysis engine
├── insights.py          # Rule generation & personalization
├── payments.py          # Stripe + Helio integration
├── access.py            # Access control & payments
├── cache.py             # Persistent caching layer
├── config.py            # Environment configuration
├── requirements.txt
├── Procfile
└── SETUP_GUIDE.md
```
---
Roadmap
[ ] Public launch
[ ] Improved AI-powered premium analysis
[ ] Better mobile experience
[ ] Community features & shared playbooks
---
License
This project is currently private. All rights reserved.
---
Built by a trader, for traders.  
If you found this useful, feel free to star the repo or reach out.
