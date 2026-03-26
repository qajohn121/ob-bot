#!/usr/bin/env python3
import json, logging, os, time
from datetime import datetime, timedelta
import requests
import yfinance as yf

log = logging.getLogger("ob.sentiment")
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "")

BULLISH_KEYWORDS = [
    "beat","beats","exceeds","surprise","blowout","record","growth","upgrade","buy",
    "outperform","bullish","raises guidance","strong","partnership","contract","wins",
    "awarded","approval","FDA","breakout","momentum","surge","soars","rockets","war",
    "conflict","defense contract","military","NATO","energy crisis","oil spike","drill",
    "LNG","gold rally","safe haven","inflation hedge",
]
BEARISH_KEYWORDS = [
    "miss","misses","disappoints","shortfall","below expectations","downgrade","sell",
    "underperform","bearish","lowers guidance","weak","layoffs","restructuring",
    "investigation","lawsuit","bankruptcy","default","debt","insolvent","chapter 11",
    "fraud","SEC probe","DOJ","criminal","recall","crashes","plunges","tanks",
    "collapses","warning","outlook cut","margin squeeze","cash burn","going concern",
]
WAR_KEYWORDS = [
    "war","conflict","military","attack","missile","bomb","NATO","defense","Ukraine",
    "Russia","China","Taiwan","Middle East","Israel","Gaza","Iran","North Korea",
    "sanctions","escalation","troops","invasion",
]
BANKRUPTCY_KEYWORDS = [
    "bankruptcy","chapter 11","chapter 7","default","insolvency",
    "going concern","debt restructuring","liquidation",
]

# ── In-memory cache: {symbol: (timestamp, result)} ────────────────────────────
_SENT_CACHE: dict = {}
_ST_CACHE: dict = {}       # StockTwits per-ticker cache
_SOCIAL_CACHE: dict = {}   # Combined social sentiment cache

CACHE_TTL = 1800           # 30 minutes
TTL_STOCKTWITS = 900       # 15 minutes (rate limit risk)
TTL_SOCIAL = 900           # 15 minutes


def get_yfinance_sentiment(symbol):
    try:
        tk = yf.Ticker(symbol)
        news = tk.news
        if not news:
            return {"score": 0, "label": "neutral", "headlines": [], "source": "yfinance"}
        b = 0; bear = 0; war = 0; bank = 0; headlines = []
        for a in news[:10]:
            t = a.get("title", "").lower()
            headlines.append(a.get("title", ""))
            for k in BULLISH_KEYWORDS:
                if k in t: b += 1
            for k in BEARISH_KEYWORDS:
                if k in t: bear += 1
            for k in WAR_KEYWORDS:
                if k in t: war += 1
            for k in BANKRUPTCY_KEYWORDS:
                if k in t: bank += 1
        total = b + bear
        score = round((b - bear) / max(total, 1) * 100) if total > 0 else 0
        return {
            "score": score,
            "label": "bullish" if score > 20 else ("bearish" if score < -20 else "neutral"),
            "bullish_hits": b, "bearish_hits": bear,
            "war_hits": war, "bank_hits": bank,
            "headlines": headlines[:5], "source": "yfinance",
        }
    except Exception as e:
        log.debug(f"yf news {symbol}: {e}")
        return {"score": 0, "label": "neutral", "headlines": [], "source": "yfinance"}


