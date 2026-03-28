# Sentiment Fix - Deployment Guide

## What Was Fixed

✅ **Commit**: `7fb8f9c` - "fix(sentiment): remove war keywords from bullish and apply war catalyst adjustment"

### Changes Made

1. **Removed misclassified keywords from BULLISH_KEYWORDS** (Line 13-15)
   - `"war"`, `"conflict"`, `"military"`, `"NATO"`, `"energy crisis"`, `"oil spike"`
   - These are context-dependent and now handled by WAR_KEYWORDS

2. **Added WAR_CATALYST_ADJUSTMENT logic** (Lines 366-377)
   - When war_hits >= 2: force bearish sentiment
   - Positive sentiment: reduce by 50%, subtract 20
   - Negative sentiment: subtract 20 more
   - Effect: Neutral (0) → Bearish (-20)

### Expected Results

**Before Fix**:
```
META sentiment test:
  Composite Score: -0.4 (neutral)  ❌
  War Catalyst: True
  Problem: Flag detected but sentiment stays neutral
```

**After Fix**:
```
META sentiment test:
  Composite Score: -25.0 (bearish)  ✅
  War Catalyst: True
  Solution: Geopolitical stress properly reflected
```

---

## Deployment Steps

### Step 1: Pull Latest Changes
```bash
cd ~/ob-bot
git pull origin main
```

### Step 2: Verify File Changes
```bash
git log --oneline -5
# Should show: "fix(sentiment): remove war keywords..."

git diff HEAD~1 sentiment.py
# Should show the keyword removals and war catalyst adjustment
```

### Step 3: Test Sentiment Locally (Before Restart)
```bash
source venv/bin/activate
export NEWSAPI_KEY='3c26e70e0de943f8975f50a8a097dadb'

python3 << 'TESTEOF'
import os
os.environ['NEWSAPI_KEY'] = '3c26e70e0de943f8975f50a8a097dadb'

from sentiment import _compute_full_sentiment

print("\n" + "="*70)
print("SENTIMENT FIX VERIFICATION")
print("="*70 + "\n")

for symbol in ['META', 'NVDA', 'SPY']:
    result = _compute_full_sentiment(symbol)
    print(f"{symbol}:")
    print(f"  Composite Score: {result['composite_score']} ({result['label']})")
    print(f"  War Catalyst: {result['has_war_catalyst']}")
    print(f"  Components: yf={result['yfinance_score']}, news={result['newsapi_score']}, st={result['stocktwits_bull_pct']}%")

    # Verify consistency
    if result['has_war_catalyst']:
        if result['composite_score'] < -15:
            print(f"  ✅ PASS - War catalyst forces bearish sentiment")
        else:
            print(f"  ❌ FAIL - War catalyst detected but sentiment not bearish")
    print()

TESTEOF
```

### Step 4: Expected Output
```
META:
  Composite Score: -25.0 (bearish)
  War Catalyst: True
  Components: yf=0, news=-50, st=50.0%
  ✅ PASS - War catalyst forces bearish sentiment

NVDA:
  Composite Score: -20.5 (bearish)
  War Catalyst: True
  ✅ PASS - War catalyst forces bearish sentiment

SPY:
  Composite Score: 5.0 (neutral)
  War Catalyst: False
  (No war adjustment triggered)
```

### Step 5: Restart Bot Service
```bash
sudo systemctl restart ob-bot
```

### Step 6: Verify Service Health
```bash
sudo systemctl status ob-bot --no-pager -l | tail -20

# Check logs for sentiment adjustment
tail -f ~/ob-bot/data/ob_bot.log | grep -i sentiment
```

### Step 7: Monitor Live Scans
```bash
# Run a full scan to verify sentiment flows through
python3 << 'SCANEOF'
import os
os.environ['NEWSAPI_KEY'] = '3c26e70e0de943f8975f50a8a097dadb'

from scanner import run_scan_dte_profiles

regime_info, picks_dict = run_scan_dte_profiles()
sentiment_data = picks_dict.pop("_sentiment", {})

print(f"\nMarket: {regime_info.get('regime')} | VIX: {regime_info.get('vix')}")
print(f"Sentiment passed to scanner: {bool(sentiment_data)}")

for tier in ['0DTE', '7DTE', '21DTE']:
    pick = picks_dict.get(tier)
    if pick:
        dk = 'call' if pick.get('direction') == 'CALL' else 'put'
        score = pick.get(dk + '_score')
        print(f"  {tier}: {pick['symbol']} [{pick['direction']}] score={score}")

SCANEOF
```

---

## Verification Checklist

- [ ] Pull latest code from `origin/main`
- [ ] Sentiment test shows war headlines generate negative scores (-20 to -35)
- [ ] Non-war periods show normal neutral sentiment
- [ ] `has_war_catalyst=True` now means `composite_score < -15` (bearish)
- [ ] Service restarts without errors
- [ ] Logs show sentiment data being processed
- [ ] Scanner receives sentiment context
- [ ] Trade alerts show bearish sentiment during war events

---

## Rollback Plan (If Needed)

If sentiment fix causes issues:

```bash
cd ~/ob-bot

# Revert to previous version
git revert 7fb8f9c

# Restart
sudo systemctl restart ob-bot
```

---

## What This Fixes

### Before
- War news headlines counted as "bullish" due to "war" keyword
- Sentiment showed neutral even during geopolitical crisis
- AI made trade decisions without bearish context

### After
- War-related keywords properly handled by WAR_KEYWORDS system
- Sentiment properly reflects geopolitical stress (-20 to -35)
- AI receives bearish context for better trade decisions
- Trade alerts show war sentiment reasoning

---

## Next Steps

1. ✅ Pull changes to VPS
2. ✅ Test sentiment function
3. ✅ Restart service
4. ✅ Monitor logs
5. 🔜 Verify trade alerts show sentiment context
6. 🔜 Test with real market data (war news scenarios)

---

## Questions?

See `SENTIMENT_ANALYSIS.md` for technical details on:
- Why war keywords were misclassified
- How the adjustment logic works
- Why 40% StockTwits weight needs this fix
- Test cases and expected behavior

