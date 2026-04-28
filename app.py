import os
import json
import traceback
import requests
from datetime import datetime
from flask import Flask, jsonify, request
from flask_cors import CORS
import yfinance as yf
import numpy as np

app = Flask(__name__)
CORS(app)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "https://sree789p.github.io/nifty-ai-backend")

CACHE = {}
CACHE_TTL = 300
SIGNAL_HISTORY_FILE = "/tmp/signal_history.json"
ALERTS_FILE = "/tmp/price_alerts.json"
STATE_FILE = "/tmp/last_state.json"

TICKERS = {}
TICKERS["nifty"]     = "^NSEI"
TICKERS["banknifty"] = "^NSEBANK"
TICKERS["nasdaq"]    = "^IXIC"
TICKERS["sp500"]     = "^GSPC"
TICKERS["nikkei"]    = "^N225"
TICKERS["hangseng"]  = "^HSI"
TICKERS["usd_inr"]   = "INR=X"
TICKERS["crude"]     = "CL=F"
TICKERS["gold"]      = "GC=F"
TICKERS["vix"]       = "^VIX"
TICKERS["india_vix"] = "^INDIAVIX"

# ── DATA FETCH ────────────────────────────────────────────────

def fetch_data(symbol):
    try:
        t = yf.Ticker(symbol)
        hist = t.history(period="30d", interval="1d")
        if hist.empty:
            return {"price": None, "change_pct": None, "rsi": 50.0, "above_sma20": 0, "change_1w": None}
        close = hist["Close"].dropna()
        price = round(float(close.iloc[-1]), 2)
        change_pct = None
        if len(close) >= 2:
            prev = float(close.iloc[-2])
            change_pct = round((price - prev) / prev * 100, 2)
        rsi = 50.0
        if len(close) >= 14:
            delta = close.diff()
            gain = delta.clip(lower=0).tail(14).mean()
            loss = -delta.clip(upper=0).tail(14).mean()
            if loss and loss > 1e-6:
                rs = gain / loss
                rsi = round(float(100 - (100 / (1 + rs))), 1)
            else:
                rsi = 100.0
        above_sma20 = 0
        if len(close) >= 20:
            sma20 = float(close.tail(20).mean())
            above_sma20 = 1 if price and price > sma20 else 0
        change_1w = None
        if len(close) >= 6:
            prev_week = float(close.iloc[-6])
            change_1w = round((price - prev_week) / prev_week * 100, 2)
        return {
            "price": price,
            "change_pct": change_pct,
            "change_1w": change_1w,
            "rsi": rsi,
            "above_sma20": above_sma20
        }
    except Exception as e:
        print("fetch error " + symbol + ": " + str(e))
        return {"price": None, "change_pct": None, "rsi": 50.0, "above_sma20": 0, "change_1w": None}

def get_cached(symbol):
    now = datetime.now().timestamp()
    if symbol in CACHE:
        data, ts = CACHE[symbol]
        if now - ts < CACHE_TTL:
            return data
    data = fetch_data(symbol)
    CACHE[symbol] = (data, now)
    return data

# ── PCR via NSE ───────────────────────────────────────────────

PCR_CACHE = {"data": None, "ts": 0}

def fetch_pcr():
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Referer": "https://www.nseindia.com"
        }
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=headers, timeout=10)
        url = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"
        res = session.get(url, headers=headers, timeout=15)
        if res.status_code != 200:
            return {"pcr": None, "signal": "neutral", "detail": "NSE API unavailable"}
        data = res.json()
        records = data.get("records", {}).get("data", [])
        put_oi = sum(r.get("PE", {}).get("openInterest", 0) for r in records if "PE" in r)
        call_oi = sum(r.get("CE", {}).get("openInterest", 0) for r in records if "CE" in r)
        if call_oi == 0:
            return {"pcr": None, "signal": "neutral", "detail": "No OI data"}
        pcr = round(put_oi / call_oi, 2)
        if pcr > 1.2:
            signal = "bullish"
            detail = "High put OI — smart money hedged"
        elif pcr < 0.8:
            signal = "bearish"
            detail = "Low put OI — downside risk"
        else:
            signal = "neutral"
            detail = "Balanced OI"
        return {"pcr": pcr, "put_oi": int(put_oi), "call_oi": int(call_oi), "signal": signal, "detail": detail}
    except Exception as e:
        print("PCR error: " + str(e))
        return {"pcr": None, "signal": "neutral", "detail": "PCR unavailable"}

def get_cached_pcr():
    now = datetime.now().timestamp()
    if PCR_CACHE["data"] and now - PCR_CACHE["ts"] < 600:
        return PCR_CACHE["data"]
    data = fetch_pcr()
    PCR_CACHE["data"] = data
    PCR_CACHE["ts"] = now
    return data

# ── FEATURES ─────────────────────────────────────────────────

