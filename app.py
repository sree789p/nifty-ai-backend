import os
import traceback
from datetime import datetime
from flask import Flask, jsonify
from flask_cors import CORS
import yfinance as yf
import pandas as pd

app = Flask(**name**)
CORS(app)

TICKERS = {
“nifty”: “^NSEI”,
“banknifty”: “^NSEBANK”,
“nasdaq”: “^IXIC”,
“usd_inr”: “INR=X”,
“crude”: “CL=F”,
“vix”: “^VIX”,
“india_vix”: “^INDIAVIX”,
}

def fetch_latest(symbol):
try:
ticker = yf.Ticker(symbol)
hist = ticker.history(period=“5d”)
if hist.empty or len(hist) < 1:
return {“price”: None, “change_pct”: None}
close = hist[“Close”].dropna()
latest = float(close.iloc[-1])
if len(close) >= 2:
prev = float(close.iloc[-2])
chg = round((latest - prev) / prev * 100, 2)
else:
chg = None
return {“price”: round(latest, 2), “change_pct”: chg}
except Exception as e:
print(“Error fetching “ + symbol + “: “ + str(e))
return {“price”: None, “change_pct”: None}

def compute_features(market):
def chg(key):
return market.get(key, {}).get(“change_pct”) or 0.0
def price(key):
return market.get(key, {}).get(“price”) or 0.0

```
f = {
    "nifty_change": chg("nifty"),
    "nasdaq_change": chg("nasdaq"),
    "usd_inr_change": chg("usd_inr"),
    "crude_change": chg("crude"),
    "us_vix": price("vix") or 15.0,
    "india_vix": price("india_vix") or 14.0,
}
f["global_risk_off"] = 1 if (f["us_vix"] > 20 or f["crude_change"] > 2.5) else 0
f["global_bull"] = 1 if f["nasdaq_change"] > 0.5 else 0
return f
```

def detect_regime(f):
if f[“india_vix”] > 18 or f[“us_vix”] > 22:
return “volatile”
if abs(f[“nifty_change”]) > 0.8:
return “trending”
return “range”

WEIGHTS = {
“nifty_change”: 0.25,
“nasdaq_change”: 0.20,
“usd_inr_change”: -0.15,
“crude_change”: -0.10,
“global_risk_off”: -0.15,
“global_bull”: 0.15,
}

def compute_signal(f, regime):
score = 0.5
for feat, w in WEIGHTS.items():
val = f.get(feat, 0)
if feat in (“nifty_change”, “nasdaq_change”, “usd_inr_change”, “crude_change”):
val = max(-3, min(3, val)) / 3.0
score += w * val
if regime == “volatile”:
score = 0.5 + (score - 0.5) * 0.6
elif regime == “trending”:
score = 0.5 + (score - 0.5) * 1.2
score = max(0.05, min(0.95, score))
if score > 0.60:
direction = “bullish”
elif score < 0.40:
direction = “bearish”
else:
direction = “neutral”
return {“direction”: direction, “probability”: round(score, 4), “confidence”: round(score * 100, 1)}

def build_drivers(f):
drivers = []
n = f[“nifty_change”]
drivers.append({“icon”: “📊”, “tag”: “pos” if n >= 0 else “neg”, “label”: “Nifty “ + (“up “ if n >= 0 else “down “) + str(abs(round(n,2))) + “%”, “detail”: str(round(n,2)) + “% today”})
nq = f[“nasdaq_change”]
drivers.append({“icon”: “📈” if nq >= 0 else “📉”, “tag”: “pos” if nq >= 0 else “neg”, “label”: “Nasdaq “ + (“strong” if nq >= 0 else “weak”) + “ (” + str(round(nq,2)) + “%)”, “detail”: “”})
fx = f[“usd_inr_change”]
drivers.append({“icon”: “💱”, “tag”: “neg” if fx > 0.2 else “pos”, “label”: “INR “ + (“weakening” if fx > 0.2 else “stable”), “detail”: “USD/INR “ + str(round(fx,2)) + “%”})
cr = f[“crude_change”]
drivers.append({“icon”: “🛢️”, “tag”: “neg” if cr > 1 else “pos”, “label”: “Crude “ + (“rising” if cr > 1 else “stable”), “detail”: str(round(cr,2)) + “%”})
vix = f[“india_vix”]
drivers.append({“icon”: “⚡” if vix > 18 else “😌”, “tag”: “neg” if vix > 18 else “pos”, “label”: “India VIX “ + str(round(vix,1)) + “ (” + (“elevated” if vix > 18 else “calm”) + “)”, “detail”: “”})
return drivers

def get_sparkline(symbol, points=20):
try:
ticker = yf.Ticker(symbol)
hist = ticker.history(period=“1mo”)
closes = hist[“Close”].dropna().tail(points).tolist()
return [round(float(v), 2) for v in closes]
except Exception:
return []

@app.route(”/”, methods=[“GET”])
def index():
return jsonify({“name”: “Nifty AI Signal Engine”, “version”: “1.0”, “status”: “running”})

@app.route(”/health”, methods=[“GET”])
def health():
return jsonify({“status”: “ok”}), 200

@app.route(”/api/signal”, methods=[“GET”])
def api_signal():
try:
market = {}
for name, sym in TICKERS.items():
market[name] = fetch_latest(sym)

```
    feats = compute_features(market)
    regime = detect_regime(feats)
    signal = compute_signal(feats, regime)
    drivers = build_drivers(feats)
    sparkline = get_sparkline("^NSEI")

    return jsonify({
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "signal": signal,
        "regime": regime,
        "drivers": drivers,
        "sparkline": sparkline,
        "market": {
            "nifty": market["nifty"],
            "banknifty": market["banknifty"],
            "nasdaq": market["nasdaq"],
            "usd_inr": market["usd_inr"],
            "crude": market["crude"],
            "india_vix": market["india_vix"],
        },
    })
except Exception as e:
    traceback.print_exc()
    return jsonify({"status": "error", "message": str(e)}), 500
```

@app.route(”/api/data”, methods=[“GET”])
def api_data():
market = {}
for name, sym in TICKERS.items():
market[name] = fetch_latest(sym)
return jsonify({“status”: “ok”, “timestamp”: datetime.now().isoformat(), “market”: market})

if **name** == “**main**”:
port = int(os.environ.get(“PORT”, 5000))
app.run(host=“0.0.0.0”, port=port, debug=False)
