# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repo Is

This directory (`ob-bot-fixes/`) contains **patch files and replacement modules** for an options trading bot running on an Oracle VPS at `ubuntu@170.9.254.97`. The canonical bot lives at `~/ob-bot/` on the VPS. Files here are SCP'd to the VPS via `~/Desktop/deploy_obbot.sh`.

The main `bot.py` on the VPS is **not** in this repo — it is patched in-place by the `bot_patch*.py` scripts.

## Deployment

**From Mac (not inside SSH):**
```bash
bash ~/Desktop/deploy_obbot.sh
```
This uploads all files via SCP, runs the three patch scripts, installs dependencies, and restarts the systemd service.

**Check bot status on VPS:**
```bash
ssh -i ~/.ssh/oracle_key ubuntu@170.9.254.97 "sudo systemctl status ob-bot --no-pager -l | tail -20"
```

**Tail live logs:**
```bash
ssh -i ~/.ssh/oracle_key ubuntu@170.9.254.97 "tail -f ~/ob-bot/data/ob_bot.log"
```

**Restart after changes:**
```bash
ssh -i ~/.ssh/oracle_key ubuntu@170.9.254.97 "sudo systemctl restart ob-bot"
```

**Quick scanner test on VPS:**
```bash
ssh -i ~/.ssh/oracle_key ubuntu@170.9.254.97 "cd ~/ob-bot && source venv/bin/activate && python -c \"from scanner import run_scan_dte_profiles; r=run_scan_dte_profiles(); print(r['regime'])\""
```

**Test Phase 1 metrics (IV rank, pre-market gap, UOA):**
```bash
ssh -i ~/.ssh/oracle_key ubuntu@170.9.254.97 "cd ~/ob-bot && python3 -c \"from scanner import fetch_ticker_data; d=fetch_ticker_data('NVDA'); print(f'Gap: {d.get(\\\"premarket_gap_pct\\\")}%, IVR: {d.get(\\\"ivr\\\")}, UOA: {d.get(\\\"uoa_flag\\\")}')\""
```

**Check IV Rank collection status:**
```bash
ssh -i ~/.ssh/oracle_key ubuntu@170.9.254.97 "sqlite3 ~/ob-bot/data/trades.db \"SELECT COUNT(*), COUNT(DISTINCT symbol) FROM iv_history;\""
```

## Architecture

```
scanner.py          → Core scoring engine. Scans ~80 liquid tickers via yfinance.
                      Returns regime dict + dte_picks {0DTE/7DTE/21DTE/30DTE/60DTE}.
                      Each pick has entry_call/entry_put with real bid/ask from option chain.
                      NEW (Phase 1): Extracts pre-market gap, IV rank, short %, UOA flags,
                      option chain volume/OI. Builds iron condors for 40-65 score range.

paper_trader.py     → SQLite trade tracker (data/trades.db).
                      Tables: trades, autopsy, learning_cycles, daily_scores, iv_history.
                      init_db() must be called on first run.
                      NEW (Phase 1): store_iv_snapshot() and get_iv_rank() functions for
                      52-week IV range tracking and IVR calculation.

learner.py          → Reads autopsy table, adjusts scoring weights + DTE thresholds.
                      Saves to data/weights.json. run_learning_cycle() is the entry point.

sentiment.py        → News/SEC sentiment scorer with 30-min in-memory cache (_SENT_CACHE).

claude_brain.py     → All AI decisions via Groq free API (llama-3.3-70b-versatile).
                      pick_best_trade(), validate_trade_thesis(), get_market_commentary().
                      Falls back to rule-based logic if GROQ_API_KEY not set.

grok_brain.py       → Groq API for Telegram message formatting and scan commentary.
                      Rate-limited to 25 calls/hour (MAX_CALLS_PER_HOUR).
                      NEW (Phase 1): Displays IV rank, pre-market gap, UOA flags in messages.

bot_patch.py        → Patches bot.py: replaces _fmt() and pr() to show entry blocks
                      (strike, expiry, option price, stop, target) in Telegram + dashboard.

bot_patch2.py       → Patches bot.py: adds /picks and /lessons Telegram commands,
                      injects Lessons Board section into the web dashboard.

bot_patch3.py       → Patches bot.py: adds autonomous scheduled jobs (10am/12:30pm/3:30pm EST
                      Mon-Fri), adds /status command showing next scheduled run times.

setup_service.sh    → Installs ob-bot as systemd service on the VPS (auto-start, RestartSec=15).
fix_imports.sh      → Diagnostic: checks bot.py syntax and patches missing imports on VPS.
```

## Key Design Decisions

**Scoring flow:** `scanner.py` scores each ticker 0–100 using weighted signals (momentum, volume, RSI, IV timing, sentiment, SMA alignment). Weights are stored in `data/weights.json` and updated by `learner.py` after each learning cycle.

**DTE pickers:** Five independent pickers (0DTE, 7DTE, 21DTE, 30DTE, 60DTE) each enforce a minimum score threshold (`MIN_SCORE` dict in scanner.py). Symbol deduplication (`used_symbols` set) ensures each tier picks a different stock.

**Option pricing:** `_real_option_price()` in scanner.py looks up the actual bid/ask from yfinance option chain data stored per ticker. Falls back to Black-Scholes approximation if chain data is unavailable. The `price_source` field in entry dict indicates `"market"` vs `"estimated"`.

