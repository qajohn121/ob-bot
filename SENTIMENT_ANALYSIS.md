# OB-Bot Sentiment System - Complete Analysis & Fix Plan

## Executive Summary

The sentiment system has **3 critical issues** preventing it from properly detecting bearish sentiment during market stress:

1. **War keywords misclassified as BULLISH** (Lines 13-15)
2. **War catalyst flag detected but never applied** (Lines 363-378)
3. **Calculation doesn't reflect geopolitical stress**

**Impact**: Bot shows NEUTRAL sentiment even when markets are crashing on war news, leading to incorrect trade decisions.

---

## Issue #1: War Keywords in BULLISH_KEYWORDS (CRITICAL)

### Current Code (Lines 10-16)
```python
BULLISH_KEYWORDS = [
    "beat","beats","exceeds","surprise","blowout","record","growth","upgrade","buy",
    "outperform","bullish","raises guidance","strong","partnership","contract","wins",
    "awarded","approval","FDA","breakout","momentum","surge","soars","rockets","war",
    "conflict","defense contract","military","NATO","energy crisis","oil spike","drill",
    "LNG","gold rally","safe haven","inflation hedge",
]
```

### The Problem
These keywords are **context-dependent**:
- "war", "conflict", "military" → Usually bearish for stocks (geopolitical risk)
- "NATO", "energy crisis", "oil spike" → Context-dependent (bullish for defense/energy, bearish for growth)
- "defense contract" → Only bullish for defense contractors

### Example Failure
Headline: "Tech stocks worst week in nearly a year, driven down by war worries"

**Current behavior:**
- "war" matches BULLISH_KEYWORDS → +1 bullish hit
- "worst" not in any keywords → 0
- Result: net +1 bullish hit, score becomes positive/neutral ❌

**Desired behavior:**
- "worst week", "driven down" should register as bearish ✅
- "war" should NOT boost bullish score ✅
- Result: heavily bearish score ✅

---

## Issue #2: War Catalyst Flag Detected But Not Applied (CRITICAL)

### Current Code (Lines 363-378)
```python
war_hits  = yf_s.get("war_hits",  0) + news_s.get("war_hits",  0)
bank_hits = yf_s.get("bank_hits", 0) + news_s.get("bank_hits", 0)
return {
    "composite_score": round(composite, 1),
    "label": "bullish" if composite > 15 else ("bearish" if composite < -15 else "neutral"),
    "yfinance_score":  yf_s.get("score",  0),
    ...
    "has_war_catalyst": war_hits >= 2,  # ← FLAG IS SET
    ...
}
```

### The Problem
The `has_war_catalyst` flag is **calculated but never used** to adjust the sentiment score.

### Real Data Example
```
NewsAPI headlines:
  - "Stocks Post Worst Week Since Start of Iran War"
  - "Tech stocks suffer worst week driven down by war"

Result:
  war_hits = 2 (triggers has_war_catalyst = True)
  composite_score = -0.4 (NEUTRAL) ❌

Expected:
  war_hits = 2 (triggers has_war_catalyst = True)
  composite_score = -25 to -35 (BEARISH) ✅
```

The flag is set but sentiment remains neutral because the calculation doesn't apply it.

---

## Issue #3: Composite Weighting Doesn't Reflect Geopolitical Stress

### Current Weights (Line 354-361)
```python
composite = (
    yf_s.get("score",   0) * 0.20 +      # yfinance: 20%
    news_s.get("score", 0) * 0.20 +      # newsapi: 20%
    st_score               * 0.40 +      # stocktwits bull%: 40%
    wsb_contrib            * 0.10 +      # wsb: 10%
    (-sec_s.get("distress_score", 0)) * 0.10  # sec distress: 10%
)
```

### The Problem
When **war catalyst is detected**, sentiment should shift heavily bearish because:
- Geopolitical risk is **immediate** and **market-wide**
- War news should override smaller technical signals
- Current weights let StockTwits (40%) dominate, which defaults to 50/50 (neutral)

### Real Data Example
```
META sentiment calculation:
  yfinance:    0 * 0.20 =  0
  newsapi:    33 * 0.20 =  6.6  (positive, but wrong keyword weighting)
  st_score:    0 * 0.40 =  0    (50% bull/bear = neutral = 0)
  wsb:       -60 * 0.10 = -6
  sec:         0 * 0.10 =  0
  ─────────────────────────────
  composite:              0.6 (NEUTRAL) ❌
```

The war catalyst is detected (war_hits >= 2) but doesn't adjust the score from +0.6 to bearish.

---

## Solution Architecture

### Fix #1: Remove Misclassified Keywords
**Remove from BULLISH_KEYWORDS:**
- `"war"`, `"conflict"`, `"military"`, `"NATO"` (inherently bearish/neutral)
- `"energy crisis"`, `"oil spike"` (context-dependent)

**Keep in BULLISH_KEYWORDS:**
- `"defense contract"` (specific to defense contractors)

