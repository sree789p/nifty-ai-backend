import os
import json
import traceback
import requests
from datetime import datetime, timedelta
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

# ─── DATA FETCH ───────────────────────────────────────────────

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

# ─── PUT CALL RATIO ───────────────────────────────────────────

def fetch_pcr():
    try:
        nifty = yf.Ticker("^NSEI")
        hist_data = nifty.history(period="2d")
        if hist_data.empty:
            return {"pcr": None, "signal": "neutral", "detail": "No price history"}
        price = hist_data["Close"].iloc[-1]
        strike = round(price / 100) * 100
        exp_dates = nifty.options
        if not exp_dates:
            return {"pcr": None, "signal": "neutral", "detail": "No options data"}
        chain = nifty.option_chain(exp_dates[0])
        put_oi = float(chain.puts["openInterest"].sum())
        call_oi = float(chain.calls["openInterest"].sum())
        if call_oi == 0:
            return {"pcr": None, "signal": "neutral", "detail": "No OI data"}
        pcr = round(put_oi / call_oi, 2)
        if pcr > 1.2:
            signal = "bullish"
            detail = "High put OI - smart money hedged, market support likely"
        elif pcr < 0.8:
            signal = "bearish"
            detail = "Low put OI - complacency, downside risk"
        else:
            signal = "neutral"
            detail = "Balanced OI - no strong directional bias"
        return {"pcr": pcr, "put_oi": int(put_oi), "call_oi": int(call_oi), "signal": signal, "detail": detail}
    except Exception as e:
        print("PCR error: " + str(e))
        return {"pcr": None, "signal": "neutral", "detail": "PCR unavailable"}

# ─── XGBOOST ML MODEL ────────────────────────────────────────

def train_and_predict(features):
    try:
        t = yf.Ticker("^NSEI")
        hist = t.history(period="2y", interval="1d")
        if hist.empty or len(hist) < 60:
            return None
        close = hist["Close"].dropna()
        X = []
        y = []
        for i in range(20, len(close) - 1):
            delta = close.diff()
            gain = delta.clip(lower=0).iloc[i-14:i].mean()
            loss = -delta.clip(upper=0).iloc[i-14:i].mean()
            rsi = 50.0
            if loss and loss > 1e-6:
                rsi = float(100 - (100 / (1 + gain / loss)))
            sma20 = float(close.iloc[i-20:i].mean())
            sma50 = float(close.iloc[max(0,i-50):i].mean()) if i >= 50 else sma20
            current = float(close.iloc[i])
            prev = float(close.iloc[i-1])
            chg1 = (current - prev) / prev * 100
            chg5 = (current - float(close.iloc[i-5])) / float(close.iloc[i-5]) * 100
            chg20 = (current - float(close.iloc[i-20])) / float(close.iloc[i-20]) * 100
            above_sma20 = 1 if current > sma20 else 0
            above_sma50 = 1 if current > sma50 else 0
            row = [rsi/100, chg1/5, chg5/10, chg20/20, above_sma20, above_sma50]
            X.append(row)
            next_close = float(close.iloc[i+1])
            y.append(1 if next_close > current else 0)

        X = np.array(X, dtype=float)
        y = np.array(y, dtype=float)
        n = len(X)
        train_size = int(n * 0.8)
        X_train = X[:train_size]
        y_train = y[:train_size]

        # Logistic regression (manual implementation)
        def sigmoid(z):
            return 1 / (1 + np.exp(-np.clip(z, -500, 500)))

        weights = np.zeros(X_train.shape[1])
        bias = 0.0
        lr = 0.01
        for _ in range(200):
            z = X_train.dot(weights) + bias
            pred = sigmoid(z)
            err = pred - y_train
            weights -= lr * X_train.T.dot(err) / len(y_train)
            bias -= lr * err.mean()

        # Current feature vector
        nifty_chg = features.get("nifty_change", 0)
        nifty_rsi = features.get("nifty_rsi", 50)
        nifty_sma = features.get("nifty_sma", 0)
        chg1w = features.get("nifty_change_1w", 0)
        feat_vec = np.array([nifty_rsi/100, nifty_chg/5, chg1w/10, 0, nifty_sma, nifty_sma])
        ml_prob = float(sigmoid(feat_vec.dot(weights) + bias))

        # Test accuracy
        X_test = X[train_size:]
        y_test = y[train_size:]
        test_preds = sigmoid(X_test.dot(weights) + bias) > 0.5
        accuracy = float(np.mean(test_preds == y_test)) * 100

        return {"probability": round(ml_prob, 4), "accuracy": round(accuracy, 1), "model": "logistic_regression"}
    except Exception as e:
        print("ML error: " + str(e))
        return None

