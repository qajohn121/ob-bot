# 🤖 OB-BOT PROJECT PRIMER

**Project:** Options Trading Bot with Sentiment Analysis
**Status:** 🟡 ACTIVE DEVELOPMENT
**Last Updated:** March 28, 2026
**Mode:** AGGRESSIVE (5+ credit spreads/day, 50%+ profit targets)

---

## 🎯 PROJECT OBJECTIVE

Build an **AI-powered options trading bot** that:
- Scans 15 liquid tickers (AAPL, MSFT, NVDA, GOOGL, META, AMZN, JPM, V, MA, UNH, TSLA, CRM, AVGO, SPY, QQQ)
- Generates **5+ daily credit spreads** (iron condors, put/call spreads) with high probability of 50%+ profit
- Uses **multi-source sentiment analysis** (yfinance, NewsAPI, StockTwits, WSB, SEC, insider buying, options flow)
- Paper trades with Claude AI decision-making (Groq API integration)
- Deploys to Oracle VPS with automated scheduling

---

## 📊 CURRENT STATUS

### Phase Completion:
- ✅ **Phase 1:** Core scanner, sentiment analysis, market regime detection
- ✅ **Phase 2:** Paper trading, AI decision engine, scoring system
- ✅ **Phase 3A:** Lowered thresholds, added insider buying + options flow metrics
- 🟡 **Phase 3B:** AGGRESSIVE MODE - deployed, pending test results

### Last Session Completed:
1. **Fixed diagnostic script** - corrected function signatures in `diagnose_scores.py`
2. **Added insider buying detection** - Form 4 filing analysis (+15 regular, +25 accumulation)
3. **Added options flow metrics** - put/call ratio analysis and unusual volume detection
4. **Set AGGRESSIVE thresholds:**
   - 0DTE: 65 → **45**
   - 7DTE: 58 → **42**
   - 21DTE: 55 → **40**
   - 30DTE: 52 → **38**
   - 60DTE: 48 → **32**
5. **Enhanced sentiment weighting** - integrated new metrics into composite scoring

### Current Issues:
- None blocking deployment
- Morning scan showed 0 trades with VIX 31.0 (CAUTION regime)
- Aggressive thresholds should resolve this

---

## 🏗️ ARCHITECTURE OVERVIEW

### Core Files:

| File | Purpose | Key Functions |
|------|---------|---|
| `scanner.py` | Main scanning engine | `run_morning_scan()`, `score_for_call()`, `score_for_put()`, `fetch_ticker_data()` |
| `sentiment.py` | Multi-source sentiment | `get_full_sentiment()`, `get_insider_buying()`, `get_options_flow()` |
| `claude_brain.py` | AI decision making | `pick_best_trade()` (Groq API) |
| `paper_trader.py` | Paper trading engine | Trade logging, backtest support |
| `vol_analysis.py` | IV/HV analysis | IV Rank, volatility metrics |
| `run_morning_scan.py` | CLI entry point | `python3 run_morning_scan.py` |
| `diagnose_scores.py` | Scoring diagnostic | Debug why stocks pass/fail thresholds |

### Data Sources:
- **yfinance:** Stock data, technical indicators (SMA20/50, RSI, 52W high/low)
- **NewsAPI:** News sentiment analysis
- **SEC EDGAR:** Insider filings, distress signals (bankruptcy, going concern)
- **StockTwits:** Social sentiment (bull/bear %)
- **Reddit/WSB:** Trending mentions (via ApeWisdom)
- **yfinance options:** IV/HV ratio, put/call volume
- **Market Regime:** VIX-based (BULL, NORMAL, CAUTION, VOLATILE, COMPLACENT)

---

## 🎮 SCORING SYSTEM

### MIN_SCORE Thresholds (AGGRESSIVE MODE):
```python
MIN_SCORE = {
    "0DTE": 45,    # Day trades - explosive momentum required
    "7DTE": 42,    # Weekly - high conviction
    "21DTE": 40,   # Primary focus - iron condors
    "30DTE": 38,   # Monthly income - spreads
    "60DTE": 32    # Long-dated - safer positions
}

MIN_CONVICTION_GAP = {
    "0DTE": 10,
    "7DTE": 8,
    "21DTE": 8,
    "30DTE": 6,
    "60DTE": 5
}
```

### Scoring Factors (in `score_for_call()` / `score_for_put()`):
1. **Momentum** (+30 for 8%+ 4h move, +22 for 5%+, etc)
2. **Volume** (+20 for 4x+ relative volume)
3. **Trend** (Above/below SMA20/50, distance from 52W high/low)
4. **RSI** (50-70 for calls healthy, <50 with momentum for room to run)
5. **Earnings** (±10 days = +10 bonus)
6. **War/Sector** (+10 for geopolitical themes)
7. **IV/HV Ratio** (+15 if cheap IV vs HV, -18 if very rich)
8. **Unusual Activity** (+12 if UOA detected)
9. **Pre-market Gap** (±8 depending on direction)
10. **Social Sentiment** (-15 to +15 adjustment)
11. **🆕 Insider Buying** (+15-25 points from insider_score)
12. **🆕 Options Flow** (±10-15 points from options_score)

### Sentiment Weighting (in `_compute_full_sentiment()`):
```python
composite = (
    yfinance_score       * 0.18 +
    newsapi_score        * 0.18 +
    stocktwits_score     * 0.35 +
    wsb_contrib          * 0.08 +
    insider_contrib      * 0.10 +  # NEW
    options_contrib      * 0.08 +  # NEW
    sec_distress         * 0.07
)
```