**Rationale:** These are handled by WAR_KEYWORDS separately for context-aware processing.

### Fix #2: Apply War Catalyst Adjustment to Sentiment
**When `war_hits >= 2`:**
```python
if war_hits >= 2:
    # Geopolitical stress overrides other signals
    if composite > 0:
        # Cut bullish confidence in half, add -20 bear bias
        composite = composite * 0.5 - 20
    else:
        # Already bearish? Make it more bearish
        composite = composite - 20
    composite = max(-100, min(100, composite))
```

**Effect:**
- Bullish sentiment (-→ -30): sentiment becomes heavily bearish
- Neutral sentiment (0) → -20: forced bearish
- Bearish sentiment (-10) → -30: reinforced bearish

### Why This Works
- **Immediate response**: War catalyst immediately creates -20 penalty
- **Proportional**: Bullish sentiment gets cut in half (less confident)
- **Minimum floor**: Even neutral becomes bearish (-15 threshold)
- **Additive**: Already bearish gets worse, not multiplied

---

## Expected Results After Fix

### Before Fix
```
META (War News):
  Composite Score: -0.4 (neutral)
  has_war_catalyst: True
  → CONTRADICTION: War detected but sentiment stays neutral ❌
```

### After Fix
```
META (War News):
  Composite Score: -25.0 (bearish)
  has_war_catalyst: True
  → CONSISTENT: War catalyst forces bearish sentiment ✅

NVDA (War News):
  Composite Score: -32.0 (bearish)
  has_war_catalyst: True
  → AI gets proper context for PUT selection ✅

SPY (No war news):
  Composite Score: -5.0 (neutral)
  has_war_catalyst: False
  → Normal sentiment, war adjustment not triggered ✅
```

---

## Implementation Steps

### Step 1: Fix BULLISH_KEYWORDS (1 line change)
Remove "war", "conflict", "military", "NATO", "energy crisis", "oil spike"

### Step 2: Add WAR_CATALYST_ADJUSTMENT (8 lines)
Insert adjustment logic after war_hits calculation, before return statement

### Step 3: Verify Flow
- Sentiment properly returns negative scores for war news
- War catalyst flag triggers adjustment
- AI receives bearish context

---

## Technical Details

### Keyword Categorization Logic
1. **BULLISH_KEYWORDS**: Only words that are inherently positive for equity markets
2. **BEARISH_KEYWORDS**: Words indicating company/market trouble
3. **WAR_KEYWORDS**: Geopolitical/conflict terms (handled separately)
4. **BANKRUPTCY_KEYWORDS**: Specific distress signals

War-related keywords should NOT be in BULLISH because their sentiment depends entirely on context, not the keyword itself.

### StockTwits Weight Problem
StockTwits is weighted at 40% but defaults to 50/50 (neutral) when unavailable. This is intentional (safe default), but means war catalyst adjustment is critical to ensure proper sentiment expression.

---

## Testing After Fix

### Unit Test
```python
from sentiment import _compute_full_sentiment

result = _compute_full_sentiment("META")
assert result['has_war_catalyst'] == True
assert result['composite_score'] < -15  # Bearish
assert result['composite_score'] > -35  # Not extreme
```

### Integration Test
```python
from scanner import run_scan_dte_profiles

regime, picks = run_scan_dte_profiles()
sentiment = picks.pop("_sentiment", {})
assert sentiment.get('composite_score') < -15  # Proper bearish
assert sentiment.get('has_war_catalyst') == True
```

### Live Test (On VPS)
```bash
export NEWSAPI_KEY='...'
cd ~/ob-bot && python3 -c "
from sentiment import _compute_full_sentiment
result = _compute_full_sentiment('META')
print(f'Score: {result[\"composite_score\"]} - {result[\"label\"]}')"
# Expected: "Score: -25.0 - bearish"
```

---

## Risk Assessment

### Low Risk
- Only modifying sentiment calculation logic
- No database changes
- No API changes
- Easy rollback (revert commit)

### Backward Compatible
- Sentiment structure unchanged
- Scanner integration unchanged
- AI decision logic works with any sentiment value
- No breaking changes to other modules

---

## Deployment Plan

1. **Branch & Fix**: Create feature branch, apply fixes
2. **Test**: Run unit + integration tests
3. **Commit**: Clean commit with detailed message
4. **Review**: Code review against requirements
5. **Deploy**: SCP to VPS, restart service
6. **Verify**: Check logs for sentiment adjustment
7. **Monitor**: Watch for proper trade selections

---

## Success Metrics

- ✅ War catalyst headlines generate negative sentiment (-20 to -40)
- ✅ Neutral baseline sentiment in non-war periods
- ✅ AI receives proper bearish context
- ✅ Trade entry alerts show war sentiment
- ✅ PUT selections increase during war events
- ✅ No false positives (war keywords in bullish headlines)

---

**Author**: Claude Code
**Date**: 2026-03-28
**Status**: Ready for Implementation