# ─── FEATURES + SIGNAL ────────────────────────────────────────

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

def compute_signal(f, regime, ml_result=None, pcr_data=None):
    score = 0.5
    continuous = ["nifty_change","nasdaq_change","sp500_change","nikkei_change","hangseng_change","usd_inr_change","crude_change"]
    for feat in WEIGHTS:
        val = f.get(feat, 0)
        if feat in continuous:
            val = max(-3, min(3, val)) / 3.0
        score += WEIGHTS[feat] * val

    # PCR adjustment
    if pcr_data and pcr_data.get("pcr"):
        pcr = pcr_data["pcr"]
        if pcr > 1.2:
            score += 0.05
        elif pcr < 0.8:
            score -= 0.05

    # ML blend
    if ml_result and ml_result.get("probability"):
        ml_prob = ml_result["probability"]
        score = score * 0.7 + ml_prob * 0.3

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

def build_drivers(f, pcr_data=None):
    drivers = []
    n = f["nifty_change"]
    d1 = {}
    d1["icon"] = "📊"
    d1["tag"] = "pos" if n >= 0 else "neg"
    d1["label"] = "Nifty " + ("up " if n >= 0 else "down ") + str(abs(round(n,2))) + "% | RSI " + str(round(f["nifty_rsi"],0))
    d1["detail"] = str(round(f["nifty_change_1w"],2)) + "% weekly"
    drivers.append(d1)
    
    nq = f["nasdaq_change"]
    sp = f["sp500_change"]
    d2 = {}
    d2["icon"] = "📈" if nq >= 0 else "📉"
    d2["tag"] = "pos" if nq >= 0 else "neg"
    d2["label"] = "Nasdaq " + str(round(nq,2)) + "% | S&P " + str(round(sp,2)) + "%"
    d2["detail"] = "US markets"
    drivers.append(d2)
    
    nk = f["nikkei_change"]
    hs = f["hangseng_change"]
    d3 = {}
    d3["icon"] = "🌏"
    d3["tag"] = "pos" if (nk + hs) >= 0 else "neg"
    d3["label"] = "Nikkei " + str(round(nk,2)) + "% | HangSeng " + str(round(hs,2)) + "%"
    d3["detail"] = "Asian markets"
    drivers.append(d3)
    
    fx = f["usd_inr_change"]
    d4 = {}
    d4["icon"] = "💱"
    d4["tag"] = "neg" if fx > 0.2 else "pos"
    d4["label"] = "INR " + ("weakening" if fx > 0.2 else "stable")
    d4["detail"] = "USD/INR " + str(round(fx,2)) + "%"
    drivers.append(d4)
    
    cr = f["crude_change"]
    d5 = {}
    d5["icon"] = "🛢"
    d5["tag"] = "neg" if cr > 1 else "pos"
    d5["label"] = "Crude " + ("rising" if cr > 1 else "stable")
    d5["detail"] = str(round(cr,2)) + "%"
    drivers.append(d5)
    
    vix = f["india_vix"]
    d6 = {}
    d6["icon"] = "⚡" if vix > 18 else "😌"
    d6["tag"] = "neg" if vix > 18 else "pos"
    d6["label"] = "India VIX " + str(round(vix,1)) + " | US VIX " + str(round(f["us_vix"],1))
    d6["detail"] = "elevated" if vix > 18 else "calm"
    drivers.append(d6)
    
    if pcr_data and pcr_data.get("pcr"):
        d7 = {}
        d7["icon"] = "⚖️"
        d7["tag"] = "pos" if pcr_data["signal"] == "bullish" else ("neg" if pcr_data["signal"] == "bearish" else "pos")
        d7["label"] = "PCR: " + str(pcr_data["pcr"]) + " — " + pcr_data["signal"].upper()
        d7["detail"] = pcr_data["detail"]
        drivers.append(d7)
    return drivers