**Regime bias:** VIX level sets regime (BULL/NORMAL/CAUTION/VOLATILE). `_apply_regime_bias()` in scanner.py re-ranks picks before DTE pickers run — CAUTION/VOLATILE boosts put scores by 10pts; BULL boosts call scores.

**ETF handling:** `ETF_TICKERS` set in scanner.py skips `.info` calls (which 404 for ETFs). All ETFs still get price/volume/options data via `.history()`.

**Patch system:** `bot_patch*.py` files use regex to find and replace specific functions in `bot.py` on the VPS. If a pattern isn't found, the script prints a warning rather than crashing. Always test patches after deployment via log inspection.

**AI availability:** Both `claude_brain.py` and `grok_brain.py` check `GROQ_API_KEY` at runtime. All functions have deterministic fallbacks — the bot runs without AI, just with lower-quality messaging and rule-based trade selection.

**Scheduled jobs:** `bot_patch3.py` injects jobs into python-telegram-bot's `JobQueue`. Chat ID for headless delivery is saved to `data/chat_id.txt` when any user sends `/status` to the bot.

## Telegram Communication System

**All bot activity communicates through Telegram:** trades (entry/exit), P&L updates, scan results, errors, daily summaries.

Key functions:
- `trade_alerts.py` — Formats and sends real-time alerts (ENTRY, EXIT, PNL_UPDATE, ERROR, ADJUSTMENT)
- `paper_trader.py` — Logs trades and returns alert-ready data
- `bot_patch3.py` — Scheduled jobs trigger scans and send results to Telegram

**Message types:**
- Entry alerts: symbol, entry price, strike, expiry, credit (spreads), IV rank, pre-market gap, reason
- Exit alerts: symbol, exit price, P&L %, days held, win/loss, exit reason
- P&L updates: all open positions with current price and unrealized P&L (midday/EOD)
- Scan results: top calls/puts, DTE picks, regime, new Phase 1 metrics (IV rank, UOA, gaps)
- Error alerts: trade failures with context and action taken
- Daily summary: win rate, P&L, confidence changes by recommendation source

**Setup:** Send bot any `/command` (e.g., `/status`) → stores chat ID automatically. All future alerts go to that chat.

See TELEGRAM_COMMUNICATION.md for full details, customization, and troubleshooting.

---

## Phase 1: Proactive Stock Selection (IV Rank + Pre-Market + Option Chain Intelligence)

**Iron Condor Builder:** `build_iron_condor()` in scanner.py creates defined-risk spreads for medium-confidence picks (40-65 score). Skips when IV Rank < 25 (insufficient premium). Boosts credit 5% and POP estimate when IV Rank > 60 (ideal selling environment).

**IV Rank System:**
- `paper_trader.py` maintains `iv_history` table with rolling 52-week IV snapshots (365-day window, auto-purged)
- `store_iv_snapshot(symbol, iv)` called after each scan; `get_iv_rank(symbol, iv)` returns IVR 0–100
- Requires 10+ daily samples to activate; signals HIGH (>60), NORMAL (40–60), LOW (<30)
- Scoring: +12pts when IVR < 25 (cheap), −10pts when IVR > 70 (expensive, prefer IC)

**Pre-Market Gap Detection:** Fetches 5d/1h bars with `prepost=True` from yfinance, extracts pre-market close vs prior regular session close. Gaps > ±3% influence call (+8) and put (−8 for positive, +8 for negative) scores.

**Unusual Options Activity (UOA):** Option chain extraction identifies strikes with volume > 3x open interest (signals informed traders). Adds +12pts to scores.

**Option Chain Metrics:** Extracts volume and open interest per DTE bucket (previously discarded). Calculates put/call volume ratio, bid/ask spread quality (rejects > 15% spread), max pain candidate.

**Short Interest:** Captures `shortPercentOfFloat` from ticker.info (already fetched). Boosts call scores +12pts when > 15% float combined with WSB trending (squeeze signal).

**Status (as of March 27):**
- ✅ All metrics extracting correctly (pre-market gap, short %, UOA flags, bid/ask spreads)
- ✅ IV Rank collection started (305 snapshots, 4–5 samples per symbol)
- ✅ Activation expected April 3–4 (10+ daily samples required)
- ✅ Iron Condors building at medium-confidence threshold
- ⏳ Phase 2 (Cramer inverse, sector rotation) queued for next week

## Environment

The VPS `.env` file at `~/ob-bot/.env` must contain:
```
TELEGRAM_TOKEN=...
GROQ_API_KEY=...   # Get free key from console.groq.com
```

Database is at `~/ob-bot/data/trades.db`. Weights at `~/ob-bot/data/weights.json`. Logs at `~/ob-bot/data/ob_bot.log`.

## Git Workflow

After every meaningful change, commit and push so work is never lost:

```bash
git add <changed files>
git commit -m "short description of what changed and why"
git push origin main
```

Commit message rules:
- Use present tense, imperative: `fix scanner min-score guard`, not `fixed` or `fixing`
- Reference the affected file/feature: `scanner: deduplicate symbols across DTE tiers`
- One logical change per commit — don't bundle unrelated fixes

Push after every commit. The remote at `github.com/qajohn121/ob-bot` is the source of truth for this patch set. Never let local commits pile up without pushing.

## Python Compatibility Note

The VPS runs Python 3.10. **Do not use backslash escapes inside f-string expressions** (e.g., `f"{'\\n'.join(...)}"`) — this is only valid in Python 3.12+. Use string concatenation or intermediate variables instead.
