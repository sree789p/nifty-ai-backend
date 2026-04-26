import os
import traceback
import requests
from datetime import datetime
from flask import Flask, jsonify, request
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
“india_vix”: “^INDIAVIX”
}

TELEGRAM_TOKEN = os.environ.get(“TELEGRAM_TOKEN”, “”)
CHAT_ID = os.environ.get(“CHAT_ID”, “”)
DASHBOARD_URL = os.environ.get(“DASHBOARD_URL”, “https://sree789p.github.io/nifty-ai-backend”)

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
print(“Error:”, str(e))
return {“price”: None, “change_pct”: None}

def compute_features(market):
def chg(key):
return market.get(key, {}).get(“change_pct”) or 0.0
def price(key):
return market.get(key, {}).get(“price”) or 0.0
f = {}
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
drivers.append({“icon”: “📊”, “tag”: “pos” if n >= 0 else “neg”, “label”: “Nifty “ + (“up “ if n >= 0 else “down “) + str(abs(round(n, 2))) + “%”, “detail”: str(round(n, 2)) + “% today”})
nq = f[“nasdaq_change”]
drivers.append({“icon”: “📈” if nq >= 0 else “📉”, “tag”: “pos” if nq >= 0 else “neg”, “label”: “Nasdaq “ + (“strong” if nq >= 0 else “weak”) + “ (” + str(round(nq, 2)) + “%)”, “detail”: “”})
fx = f[“usd_inr_change”]
drivers.append({“icon”: “💱”, “tag”: “neg” if fx > 0.2 else “pos”, “label”: “INR “ + (“weakening” if fx > 0.2 else “stable”), “detail”: “USD/INR “ + str(round(fx, 2)) + “%”})
cr = f[“crude_change”]
drivers.append({“icon”: “🛢”, “tag”: “neg” if cr > 1 else “pos”, “label”: “Crude “ + (“rising” if cr > 1 else “stable”), “detail”: str(round(cr, 2)) + “%”})
vix = f[“india_vix”]
drivers.append({“icon”: “⚡” if vix > 18 else “😌”, “tag”: “neg” if vix > 18 else “pos”, “label”: “India VIX “ + str(round(vix, 1)) + “ (” + (“elevated” if vix > 18 else “calm”) + “)”, “detail”: “”})
return drivers

def get_sparkline(symbol, points=20):
try:
ticker = yf.Ticker(symbol)
hist = ticker.history(period=“1mo”)
closes = hist[“Close”].dropna().tail(points).tolist()
return [round(float(v), 2) for v in closes]
except Exception:
return []

def build_telegram_message(data, session):
sig = data.get(“signal”, {})
mkt = data.get(“market”, {})
drivers = data.get(“drivers”, [])
regime = data.get(“regime”, “range”)
direction = sig.get(“direction”, “neutral”).upper()
confidence = sig.get(“confidence”, 0)
nifty = mkt.get(“nifty”, {})
nifty_price = nifty.get(“price”, 0) or 0
nifty_chg = nifty.get(“change_pct”, 0) or 0
india_vix = mkt.get(“india_vix”, {}).get(“price”, 0) or 0
if direction == “BULLISH”:
sig_emoji = “🟢”
elif direction == “BEARISH”:
sig_emoji = “🔴”
else:
sig_emoji = “🟡”
if regime == “volatile”:
regime_text = “HIGH VOLATILITY”
elif regime == “trending”:
regime_text = “TRENDING”
else:
regime_text = “RANGE BOUND”
chg_arrow = “▲” if nifty_chg >= 0 else “▼”
session_text = “MORNING — Pre-Market” if session == “morning” else “EVENING — Post-Market”
date_str = datetime.now().strftime(”%d %b %Y”)
driver_lines = “”
for d in drivers[:4]:
driver_lines += d.get(“icon”, “”) + “ “ + d.get(“label”, “”) + “\n”
msg = “🧠 *NIFTY AI SIGNAL ENGINE*\n”
msg += session_text + “ | “ + date_str + “\n”
msg += “━━━━━━━━━━━━━━━━━━━━\n\n”
msg += “📊 *Nifty 50:* “ + str(nifty_price) + “  “ + chg_arrow + “ “ + str(abs(nifty_chg)) + “%\n”
msg += “⚡ *Regime:* “ + regime_text + “\n”
msg += “🎯 *Signal:* “ + sig_emoji + “ *” + direction + “* (” + str(confidence) + “%)\n”
msg += “📉 *India VIX:* “ + str(india_vix) + “\n\n”
msg += “📌 *Key Drivers:*\n”
msg += driver_lines
msg += “\n🔗 [Open Dashboard](” + DASHBOARD_URL + “)\n”
msg += “━━━━━━━━━━━━━━━━━━━━\n”
msg += “*Not financial advice*”
return msg

def send_telegram(message):
if not TELEGRAM_TOKEN or not CHAT_ID:
return False, “Missing TELEGRAM_TOKEN or CHAT_ID env vars”
url = “https://api.telegram.org/bot” + TELEGRAM_TOKEN + “/sendMessage”
payload = {“chat_id”: CHAT_ID, “text”: message, “parse_mode”: “Markdown”, “disable_web_page_preview”: False}
try:
res = requests.post(url, json=payload, timeout=30)
if res.status_code == 200:
return True, “sent”
return False, res.text
except Exception as e:
return False, str(e)

@app.route(”/”, methods=[“GET”])
def index():
return jsonify({“name”: “Nifty AI”, “status”: “running”})

@app.route(”/health”, methods=[“GET”])
def health():
return jsonify({“status”: “ok”}), 200

@app.route(”/api/signal”, methods=[“GET”])
def api_signal():
try:
market = {name: fetch_latest(TICKERS[name]) for name in TICKERS}
feats = compute_features(market)
regime = detect_regime(feats)
signal = compute_signal(feats, regime)
drivers = build_drivers(feats)
sparkline = get_sparkline(”^NSEI”)
return jsonify({“status”: “ok”, “timestamp”: datetime.now().isoformat(), “signal”: signal, “regime”: regime, “drivers”: drivers, “sparkline”: sparkline, “market”: market})
except Exception as e:
traceback.print_exc()
return jsonify({“status”: “error”, “message”: str(e)}), 500

@app.route(”/api/data”, methods=[“GET”])
def api_data():
market = {name: fetch_latest(TICKERS[name]) for name in TICKERS}
return jsonify({“status”: “ok”, “timestamp”: datetime.now().isoformat(), “market”: market})

@app.route(”/api/telegram”, methods=[“GET”])
def api_telegram():
try:
session = request.args.get(“session”, “morning”)
market = {name: fetch_latest(TICKERS[name]) for name in TICKERS}
feats = compute_features(market)
regime = detect_regime(feats)
signal = compute_signal(feats, regime)
drivers = build_drivers(feats)
data = {“signal”: signal, “regime”: regime, “drivers”: drivers, “market”: market}
message = build_telegram_message(data, session)
ok, result = send_telegram(message)
if ok:
return jsonify({“status”: “ok”, “message”: “Telegram alert sent”})
return jsonify({“status”: “error”, “message”: result}), 500
except Exception as e:
traceback.print_exc()
return jsonify({“status”: “error”, “message”: str(e)}), 500

@app.route(”/api/telegram/test”, methods=[“GET”])
def api_telegram_test():
ok, result = send_telegram(“✅ *Nifty AI Bot is connected!*\nYour alerts are set up correctly.”)
if ok:
return jsonify({“status”: “ok”, “message”: “Test message sent to Telegram”})
return jsonify({“status”: “error”, “message”: result}), 500

if **name** == “**main**”:
port = int(os.environ.get(“PORT”, 5000))
app.run(host=“0.0.0.0”, port=port, debug=False)