# ─── SIGNAL HISTORY ───────────────────────────────────────────

def load_history():
    try:
        if os.path.exists(SIGNAL_HISTORY_FILE):
            with open(SIGNAL_HISTORY_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return []

def save_signal_history(signal, nifty_price, regime):
    try:
        history = load_history()
        entry = {}
        entry["timestamp"] = datetime.now().isoformat()
        entry["date"] = datetime.now().strftime("%d %b %Y %H:%M")
        entry["direction"] = signal.get("direction", "neutral")
        entry["confidence"] = signal.get("confidence", 0)
        entry["nifty_price"] = nifty_price
        entry["regime"] = regime
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

# ─── PRICE ALERTS ─────────────────────────────────────────────

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
            msg = "🚨 NIFTY PRICE ALERT\n\n"
            msg += "Nifty " + direction + " " + str(level) + " triggered!\n"
            msg += "Current: " + str(nifty_price) + "\n"
            msg += "Time: " + datetime.now().strftime("%d %b %Y %H:%M IST")
            send_telegram(msg)
        else:
            remaining.append(alert)
    if len(remaining) != len(alerts):
        save_alerts(remaining)

# ─── BACKTEST ─────────────────────────────────────────────────

def run_backtest():
    try:
        t = yf.Ticker("^NSEI")
        hist = t.history(period="3mo", interval="1d")
        if hist.empty or len(hist) < 30:
            return {"error": "Not enough data"}
        close = hist["Close"].dropna()
        correct = 0
        total = 0
        last_10 = []
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
            if rsi < 35:
                score += 0.10
            elif rsi > 65:
                score -= 0.10
            score += 0.08 if current > sma20 else -0.08
            score = max(0.05, min(0.95, score))
            predicted = "bullish" if score > 0.5 else "bearish"
            if predicted == actual:
                correct += 1
            total += 1
            if i >= len(close) - 11:
                row = {}
                row["date"] = hist.index[i].strftime("%Y-%m-%d")
                row["predicted"] = predicted
                row["actual"] = actual
                row["correct"] = predicted == actual
                row["confidence"] = round(score * 100, 1)
                last_10.append(row)
        accuracy = round(correct / total * 100, 1) if total > 0 else 0
        return {"accuracy": accuracy, "total_days": total, "correct": correct,
                "last_10": last_10, "summary": "Model correct " + str(accuracy) + "% over 3 months"}
    except Exception as e:
        traceback.print_exc()
        return {"error": str(e)}

def get_sparkline():
    try:
        t = yf.Ticker("^NSEI")
        hist = t.history(period="1mo", interval="1d")
        closes = hist["Close"].dropna().tail(20).tolist()
        return [round(float(v), 2) for v in closes]
    except Exception:
        return []

# ─── TELEGRAM ─────────────────────────────────────────────────

def build_telegram_msg(data, session):
    sig = data.get("signal", {})
    mkt = data.get("market", {})
    drivers = data.get("drivers", [])
    regime = data.get("regime", "range")
    ml = data.get("ml", {})
    pcr = data.get("pcr", {})
    direction = sig.get("direction", "neutral").upper()
    confidence = sig.get("confidence", 0)
    nifty = mkt.get("nifty", {})
    price = nifty.get("price", 0) or 0
    chg = nifty.get("change_pct", 0) or 0
    rsi = nifty.get("rsi", 0) or 0
    india_vix = mkt.get("india_vix", {}).get("price", 0) or 0
    sig_emoji = "🟢" if direction == "BULLISH" else ("🔴" if direction == "BEARISH" else "🟡")
    regime_map = {}
    regime_map["volatile"]   = "HIGH VOL"
    regime_map["trending"]   = "TRENDING"
    regime_map["oversold"]   = "OVERSOLD"
    regime_map["overbought"] = "OVERBOUGHT"
    regime_map["range"]      = "RANGE"
    regime_text = regime_map.get(regime, regime.upper())
    arrow = "up" if chg >= 0 else "down"
    session_text = "MORNING" if session == "morning" else "EVENING"
    date_str = datetime.now().strftime("%d %b %Y")
    driver_lines = ""
    for d in drivers[:4]:
        driver_lines += d.get("icon","") + " " + d.get("label","") + "\n"
    msg = "Nifty AI v3.0\n"
    msg += session_text + " | " + date_str + "\n\n"
    msg += "Nifty: " + str(price) + " (" + arrow + " " + str(abs(chg)) + "%)\n"
    msg += "RSI: " + str(rsi) + " | VIX: " + str(india_vix) + "\n"
    if pcr.get("pcr"):
        msg += "PCR: " + str(pcr["pcr"]) + " | " + pcr.get("signal","").upper() + "\n"
    msg += "Regime: " + regime_text + "\n"
    msg += "Signal: " + direction + " (" + str(confidence) + "%)\n"
    if ml and ml.get("probability"):
        msg += "ML Model: " + str(round(ml["probability"]*100,1)) + "% bull prob\n"
    msg += "\nDrivers:\n" + driver_lines
    msg += "\nDashboard: " + DASHBOARD_URL + "\n"
    msg += "Not financial advice"
    return msg

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

# ─── ROUTES ───────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    return jsonify({"name": "Nifty AI", "version": "3.0", "status": "running"})

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

        pcr_data = fetch_pcr()
        ml_result = train_and_predict(feats)
        signal = compute_signal(feats, regime, ml_result, pcr_data)
        drivers = build_drivers(feats, pcr_data)
        sparkline = get_sparkline()

        nifty_price = market.get("nifty", {}).get("price")
        save_signal_history(signal, nifty_price, regime)
        check_price_alerts(nifty_price)

        result = {}
        result["status"] = "ok"
        result["timestamp"] = datetime.now().isoformat()
        result["signal"] = signal
        result["regime"] = regime
        result["drivers"] = drivers
        result["sparkline"] = sparkline
        result["pcr"] = pcr_data
        result["ml"] = ml_result
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
        alert["level"] = level
        alert["direction"] = direction
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

@app.route("/api/pcr", methods=["GET"])
def api_pcr():
    try:
        pcr_data = fetch_pcr()
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
        pcr_data = fetch_pcr()
        ml_result = train_and_predict(feats)
        signal = compute_signal(feats, regime, ml_result, pcr_data)
        drivers = build_drivers(feats, pcr_data)
        data = {}
        data["signal"] = signal
        data["regime"] = regime
        data["drivers"] = drivers
        data["market"] = market
        data["pcr"] = pcr_data
        data["ml"] = ml_result
        message = build_telegram_msg(data, session)
        ok, res = send_telegram(message)
        if ok:
            return jsonify({"status": "ok", "message": "Sent"})
        return jsonify({"status": "error", "message": res}), 500
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/telegram/test", methods=["GET"])
def api_telegram_test():
    ok, res = send_telegram("Nifty AI v3.0 connected! PCR + ML + Alerts + History all active.")
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
