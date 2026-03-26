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
CACHE_TTL = 1800  # 30 minutes


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


def _compute_full_sentiment(symbol):
    """Internal — do the actual API calls."""
    yf_s   = get_yfinance_sentiment(symbol)
    news_s = get_newsapi_sentiment(symbol)
    sec_s  = get_sec_signals(symbol)

    composite = (
        yf_s.get("score",   0) * 0.40 +
        news_s.get("score", 0) * 0.50 +
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
