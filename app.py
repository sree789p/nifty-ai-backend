import os
import traceback
from datetime import datetime
from flask import Flask, jsonify
from flask_cors import CORS
import yfinance as yf
import pandas as pd

app = Flask(**name**)
CORS(app)

TICKERS = dict()
TICKERS[“nifty”] = “^NSEI”
TICKERS[“banknifty”] = “^NSEBANK”
TICKERS[“nasdaq”] = “^IXIC”
TICKERS[“usd_inr”] = “INR=X”
TICKERS[“crude”] = “CL=F”
TICKERS[“vix”] = “^VIX”
TICKERS[“india_vix”] = “^INDIAVIX”

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
print(“Error: “ + str(e))
return {“price”: None, “change_pct”: None}

def compute_features(market):
def chg(key):
return market.get(key, {}).get(“change_pct”) or 0.0
def price(key):
return market.get(key, {}).get(“price”) or 0.0
f = dict()
f[“nifty_change”] = chg(“nifty”)
f[“nasdaq_change”] = chg(“nasdaq”)
f[“usd_inr_change”] = chg(“usd_inr”)
f[“crude_change”] = chg(“crude”)
f[“us_vix”] = price(“vix”) or 15.0
f[“india_vix”] = price(“india_vix”) or 14.0
f[“global_risk_off”] = 1 if (f[“us_vix”] > 20 or f[“crude_change”] > 2.5) else 0
f[“global_bull”] = 1 if f[“nasdaq_change”] > 0.5 else 0
return f

def detect_regime(f):
if f[“india_vix”] > 18 or f[“us_vix”] > 22:
return “volatile”
if abs(f[“nifty_change”]) > 0.8:
return “trending”
return “range”

def compute_signal(f, regime):
score = 0.5
score += 0.25 * max(-1, min(1, f[“nifty_change”] / 3.0))
score += 0.20 * max(-1, min(1, f[“nasdaq_change”] / 3.0))
score += -0.15 * max(-1, min(1, f[“usd_inr_change”] / 3.0))
score += -0.10 * max(-1, min(1, f[“crude_change”] / 3.0))
score += -0.15 * f[“global_risk_off”]
score += 0.15 * f[“global_bull”]
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
d1 = dict()
d1[“icon”] = “📊”
d1[“tag”] = “pos” if n >= 0 else “neg”
d1[“label”] = “Nifty “ + (“up “ if n >= 0 else “down “) + str(abs(round(n, 2))) + “%”
d1[“detail”] = str(round(n, 2)) + “% today”
drivers.append(d1)
nq = f[“nasdaq_change”]
d2 = dict()
d2[“icon”] = “📈” if nq >= 0 else “📉”
d2[“tag”] = “pos” if nq >= 0 else “neg”
d2[“label”] = “Nasdaq “ + (“strong” if nq >= 0 else “weak”) + “ (” + str(round(nq, 2)) + “%)”
d2[“detail”] = “”
drivers.append(d2)
fx = f[“usd_inr_change”]
d3 = dict()
d3[“icon”] = “💱”
d3[“tag”] = “neg” if fx > 0.2 else “pos”
d3[“label”] = “INR “ + (“weakening” if fx > 0.2 else “stable”)
d3[“detail”] = “USD/INR “ + str(round(fx, 2)) + “%”
drivers.append(d3)
cr = f[“crude_change”]
d4 = dict()
d4[“icon”] = “🛢”
d4[“tag”] = “neg” if cr > 1 else “pos”
d4[“label”] = “Crude “ + (“rising” if cr > 1 else “stable”)
d4[“detail”] = str(round(cr, 2)) + “%”
drivers.append(d4)
vix = f[“india_vix”]
d5 = dict()
d5[“icon”] = “⚡” if vix > 18 else “😌”
d5[“tag”] = “neg” if vix > 18 else “pos”
d5[“label”] = “India VIX “ + str(round(vix, 1)) + “ (” + (“elevated” if vix > 18 else “calm”) + “)”
d5[“detail”] = “”
drivers.append(d5)
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
return jsonify({“name”: “Nifty AI”, “status”: “running”})

@app.route(”/health”, methods=[“GET”])
def health():
return jsonify({“status”: “ok”}), 200

@app.route(”/api/signal”, methods=[“GET”])
def api_signal():
try:
market = dict()
for name in TICKERS:
market[name] = fetch_latest(TICKERS[name])
feats = compute_features(market)
regime = detect_regime(feats)
signal = compute_signal(feats, regime)
drivers = build_drivers(feats)
sparkline = get_sparkline(”^NSEI”)
result = dict()
result[“status”] = “ok”
result[“timestamp”] = datetime.now().isoformat()
result[“signal”] = signal
result[“regime”] = regime
result[“drivers”] = drivers
result[“sparkline”] = sparkline
mkt = dict()
mkt[“nifty”] = market[“nifty”]
mkt[“banknifty”] = market[“banknifty”]
mkt[“nasdaq”] = market[“nasdaq”]
mkt[“usd_inr”] = market[“usd_inr”]
mkt[“crude”] = market[“crude”]
mkt[“india_vix”] = market[“india_vix”]
result[“market”] = mkt
return jsonify(result)
except Exception as e:
traceback.print_exc()
return jsonify({“status”: “error”, “message”: str(e)}), 500

@app.route(”/api/data”, methods=[“GET”])
def api_data():
market = dict()
for name in TICKERS:
market[name] = fetch_latest(TICKERS[name])
return jsonify({“status”: “ok”, “timestamp”: datetime.now().isoformat(), “market”: market})

if **name** == “**main**”:
port = int(os.environ.get(“PORT”, 5000))
app.run(host=“0.0.0.0”, port=port, debug=False)