def compute_features(market):
    def chg(key):
        return market.get(key, {}).get("change_pct") or 0.0
    def pr(key):
        return market.get(key, {}).get("price") or 0.0
    f = {}
    f["nifty_change"]    = chg("nifty")
    f["nifty_change_1w"] = market.get("nifty", {}).get("change_1w") or 0.0
    f["nifty_rsi"]       = market.get("nifty", {}).get("rsi") or 50.0
    f["nifty_sma"]       = market.get("nifty", {}).get("above_sma20") or 0
    f["nasdaq_change"]   = chg("nasdaq")
    f["sp500_change"]    = chg("sp500")
    f["nikkei_change"]   = chg("nikkei")
    f["hangseng_change"] = chg("hangseng")
    f["usd_inr_change"]  = chg("usd_inr")
    f["crude_change"]    = chg("crude")
    f["gold_change"]     = chg("gold")
    f["us_vix"]          = pr("vix") or 15.0
    f["india_vix"]       = pr("india_vix") or 14.0
    f["rsi_signal"]      = 1 if f["nifty_rsi"] < 35 else (-1 if f["nifty_rsi"] > 65 else 0)
    f["trend_signal"]    = 1 if f["nifty_sma"] else -1
    f["asia_bull"]       = 1 if (f["nikkei_change"] > 0.5 and f["hangseng_change"] > 0.5) else 0
    f["asia_bear"]       = 1 if (f["nikkei_change"] < -0.5 and f["hangseng_change"] < -0.5) else 0
    f["global_bull"]     = 1 if (f["nasdaq_change"] > 0.5 and f["sp500_change"] > 0.3) else 0
    f["global_risk"]     = 1 if (f["us_vix"] > 20 or f["crude_change"] > 2.5) else 0
    f["gold_fear"]       = 1 if (f["gold_change"] > 1.0 and f["us_vix"] > 18) else 0
    f["weekly_trend"]    = 1 if f["nifty_change_1w"] > 1.5 else (-1 if f["nifty_change_1w"] < -1.5 else 0)
    return f

def detect_regime(f):
    if f["india_vix"] > 20 or f["us_vix"] > 25:
        return "volatile"
    if f["nifty_rsi"] < 35:
        return "oversold"
    if f["nifty_rsi"] > 65:
        return "overbought"
    if f["nifty_sma"] and f["nifty_change_1w"] > 2.0:
        return "trending"
    return "range"

WEIGHTS = {}
WEIGHTS["nifty_change"]    =  0.18
WEIGHTS["nasdaq_change"]   =  0.12
WEIGHTS["sp500_change"]    =  0.08
WEIGHTS["nikkei_change"]   =  0.08
WEIGHTS["hangseng_change"] =  0.06
WEIGHTS["usd_inr_change"]  = -0.10
WEIGHTS["crude_change"]    = -0.08
WEIGHTS["global_risk"]     = -0.10
WEIGHTS["global_bull"]     =  0.08
WEIGHTS["asia_bull"]       =  0.06
WEIGHTS["asia_bear"]       = -0.06
WEIGHTS["rsi_signal"]      =  0.08
WEIGHTS["trend_signal"]    =  0.06
WEIGHTS["weekly_trend"]    =  0.05
WEIGHTS["gold_fear"]       = -0.05

def compute_signal(f, regime):
    score = 0.5
    continuous = ["nifty_change","nasdaq_change","sp500_change","nikkei_change","hangseng_change","usd_inr_change","crude_change"]
    for feat in WEIGHTS:
        val = f.get(feat, 0)
        if feat in continuous:
            val = max(-3, min(3, val)) / 3.0
        score += WEIGHTS[feat] * val
    if regime == "volatile":
        score = 0.5 + (score - 0.5) * 0.5
    elif regime == "trending":
        score = 0.5 + (score - 0.5) * 1.3
    elif regime == "oversold":
        score = max(score, 0.55)
    elif regime == "overbought":
        score = min(score, 0.45)
    score = max(0.05, min(0.95, score))
    if score > 0.62:
        direction = "bullish"
    elif score < 0.38:
        direction = "bearish"
    else:
        direction = "neutral"
    return {"direction": direction, "probability": round(score, 4), "confidence": round(score * 100, 1)}

# ── EDGE SCORE (single source of truth) ──────────────────────

def compute_edge(direction, conf, regime, rsi, vix):
    score = 0
    if conf > 70:
        score += 3
    elif conf > 62:
        score += 2
    elif conf > 55:
        score += 1
    if regime == "trending":
        score += 2
    elif regime == "range":
        score += 1
    elif regime == "volatile":
        score -= 1
    if rsi < 35 and direction == "bullish":
        score += 2
    elif rsi > 65 and direction == "bearish":
        score += 2
    elif 40 < rsi < 60:
        score += 1
    elif rsi > 72 or rsi < 28:
        score -= 1
    if vix < 14:
        score += 2
    elif vix < 18:
        score += 1
    elif vix > 20:
        score -= 1
    return round(max(0, min(10, score)), 1)

