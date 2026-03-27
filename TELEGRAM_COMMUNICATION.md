# Telegram Communication System

## Overview

The bot communicates **all trading activity** through Telegram in real-time. Every trade action sends an instant message so you have complete visibility into bot operations.

## Message Types

### 1. **Entry Alerts** 📍
Sent when a position opens.

```
📍 POSITION OPENED — NVDA
⏰ 14:32:15

Strategy: IRON_CONDOR
Credit: $3.50
Profit Zone: $145.00 - $155.00
Max Loss: $1.50
POP (est): ~85%

IV Rank: 72% (🔥 HIGH)
Pre-market: ☀️ +2.3%

Reason: Medium-confidence IC setup
✅ Position logged and tracked
```

**Includes:**
- Symbol, DTE profile, entry price/credit
- Target, stop, max loss (if spread)
- IV Rank status with emoji
- Pre-market gap (if significant)
- Entry reason / signal explanation

---

### 2. **Exit Alerts** ✅/❌
Sent when a position closes (target hit, stop loss, expiration).

```
✅ WIN — TSLA
⏰ 15:45:22

Exit Type: CLOSED
Exit Price: $185.50
Days Held: 2

P&L: +8.3% | $250.00

Reason: TP (Target Profit)
```

**Includes:**
- Win/Loss status with emoji
- Exit reason (TP hit, SL, expiration, etc.)
- P&L percentage and dollar amount
- Days held
- Exit price

---

### 3. **P&L Updates** 📊
Sent during midday/EOD reviews for all open positions.

```
📊 Open Trades (3)
📈 AAPL | CALL @ $175.50 | 30DTE
📉 SPY | SPREAD @ $450.00 | 7DTE
📈 QQQ | PUT @ $380.00 | 21DTE

Next Review: 3:30 PM EST
```

---

### 4. **Scan Results** 🎯
Morning, midday, and EOD scans with top picks.

```
🎯 MORNING SCAN — March 27, 10:00 AM
Regime: CAUTION (VIX: 27.4) | Bias: PUTS 📉

TOP CALLS
1. MSFT (Score: 78) | $420 | IV Rank: 65% 🔥
   Reason: UOA detected, pre-market gap +1.8%

TOP PUTS
1. BA (Score: 82) | $180 | IV Rank: 42%
   Reason: RSI oversold, heavy put buying

DTE PICKS
0DTE: None (insufficient score)
7DTE: AMZN $3850 (score 68)
...
```

---

### 5. **Error Alerts** ⚠️
Sent when something goes wrong.

```
⚠️ TRADE ERROR — GOOG
⏰ 09:15:30

Error: INSUFFICIENT_MARGIN
Details: Attempted to open $5,000 position with $2,000 available

Action Taken: Trade skipped, logged for review
```

---

### 6. **Status Updates** 📈
Sent at EOD or on-demand via `/status` command.

```
📈 DAILY SUMMARY — March 27

Total Trades: 8
Wins: 6 (75% WR)
Losses: 2
Daily P&L: +$1,240.50

By Source:
  ic_pick: 3W, 1L (75% WR)
  top_call: 2W, 1L (67% WR)
  top_put: 1W, 0L (100% WR)

Confidence Changes:
  ic_pick: +8 → 58
  top_call: +5 → 55
  top_put: +8 → 58
```

---

## How to Set Up

### 1. **First Time Setup**

After deploying, send the bot any command (e.g., `/status` or `/help`):

```bash
# Via Telegram on your phone/desktop, send to the bot:
/status
```

This stores your chat ID automatically. All future alerts will go to this chat.

### 2. **Verify Connection**

Check that the bot has your chat ID:

```bash
ssh -i ~/.ssh/oracle_key ubuntu@170.9.254.97 "cat ~/ob-bot/data/chat_id.txt"
# Should output a number like: 123456789
```

### 3. **Test Alerts**

Test the system by manually logging a trade:

```bash
ssh -i ~/.ssh/oracle_key ubuntu@170.9.254.97 "cd ~/ob-bot && python3 << 'EOF'
from scanner import run_scan
from paper_trader import log_trade
from trade_alerts import set_telegram_app

# Simulate a trade
d = {'symbol': 'TEST', 'price': 100, 'call_score': 75, 'call_reason': 'Testing alerts'}
tid = log_trade(d, 'CALL', dte_profile='30DTE')
print(f'Trade #{tid} logged; Telegram alert should arrive shortly')
EOF
"
```

---

## Integration Points

### Entry Alerts (When Trade Opens)

Called from:
- `scanner.run_scan()` → `paper_trader.log_trade()` → Telegram
- `scanner.build_iron_condor()` → `paper_trader.log_iron_condor()` → Telegram

**Data sent:**
- Symbol, entry price, direction
- Strike, expiry, credit (if spread)
- IV Rank, pre-market gap
- Entry reason/signal

---