def get_newsapi_sentiment(symbol):
    if not NEWSAPI_KEY or NEWSAPI_KEY == "your_newsapi_key_here":
        return {"score": 0, "label": "neutral", "headlines": [], "source": "newsapi_disabled"}
    try:
        url = "https://newsapi.org/v2/everything"
        params = {
            "q": symbol, "apiKey": NEWSAPI_KEY, "language": "en",
            "sortBy": "publishedAt", "pageSize": 20,
            "from": (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d"),
        }
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code != 200:
            return {"score": 0, "label": "neutral", "headlines": [], "source": "newsapi"}
        articles = resp.json().get("articles", [])
        b = 0; bear = 0; war = 0; bank = 0; headlines = []
        for a in articles[:15]:
            text = (a.get("title", "") + a.get("description", "")).lower()
            headlines.append(a.get("title", ""))
            for k in BULLISH_KEYWORDS:
                if k in text: b += 1
            for k in BEARISH_KEYWORDS:
                if k in text: bear += 1
            for k in WAR_KEYWORDS:
                if k in text: war += 1
            for k in BANKRUPTCY_KEYWORDS:
                if k in text: bank += 1
        total = b + bear
        score = round((b - bear) / max(total, 1) * 100) if total > 0 else 0
        return {
            "score": score,
            "label": "bullish" if score > 20 else ("bearish" if score < -20 else "neutral"),
            "bullish_hits": b, "bearish_hits": bear,
            "war_hits": war, "bank_hits": bank,
            "headlines": headlines[:5], "source": "newsapi",
        }
    except Exception as e:
        log.debug(f"newsapi {symbol}: {e}")
        return {"score": 0, "label": "neutral", "headlines": [], "source": "newsapi"}


def get_sec_signals(symbol):
    try:
        today     = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        url = (f'https://efts.sec.gov/LATEST/search-index?q="{symbol}"+"going+concern"'
               f'&dateRange=custom&startdt={yesterday}&enddt={today}&forms=8-K,10-Q,10-K')
        headers = {"User-Agent": "OBBot research@obbot.com"}
        resp = requests.get(url, headers=headers, timeout=10)
        gc = False; bank = False; filing_count = 0; signals = []
        if resp.status_code == 200:
            data = resp.json()
            hits = data.get("hits", {}).get("hits", [])
            filing_count = len(hits)
            for hit in hits[:5]:
                s = str(hit).lower()
                if "going concern" in s: gc = True; signals.append("⚠️ Going concern in filing")
                if "bankruptcy" in s or "chapter 11" in s: bank = True; signals.append("🚨 Bankruptcy filing")
        ds = 0
        if gc:             ds += 40
        if bank:           ds += 60
        if filing_count > 3: ds += 10
        return {
            "going_concern": gc, "bankruptcy": bank,
            "filing_count": filing_count, "distress_score": ds,
            "signals": signals[:3], "source": "sec",
        }
    except Exception as e:
        log.debug(f"sec {symbol}: {e}")
        return {"going_concern": False, "bankruptcy": False, "distress_score": 0, "signals": [], "source": "sec"}


def get_stocktwits_sentiment(symbol):
    """
    Fetches StockTwits stream for symbol, parses bull/bear sentiment from last 30 messages.
    No API key required. TTL: 15 min (rate limit conscious).
    Returns: {bull_pct, bear_pct, total_messages, labeled_count, source}
    """
    now = time.time()
    if symbol in _ST_CACHE:
        ts, cached = _ST_CACHE[symbol]
        if now - ts < TTL_STOCKTWITS:
            return cached

    try:
        url = f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
        headers = {"User-Agent": "Mozilla/5.0 (compatible; OBBot/1.0)"}
        resp = requests.get(url, headers=headers, timeout=8)

        if resp.status_code == 429:
            result = {
                "bull_pct": 50.0, "bear_pct": 50.0,
                "total_messages": 0, "labeled_count": 0,
                "source": "stocktwits_ratelimited"
            }
            _ST_CACHE[symbol] = (now, result)
            return result

        if resp.status_code != 200:
            result = {
                "bull_pct": 50.0, "bear_pct": 50.0,
                "total_messages": 0, "labeled_count": 0,
                "source": "stocktwits_unavailable"
            }
            _ST_CACHE[symbol] = (now, result)
            return result

        messages = resp.json().get("messages", [])
        total_messages = len(messages)

        bull_count = 0
        bear_count = 0
        for msg in messages[:30]:
            entities = msg.get("entities", {})
            sentiment = entities.get("sentiment", {})
            basic = sentiment.get("basic")
            if basic == "Bullish":
                bull_count += 1
            elif basic == "Bearish":
                bear_count += 1

        labeled_count = bull_count + bear_count
        if labeled_count > 0:
            bull_pct = bull_count / labeled_count * 100
            bear_pct = bear_count / labeled_count * 100
        else:
            bull_pct = 50.0
            bear_pct = 50.0

        result = {
            "bull_pct": round(bull_pct, 1),
            "bear_pct": round(bear_pct, 1),
            "total_messages": total_messages,
            "labeled_count": labeled_count,
            "source": "stocktwits"
        }
        _ST_CACHE[symbol] = (now, result)
        return result

    except Exception as e:
        log.debug(f"stocktwits {symbol}: {e}")
        result = {
            "bull_pct": 50.0, "bear_pct": 50.0,
            "total_messages": 0, "labeled_count": 0,
            "source": "stocktwits_error"
        }
        _ST_CACHE[symbol] = (now, result)
        return result


def get_wsb_mention_score(symbol, wsb_data=None):
    """
    Scores WSB mention rank. Pure computation on pre-fetched ApeWisdom data.
    wsb_data: list of {ticker, mentions, rank, rank_change_24h}
    Returns: {wsb_score, rank, mentions, rank_change_24h, in_wsb_top}
    No API call — no caching needed.
    """
    if not wsb_data:
        wsb_data = []

    # Find symbol in wsb_data
    target = None
    for w in wsb_data:
        if w.get("ticker") == symbol:
            target = w
            break

    if target is None:
        return {
            "wsb_score": 0, "rank": 0, "mentions": 0,
            "rank_change_24h": 0, "in_wsb_top": False
        }

    rank = target.get("rank", 0)
    mentions = target.get("mentions", 0)
    rank_change_24h = target.get("rank_change_24h", 0)

    # Base score by rank
    if rank <= 3:
        base = 80
    elif rank <= 10:
        base = 60
    elif rank <= 25:
        base = 40
    elif rank <= 50:
        base = 20
    else:
        base = 0

    # Rank change bonus
    if rank_change_24h > 20:
        change_bonus = 20
    elif rank_change_24h > 10:
        change_bonus = 15
    elif rank_change_24h > 5:
        change_bonus = 10
    elif rank_change_24h > 0:
        change_bonus = 5
    elif rank_change_24h < -10:
        change_bonus = -10
    elif rank_change_24h < -5:
        change_bonus = -5
    else:
        change_bonus = 0

    wsb_score = min(100, max(0, base + change_bonus))

    return {
        "wsb_score": wsb_score, "rank": rank, "mentions": mentions,
        "rank_change_24h": rank_change_24h, "in_wsb_top": True
    }


def get_social_sentiment(symbol, wsb_data=None):
    """
    Fast-path social sentiment: StockTwits + WSB only (no news, no SEC).
    Returns composite score 0-100 for use in quick decision making.
    TTL: 15 min.
    """
    now = time.time()
    if symbol in _SOCIAL_CACHE:
        ts, cached = _SOCIAL_CACHE[symbol]
        if now - ts < TTL_SOCIAL:
            return cached

    try:
        st = get_stocktwits_sentiment(symbol)
        wsb = get_wsb_mention_score(symbol, wsb_data or [])

        # Convert StockTwits bull_pct to 0-100 scale
        st_score = st.get("bull_pct", 50.0)

        # Composite: StockTwits 70%, WSB 30%
        composite_social_score = st_score * 0.70 + wsb.get("wsb_score", 0) * 0.30

        result = {
            "composite_social_score": round(composite_social_score, 1),
            "stocktwits_bull_pct": st.get("bull_pct", 50.0),
            "stocktwits_bear_pct": st.get("bear_pct", 50.0),
            "wsb_score": wsb.get("wsb_score", 0),
            "wsb_rank": wsb.get("rank", 0),
            "is_bearish_social": st.get("bear_pct", 50.0) > 65,
            "is_bullish_social": st.get("bull_pct", 50.0) > 65,
            "source": "social_composite"
        }
        _SOCIAL_CACHE[symbol] = (now, result)
        return result

    except Exception as e:
        log.debug(f"social_sentiment {symbol}: {e}")
        result = {
            "composite_social_score": 50.0,
            "stocktwits_bull_pct": 50.0,
            "stocktwits_bear_pct": 50.0,
            "wsb_score": 0,
            "wsb_rank": 0,
            "is_bearish_social": False,
            "is_bullish_social": False,
            "source": "social_error"
        }
        _SOCIAL_CACHE[symbol] = (now, result)
        return result


def _compute_full_sentiment(symbol):
    """Internal — do the actual API calls. New weight distribution includes social sentiment."""
    yf_s   = get_yfinance_sentiment(symbol)
    news_s = get_newsapi_sentiment(symbol)
    sec_s  = get_sec_signals(symbol)
    st_s   = get_stocktwits_sentiment(symbol)  # NEW
    wsb_s  = get_wsb_mention_score(symbol, [])  # NEW (no pre-fetched wsb_data here)

    # Convert StockTwits bull_pct (0-100) to -100..+100 sentiment scale
    # bull_pct=50 → neutral (0), bull_pct=80 → bullish (+60), bull_pct=20 → bearish (-60)
    st_score = (st_s.get("bull_pct", 50) - 50) * 2

    # WSB score 0-100, map to -60..+60 contribution
    wsb_contrib = (wsb_s.get("wsb_score", 0) - 50) * 1.2

    # New weight distribution: yf 20%, news 20%, ST 40%, wsb 10%, sec 10%
    composite = (
        yf_s.get("score",   0) * 0.20 +
        news_s.get("score", 0) * 0.20 +
        st_score               * 0.40 +
        wsb_contrib            * 0.10 +
        (-sec_s.get("distress_score", 0)) * 0.10
    )
    composite = max(-100, min(100, composite))
    war_hits  = yf_s.get("war_hits",  0) + news_s.get("war_hits",  0)
    bank_hits = yf_s.get("bank_hits", 0) + news_s.get("bank_hits", 0)
    return {
        "composite_score": round(composite, 1),
        "label": "bullish" if composite > 15 else ("bearish" if composite < -15 else "neutral"),
        "yfinance_score":  yf_s.get("score",  0),
        "newsapi_score":   news_s.get("score", 0),
        "stocktwits_bull_pct": st_s.get("bull_pct", 50.0),
        "stocktwits_bear_pct": st_s.get("bear_pct", 50.0),
        "wsb_score": wsb_s.get("wsb_score", 0),
        "sec_distress":    sec_s.get("distress_score", 0),
        "has_war_catalyst": war_hits >= 2,
        "has_bankruptcy":   bank_hits >= 3 or sec_s.get("bankruptcy") or sec_s.get("going_concern"),
        "top_headlines": (yf_s.get("headlines", [])[:2] + news_s.get("headlines", [])[:2]),
        "sec_signals":   sec_s.get("signals", []),
    }


def get_full_sentiment(symbol):
    """Public API — returns cached result if fresh, otherwise fetches."""
    now = time.time()
    if symbol in _SENT_CACHE:
        ts, cached = _SENT_CACHE[symbol]
        if now - ts < CACHE_TTL:
            log.debug(f"sentiment cache hit: {symbol}")
            return cached
    result = _compute_full_sentiment(symbol)
    _SENT_CACHE[symbol] = (now, result)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from dotenv import load_dotenv
    load_dotenv("/home/ubuntu/ob-bot/.env")
    print(json.dumps(get_full_sentiment("NVDA"), indent=2))
    # Second call should be instant (cache hit)
    print(json.dumps(get_full_sentiment("NVDA"), indent=2))
