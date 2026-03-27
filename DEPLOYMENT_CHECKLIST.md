# Phase 1 Iron Condor Deployment Checklist

## ✅ Deployment Status: COMPLETE

### Files Deployed
- [x] scanner.py — Iron Condor builder function
- [x] paper_trader.py — Iron Condor logging function
- [x] grok_brain.py — Iron Condor Telegram formatter
- [x] bot_patch3.py — Morning scan integration
- [x] vol_analysis.py — Dependency module

### Service Status
- [x] Bot service deployed to `/home/ubuntu/ob-bot/`
- [x] Bot service restarted and running
- [x] All syntax checks passed
- [x] Iron Condor functions imported successfully
- [x] Confidence sources initialized (including "ic_pick")

### Database Initialization
- [x] recommendation_confidence table ready
- [x] trades table ready with ic_pick source
- [x] All migrations applied

---

## 🚀 Live Monitoring Instructions

### Quick Status Check
```bash
# Check bot is running
ssh -i ~/.ssh/oracle_key ubuntu@170.9.254.97 "sudo systemctl status ob-bot"

# Tail live logs (shows every action)
ssh -i ~/.ssh/oracle_key ubuntu@170.9.254.97 "tail -f ~/ob-bot/data/ob_bot.log"

# Quick database query
ssh -i ~/.ssh/oracle_key ubuntu@170.9.254.97 "cd ~/ob-bot && sqlite3 data/trades.db 'SELECT COUNT(*) FROM trades;'"
```

### Daily Monitoring
Run the dashboard script to see IC vs Naked performance:
```bash
bash /Users/adhi/Desktop/claude_code_test/ob-bot-fixes/MONITORING_DASHBOARD.sh
```

This shows:
- Iron Condor win rate (target: >75%)
- Naked options win rate (target: ~55%)
- Confidence scores per recommendation source
- Open trades and P&L
- Live updates from VPS database

---

## 📅 What to Expect

### Today (March 27, 2026)

**Morning Scan (10:00 AM EST):**
- Bot runs `run_scan_dte_profiles()`
- For each DTE tier with score 40-65:
  - Automatically builds iron condor entry
  - Logs 3 versions: naked, IC, spread
  - Sends Telegram with all alternatives
  - You'll see IC in the chat

**Midday Review (12:30 PM EST):**
- Shows open trades with P&L
- IC trades grouped under "🎯 DTE Picks" (if using IC)
- Traffic light system shows risk level

**EOD Review (3:30 PM EST):**
- Closes all remaining trades
- Shows performance by recommendation source
  - ic_pick WR: ?% (depends on today's outcomes)
  - top_call WR: ?%
  - top_put WR: ?%
  - dte_pick WR: ?%
- Shows confidence changes
  - IC confidence will adjust up/down based on wins/losses

### Week 1 Tracking

| Day | Focus | What to Monitor |
|-----|-------|-----------------|
| Mar 27 | Initial IC creation | Do ICs appear in scan? |
| Mar 28 | First IC outcomes | Do they close at 50% profit? |
| Mar 29-31 | Pattern formation | Win rate trending >70%? |
| Apr 1 | Weekly review | IC WR > Naked WR? |
| Apr 2-3 | Confidence learning | Is confidence adjusting? |
| Apr 4 | Decision point | Proceed to Phase 2? |

---

## 🎯 Success Criteria

Phase 1 is successful if by end of Week 1:

### Quantitative
- [ ] Iron Condor win rate > 70% (target: 75-80%)
- [ ] Naked option win rate < 60% (current: ~55%)
- [ ] IC confidence score increases (started at 50%)
- [ ] Minimum 15-20 IC trades closed

### Qualitative
- [ ] ICs appear automatically in morning scans
- [ ] Users can see IC alternatives clearly
- [ ] EOD report shows IC performance separately
- [ ] No errors in logs related to IC functions

### Phase 2 Readiness
If all above are met:
- [ ] Ready to implement Volatility Gating (reduce naked options when VIX > 25)
- [ ] Ready to implement Wheel Strategy (systematic put selling)

---

## 🔍 Troubleshooting

### Issue: No ICs Appearing in Morning Scan

**Check:**
```bash
# 1. Verify build_iron_condor() is imported
ssh -i ~/.ssh/oracle_key ubuntu@170.9.254.97 "cd ~/ob-bot && python3 -c 'from scanner import build_iron_condor; print(\"OK\")'`

# 2. Check for errors in logs
ssh -i ~/.ssh/oracle_key ubuntu@170.9.254.97 "grep -i 'iron\|ic\|error' ~/ob-bot/data/ob_bot.log | tail -20"

# 3. Verify medium-confidence picks exist
# (Need picks with 40-65 score; if all picks are 70+ or <40, no ICs)
```

### Issue: Bot Crashes After Deployment

**Check:**
```bash
# 1. View error
ssh -i ~/.ssh/oracle_key ubuntu@170.9.254.97 "tail -50 ~/ob-bot/data/ob_bot.log | grep -i 'error\|traceback'"

# 2. Verify all files uploaded
ssh -i ~/.ssh/oracle_key ubuntu@170.9.254.97 "ls -la ~/ob-bot/ | grep '.py$'"

# 3. Restart
ssh -i ~/.ssh/oracle_key ubuntu@170.9.254.97 "sudo systemctl restart ob-bot"
```

### Issue: IC Logging Fails

**Check:**
```bash
# Verify log_iron_condor exists
ssh -i ~/.ssh/oracle_key ubuntu@170.9.254.97 "cd ~/ob-bot && python3 -c 'from paper_trader import log_iron_condor; print(\"OK\")'`

# Check database tables exist
ssh -i ~/.ssh/oracle_key ubuntu@170.9.254.97 "cd ~/ob-bot && sqlite3 data/trades.db '.tables'"
```

---

## 📞 Support

If Phase 1 doesn't work as expected:

1. **Check logs first:** `tail -100 ~/ob-bot/data/ob_bot.log | grep -E 'ERROR|iron_condor|ic_pick'`
2. **Verify database:** `sqlite3 ~/ob-bot/data/trades.db "SELECT COUNT(*) FROM trades WHERE spread_type='IRON CONDOR';"`
3. **Check confidence:** `sqlite3 ~/ob-bot/data/trades.db "SELECT * FROM recommendation_confidence;"`
4. **Review Telegram messages:** Check if ICs are shown as alternatives

---

## 📊 Performance Targets

### Week 1 Target Metrics

**Iron Condor Expectations:**
- 75-80% win rate (from GitHub research: 80-85% realistic)
- 50% profit taken in 3-5 days (theta decay)
- 25-30 trades logged (3-5 per DTE tier × 5 tiers)
- Confidence should increase if performing well

**Naked Options Expectations:**
- 55% win rate (current bot performance)
- Larger individual wins (+18%) and losses (-45%)
- Rougher equity curve (volatile)

**Expected Monthly Impact (If trends hold):**
- Mixed portfolio (half IC, half naked): 65-70% overall WR
- More consistent monthly returns (smoother equity curve)
- Lower maximum drawdown (defined risk on ICs)

---

## ✅ Deployment Complete

Bot is now running with Phase 1 Iron Condor implementation.

**Next action:**
1. Wait for morning scan (10:00 AM EST) to see ICs in action
2. Run monitoring dashboard daily
3. Track performance for 1 week
4. Evaluate success criteria by Friday, April 3

**Questions?** Check the logs or review the IRON_CONDOR_IMPLEMENTATION.md document for technical details.

---

**Deployed:** March 27, 2026
**Status:** LIVE
**Monitoring:** Enabled
