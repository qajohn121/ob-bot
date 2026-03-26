#!/usr/bin/env python3
"""
learner.py — adaptive weight system.
Reads trade/autopsy history and adjusts scanner scoring weights
so the bot gets better with each closed trade.
"""
import json, logging, sqlite3
from datetime import datetime
from pathlib import Path

log     = logging.getLogger("ob.learner")
DB_PATH = Path("/home/ubuntu/ob-bot/data/trades.db")
WEIGHTS_PATH = Path("/home/ubuntu/ob-bot/data/weights.json")

# ── Default scoring weights (scanner uses these if no learned weights exist) ──
DEFAULT_WEIGHTS = {
    "momentum":  0.30,   # 4h move contribution to score
    "volume":    0.20,   # relative volume spike
    "rsi":       0.15,   # RSI position
    "iv_timing": 0.15,   # IV percentile (want low IV at entry)
    "sentiment": 0.10,   # news/SEC sentiment
    "sma_align": 0.10,   # price vs SMA50/SMA20 alignment
}

# Per-DTE minimum score thresholds (raised/lowered by learner)
DEFAULT_THRESHOLDS = {
    "0DTE":  75,
    "7DTE":  65,
    "21DTE": 60,
    "30DTE": 55,
    "60DTE": 50,
}

# ── Signal names that autopsy tracks ──────────────────────────────────────────
SIGNAL_NAMES = ["momentum", "volume", "iv_timing", "iv_rank", "hold_time", "low_score", "none"]


def _conn():
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def load_weights():
    """Load current learned weights, falling back to defaults."""
    try:
        if WEIGHTS_PATH.exists():
            data = json.loads(WEIGHTS_PATH.read_text())
            return (
                data.get("weights",    DEFAULT_WEIGHTS.copy()),
                data.get("thresholds", DEFAULT_THRESHOLDS.copy()),
            )
    except Exception as e:
        log.warning(f"load_weights: {e}")
    return DEFAULT_WEIGHTS.copy(), DEFAULT_THRESHOLDS.copy()


def save_weights(weights, thresholds, summary=""):
    WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    WEIGHTS_PATH.write_text(json.dumps({
        "weights":    weights,
        "thresholds": thresholds,
        "updated_at": datetime.now().isoformat(),
        "summary":    summary,
    }, indent=2))
    log.info("Weights saved.")


# ── Core analysis helpers ─────────────────────────────────────────────────────
def _signal_accuracy():
    """Return {signal: {win, loss, win_rate}} from autopsy table."""
    try:
        conn = _conn()
        rows = [dict(r) for r in conn.execute(
            "SELECT failed_signal, outcome, COUNT(*) as cnt FROM autopsy "
            "GROUP BY failed_signal, outcome"
        ).fetchall()]
        conn.close()
        signals = {}
        for r in rows:
            for s in (r["failed_signal"] or "none").split(", "):
                s = s.strip() or "none"
                if s not in signals:
                    signals[s] = {"win": 0, "loss": 0}
                if r["outcome"] == "WIN":
                    signals[s]["win"] += r["cnt"]
                else:
                    signals[s]["loss"] += r["cnt"]
        result = {}
        for s, v in signals.items():
            total = v["win"] + v["loss"]
            result[s] = {
                "win": v["win"], "loss": v["loss"], "total": total,
                "win_rate": round(v["win"] / max(total, 1) * 100, 1),
            }
        return result
    except Exception as e:
        log.error(f"_signal_accuracy: {e}"); return {}


def _dte_ev():
    """Return {profile: {trades, win_rate, avg_win, avg_loss, ev}} from trades."""
    try:
        conn = _conn()
        rows = [dict(r) for r in conn.execute(
            "SELECT dte_profile, status, pnl_pct FROM trades "
            "WHERE status IN ('WIN','LOSS')"
        ).fetchall()]
        conn.close()
        profiles = {}
        for r in rows:
            p = (r["dte_profile"] or "30DTE")
            if p not in profiles:
                profiles[p] = {"pnl": [], "wins": 0, "losses": 0}
            pnl = r["pnl_pct"] or 0
            profiles[p]["pnl"].append(pnl)
            if r["status"] == "WIN":
                profiles[p]["wins"] += 1
            else:
                profiles[p]["losses"] += 1
        result = {}
        for p, v in profiles.items():
            total = v["wins"] + v["losses"]
            wr    = round(v["wins"] / max(total, 1) * 100, 1)
            wp    = [x for x in v["pnl"] if x > 0]
            lp    = [x for x in v["pnl"] if x <= 0]
            aw    = round(sum(wp) / len(wp), 1) if wp else 0
            al    = round(sum(lp) / len(lp), 1) if lp else 0
            ev    = round((wr / 100 * aw) + ((1 - wr / 100) * al), 1)
            result[p] = {
                "trades": total, "win_rate": wr,
                "avg_win": aw, "avg_loss": al, "ev": ev,
            }
        return result
    except Exception as e:
        log.error(f"_dte_ev: {e}"); return {}