def get_trade_state(edge):
    if edge >= 6:
        return "TRADE_READY"
    elif edge >= 3:
        return "SETUP_FORMING"
    else:
        return "NO_TRADE"

# ── EXECUTION PLAN — only when trade ready ───────────────────

def compute_execution(price, vix, direction, regime):
    if not price or not vix:
        return None
    daily_move = price * (vix / 100) * (1 / (252 ** 0.5))
    mult = 1.5 if regime == "volatile" else 1.2 if regime == "trending" else 1.0
    move = round(daily_move * mult)
    sl_pct = 0.003
    t1_pct = 0.004
    t2_pct = 0.007
    if direction == "bullish":
        entry_low = round(price - move * 0.3)
        entry_high = round(price)
        sl = round(price * (1 - sl_pct))
        t1 = round(price * (1 + t1_pct))
        t2 = round(price * (1 + t2_pct))
    else:
        entry_low = round(price)
        entry_high = round(price + move * 0.3)
        sl = round(price * (1 + sl_pct))
        t1 = round(price * (1 - t1_pct))
        t2 = round(price * (1 - t2_pct))
    rr = round(t1_pct / sl_pct, 1)
    range_low = round(price - move)
    range_high = round(price + move)
    return {
        "entry": str(entry_low) + " - " + str(entry_high),
        "sl": str(sl),
        "t1": str(t1),
        "t2": str(t2),
        "rr": "1:" + str(rr),
        "range": str(range_low) + " - " + str(range_high)
    }

def build_drivers(f):
    drivers = []
    n = f["nifty_change"]
    drivers.append({
        "icon": "📊", "tag": "pos" if n >= 0 else "neg", "impact": "HIGH",
        "label": "Nifty " + ("up " if n >= 0 else "down ") + str(abs(round(n,2))) + "% | RSI " + str(round(f["nifty_rsi"],0)),
        "detail": str(round(f["nifty_change_1w"],2)) + "% weekly"
    })
    nq = f["nasdaq_change"]; sp = f["sp500_change"]
    drivers.append({
        "icon": "📈" if nq >= 0 else "📉", "tag": "pos" if nq >= 0 else "neg", "impact": "MEDIUM",
        "label": "Nasdaq " + str(round(nq,2)) + "% | S&P " + str(round(sp,2)) + "%",
        "detail": "US markets"
    })
    nk = f["nikkei_change"]; hs = f["hangseng_change"]
    drivers.append({
        "icon": "🌏", "tag": "pos" if (nk+hs) >= 0 else "neg", "impact": "LOW",
        "label": "Nikkei " + str(round(nk,2)) + "% | HangSeng " + str(round(hs,2)) + "%",
        "detail": "Asian markets"
    })
    fx = f["usd_inr_change"]
    drivers.append({
        "icon": "💱", "tag": "neg" if fx > 0.2 else "pos", "impact": "MEDIUM",
        "label": "INR " + ("weakening" if fx > 0.2 else "stable"),
        "detail": "USD/INR " + str(round(fx,2)) + "%"
    })
    cr = f["crude_change"]
    drivers.append({
        "icon": "🛢", "tag": "neg" if cr > 1 else "pos", "impact": "HIGH",
        "label": "Crude " + ("rising" if cr > 1 else "stable"),
        "detail": str(round(cr,2)) + "%"
    })
    vix = f["india_vix"]
    drivers.append({
        "icon": "⚡" if vix > 18 else "😌", "tag": "neg" if vix > 18 else "pos", "impact": "HIGH",
        "label": "India VIX " + str(round(vix,1)) + " | US VIX " + str(round(f["us_vix"],1)),
        "detail": "elevated" if vix > 18 else "calm"
    })
    return drivers

def get_sparkline():
    try:
        t = yf.Ticker("^NSEI")
        hist = t.history(period="1mo", interval="1d")
        closes = hist["Close"].dropna().tail(20).tolist()
        return [round(float(v), 2) for v in closes]
    except Exception:
        return []

