# Morning Scan & Dashboard Guide

## 📊 Dashboard (Live Now)
**URL**: http://170.9.254.97:3003

- Auto-refreshes every 3 seconds
- Shows all open trades with real-time P&L
- All times in EST timezone
- API: `curl http://170.9.254.97:3003/api/summary`

Current status: **13 open trades, $0.00 P&L**

---

## 🚀 Tomorrow Morning (March 28, Friday)

### Run Morning Scan (During Market Hours: 9:30 AM - 4 PM EST)

From your Mac (not SSH):

```bash
# Run morning scan and get full report
ssh -i ~/.ssh/oracle_key ubuntu@170.9.254.97 "cd ~/ob-bot && python3 run_morning_scan.py"
```

This will:
1. ✅ Scan 15 institutional stocks
2. ✅ Calculate scores based on market regime (VIX, momentum, technicals)
3. ✅ Auto-log all picks that meet MIN_SCORE thresholds
4. ✅ Return report showing:
   - Market regime (BULL/NORMAL/CAUTION/VOLATILE)
   - All 5 DTE picks (0DTE/7DTE/21DTE/30DTE/60DTE)
   - Symbols, scores, and status
   - Number of auto-trades executed

### Dashboard Auto-Updates

Once picks are logged:
1. New trades appear in dashboard within 1-2 seconds
2. Real P&L calculated from live yfinance prices
3. Each position tracks: Entry time, Exit time, Days held, Profit %, P&L $
4. Positions auto-close at profit targets (50% for 0DTE, 40% for 7DTE, etc.)

### View Detailed Results

```bash
# Get all open trades with P&L
curl http://170.9.254.97:3003/api/trades | python3 -m json.tool

# Get summary stats
curl http://170.9.254.97:3003/api/summary | python3 -m json.tool
```

---

## ⚙️ Technical Details

### Scoring System (Institutional Grade)

**Stock Universe**: 15 liquid mega-caps
- Tech: AAPL, MSFT, NVDA, GOOGL, META, AMZN
- Finance: JPM, V, MA
- Healthcare: UNH
- Growth: TSLA, CRM, AVGO
- Broad Market: SPY, QQQ

**Scoring Factors**:
- 4-hour momentum (move_4h)
- Relative volume (rel_volume)
- SMA20/SMA50 trend alignment
- RSI (oversold/healthy range)
- Days to earnings
- IV/HV ratio (cheap vs expensive options)
- Market regime bias (VIX-based)

**MIN_SCORE Thresholds**:
- 0DTE: 75 (tightest filter)
- 7DTE: 70
- 21DTE: 68
- 30DTE: 65
- 60DTE: 60 (loosest filter)

### Trade Exit Logic

**Profit Targets** (realistic for options):
- 0DTE: 50% profit target (short decay window)
- 7DTE: 40%
- 21DTE: 35%
- 30DTE: 30%
- 60DTE: 25%

**Iron Condors**: Close at 50% of max profit

**Stop Loss**: 20-30% loss (position-specific)

### Real-Time P&L Calculation

```python
# CALL position P&L
pnl_pct = ((current_price - entry_price) / entry_price) × 100

# PUT position P&L
pnl_pct = ((entry_price - current_price) / entry_price) × 100

# Dollars
pnl_dollar = (pnl_pct / 100) × entry_option_price × 100
```

All prices refreshed from yfinance every API call.

---

## 📱 Telegram Alerts

Every trade event sends real-time alerts:
- **ENTRY**: Symbol, strike, expiry, IV rank, pre-market gap
- **EXIT**: Symbol, profit %, days held
- **P&L UPDATE**: Midday/EOD summary of all open positions
- **ERROR**: Any issues caught and reported

Chat ID: Auto-saved when you first message `/status` to the bot

---

## 🎯 Expected Behavior

### Good Day (BULL regime, VIX < 15)
- Most stocks score 65-85+
- 5 picks expected
- Likely calls + some iron condors
- Short-term exits (0DTE/7DTE close quickly)

### Tough Day (CAUTION regime, VIX > 25)
- Lower scores across the board
- Fewer picks (may have 0 if below thresholds)
- Bias toward puts
- Positions hold longer due to higher risk

### Normal Day (NORMAL regime, VIX 18-25)
- Mixed calls and puts
- Moderate scores (65-75)
- Full slate of 5 picks expected
- Good balance of quick wins + swing positions

---

## 🔍 Troubleshooting

**No picks qualified?**
- Check dashboard `/api/summary` for current regime
- Check if it's after-hours (market closes 4 PM EST)
- Institutional stocks may have lower scores than growth stocks

**Trade didn't log?**
- Check VPS logs: `ssh -i ~/.ssh/oracle_key ubuntu@170.9.254.97 "tail -50 ~/ob-bot/data/ob_bot.log"`
- Check database: `sqlite3 ~/ob-bot/data/trades.db "SELECT COUNT(*) FROM trades;"`

**P&L shows $0?**
- Normal if all trades are underwater (losing money)
- Reflects real unrealized P&L from live market data
- Updates every time dashboard is refreshed

---

## 📈 Example Tomorrow

```
🤖 MORNING SCAN REPORT
Time: 03/28 10:15:00 EST
Regime: NORMAL | VIX: 18.5 | Bias: BOTH

0DTE    NVDA     89/100   IC       380/390   03/28   ✅ LOGGED (IC)
7DTE    TSLA     78/100   CALL     260       04/04   ✅ LOGGED (CALL)
21DTE   META     72/100   PUT      320       04/18   ✅ LOGGED (PUT)
30DTE   AAPL     68/100   CALL     190       04/25   ✅ LOGGED (CALL)
60DTE   JPM      65/100   IC       155/160   05/26   ✅ LOGGED (IC)

✅ Auto-Trades Executed: 5
Dashboard: http://170.9.254.97:3003
```

Each trade now tracks in the dashboard with live P&L!