---

## 🚀 DEPLOYMENT

### VPS Details:
- **Host:** ubuntu@170.9.254.97
- **Bot Directory:** `/home/ubuntu/ob-bot`
- **Service:** `ob-bot.service` (systemd)
- **Git:** `https://github.com/qajohn121/ob-bot` (source of truth)
- **Dashboard:** `http://170.9.254.97:3003` (paper trading results)

### Deployment Workflow:
```bash
# 1. Make code changes locally in VS Code
# 2. Commit to GitHub via VS Code
git add .
git commit -m "Description of changes"
git push origin main

# 3. On VPS, pull and restart
cd ~/ob-bot
git pull origin main
sudo systemctl restart ob-bot

# 4. Monitor
sudo journalctl -u ob-bot -f
```

---

## 📈 MARKET REGIME RULES

Bot adjusts behavior based on VIX (market volatility):

| Regime | VIX Range | Bias | Size | Action |
|--------|-----------|------|------|--------|
| BULL | <15 | CALLS | 1.0x | Aggressive calls |
| NORMAL | 15-20 | BOTH | 1.0x | Balanced |
| CAUTION | 20-30 | PUTS | 0.7x | Reduce size, prefer puts |
| VOLATILE | 30-40 | PUTS | 0.5x | Very conservative |
| COMPLACENT | >40 | N/A | 0.3x | Avoid trading |

---

## 🎯 NEXT STEPS (Priority Order)

1. **TEST AGGRESSIVE MODE** (IMMEDIATE)
   - Pull changes to VPS: `git pull origin main`
   - Run: `python3 run_morning_scan.py`
   - Expected: 5+ trades (previously 0)
   - Diagnostic: `python3 diagnose_scores.py` to debug scores

2. **VALIDATE SPREAD QUALITY**
   - Check dashboard for trade selections
   - Verify spreads have >50% profit potential
   - Monitor Greeks (delta, theta, vega)

3. **OPTIMIZE CREDIT SPREAD SELECTION**
   - Review `build_spread()` logic in scanner.py
   - Ensure iron condors are 30-40 delta (safest)
   - Target 50-70% probability of profit (PoP)

4. **FINE-TUNE THRESHOLDS (if needed)**
   - If still <5 trades: lower MIN_SCORE by 5 points
   - If too many poor quality trades: raise conviction gap
   - Watch for 50%+ winners vs losers ratio

5. **INTEGRATE RISK MANAGEMENT**
   - Position sizing based on account
   - Max loss per trade (2-5% of capital)
   - Stop loss logic for paper trading

---

## 🚨 OPEN BLOCKERS

### None Currently

**Resolved:**
- ✅ Git merge conflict on VPS (removed untracked files)
- ✅ Function signature mismatch in diagnose_scores.py
- ✅ VIX 31.0 (CAUTION) preventing trades - aggressive thresholds should fix

---

## 🔧 CONFIGURATION

### Environment Variables (`.env` on VPS):
```
GROQ_API_KEY=your_key          # For Claude brain
NEWSAPI_KEY=your_key           # For news sentiment
APEWISDOM_API_KEY=your_key    # For WSB data (optional)
```

### Key Parameters:
- **Scan Interval:** 9:30 AM - 4:00 PM EST (market hours)
- **Paper Trading:** Enabled by default
- **Telegram Alerts:** Configured (optional)
- **Cache TTL:** 300 seconds (sentiment data)

---

## 📋 QUICK COMMANDS

```bash
# SSH to VPS
ssh -i ~/Downloads/ssh-key-2026-02-14.key ubuntu@170.9.254.97

# Run morning scan
cd ~/ob-bot
python3 run_morning_scan.py

# Run diagnostic
python3 diagnose_scores.py

# Restart bot service
sudo systemctl restart ob-bot
sudo systemctl status ob-bot --no-pager

# View live logs
sudo journalctl -u ob-bot -f

# Check git status
git status
git log --oneline -10
```

---

## 📝 SESSION HISTORY

### Session 1 (Context Start):
- User wanted to understand project status
- Clarified development environment (GitHub as source of truth)
- Diagnosed trade generation bottleneck (high MIN_SCORE thresholds)

### Session 2 (Current - March 28, 2026):
- Added insider buying detection (Form 4 analysis)
- Added options flow metrics (put/call ratios)
- Enhanced sentiment weighting
- Fixed diagnostic script function signatures
- **Set AGGRESSIVE thresholds for 5+ trades/day**
- Ready for deployment and testing

---

## 🎓 LESSONS LEARNED

1. **High VIX + CAUTION regime = very conservative scoring** → Need aggressive thresholds
2. **Sentiment metrics alone aren't enough** → Need structural changes (lower thresholds)
3. **Credit spreads > directional** → Focus on iron condors for consistent income
4. **Insider buying + options flow = high conviction** → Worth the development effort
5. **Diagnostic tool essential** → Helps debug when trades don't generate

---

## 📌 PRIMER MAINTENANCE

This document is **auto-generated at session end** and should include:
- [ ] Current project status
- [ ] Last completed work
- [ ] Next priority steps
- [ ] Open blockers/issues
- [ ] Key configuration values
- [ ] Quick command reference

**Rule:** Update this before ending each session on ob-bot.

---

*Auto-generated by Claude | Next review: After morning scan test results*