def run_backtest():
    try:
        t = yf.Ticker("^NSEI")
        hist = t.history(period="3mo", interval="1d")
        if hist.empty or len(hist) < 30:
            return {"error": "Not enough data"}
        close = hist["Close"].dropna()
        correct = 0; total = 0; last_10 = []
        for i in range(20, len(close) - 1):
            current = float(close.iloc[i])
            next_day = float(close.iloc[i + 1])
            actual = "bullish" if next_day > current else "bearish"
            delta = close.diff()
            gain = delta.clip(lower=0).iloc[i-14:i].mean()
            loss = -delta.clip(upper=0).iloc[i-14:i].mean()
            rsi = 50.0
            if loss and loss > 1e-6:
                rsi = float(100 - (100 / (1 + gain / loss)))
            sma20 = float(close.iloc[i-20:i].mean())
            chg = float((current - float(close.iloc[i-1])) / float(close.iloc[i-1]) * 100)
            chg5 = float((current - float(close.iloc[i-5])) / float(close.iloc[i-5]) * 100)
            score = 0.5
            score += 0.25 * max(-1, min(1, chg / 3.0))
            score += 0.10 * max(-1, min(1, chg5 / 5.0))
            if rsi < 35: score += 0.10
            elif rsi > 65: score -= 0.10
            score += 0.08 if current > sma20 else -0.08
            score = max(0.05, min(0.95, score))
            predicted = "bullish" if score > 0.5 else "bearish"
            if predicted == actual: correct += 1
            total += 1
            if i >= len(close) - 11:
                row = {}
                row["date"] = hist.index[i].strftime("%Y-%m-%d")
                row["predicted"] = predicted; row["actual"] = actual
                row["correct"] = predicted == actual
                row["confidence"] = round(score * 100, 1)
                last_10.append(row)
        accuracy = round(correct / total * 100, 1) if total > 0 else 0
        return {
            "accuracy": accuracy, "total_days": total, "correct": correct,
            "last_10": last_10, "summary": "Model correct " + str(accuracy) + "% over 3 months"
        }
    except Exception as e:
        traceback.print_exc()
        return {"error": str(e)}

# ── SIGNAL HISTORY ────────────────────────────────────────────

