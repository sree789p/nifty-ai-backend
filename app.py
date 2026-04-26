import os
import traceback
import requests
from datetime import datetime
from flask import Flask, jsonify, request
from flask_cors import CORS
import yfinance as yf

app = Flask(__name__)
CORS(app)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "https://sree789p.github.io/nifty-ai-backend")

CACHE = {}
CACHE_TTL = 300

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


def compute_features(market):
    def chg(key):
        return market.get(key, {}).get("change_pct") or 0.0

    def pr(key):
        return market.get(key, {}).get("price") or 0.0

    f = {}
    f["nifty_change"] = chg("nifty")
    f["nifty_change_1w"] = market.get("nifty", {}).get("change_1w") or 0.0
    f["nifty_rsi"] = market.get("nifty", {}).get("rsi") or 50.0
    f["nifty_sma"] = market.get("nifty", {}).get("above_sma20") or 0

    f["nasdaq_change"] = chg("nasdaq")
    f["sp500_change"] = chg("sp500")
    f["nikkei_change"] = chg("nikkei")
    f["hangseng_change"] = chg("hangseng")

    f["usd_inr_change"] = chg("usd_inr")
    f["crude_change"] = chg("crude")
    f["gold_change"] = chg("gold")

    f["us_vix"] = pr("vix") or 15.0
    f["india_vix"] = pr("india_vix") or 14.0

    f["rsi_signal"] = 1 if f["nifty_rsi"] < 35 else (-1 if f["nifty_rsi"] > 65 else 0)
    f["trend_signal"] = 1 if f["nifty_sma"] else -1
    f["asia_bull"] = 1 if (f["nikkei_change"] > 0.5 and f["hangseng_change"] > 0.5) else 0
    f["asia_bear"] = 1 if (f["nikkei_change"] < -0.5 and f["hangseng_change"] < -0.5) else 0
    f["global_bull"] = 1 if (f["nasdaq_change"] > 0.5 and f["sp500_change"] > 0.3) else 0
    f["global_risk"] = 1 if (f["us_vix"] > 20 or f["crude_change"] > 2.5) else 0
    f["gold_fear"] = 1 if (f["gold_change"] > 1.0 and f["us_vix"] > 18) else 0
    f["weekly_trend"] = 1 if f["nifty_change_1w"] > 1.5 else (-1 if f["nifty_change_1w"] < -1.5 else 0)

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
WEIGHTS["nifty_change"] = 0.18
WEIGHTS["nasdaq_change"] = 0.12
WEIGHTS["sp500_change"] = 0.08
WEIGHTS["nikkei_change"] = 0.08
WEIGHTS["hangseng_change"] = 0.06
WEIGHTS["usd_inr_change"] = -0.10
WEIGHTS["crude_change"] = -0.08
WEIGHTS["global_risk"] = -0.10
WEIGHTS["global_bull"] = 0.08
WEIGHTS["asia_bull"] = 0.06
WEIGHTS["asia_bear"] = -0.06
WEIGHTS["rsi_signal"] = 0.08
WEIGHTS["trend_signal"] = 0.06
WEIGHTS["weekly_trend"] = 0.05
WEIGHTS["gold_fear"] = -0.05


def compute_signal(f, regime):
    score = 0.5
    continuous = ["nifty_change", "nasdaq_change", "sp500_change", "nikkei_change",
                  "hangseng_change", "usd_inr_change", "crude_change"]

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


def build_drivers(f):
    drivers = []

    drivers.append({
        "icon": "📊",
        "tag": "pos" if f["nifty_change"] >= 0 else "neg",
        "label": f"Nifty {'up' if f['nifty_change']>=0 else 'down'} {abs(round(f['nifty_change'],2))}% | RSI {round(f['nifty_rsi'],0)}",
        "detail": f"{round(f['nifty_change_1w'],2)}% weekly"
    })

    drivers.append({
        "icon": "📈" if f["nasdaq_change"] >= 0 else "📉",
        "tag": "pos" if f["nasdaq_change"] >= 0 else "neg",
        "label": f"Nasdaq {round(f['nasdaq_change'],2)}% | S&P {round(f['sp500_change'],2)}%",
        "detail": "US markets"
    })

    drivers.append({
        "icon": "🌏",
        "tag": "pos" if (f["nikkei_change"] + f["hangseng_change"]) >= 0 else "neg",
        "label": f"Nikkei {round(f['nikkei_change'],2)}% | HangSeng {round(f['hangseng_change'],2)}%",
        "detail": "Asian markets"
    })

    drivers.append({
        "icon": "💱",
        "tag": "neg" if f["usd_inr_change"] > 0.2 else "pos",
        "label": f"INR {'weakening' if f['usd_inr_change']>0.2 else 'stable'}",
        "detail": f"USD/INR {round(f['usd_inr_change'],2)}%"
    })

    drivers.append({
        "icon": "🛢",
        "tag": "neg" if f["crude_change"] > 1 else "pos",
        "label": f"Crude {'rising' if f['crude_change']>1 else 'stable'}",
        "detail": f"{round(f['crude_change'],2)}%"
    })

    drivers.append({
        "icon": "⚡" if f["india_vix"] > 18 else "😌",
        "tag": "neg" if f["india_vix"] > 18 else "pos",
        "label": f"India VIX {round(f['india_vix'],1)} | US VIX {round(f['us_vix'],1)}",
        "detail": "elevated" if f["india_vix"] > 18 else "calm"
    })

    return drivers


# Remaining endpoints SAME AS YOUR CODE (unchanged)
# (Already correct after syntax fixes)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
