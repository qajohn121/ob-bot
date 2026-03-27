# Quick Start: Telegram Trade Alerts

Get instant Telegram notifications for every trade (entry, exit, P&L, errors).

## 3-Step Setup

### Step 1: Deploy New Files

The following new files are ready to deploy:

- `trade_alerts.py` — Trade alert system (send to VPS)
- `paper_trader.py` — Updated with alert data functions (already deployed)
- `TELEGRAM_COMMUNICATION.md` — Full documentation

Deploy:
```bash
scp -i ~/.ssh/oracle_key trade_alerts.py bot_patch4.py ubuntu@170.9.254.97:~/ob-bot/
```

### Step 2: Initialize Trade Alerts on VPS

```bash
ssh -i ~/.ssh/oracle_key ubuntu@170.9.254.97 "cd ~/ob-bot && python3 -c \"from trade_alerts import get_chat_id; print('✓ trade_alerts module ready')\""
```

Should output: `✓ trade_alerts module ready`

### Step 3: Register Your Chat ID

Open Telegram and send ANY message to your bot (e.g., `/status` or `/help`):

```
Your Bot: /status

Bot Response:
⏱️ NEXT SCANS
10:00 AM EST — Morning Scan
12:30 PM EST — Midday Review
3:30 PM EST — EOD Review
```

**Your chat ID is now saved.** All future alerts will go to this chat.

Verify:
```bash
ssh -i ~/.ssh/oracle_key ubuntu@170.9.254.97 "cat ~/ob-bot/data/chat_id.txt"
# Should output a number like: 1234567890
```

---

## ✅ Ready to Go

The bot will now send Telegram notifications for:

📍 **Entry Alerts** — When a position opens
- Symbol, entry price, strategy, credit (spreads)
- IV Rank, pre-market gap, entry reason

❌/✅ **Exit Alerts** — When a position closes
- Symbol, exit price, P&L %, days held
- Win/Loss status, exit reason

📊 **P&L Updates** — Midday & EOD
- All open positions with current price
- Unrealized P&L and next actions

🎯 **Scan Results** — Morning, Midday, EOD
- Top calls/puts with scores
- DTE recommendations, regime, VIX

⚠️ **Error Alerts** — If something goes wrong
- Error type, details, action taken

---

## Test Alerts

To test the system is working:

```bash
# Manually trigger a scan (will send Telegram alert)
ssh -i ~/.ssh/oracle_key ubuntu@170.9.254.97 "cd ~/ob-bot && python3 << 'EOF'
from scanner import run_scan_dte_profiles
from paper_trader import log_trade

# Run a quick scan
result = run_scan_dte_profiles()
print(f"Scan complete. Top pick: {result['top_calls'][0]['symbol'] if result['top_calls'] else 'None'}")

# You should see a Telegram message within 10 seconds
EOF
"
```

Check Telegram — you should see scan results with symbols and scores.

---

## Customize Alert Content

Edit `trade_alerts.py` functions to customize what's shown:

**Show less detail:**
```python
# In _format_entry(), remove this line:
# if iv_rank is not None: ...
```

**Group alerts into digest:**
```python
# In send_trade_alert(), queue alerts instead of sending instantly:
# Store in memory, send at EOD instead
```

**Add custom alerts:**
```python
# In _format_alert(), add new alert types:
elif alert_type == "POSITION_AT_50_PROFIT":
    lines.append("🎯 Position at 50% profit target!")
```

---

## Troubleshooting

**Alert not arriving after 10 seconds?**

1. Check chat ID is set:
   ```bash
   ssh -i ~/.ssh/oracle_key ubuntu@170.9.254.97 "cat ~/ob-bot/data/chat_id.txt"
   ```

2. Check bot is running:
   ```bash
   ssh -i ~/.ssh/oracle_key ubuntu@170.9.254.97 "sudo systemctl status ob-bot"
   ```

3. Check logs:
   ```bash
   ssh -i ~/.ssh/oracle_key ubuntu@170.9.254.97 "tail -30 ~/ob-bot/data/ob_bot.log | grep -i 'alert\|telegram'"
   ```

4. Verify Telegram token is valid:
   ```bash
   ssh -i ~/.ssh/oracle_key ubuntu@170.9.254.97 "grep TELEGRAM_TOKEN ~/ob-bot/.env"
   ```

---

## Summary

✅ Files deployed
✅ Module initialized
✅ Chat ID registered (via `/status`)
✅ Alerts active

You're now getting real-time Telegram notifications for **every trade action**.

See `TELEGRAM_COMMUNICATION.md` for full details on message types, customization, and advanced features.