def load_history():
    try:
        if os.path.exists(SIGNAL_HISTORY_FILE):
            with open(SIGNAL_HISTORY_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return []

def save_signal_history(signal, nifty_price, regime, edge):
    try:
        history = load_history()
        entry = {
            "timestamp": datetime.now().isoformat(),
            "date": datetime.now().strftime("%d %b %Y %H:%M"),
            "direction": signal.get("direction", "neutral"),
            "confidence": signal.get("confidence", 0),
            "nifty_price": nifty_price,
            "regime": regime,
            "edge": edge
        }
        history.append(entry)
        history = history[-50:]
        with open(SIGNAL_HISTORY_FILE, "w") as f:
            json.dump(history, f)
    except Exception as e:
        print("History save error: " + str(e))

def evaluate_history():
    try:
        history = load_history()
        if len(history) < 2:
            return history
        for i in range(len(history) - 1):
            entry = history[i]
            next_entry = history[i + 1]
            if "outcome" not in entry:
                curr_price = entry.get("nifty_price", 0) or 0
                next_price = next_entry.get("nifty_price", 0) or 0
                if curr_price and next_price:
                    actual = "bullish" if next_price > curr_price else "bearish"
                    predicted = entry.get("direction", "neutral")
                    if predicted == "neutral":
                        entry["outcome"] = "neutral"
                    else:
                        entry["outcome"] = "correct" if predicted == actual else "wrong"
        return history
    except Exception as e:
        print("History eval error: " + str(e))
        return []

# ── PRICE ALERTS ──────────────────────────────────────────────

def load_alerts():
    try:
        if os.path.exists(ALERTS_FILE):
            with open(ALERTS_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return []

def save_alerts(alerts):
    try:
        with open(ALERTS_FILE, "w") as f:
            json.dump(alerts, f)
    except Exception as e:
        print("Alert save error: " + str(e))

def check_price_alerts(nifty_price):
    if not nifty_price:
        return
    alerts = load_alerts()
    remaining = []
    for alert in alerts:
        level = alert.get("level", 0)
        direction = alert.get("direction", "above")
        triggered = (direction == "above" and nifty_price >= level) or \
                    (direction == "below" and nifty_price <= level)
        if triggered:
            msg = "NIFTY PRICE ALERT\n\n"
            msg += "Nifty " + direction + " " + str(level) + " triggered!\n"
            msg += "Current: " + str(nifty_price) + "\n"
            msg += "Time: " + datetime.now().strftime("%d %b %Y %H:%M IST")
            send_telegram(msg)
        else:
            remaining.append(alert)
    if len(remaining) != len(alerts):
        save_alerts(remaining)

# ── STATE TRACKING with anti-noise filter ────────────────────

def load_state():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {"trade_state": "NO_TRADE", "edge": 0, "cycle_count": 0, "pending_state": None, "pending_count": 0}

def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print("State save error: " + str(e))

def check_state_change(new_state, new_edge, data):
    last = load_state()
    old_state = last.get("trade_state", "NO_TRADE")
    pending_state = last.get("pending_state", None)
    pending_count = last.get("pending_count", 0)
    alert_sent = False

    # Anti-noise: confirm state change for 2 cycles before alerting
    if new_state != old_state:
        if pending_state == new_state:
            pending_count += 1
        else:
            pending_state = new_state
            pending_count = 1

        if pending_count >= 2:
            # State confirmed — send alert
            msg = build_state_change_msg(old_state, new_state, new_edge, data)
            if msg:
                send_telegram(msg)
                alert_sent = True
            save_state({
                "trade_state": new_state,
                "edge": new_edge,
                "cycle_count": 0,
                "pending_state": None,
                "pending_count": 0
            })
        else:
            save_state({
                "trade_state": old_state,
                "edge": new_edge,
                "cycle_count": last.get("cycle_count", 0),
                "pending_state": pending_state,
                "pending_count": pending_count
            })
    else:
        save_state({
            "trade_state": old_state,
            "edge": new_edge,
            "cycle_count": last.get("cycle_count", 0) + 1,
            "pending_state": None,
            "pending_count": 0
        })
    return alert_sent

# ── TELEGRAM MESSAGES — clean formatted ──────────────────────

def send_telegram(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return False, "Missing env vars"
    url = "https://api.telegram.org/bot" + TELEGRAM_TOKEN + "/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message}
    try:
        res = requests.post(url, json=payload, timeout=30)
        return (True, "sent") if res.status_code == 200 else (False, res.text)
    except Exception as e:
        return False, str(e)

def get_why_no_trade(f, vix, rsi, regime):
    reasons = []
    if vix > 20:
        reasons.append("VIX at " + str(round(vix,1)) + " (high volatility)")
    elif vix > 18:
        reasons.append("VIX at " + str(round(vix,1)) + " (elevated)")
    if rsi > 70:
        reasons.append("RSI at " + str(round(rsi,1)) + " (overbought)")
    elif rsi < 30:
        reasons.append("RSI at " + str(round(rsi,1)) + " (oversold, risky)")
    if f.get("crude_change", 0) > 2:
        reasons.append("Crude up " + str(round(f["crude_change"],1)) + "% (inflation risk)")
    if f.get("global_risk", 0):
        reasons.append("Global risk elevated")
    if regime == "volatile":
        reasons.append("Market in volatile regime")
    if not reasons:
        reasons.append("Edge too low — unclear direction")
    return reasons

def build_no_trade_msg(data, session):
    sig = data.get("signal", {})
    mkt = data.get("market", {})
    f = data.get("features", {})
    edge = data.get("edge", 0)
    nifty = mkt.get("nifty", {})
    price = nifty.get("price", 0) or 0
    chg = nifty.get("change_pct", 0) or 0
    vix = (mkt.get("india_vix", {}) or {}).get("price", 0) or 0
    rsi = nifty.get("rsi", 0) or 0
    regime = data.get("regime", "range")
    arrow = "+" if chg >= 0 else ""
    session_text = "MORNING" if session == "morning" else "EVENING"
    date_str = datetime.now().strftime("%d %b %Y")
    reasons = get_why_no_trade(f, vix, rsi, regime)
    lines = []
    lines.append("NIFTY AI - INTRADAY DECISION")
    lines.append(session_text + " | " + date_str)
    lines.append("")
    lines.append("NO TRADE ZONE")
    lines.append("Edge: " + str(edge) + " / 10  |  Confidence: LOW")
    lines.append("")
    lines.append("Why not trading:")
    for r in reasons[:3]:
        lines.append("  - " + r)
    lines.append("")
    lines.append("Action:")
    lines.append("  - Stay in cash")
    lines.append("  - Wait for edge > 3")
    lines.append("  - Next update in 5 min")
    lines.append("")
    lines.append("Nifty: " + str(price) + " (" + arrow + str(chg) + "%)")
    lines.append("Dashboard: " + DASHBOARD_URL)
    return "\n".join(lines)

def build_setup_forming_msg(data, session):
    sig = data.get("signal", {})
    mkt = data.get("market", {})
    edge = data.get("edge", 0)
    nifty = mkt.get("nifty", {})
    price = nifty.get("price", 0) or 0
    chg = nifty.get("change_pct", 0) or 0
    rsi = nifty.get("rsi", 0) or 0
    vix = (mkt.get("india_vix", {}) or {}).get("price", 0) or 0
    direction = sig.get("direction", "neutral").upper()
    session_text = "MORNING" if session == "morning" else "EVENING"
    date_str = datetime.now().strftime("%d %b %Y")
    arrow = "+" if chg >= 0 else ""
    lines = []
    lines.append("NIFTY AI - INTRADAY DECISION")
    lines.append(session_text + " | " + date_str)
    lines.append("")
    lines.append("SETUP FORMING")
    lines.append("Edge: " + str(edge) + " / 10  |  Bias: " + direction)
    lines.append("")
    lines.append("Market improving - not ready yet")
    lines.append("Watch for:")
    if direction == "BULLISH":
        lines.append("  - Pullback to support")
        lines.append("  - VIX below 16")
    else:
        lines.append("  - Rally to resistance")
        lines.append("  - VIX staying elevated")
    lines.append("")
    lines.append("Do NOT trade yet")
    lines.append("Wait for edge > 6")
    lines.append("")
    lines.append("Nifty: " + str(price) + " (" + arrow + str(chg) + "%)")
    lines.append("RSI: " + str(rsi) + "  |  VIX: " + str(vix))
    lines.append("Dashboard: " + DASHBOARD_URL)
    return "\n".join(lines)

def build_trade_ready_msg(data, session):
    sig = data.get("signal", {})
    mkt = data.get("market", {})
    edge = data.get("edge", 0)
    exec_plan = data.get("execution", {}) or {}
    nifty = mkt.get("nifty", {})
    price = nifty.get("price", 0) or 0
    chg = nifty.get("change_pct", 0) or 0
    rsi = nifty.get("rsi", 0) or 0
    vix = (mkt.get("india_vix", {}) or {}).get("price", 0) or 0
    direction = sig.get("direction", "neutral").upper()
    conf = sig.get("confidence", 0)
    session_text = "MORNING" if session == "morning" else "EVENING"
    date_str = datetime.now().strftime("%d %b %Y")
    arrow = "+" if chg >= 0 else ""
    dir_emoji = "BULLISH" if direction == "BULLISH" else "BEARISH"
    lines = []
    lines.append("NIFTY AI - INTRADAY DECISION")
    lines.append(session_text + " | " + date_str)
    lines.append("")
    lines.append("TRADE READY")
    lines.append("Bias: " + dir_emoji + "  |  Edge: " + str(edge) + " / 10")
    lines.append("Confidence: " + str(conf) + "%")
    lines.append("")
    if exec_plan:
        lines.append("EXECUTION PLAN:")
        lines.append("  Entry:   " + str(exec_plan.get("entry", "-")))
        lines.append("  SL:      " + str(exec_plan.get("sl", "-")))
        lines.append("  Target1: " + str(exec_plan.get("t1", "-")))
        lines.append("  Target2: " + str(exec_plan.get("t2", "-")))
        lines.append("  R:R      " + str(exec_plan.get("rr", "-")))
        lines.append("  Range:   " + str(exec_plan.get("range", "-")))
        lines.append("")
    lines.append("Rules:")
    lines.append("  - Exit T1 partially")
    lines.append("  - Move SL to entry after T1")
    lines.append("  - Exit all by 3:15 PM IST")
    lines.append("  - Not financial advice")
    lines.append("")
    lines.append("Nifty: " + str(price) + " (" + arrow + str(chg) + "%)")
    lines.append("Dashboard: " + DASHBOARD_URL)
    return "\n".join(lines)

def build_daily_review_msg(data):
    sig = data.get("signal", {})
    mkt = data.get("market", {})
    edge = data.get("edge", 0)
    regime = data.get("regime", "range")
    nifty = mkt.get("nifty", {})
    price = nifty.get("price", 0) or 0
    chg = nifty.get("change_pct", 0) or 0
    rsi = nifty.get("rsi", 0) or 0
    vix = (mkt.get("india_vix", {}) or {}).get("price", 0) or 0
    direction = sig.get("direction", "neutral")
    trade_state = get_trade_state(edge)
    date_str = datetime.now().strftime("%d %b %Y")
    arrow = "+" if chg >= 0 else ""
    regime_map = {
        "volatile": "High Volatility", "trending": "Trending",
        "oversold": "Oversold", "overbought": "Overbought", "range": "Range Bound"
    }
    regime_text = regime_map.get(regime, regime.title())
    lines = []
    lines.append("NIFTY AI - DAILY REVIEW")
    lines.append(date_str)
    lines.append("")
    lines.append("Nifty: " + str(price) + " (" + arrow + str(chg) + "%)")
    lines.append("VIX: " + str(vix) + "  |  RSI: " + str(rsi))
    lines.append("Regime: " + regime_text)
    lines.append("Edge: " + str(edge) + " / 10")
    lines.append("")
    if trade_state == "NO_TRADE":
        lines.append("Today: NO TRADE")
        lines.append("")
        lines.append("Why:")
        if vix > 18:
            lines.append("  - High VIX (" + str(vix) + ")")
        if rsi > 65:
            lines.append("  - Overbought RSI")
        elif rsi < 35:
            lines.append("  - Oversold RSI")
        if edge < 3:
            lines.append("  - Edge below threshold")
        lines.append("")
        lines.append("Lesson: Correct call = NO TRADE")
        lines.append("Capital preserved")
    elif trade_state == "SETUP_FORMING":
        lines.append("Today: WATCHED - NOT TRADED")
        lines.append("Edge: " + str(edge) + " (below 6 threshold)")
        lines.append("")
        lines.append("Lesson: Patience pays")
    else:
        lines.append("Today: TRADE SIGNAL ISSUED")
        lines.append("Bias: " + direction.upper())
        lines.append("Edge: " + str(edge) + " / 10")
        lines.append("")
        lines.append("Check Dashboard for outcome")
    lines.append("")
    lines.append("Dashboard: " + DASHBOARD_URL)
    return "\n".join(lines)

def build_state_change_msg(old_state, new_state, edge, data):
    sig = data.get("signal", {})
    mkt = data.get("market", {})
    nifty = mkt.get("nifty", {})
    price = nifty.get("price", 0) or 0
    direction = sig.get("direction", "neutral").upper()
    date_str = datetime.now().strftime("%d %b %Y %H:%M")
    lines = []
    if old_state == "NO_TRADE" and new_state == "SETUP_FORMING":
        lines.append("STATE CHANGE ALERT")
        lines.append(date_str)
        lines.append("")
        lines.append("NO TRADE  --->  SETUP FORMING")
        lines.append("Edge: " + str(edge) + " / 10")
        lines.append("Bias: " + direction)
        lines.append("")
        lines.append("Market conditions improving")
        lines.append("Monitor closely")
        lines.append("Do NOT trade yet")
    elif new_state == "TRADE_READY":
        exec_plan = data.get("execution", {}) or {}
        lines.append("TRADE READY ALERT")
        lines.append(date_str)
        lines.append("")
        lines.append("Edge crossed 6 / 10")
        lines.append("Bias: " + direction + "  |  Edge: " + str(edge))
        if exec_plan:
            lines.append("")
            lines.append("Entry:   " + str(exec_plan.get("entry", "-")))
            lines.append("SL:      " + str(exec_plan.get("sl", "-")))
            lines.append("Target1: " + str(exec_plan.get("t1", "-")))
            lines.append("R:R      " + str(exec_plan.get("rr", "-")))
    elif old_state == "TRADE_READY" and new_state != "TRADE_READY":
        lines.append("SETUP CHANGED")
        lines.append(date_str)
        lines.append("")
        lines.append("Trade setup no longer valid")
        lines.append("Edge dropped to: " + str(edge) + " / 10")
        lines.append("New State: " + new_state.replace("_", " "))
        lines.append("")
        lines.append("Exit or tighten stops if in trade")
    else:
        return None
    lines.append("")
    lines.append("Nifty: " + str(price))
    lines.append("Dashboard: " + DASHBOARD_URL)
    return "\n".join(lines)

# ── ROUTES ────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    return jsonify({"name": "Nifty AI", "version": "5.0", "status": "running"})

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200

@app.route("/api/signal", methods=["GET"])
def api_signal():
    try:
        market = {}
        for name in TICKERS:
            market[name] = get_cached(TICKERS[name])

        feats = compute_features(market)
        regime = detect_regime(feats)
        signal = compute_signal(feats, regime)
        drivers = build_drivers(feats)
        sparkline = get_sparkline()
        pcr_data = get_cached_pcr()

        direction = signal.get("direction", "neutral")
        conf = signal.get("confidence", 50)
        rsi = (market.get("nifty", {}) or {}).get("rsi", 50) or 50
        vix = (market.get("india_vix", {}) or {}).get("price", 14) or 14
        edge = compute_edge(direction, conf, regime, rsi, vix)
        trade_state = get_trade_state(edge)

        # Only compute execution when trade is ready
        exec_plan = compute_execution(
            (market.get("nifty", {}) or {}).get("price"),
            vix, direction, regime
        ) if trade_state == "TRADE_READY" else None

        nifty_price = (market.get("nifty", {}) or {}).get("price")
        save_signal_history(signal, nifty_price, regime, edge)
        check_price_alerts(nifty_price)

        # State change with anti-noise
        full_data = {
            "signal": signal, "market": market, "regime": regime,
            "edge": edge, "features": feats, "execution": exec_plan
        }
        check_state_change(trade_state, edge, full_data)

        result = {
            "status": "ok",
            "timestamp": datetime.now().isoformat(),
            "signal": signal,
            "regime": regime,
            "drivers": drivers,
            "sparkline": sparkline,
            "edge": edge,
            "trade_state": trade_state,
            "execution": exec_plan,
            "pcr": pcr_data,
            "market": {
                "nifty":     market["nifty"],
                "banknifty": market["banknifty"],
                "nasdaq":    market["nasdaq"],
                "nikkei":    market["nikkei"],
                "hangseng":  market["hangseng"],
                "usd_inr":   market["usd_inr"],
                "crude":     market["crude"],
                "gold":      market["gold"],
                "india_vix": market["india_vix"]
            }
        }
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/history", methods=["GET"])
def api_history():
    try:
        history = evaluate_history()
        total = len([h for h in history if h.get("outcome") in ["correct","wrong"]])
        correct = len([h for h in history if h.get("outcome") == "correct"])
        accuracy = round(correct / total * 100, 1) if total > 0 else 0
        return jsonify({
            "status": "ok",
            "history": list(reversed(history[-30:])),
            "accuracy": accuracy, "total": total, "correct": correct
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/alerts", methods=["GET"])
def api_get_alerts():
    return jsonify({"status": "ok", "alerts": load_alerts()})

@app.route("/api/alerts/add", methods=["GET"])
def api_add_alert():
    try:
        level = float(request.args.get("level", 0))
        direction = request.args.get("direction", "above")
        if level == 0:
            return jsonify({"status": "error", "message": "level required"}), 400
        alerts = load_alerts()
        alerts.append({"level": level, "direction": direction, "created": datetime.now().isoformat()})
        save_alerts(alerts)
        return jsonify({"status": "ok", "message": "Alert set for Nifty " + direction + " " + str(level)})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/alerts/clear", methods=["GET"])
def api_clear_alerts():
    save_alerts([])
    return jsonify({"status": "ok", "message": "All alerts cleared"})

@app.route("/api/backtest", methods=["GET"])
def api_backtest():
    try:
        result = run_backtest()
        return jsonify({"status": "ok", "timestamp": datetime.now().isoformat(), "backtest": result})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/pcr", methods=["GET"])
def api_pcr():
    try:
        pcr_data = get_cached_pcr()
        return jsonify({"status": "ok", "timestamp": datetime.now().isoformat(), "pcr": pcr_data})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/telegram", methods=["GET"])
def api_telegram():
    try:
        session = request.args.get("session", "morning")
        market = {}
        for name in TICKERS:
            market[name] = get_cached(TICKERS[name])
        feats = compute_features(market)
        regime = detect_regime(feats)
        signal = compute_signal(feats, regime)
        direction = signal.get("direction", "neutral")
        conf = signal.get("confidence", 50)
        rsi = (market.get("nifty", {}) or {}).get("rsi", 50) or 50
        vix = (market.get("india_vix", {}) or {}).get("price", 14) or 14
        edge = compute_edge(direction, conf, regime, rsi, vix)
        trade_state = get_trade_state(edge)
        exec_plan = compute_execution(
            (market.get("nifty", {}) or {}).get("price"),
            vix, direction, regime
        ) if trade_state == "TRADE_READY" else None
        data = {
            "signal": signal, "market": market, "regime": regime,
            "edge": edge, "features": feats, "execution": exec_plan
        }
        if trade_state == "TRADE_READY":
            msg = build_trade_ready_msg(data, session)
        elif trade_state == "SETUP_FORMING":
            msg = build_setup_forming_msg(data, session)
        else:
            msg = build_no_trade_msg(data, session)
        ok, res = send_telegram(msg)
        if ok:
            return jsonify({"status": "ok", "message": "Sent", "trade_state": trade_state, "edge": edge})
        return jsonify({"status": "error", "message": res}), 500
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/telegram/review", methods=["GET"])
def api_telegram_review():
    try:
        market = {}
        for name in TICKERS:
            market[name] = get_cached(TICKERS[name])
        feats = compute_features(market)
        regime = detect_regime(feats)
        signal = compute_signal(feats, regime)
        direction = signal.get("direction", "neutral")
        conf = signal.get("confidence", 50)
        rsi = (market.get("nifty", {}) or {}).get("rsi", 50) or 50
        vix = (market.get("india_vix", {}) or {}).get("price", 14) or 14
        edge = compute_edge(direction, conf, regime, rsi, vix)
        data = {"signal": signal, "market": market, "regime": regime, "edge": edge, "features": feats}
        msg = build_daily_review_msg(data)
        ok, res = send_telegram(msg)
        if ok:
            return jsonify({"status": "ok", "message": "Daily review sent"})
        return jsonify({"status": "error", "message": res}), 500
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/telegram/test", methods=["GET"])
def api_telegram_test():
    lines = []
    lines.append("Nifty AI v5.0 - Decision Bot")
    lines.append("")
    lines.append("Bot is connected!")
    lines.append("")
    lines.append("Message types:")
    lines.append("  - NO TRADE ZONE")
    lines.append("  - SETUP FORMING")
    lines.append("  - TRADE READY")
    lines.append("  - State change alerts")
    lines.append("  - Daily review at 3:30 PM")
    lines.append("")
    lines.append("Anti-noise filter: ON")
    lines.append("State confirms after 2 cycles")
    ok, res = send_telegram("\n".join(lines))
    if ok:
        return jsonify({"status": "ok"})
    return jsonify({"status": "error", "message": res}), 500

@app.route("/api/data", methods=["GET"])
def api_data():
    market = {}
    for name in TICKERS:
        market[name] = get_cached(TICKERS[name])
    return jsonify({"status": "ok", "timestamp": datetime.now().isoformat(), "market": market})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