# ── Weight adjustment logic ────────────────────────────────────────────────────
def _adjust_weights(weights, signal_acc):
    """
    Nudge weights up/down based on which signals correlate with wins vs losses.
    Cap each weight at [0.05, 0.50] and re-normalise to sum=1.
    """
    new_w = dict(weights)
    notes = []

    # Map autopsy signal names → weight keys
    signal_to_weight = {
        "momentum":  "momentum",
        "volume":    "volume",
        "iv_timing": "iv_timing",
        "iv_rank":   "iv_timing",   # same bucket
        "hold_time": "momentum",    # bad timing = momentum issue
        "low_score": "sentiment",   # proxy
    }

    for sig, acc in signal_acc.items():
        wkey = signal_to_weight.get(sig)
        if not wkey or acc["total"] < 3:   # need ≥3 data points
            continue
        wr = acc["win_rate"]
        if wr < 35:        # this signal failing badly → down-weight it
            adj = -0.03
            notes.append(f"{wkey} ↓ (signal '{sig}' win_rate={wr:.0f}%)")
        elif wr > 65:      # this signal working well → up-weight it
            adj = +0.02
            notes.append(f"{wkey} ↑ (signal '{sig}' win_rate={wr:.0f}%)")
        else:
            continue
        new_w[wkey] = round(min(0.50, max(0.05, new_w.get(wkey, 0.15) + adj)), 3)

    # Re-normalise so weights sum to 1
    total = sum(new_w.values())
    if total > 0:
        new_w = {k: round(v / total, 4) for k, v in new_w.items()}

    return new_w, notes


def _adjust_thresholds(thresholds, dte_ev):
    """
    Raise minimum score threshold for DTE tiers with negative EV (losing money),
    lower for tiers with strong positive EV and enough trade count.
    """
    new_t = dict(thresholds)
    notes = []
    for profile, stats in dte_ev.items():
        if stats["trades"] < 5:    # need ≥5 closed trades to trust the data
            continue
        ev = stats["ev"]
        cur = new_t.get(profile, 55)
        if ev < -10:               # losing badly → raise the bar
            new_t[profile] = min(cur + 3, 85)
            notes.append(f"{profile} threshold ↑{new_t[profile]} (EV={ev:+.1f}%)")
        elif ev > 20 and stats["win_rate"] > 60:
            new_t[profile] = max(cur - 2, 40)
            notes.append(f"{profile} threshold ↓{new_t[profile]} (EV={ev:+.1f}%)")
    return new_t, notes


# ── Public API ────────────────────────────────────────────────────────────────
def run_learning_cycle():
    """
    Main entry point. Analyses all autopsy rows, adjusts weights + thresholds,
    saves to weights.json, logs to learning_cycles table, and returns a summary dict.
    """
    signal_acc = _signal_accuracy()
    dte_ev_map = _dte_ev()

    weights, thresholds = load_weights()
    new_weights, w_notes = _adjust_weights(weights, signal_acc)
    new_thresholds, t_notes = _adjust_thresholds(thresholds, dte_ev_map)

    all_notes = w_notes + t_notes
    summary = "; ".join(all_notes) if all_notes else "No significant adjustments"

    save_weights(new_weights, new_thresholds, summary)

    # Persist to learning_cycles table
    try:
        conn = _conn()
        total_trades = conn.execute("SELECT COUNT(*) FROM trades WHERE status IN ('WIN','LOSS')").fetchone()[0]
        wins         = conn.execute("SELECT COUNT(*) FROM trades WHERE status='WIN'").fetchone()[0]
        pnl_rows     = [r[0] for r in conn.execute("SELECT pnl_pct FROM trades WHERE status IN ('WIN','LOSS') AND pnl_pct IS NOT NULL").fetchall()]
        avg_pnl      = round(sum(pnl_rows) / len(pnl_rows), 1) if pnl_rows else 0
        win_rate     = round(wins / max(total_trades, 1) * 100, 1)

        best_sig = max(signal_acc.items(), key=lambda x: x[1]["win_rate"], default=(None,{}))[0] if signal_acc else None
        worst_sig= min(signal_acc.items(), key=lambda x: x[1]["win_rate"], default=(None,{}))[0] if signal_acc else None

        conn.execute("""INSERT INTO learning_cycles
            (ran_at, total_trades, win_rate, avg_pnl, best_signal, worst_signal, adjustments, summary)
            VALUES (?,?,?,?,?,?,?,?)""",
            (datetime.now().isoformat(), total_trades, win_rate, avg_pnl,
             best_sig, worst_sig, json.dumps(all_notes), summary))
        conn.commit(); conn.close()
    except Exception as e:
        log.error(f"learning_cycles insert: {e}")

    result = {
        "weights":    new_weights,
        "thresholds": new_thresholds,
        "signal_accuracy": signal_acc,
        "dte_ev":     dte_ev_map,
        "adjustments": all_notes,
        "summary":    summary,
    }
    log.info(f"Learning cycle done: {summary}")
    return result


def get_learning_history(limit=10):
    """Return recent learning cycle rows for dashboard display."""
    try:
        conn = _conn()
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM learning_cycles ORDER BY ran_at DESC LIMIT ?", (limit,)
        ).fetchall()]
        conn.close()
        return rows
    except:
        return []


def get_ev_by_dte():
    """Wrapper for dashboard use."""
    return _dte_ev()


def get_signal_summary():
    """Wrapper returning sorted list for dashboard."""
    acc = _signal_accuracy()
    out = []
    for sig, v in acc.items():
        out.append({"signal": sig, "win_rate": v["win_rate"],
                    "total": v["total"], "win": v["win"], "loss": v["loss"]})
    return sorted(out, key=lambda x: x["total"], reverse=True)


if __name__ == "__main__":
    import logging as _log
    _log.basicConfig(level=logging.INFO)
    result = run_learning_cycle()
    print("=== Learning Cycle Complete ===")
    print(f"Summary: {result['summary']}")
    print("\nUpdated weights:")
    for k, v in result["weights"].items():
        print(f"  {k:12s}: {v:.4f}")
    print("\nDTE Expected Value:")
    for p, s in result["dte_ev"].items():
        if s["trades"] > 0:
            print(f"  {p:6s}: {s['trades']} trades | WR {s['win_rate']:.0f}% | EV {s['ev']:+.1f}%")
    print("\nSignal Accuracy:")
    for s in get_signal_summary():
        print(f"  {s['signal']:12s}: WR {s['win_rate']:.0f}% ({s['total']} trades)")
