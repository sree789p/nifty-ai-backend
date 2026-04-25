“””
Nifty AI Signal Engine — Flask Backend
Cloud-ready for Render / Railway deployment.

Fetches LIVE data from Yahoo Finance via yfinance.
No API keys needed. No local Python required.
“””

import os
import traceback
from datetime import datetime

from flask import Flask, jsonify
from flask_cors import CORS
import yfinance as yf
import pandas as pd
import numpy as np

app = Flask(**name**)

# Allow ALL origins in free-tier deploy (lock this down later if needed)

CORS(app, resources={r”/api/*”: {“origins”: “*”}})

# ─────────────────────────────────────────────

# TICKER MAP  (Yahoo Finance symbols)

# ─────────────────────────────────────────────

TICKERS = {
“nifty”:      “^NSEI”,
“banknifty”:  “^NSEBANK”,
“nasdaq”:     “^IXIC”,
“usd_inr”:    “INR=X”,
“crude”:      “CL=F”,
“vix”:        “^VIX”,
“india_vix”:  “^INDIAVIX”,
“gold”:       “GC=F”,
“us10y”:      “^TNX”,
}

# ─────────────────────────────────────────────

# DATA FETCHER

# ─────────────────────────────────────────────

def fetch_price(symbol: str, period: str = “5d”, interval: str = “1d”) -> pd.DataFrame:
try:
df = yf.download(symbol, period=period, interval=interval,
progress=False, auto_adjust=True)
return df
except Exception as e:
print(f”[WARN] fetch_price({symbol}): {e}”)
return pd.DataFrame()

def get_latest(df: pd.DataFrame) -> dict:
if df is None or df.empty:
return {“price”: None, “prev”: None, “change_pct”: None}
close = df[“Close”].dropna()
if len(close) == 0:
return {“price”: None, “prev”: None, “change_pct”: None}
latest = float(close.iloc[-1])
if len(close) >= 2:
prev = float(close.iloc[-2])
chg  = round((latest - prev) / prev * 100, 2)
else:
prev, chg = None, None
return {“price”: round(latest, 2), “prev”: prev and round(prev, 2), “change_pct”: chg}

# ─────────────────────────────────────────────

# FEATURES

# ─────────────────────────────────────────────

def compute_features(market: dict) -> dict:
def chg(key):
return market.get(key, {}).get(“change_pct”) or 0.0

```
f = {
    "nifty_change":     chg("nifty"),
    "banknifty_change": chg("banknifty"),
    "nasdaq_change":    chg("nasdaq"),
    "usd_inr_change":   chg("usd_inr"),
    "crude_change":     chg("crude"),
    "gold_change":      chg("gold"),
    "us_vix":           market.get("vix",        {}).get("price") or 15.0,
    "india_vix":        market.get("india_vix",  {}).get("price") or 14.0,
    "us10y":            market.get("us10y",       {}).get("price") or 4.3,
}

f["global_risk_off"] = 1 if (f["us_vix"] > 20 or f["crude_change"] > 2.5
                              or f["usd_inr_change"] > 0.5) else 0
f["global_bull"]     = 1 if f["nasdaq_change"] > 0.5 else 0
f["inr_pressure"]    = 1 if f["usd_inr_change"] > 0.3 else 0
return f
```

# ─────────────────────────────────────────────

# REGIME ENGINE

# ─────────────────────────────────────────────

def detect_regime(f: dict) -> str:
if f[“india_vix”] > 18 or f[“us_vix”] > 22:
return “volatile”
if abs(f[“nifty_change”]) > 0.8:
return “trending”
return “range”

# ─────────────────────────────────────────────

# SIGNAL ENGINE

# ─────────────────────────────────────────────

WEIGHTS = {
“nifty_change”:     0.25,
“nasdaq_change”:    0.20,
“usd_inr_change”:  -0.15,
“crude_change”:    -0.10,
“global_risk_off”: -0.15,
“global_bull”:      0.15,
}

def compute_signal(f: dict, regime: str) -> dict:
score = 0.5
for feat, w in WEIGHTS.items():
val = f.get(feat, 0)
if feat in (“nifty_change”, “nasdaq_change”, “usd_inr_change”, “crude_change”):
val = max(-3, min(3, val)) / 3.0
score += w * val

```
if regime == "volatile":
    score = 0.5 + (score - 0.5) * 0.6
elif regime == "trending":
    score = 0.5 + (score - 0.5) * 1.2

score = max(0.05, min(0.95, score))

if score > 0.60:
    direction = "bullish"
elif score < 0.40:
    direction = "bearish"
else:
    direction = "neutral"

return {
    "direction":   direction,
    "probability": round(score, 4),
    "confidence":  round(score * 100, 1),
}
```

# ─────────────────────────────────────────────

# DRIVERS

# ─────────────────────────────────────────────

def build_drivers(f: dict) -> list:
drivers = []

```
n = f["nifty_change"]
drivers.append({"icon": "📊", "tag": "pos" if n >= 0 else "neg",
                "label": f"Nifty {'up' if n>=0 else 'down'} {abs(n):.2f}%",
                "detail": f"{n:+.2f}% today"})

nq = f["nasdaq_change"]
drivers.append({"icon": "📈" if nq >= 0 else "📉", "tag": "pos" if nq >= 0 else "neg",
                "label": f"Nasdaq {'strong' if nq>=0 else 'weak'} ({nq:+.2f}%)",
                "detail": ""})

fx = f["usd_inr_change"]
drivers.append({"icon": "💱", "tag": "neg" if fx > 0.2 else "pos",
                "label": f"INR {'weakening' if fx>0.2 else 'stable/strong'}",
                "detail": f"USD/INR {fx:+.2f}%"})

cr = f["crude_change"]
drivers.append({"icon": "🛢️", "tag": "neg" if cr > 1 else "pos",
                "label": f"Crude {'rising — headwind' if cr>1 else 'stable'}",
                "detail": f"{cr:+.2f}%"})

vix = f["india_vix"]
drivers.append({"icon": "⚡" if vix > 18 else "😌",
                "tag": "neg" if vix > 18 else "pos",
                "label": f"India VIX {vix:.1f} ({'elevated' if vix>18 else 'calm'})",
                "detail": ""})

return drivers
```

# ─────────────────────────────────────────────

# SPARKLINE (last 20 daily closes for Nifty)

# ─────────────────────────────────────────────

def get_sparkline(symbol: str, points: int = 20) -> list:
try:
df = yf.download(symbol, period=“1mo”, interval=“1d”,
progress=False, auto_adjust=True)
closes = df[“Close”].dropna().tail(points).tolist()
return [round(float(v), 2) for v in closes]
except Exception:
return []

# ─────────────────────────────────────────────

# HEALTH CHECK

# ─────────────────────────────────────────────

@app.route(”/”, methods=[“GET”])
def index():
return jsonify({
“name”: “Nifty AI Signal Engine”,
“version”: “1.0”,
“status”: “running”,
“endpoints”: [”/api/data”, “/api/signal”, “/api/features”],
})

@app.route(”/health”, methods=[“GET”])
def health():
return jsonify({“status”: “ok”}), 200

# ─────────────────────────────────────────────

# API ROUTES

# ─────────────────────────────────────────────

@app.route(”/api/data”, methods=[“GET”])
def api_data():
market = {}
for name, sym in TICKERS.items():
df = fetch_price(sym)
market[name] = {**get_latest(df), “symbol”: sym}
return jsonify({“status”: “ok”, “timestamp”: datetime.now().isoformat(), “market”: market})

@app.route(”/api/signal”, methods=[“GET”])
def api_signal():
try:
# 1. Fetch prices
market = {}
for name, sym in TICKERS.items():
market[name] = get_latest(fetch_price(sym))

```
    # 2. Features → Regime → Signal → Drivers → Sparkline
    feats    = compute_features(market)
    regime   = detect_regime(feats)
    signal   = compute_signal(feats, regime)
    drivers  = build_drivers(feats)
    sparkline = get_sparkline(TICKERS["nifty"])

    return jsonify({
        "status":    "ok",
        "timestamp": datetime.now().isoformat(),
        "signal":    signal,
        "regime":    regime,
        "drivers":   drivers,
        "sparkline": sparkline,
        "market": {
            "nifty":     market["nifty"],
            "banknifty": market["banknifty"],
            "nasdaq":    market["nasdaq"],
            "usd_inr":   market["usd_inr"],
            "crude":     market["crude"],
            "india_vix": market["india_vix"],
        },
    })

except Exception as e:
    traceback.print_exc()
    return jsonify({"status": "error", "message": str(e)}), 500
```

@app.route(”/api/features”, methods=[“GET”])
def api_features():
try:
market = {n: get_latest(fetch_price(s)) for n, s in TICKERS.items()}
feats  = compute_features(market)
return jsonify({
“status”:    “ok”,
“timestamp”: datetime.now().isoformat(),
“features”:  feats,
“regime”:    detect_regime(feats),
})
except Exception as e:
return jsonify({“status”: “error”, “message”: str(e)}), 500

# ─────────────────────────────────────────────

# ENTRY POINT

# Render/Railway inject PORT via environment variable.

# Gunicorn reads this automatically via Procfile.

# ─────────────────────────────────────────────

if **name** == “**main**”:
port = int(os.environ.get(“PORT”, 5000))
app.run(host=“0.0.0.0”, port=port, debug=False)