### Exit Alerts (When Trade Closes)

Called from:
- `paper_trader.check_open_trades()` (auto-called during bot monitoring)

**Data sent:**
- Symbol, exit price, exit reason
- P&L % and $
- Days held
- Win/Loss status

---

### P&L Updates (Scheduled)

Called from:
- `bot_patch3.py` midday_review() and eod_review()

**Data sent:**
- All open positions with current price
- Unrealized P&L
- Next scheduled action

---

### Scan Results (Scheduled)

Called from:
- `bot_patch3.py` scan_morning(), midday_review(), eod_review()

**Data sent:**
- Top calls/puts (5 each)
- DTE picks (0/7/21/30/60DTE)
- Regime, VIX, sentiment bias
- New Phase 1 metrics (IV rank, pre-market gap, UOA)

---

## Customization

### Change Alert Verbosity

Edit `trade_alerts.py` function `_format_alert()`:
- Remove IV Rank line for less detail
- Remove pre-market gap for less noise
- Combine multiple alerts into daily digest

### Add Custom Alerts

Example: Alert when a trade hits 50% of target:

```python
# In paper_trader.check_open_trades()
if pnl_pct >= 40:  # 50% of 80% target
    send_trade_alert('MILESTONE', t['symbol'],
                     milestone='50% profit', pnl_pct=pnl_pct)
```

### Quiet Hours

To disable alerts during certain hours:

```python
# In trade_alerts.py get_chat_id()
from datetime import datetime
hour = datetime.now().hour
if 20 <= hour or hour < 9:  # No alerts 8pm-9am
    return None
```

---

## Troubleshooting

### "No chat_id configured; cannot send alert"

**Problem:** Bot doesn't know your Telegram chat ID.

**Solution:** Send any command to the bot first (e.g., `/status`):

```bash
# Or manually set chat ID:
ssh -i ~/.ssh/oracle_key ubuntu@170.9.254.97 "echo '123456789' > ~/ob-bot/data/chat_id.txt"
```

---

### Alerts Not Arriving

**Check 1:** Verify chat ID is set:
```bash
ssh -i ~/.ssh/oracle_key ubuntu@170.9.254.97 "ls -la ~/ob-bot/data/chat_id.txt"
```

**Check 2:** Verify bot is running:
```bash
ssh -i ~/.ssh/oracle_key ubuntu@170.9.254.97 "sudo systemctl status ob-bot"
```

**Check 3:** Check logs for errors:
```bash
ssh -i ~/.ssh/oracle_key ubuntu@170.9.254.97 "tail -50 ~/ob-bot/data/ob_bot.log | grep -i 'alert\|telegram\|error'"
```

**Check 4:** Test manually:
```bash
ssh -i ~/.ssh/oracle_key ubuntu@170.9.254.97 "cd ~/ob-bot && python3 -c \"from trade_alerts import get_chat_id; import asyncio; print(asyncio.run(get_chat_id()))\""
```

---

### Too Many Alerts

If getting too many alerts, edit `bot_patch3.py` to consolidate messages:
- Send midday summary instead of individual updates
- Group exits into EOD report
- Suppress P&L updates if trade not near target/stop

---

## Bot Commands (Telegram)

### `/status`
Shows next scheduled scan time and stores your chat ID.

```
⏱️ NEXT SCANS
10:00 AM EST — Morning Scan
12:30 PM EST — Midday Review
3:30 PM EST — EOD Review
```

### `/picks`
Shows top 5 calls and 5 puts + DTE recommendations.

### `/lessons`
Shows last 5 closed trades + key lessons learned.

### `/help`
Shows all available commands.

---

## FAQ

**Q: Can I disable alerts for certain symbol?**
A: Not currently. You can modify `trade_alerts.py` to add a blocklist:
```python
BLOCKED_SYMBOLS = {'SPYG', 'SPY'}  # Don't alert on these
```

**Q: Do alerts include real-time position sizing?**
A: Yes, credit received and max loss are shown for spreads.

**Q: Can I get alerts in multiple Telegram chats?**
A: Edit `get_chat_id()` in `trade_alerts.py` to return multiple IDs, then loop in `send_trade_alert()`.

**Q: Are alerts guaranteed to send?**
A: Alerts have retry logic (3 attempts). If Telegram API is down, alerts queue and retry on next scan.

---

## Summary

**You now have complete Telegram visibility into:**
- ✅ Every trade entry (symbol, price, reason, IV rank, gaps)
- ✅ Every trade exit (P&L, outcome, reason)
- ✅ All open positions (real-time P&L updates)
- ✅ Scan results (top picks, regimes, metrics)
- ✅ Errors and warnings (immediate notification)
- ✅ Daily performance (W/L, confidence changes)

**Next:**
1. Send bot any `/command` to set up chat ID
2. Let it run through a full trading day
3. Monitor alerts — all trading activity flows to Telegram
