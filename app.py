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

# DATA FETCH

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
        return {"price": price, "change_pct": change_pct, "change_1w": change_1w, "rsi": rsi, "above_sma20": above_sma20}
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

# PUT CALL RATIO via NSE public API

def fetch_pcr():
    try:
        headers = {}
        headers["User-Agent"] = "Mozilla/5.0"
        headers["Accept"] = "application/json"
        headers["Referer"] = "https://www.nseindia.com"

        session = requests.Session()
        session.get("https://www.nseindia.com", headers=headers, timeout=10)

        url = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"
        res = session.get(url, headers=headers, timeout=15)

        if res.status_code != 200:
            return {"pcr": None, "signal": "neutral", "detail": "NSE API unavailable"}

        data = res.json()
        records = data.get("records", {}).get("data", [])

        total_put_oi = 0
        total_call_oi = 0

        for record in records:
            if "PE" in record:
                total_put_oi += record["PE"].get("openInterest", 0)
            if "CE" in record:
                total_call_oi += record["CE"].get("openInterest", 0)

        if total_call_oi == 0:
            return {"pcr": None, "signal": "neutral", "detail": "No OI data"}

        pcr = round(total_put_oi / total_call_oi, 2)

        if pcr > 1.2:
            signal = "bullish"
            detail = "High put OI — smart money hedged, support likely"
        elif pcr < 0.8:
            signal = "bearish"
            detail = "Low put OI — complacency, downside risk"
        else:
            signal = "neutral"
            detail = "Balanced OI — no strong directional bias"

        return {
            "pcr": pcr,
            "put_oi": int(total_put_oi),
            "call_oi": int(total_call_oi),
            "signal": signal,
            "detail": detail
        }

    except Exception as e:
        print("PCR error: " + str(e))
        return {"pcr": None, "signal": "neutral", "detail": "PCR fetch failed"}

PCR_CACHE = {"data": None, "ts": 0}

def get_cached_pcr():
    now = datetime.now().timestamp()
    if PCR_CACHE["data"] and now - PCR_CACHE["ts"] < 600:
        return PCR_CACHE["data"]
    data = fetch_pcr()
    PCR_CACHE["data"] = data
    PCR_CACHE["ts"] = now
    return data

# FEATURES

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

def compute_edge(direction, conf, regime, rsi, vix):
    score = 0
    if conf > 70: score += 3
    elif conf > 62: score += 2
    elif conf > 55: score += 1
    if regime == "trending": score += 2
    elif regime == "range": score += 1
    elif regime == "volatile": score -= 1
    if rsi < 35 and direction == "bullish": score += 2
    elif rsi > 65 and direction == "bearish": score += 2
    elif 40 < rsi < 60: score += 1
    elif rsi > 70 or rsi < 30: score -= 1
    if vix < 14: score += 2
    elif vix < 18: score += 1
    elif vix > 20: score -= 1
    return round(max(0, min(10, score)), 1)

def compute_execution(price, vix, direction, regime, edge):
    if not price or edge < 3:
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
    drivers.append({"icon": "📊", "tag": "pos" if n >= 0 else "neg",
        "label": "Nifty " + ("up " if n >= 0 else "down ") + str(abs(round(n,2))) + "% | RSI " + str(round(f["nifty_rsi"],0)),
        "detail": str(round(f["nifty_change_1w"],2)) + "% weekly", "impact": "HIGH"})
    nq = f["nasdaq_change"]; sp = f["sp500_change"]
    drivers.append({"icon": "📈" if nq >= 0 else "📉", "tag": "pos" if nq >= 0 else "neg",
        "label": "Nasdaq " + str(round(nq,2)) + "% | S&P " + str(round(sp,2)) + "%",
        "detail": "US markets", "impact": "MEDIUM"})
    nk = f["nikkei_change"]; hs = f["hangseng_change"]
    drivers.append({"icon": "🌏", "tag": "pos" if (nk+hs)>=0 else "neg",
        "label": "Nikkei " + str(round(nk,2)) + "% | HangSeng " + str(round(hs,2)) + "%",
        "detail": "Asian markets", "impact": "LOW"})
    fx = f["usd_inr_change"]
    drivers.append({"icon": "💱", "tag": "neg" if fx > 0.2 else "pos",
        "label": "INR " + ("weakening" if fx > 0.2 else "stable"),
        "detail": "USD/INR " + str(round(fx,2)) + "%", "impact": "MEDIUM"})
    cr = f["crude_change"]
    drivers.append({"icon": "🛢", "tag": "neg" if cr > 1 else "pos",
        "label": "Crude " + ("rising" if cr > 1 else "stable"),
        "detail": str(round(cr,2)) + "%", "impact": "HIGH"})
    vix = f["india_vix"]
    drivers.append({"icon": "⚡" if vix > 18 else "😌", "tag": "neg" if vix > 18 else "pos",
        "label": "India VIX " + str(round(vix,1)) + " | US VIX " + str(round(f["us_vix"],1)),
        "detail": "elevated" if vix > 18 else "calm", "impact": "HIGH"})
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
                row["correct"] = predicted == actual; row["confidence"] = round(score * 100, 1)
                last_10.append(row)
        accuracy = round(correct / total * 100, 1) if total > 0 else 0
        return {"accuracy": accuracy, "total_days": total, "correct": correct,
                "last_10": last_10, "summary": "Model correct " + str(accuracy) + "% over 3 months"}
    except Exception as e:
        traceback.print_exc(); return {"error": str(e)}

