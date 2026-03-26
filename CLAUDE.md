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

## Architecture

```
scanner.py          → Core scoring engine. Scans ~80 liquid tickers via yfinance.
                      Returns regime dict + dte_picks {0DTE/7DTE/21DTE/30DTE/60DTE}.
                      Each pick has entry_call/entry_put with real bid/ask from option chain.

paper_trader.py     → SQLite trade tracker (data/trades.db).
                      Tables: trades, autopsy, learning_cycles, daily_scores.
                      init_db() must be called on first run.

learner.py          → Reads autopsy table, adjusts scoring weights + DTE thresholds.
                      Saves to data/weights.json. run_learning_cycle() is the entry point.

sentiment.py        → News/SEC sentiment scorer with 30-min in-memory cache (_SENT_CACHE).

claude_brain.py     → All AI decisions via Groq free API (llama-3.3-70b-versatile).
                      pick_best_trade(), validate_trade_thesis(), get_market_commentary().
                      Falls back to rule-based logic if GROQ_API_KEY not set.

grok_brain.py       → Groq API for Telegram message formatting and scan commentary.
                      Rate-limited to 25 calls/hour (MAX_CALLS_PER_HOUR).

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