# HISTORY

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
        entry = {}
        entry["timestamp"] = datetime.now().isoformat()
        entry["date"] = datetime.now().strftime("%d %b %Y %H:%M")
        entry["direction"] = signal.get("direction", "neutral")
        entry["confidence"] = signal.get("confidence", 0)
        entry["nifty_price"] = nifty_price
        entry["regime"] = regime
        entry["edge"] = edge
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

# PRICE ALERTS

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
        triggered = False
        if direction == "above" and nifty_price >= level:
            triggered = True
        elif direction == "below" and nifty_price <= level:
            triggered = True
        if triggered:
            msg = "🚨 NIFTY PRICE ALERT"
            msg += "Nifty " + direction + " " + str(level) + " triggered! "
            msg += "Current: " + str(nifty_price) + " "
            msg += "Time: " + datetime.now().strftime("%d %b %Y %H:%M IST")
            send_telegram(msg)
        else:
            remaining.append(alert)
    if len(remaining) != len(alerts):
        save_alerts(remaining)

# STATE TRACKING (for change alerts)

def load_state():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print("State save error: " + str(e))

def get_trade_state(edge):
    if edge >= 6:
        return "TRADE_READY"
    elif edge >= 3:
        return "SETUP_FORMING"
    else:
        return "NO_TRADE"

# TELEGRAM DECISION MESSAGES

def send_telegram(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return False, "Missing env vars"
    url = "https://api.telegram.org/bot" + TELEGRAM_TOKEN + "/sendMessage"
    payload = {}
    payload["chat_id"] = CHAT_ID
    payload["text"] = message
    try:
        res = requests.post(url, json=payload, timeout=30)
        return (True, "sent") if res.status_code == 200 else (False, res.text)
    except Exception as e:
        return False, str(e)

def build_reason_lines(f, regime, vix, rsi):
    reasons = []
    if vix > 20:
        reasons.append("VIX at " + str(round(vix,1)) + " - high volatility, risk elevated")
    elif vix > 18:
        reasons.append("VIX at " + str(round(vix,1)) + " - volatility elevated, caution needed")
    else:
        reasons.append("VIX at " + str(round(vix,1)) + " - market calm")
    if rsi > 70:
        reasons.append("RSI at " + str(round(rsi,1)) + " - overbought, upside limited")
    elif rsi > 65:
        reasons.append("RSI at " + str(round(rsi,1)) + " - approaching overbought zone")
    elif rsi < 30:
        reasons.append("RSI at " + str(round(rsi,1)) + " - oversold, bounce possible")
    elif rsi < 35:
        reasons.append("RSI at " + str(round(rsi,1)) + " - approaching oversold zone")
    else:
        reasons.append("RSI at " + str(round(rsi,1)) + " - neutral zone")
    nq = f.get("nasdaq_change", 0)
    if nq > 1:
        reasons.append("Nasdaq up " + str(round(nq,1)) + "% - global tailwind")
    elif nq < -1:
        reasons.append("Nasdaq down " + str(round(abs(nq),1)) + "% - global headwind")
    cr = f.get("crude_change", 0)
    if cr > 2:
        reasons.append("Crude up " + str(round(cr,1)) + "% - inflation risk")
    return reasons

def build_no_trade_msg(data, session):
    sig = data.get("signal", {}); mkt = data.get("market", {})
    f = data.get("features", {}); edge = data.get("edge", 0)
    nifty = mkt.get("nifty", {}); price = nifty.get("price", 0) or 0
    chg = nifty.get("change_pct", 0) or 0; rsi = nifty.get("rsi", 0) or 0
    vix = (mkt.get("india_vix", {}) or {}).get("price", 0) or 0
    regime = data.get("regime", "range")
    arrow = "+" if chg >= 0 else ""
    session_text = "MORNING" if session == "morning" else "EVENING"
    date_str = datetime.now().strftime("%d %b %Y")
    reasons = build_reason_lines(f, regime, vix, rsi)
    reason_block = ""
    for r in reasons[:3]:
        reason_block += "- " + r + " "
    msg = "🧠 NIFTY AI - INTRADAY DECISION"
    msg += session_text + " | " + date_str + " "
    msg += "🚫 NO TRADE ZONE"
    msg += "Edge: " + str(edge) + " / 10 | Confidence: LOW"
    msg += "Reason:" + reason_block + " "
    msg += "Action:"
    msg += "- Stay in cash"
    msg += "- Wait for edge > 3"
    msg += "- Check again at next signal"
    msg += "Nifty: " + str(price) + " (" + arrow + str(chg) + "%)"
    msg += "Dashboard: " + DASHBOARD_URL
    return msg

def build_setup_forming_msg(data, session):
    sig = data.get("signal", {}); mkt = data.get("market", {})
    f = data.get("features", {}); edge = data.get("edge", 0)
    nifty = mkt.get("nifty", {}); price = nifty.get("price", 0) or 0
    chg = nifty.get("change_pct", 0) or 0; rsi = nifty.get("rsi", 0) or 0
    vix = (mkt.get("india_vix", {}) or {}).get("price", 0) or 0
    direction = sig.get("direction", "neutral").upper()
    conf = sig.get("confidence", 0)
    arrow = "+" if chg >= 0 else ""
    session_text = "MORNING" if session == "morning" else "EVENING"
    date_str = datetime.now().strftime("%d %b %Y")
    msg = "🧠 NIFTY AI - INTRADAY DECISION"
    msg += session_text + " | " + date_str + " "
    msg += "⚠️ SETUP FORMING"
    msg += "Edge: " + str(edge) + " / 10 | Bias: " + direction + " "
    msg += "Market improving - not ready yet"
    msg += "Watch for:"
    if direction == "BULLISH":
        msg += "- Pullback to support for entry"
        msg += "- VIX below 16 for confirmation"
        msg += "- Volume spike on bounce"
    else:
        msg += "- Rally to resistance for short entry"
        msg += "- VIX staying elevated"
        msg += "- Weak global cues"
    msg += "Do NOT trade yet - wait for edge > 6"
    msg += "Nifty: " + str(price) + " (" + arrow + str(chg) + "%)"
    msg += "RSI: " + str(rsi) + " | VIX: " + str(vix) + " "
    msg += "Dashboard: " + DASHBOARD_URL
    return msg

def build_trade_ready_msg(data, session):
    sig = data.get("signal", {}); mkt = data.get("market", {})
    f = data.get("features", {}); edge = data.get("edge", 0)
    exec_plan = data.get("execution", {})
    nifty = mkt.get("nifty", {}); price = nifty.get("price", 0) or 0
    chg = nifty.get("change_pct", 0) or 0; rsi = nifty.get("rsi", 0) or 0
    vix = (mkt.get("india_vix", {}) or {}).get("price", 0) or 0
    direction = sig.get("direction", "neutral").upper()
    conf = sig.get("confidence", 0)
    regime = data.get("regime", "range")
    arrow = "+" if chg >= 0 else ""
    session_text = "MORNING" if session == "morning" else "EVENING"
    date_str = datetime.now().strftime("%d %b %Y")
    dir_emoji = "📈" if direction == "BULLISH" else "📉"
    msg = "🧠 NIFTY AI - INTRADAY DECISION"
    msg += session_text + " | " + date_str + " "
    msg += "🔥 TRADE READY"
    msg += "Edge: " + str(edge) + " / 10 | " + dir_emoji + " " + direction + " "
    msg += "Confidence: " + str(conf) + "%"
    if exec_plan:
        msg += "EXECUTION PLAN (INTRADAY):"
        msg += "Entry: " + str(exec_plan.get("entry", "—")) + " "
        msg += "Stop Loss: " + str(exec_plan.get("sl", "—")) + " "
        msg += "Target 1: " + str(exec_plan.get("t1", "—")) + " "
        msg += "Target 2: " + str(exec_plan.get("t2", "—")) + " "
        msg += "Risk:Reward: " + str(exec_plan.get("rr", "—")) + " "
        msg += "Today Range: " + str(exec_plan.get("range", "—")) + " "
    msg += "Rules:"
    msg += "- Exit at Target 1 partially"
    msg += "- Move SL to entry after T1 hit"
    msg += "- Exit all by 3:15 PM IST"
    msg += "- Not financial advice"
    msg += "Nifty: " + str(price) + " (" + arrow + str(chg) + "%)"
    msg += "Dashboard: " + DASHBOARD_URL
    return msg

def build_daily_review_msg(data):
    sig = data.get("signal", {}); mkt = data.get("market", {})
    edge = data.get("edge", 0); regime = data.get("regime", "range")
    nifty = mkt.get("nifty", {}); price = nifty.get("price", 0) or 0
    chg = nifty.get("change_pct", 0) or 0; rsi = nifty.get("rsi", 0) or 0
    vix = (mkt.get("india_vix", {}) or {}).get("price", 0) or 0
    direction = sig.get("direction", "neutral")
    trade_state = get_trade_state(edge)
    date_str = datetime.now().strftime("%d %b %Y")
    regime_map = {"volatile":"High Volatility","trending":"Trending","oversold":"Oversold","overbought":"Overbought","range":"Range Bound"}
    regime_text = regime_map.get(regime, regime.title())
    arrow = "+" if chg >= 0 else ""
    msg = "📊 NIFTY AI - DAILY REVIEW"
    msg += date_str + " "
    msg += "Nifty: " + str(price) + " (" + arrow + str(chg) + "%)"
    msg += "VIX: " + str(vix) + " | RSI: " + str(rsi) + " "
    msg += "Regime: " + regime_text + " "
    if trade_state == "NO_TRADE":
        msg += "Today Decision: NO TRAD"
        msg += "Edge: " + str(edge) + " / 10 "
        msg += "Market Type:"
        if vix > 18:
            msg += "- High VIX (" + str(vix) + ") = risky environment"
        if rsi > 65:
            msg += "- Overbought RSI = upside limited"
        elif rsi < 35:
            msg += "- Oversold RSI = downside limited"
        msg += "Lesson: Correct decision = NO TRADE"
        msg += "Avoiding bad setups = protecting capital"
    elif trade_state == "SETUP_FORMING":
        msg += "Today Decision: WATCHED, NOT TRADED"
        msg += "Edge: " + str(edge) + " / 10 (below threshold) "
        msg += "Lesson: Patience pays"
        msg += "Setup was forming but not ready"
    else:
        msg += "Today Decision: TRADE SIGNAL ISSUED"
        msg += "Bias: " + direction.upper() + " "
        msg += "Edge: " + str(edge) + " / 10 "
        msg += "Check Dashboard for results"
    msg += "Dashboard: " + DASHBOARD_URL
    return msg

def build_state_change_msg(old_state, new_state, data):
    edge = data.get("edge", 0)
    sig = data.get("signal", {}); mkt = data.get("market", {})
    nifty = mkt.get("nifty", {}); price = nifty.get("price", 0) or 0
    direction = sig.get("direction", "neutral").upper()
    date_str = datetime.now().strftime("%d %b %Y %H:%M")
    if old_state == "NO_TRADE" and new_state == "SETUP_FORMING":
        msg = "⚠️ STATE CHANGE ALERT" + date_str + " "
        msg += "NO TRADE → SETUP FORMING"
        msg += "Edge: " + str(edge) + " / 10"
        msg += "Bias: " + direction + " "
        msg += "Market conditions improving"
        msg += "Monitor closely - do not trade yet"
        msg += "Nifty: " + str(price) + " "
        msg += "Dashboard: " + DASHBOARD_URL
    elif new_state == "TRADE_READY":
        msg = "🔥 TRADE READY ALERT" + date_str + " "
        msg += "Edge crossed 6 / 10"
        msg += "Bias: " + direction + " | Edge: " + str(edge) + " "
        msg += "Check Dashboard for full execution plan "
        msg += "Dashboard: " + DASHBOARD_URL
    elif old_state == "TRADE_READY" and new_state != "TRADE_READY":
        msg = "🚨 SETUP CHANGED" + date_str + " "
        msg += "Trade setup no longer valid "
        msg += "Edge dropped to: " + str(edge) + " / 10 "
        msg += "New State: " + new_state.replace("_"," ") + " "
        msg += "Exit or tighten stops if in trade "
        msg += "Dashboard: " + DASHBOARD_URL
    else:
        return None
    return msg

# ROUTES

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
        direction = signal.get("direction", "neutral")
        conf = signal.get("confidence", 50)
        rsi = market.get("nifty", {}).get("rsi", 50) or 50
        vix = (market.get("india_vix", {}) or {}).get("price", 14) or 14
        edge = compute_edge(direction, conf, regime, rsi, vix)
        nifty_price = (market.get("nifty", {}) or {}).get("price")
        exec_plan = compute_execution(nifty_price, vix, direction, regime, edge)
        pcr_data = get_cached_pcr()
        save_signal_history(signal, nifty_price, regime, edge)
        check_price_alerts(nifty_price)
        trade_state = get_trade_state(edge)
        last_state = load_state()
        old_trade_state = last_state.get("trade_state", "NO_TRADE")
        if old_trade_state != trade_state:
            full_data = {}
            full_data["signal"] = signal; full_data["market"] = market
            full_data["regime"] = regime; full_data["edge"] = edge
            full_data["features"] = feats; full_data["execution"] = exec_plan
            change_msg = build_state_change_msg(old_trade_state, trade_state, full_data)
            if change_msg:
                send_telegram(change_msg)
        save_state({"trade_state": trade_state, "edge": edge, "timestamp": datetime.now().isoformat()})
        result = {}
        result["status"] = "ok"
        result["timestamp"] = datetime.now().isoformat()
        result["signal"] = signal
        result["regime"] = regime
        result["drivers"] = drivers
        result["sparkline"] = sparkline
        result["edge"] = edge
        result["trade_state"] = trade_state
        result["execution"] = exec_plan
        result["pcr"] = pcr_data
        mkt = {}
        mkt["nifty"]     = market["nifty"]
        mkt["banknifty"] = market["banknifty"]
        mkt["nasdaq"]    = market["nasdaq"]
        mkt["nikkei"]    = market["nikkei"]
        mkt["hangseng"]  = market["hangseng"]
        mkt["usd_inr"]   = market["usd_inr"]
        mkt["crude"]     = market["crude"]
        mkt["gold"]      = market["gold"]
        mkt["india_vix"] = market["india_vix"]
        result["market"] = mkt
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
        return jsonify({"status": "ok", "history": list(reversed(history[-30:])),
                        "accuracy": accuracy, "total": total, "correct": correct})
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
        alert = {}
        alert["level"] = level; alert["direction"] = direction
        alert["created"] = datetime.now().isoformat()
        alerts.append(alert)
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
        direction = signal.get("direction","neutral")
        conf = signal.get("confidence",50)
        rsi = (market.get("nifty",{}) or {}).get("rsi",50) or 50
        vix = (market.get("india_vix",{}) or {}).get("price",14) or 14
        edge = compute_edge(direction, conf, regime, rsi, vix)
        exec_plan = compute_execution((market.get("nifty",{}) or {}).get("price"), vix, direction, regime, edge)
        trade_state = get_trade_state(edge)
        data = {}
        data["signal"] = signal; data["market"] = market; data["regime"] = regime
        data["edge"] = edge; data["features"] = feats; data["execution"] = exec_plan
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
        direction = signal.get("direction","neutral")
        conf = signal.get("confidence",50)
        rsi = (market.get("nifty",{}) or {}).get("rsi",50) or 50
        vix = (market.get("india_vix",{}) or {}).get("price",14) or 14
        edge = compute_edge(direction, conf, regime, rsi, vix)
        data = {}
        data["signal"] = signal; data["market"] = market
        data["regime"] = regime; data["edge"] = edge; data["features"] = feats
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
    msg = "Nifty AI v5.0 Decision Bot connected!\n\n"
    msg += "Messages now:\n"
    msg += "- NO TRADE ZONE\n"
    msg += "- SETUP FORMING\n"
    msg += "- TRADE READY\n"
    msg += "- State change alerts\n"
    msg += "- Daily review at close"
    ok, res = send_telegram(msg)
    if ok:
        return jsonify({"status": "ok"})
    return jsonify({"status": "error", "message": res}), 500

@app.route("/api/pcr", methods=["GET"])
def api_pcr():
    try:
        pcr_data = get_cached_pcr()
        return jsonify({"status": "ok", "timestamp": datetime.now().isoformat(), "pcr": pcr_data})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/data", methods=["GET"])
def api_data():
    market = {}
    for name in TICKERS:
        market[name] = get_cached(TICKERS[name])
    return jsonify({"status": "ok", "timestamp": datetime.now().isoformat(), "market": market})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
