import os
import traceback
import requests
from datetime import datetime
from flask import Flask, jsonify, request
from flask_cors import CORS
import yfinance as yf
import pandas as pd

app = Flask(__name__)
CORS(app)

# ================= CONFIG =================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "https://sree789p.github.io/nifty-ai-backend")

TICKERS = {
    "nifty": "^NSEI",
    "banknifty": "^NSEBANK",
    "nasdaq": "^IXIC",
    "sp500": "^GSPC",
    "usd_inr": "INR=X",
    "crude": "CL=F",
    "gold": "GC=F",
    "vix": "^VIX",
    "india_vix": "^INDIAVIX",
    "us10y": "^TNX",
    "nifty_it": "^CNXIT"
}

# ================= CACHE =================
CACHE = {}
CACHE_TTL = 60

def get_cached_data(symbol):
    now = datetime.now().timestamp()
    if symbol in CACHE:
        data, ts = CACHE[symbol]
        if now - ts < CACHE_TTL:
            return data
    data = get_price_data(symbol)
    CACHE[symbol] = (data, now)
    return data

# ================= DATA =================
def fetch_intraday(symbol):
    try:
        return yf.Ticker(symbol).history(period="5d", interval="1h")
    except:
        return pd.DataFrame()

def fetch_daily(symbol):
    try:
        return yf.Ticker(symbol).history(period="30d", interval="1d")
    except:
        return pd.DataFrame()

def get_price_data(symbol):
    result = {"price": None,"prev_close": None,"change_pct": None,"change_1h": None,"change_1w": None,"rsi": None,"above_sma20": None,"momentum": None}
    try:
        intraday = fetch_intraday(symbol)
        daily = fetch_daily(symbol)

        if not intraday.empty:
            c = intraday["Close"].dropna()
            if len(c) >= 2:
                result["price"] = float(c.iloc[-1])
                result["change_1h"] = round((c.iloc[-1]-c.iloc[-2])/c.iloc[-2]*100,2)
            if len(c) >= 7:
                result["change_1w"] = round((c.iloc[-1]-c.iloc[-7])/c.iloc[-7]*100,2)

        if not daily.empty:
            c = daily["Close"].dropna()
            if len(c) >= 2:
                if result["price"] is None:
                    result["price"] = float(c.iloc[-1])
                result["prev_close"] = float(c.iloc[-2])
                result["change_pct"] = round((result["price"]-result["prev_close"])/result["prev_close"]*100,2)

            if len(c) >= 20:
                sma = c.tail(20).mean()
                result["above_sma20"] = 1 if result["price"] and result["price"] > sma else 0

            if len(c) >= 14:
                delta = c.diff()
                gain = delta.clip(lower=0).tail(14).mean()
                loss = -delta.clip(upper=0).tail(14).mean()
                if loss and loss > 1e-6:
                    rs = gain/loss
                    result["rsi"] = round(100-(100/(1+rs)),1)
                else:
                    result["rsi"] = 100.0

            if len(c) >= 5:
                result["momentum"] = float(c.iloc[-1]-c.iloc[-5])

    except Exception as e:
        print("Data error:", symbol, str(e))
    return result

# ================= FEATURES =================
def compute_features(m):
    def v(k,f,d=0): return m.get(k,{}).get(f) or d

    f={}
    f["nifty_change"]=v("nifty","change_pct")
    f["nifty_change_1h"]=v("nifty","change_1h")
    f["nifty_change_1w"]=v("nifty","change_1w")
    f["nifty_rsi"]=v("nifty","rsi",50)
    f["nifty_above_sma20"]=v("nifty","above_sma20")

    f["nasdaq_change"]=v("nasdaq","change_pct")
    f["sp500_change"]=v("sp500","change_pct")
    f["usd_inr_change"]=v("usd_inr","change_pct")
    f["crude_change"]=v("crude","change_pct")
    f["gold_change"]=v("gold","change_pct")

    f["us_vix"]=v("vix","price",15)
    f["india_vix"]=v("india_vix","price",14)

    f["rsi_signal"]=1 if f["nifty_rsi"]<35 else (-1 if f["nifty_rsi"]>65 else 0)
    f["trend_signal"]=1 if f["nifty_above_sma20"]==1 else -1
    f["global_risk_off"]=1 if (f["us_vix"]>20 or f["crude_change"]>2.5 or f["usd_inr_change"]>0.5) else 0
    f["global_bull"]=1 if (f["nasdaq_change"]>0.5 and f["sp500_change"]>0.3) else 0
    f["intraday_momentum"]=1 if f["nifty_change_1h"]>0.3 else (-1 if f["nifty_change_1h"]<-0.3 else 0)
    f["weekly_trend"]=1 if f["nifty_change_1w"]>1 else (-1 if f["nifty_change_1w"]<-1 else 0)

    return f

def detect_regime(f):
    if f["india_vix"]>20 or f["us_vix"]>25: return "volatile"
    if abs(f["nifty_change_1w"])>2: return "trending"
    if f["nifty_rsi"]<35: return "oversold"
    if f["nifty_rsi"]>65: return "overbought"
    return "range"

# ================= MODEL =================
WEIGHTS={"nifty_change":0.15,"nifty_change_1h":0.07,"nasdaq_change":0.15,"sp500_change":0.08,"usd_inr_change":-0.1,"crude_change":-0.08,"global_risk_off":-0.12,"global_bull":0.1,"rsi_signal":0.08,"trend_signal":0.06,"intraday_momentum":0.03,"weekly_trend":0.03}

def compute_signal(f,regime):
    score=0.5
    for k,w in WEIGHTS.items():
        val=f.get(k,0)
        if "change" in k:
            val=max(-3,min(3,val))/3
        score+=w*val

    if regime=="volatile": score=0.5+(score-0.5)*0.5
    elif regime=="trending": score=0.5+(score-0.5)*1.3

    score=max(0.05,min(0.95,score))

    if score>0.62: d="bullish"
    elif score<0.38: d="bearish"
    else: d="neutral"

    return {"direction":d,"probability":round(score,4),"confidence":round(score*100,1)}

# ================= DRIVERS =================
def build_drivers(f):
    return [
        {"icon":"📊","label":f"Nifty {round(f['nifty_change'],2)}%"},
        {"icon":"📈","label":f"Nasdaq {round(f['nasdaq_change'],2)}%"},
        {"icon":"💱","label":f"INR {round(f['usd_inr_change'],2)}%"},
        {"icon":"🛢","label":f"Crude {round(f['crude_change'],2)}%"},
    ]

# ================= BACKTEST =================
def run_backtest():
    try:
        hist=yf.Ticker("^NSEI").history(period="3mo")
        c=hist["Close"].dropna()
        correct=0
        for i in range(20,len(c)-1):
            pred="bullish" if c.iloc[i]>c.iloc[i-1] else "bearish"
            actual="bullish" if c.iloc[i+1]>c.iloc[i] else "bearish"
            if pred==actual: correct+=1
        acc=round(correct/(len(c)-21)*100,1)
        return {"accuracy":acc}
    except:
        return {"error":"backtest failed"}

# ================= TELEGRAM =================
def send_telegram(msg):
    if not TELEGRAM_TOKEN: return False
    url=f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url,json={"chat_id":CHAT_ID,"text":msg})
    return True

# ================= ROUTES =================
@app.route("/")
def index():
    return jsonify({"status":"running"})

@app.route("/api/signal")
def api_signal():
    m={k:get_cached_data(v) for k,v in TICKERS.items()}
    f=compute_features(m)
    r=detect_regime(f)
    s=compute_signal(f,r)
    return jsonify({"signal":s,"regime":r,"market":m})

@app.route("/api/backtest")
def api_backtest():
    return jsonify(run_backtest())

@app.route("/api/telegram")
def api_telegram():
    m={k:get_cached_data(v) for k,v in TICKERS.items()}
    f=compute_features(m)
    r=detect_regime(f)
    s=compute_signal(f,r)
    msg=f"{s['direction']} ({s['confidence']}%)"
    send_telegram(msg)
    return jsonify({"status":"sent"})

if __name__=="__main__":
    port=int(os.environ.get("PORT",5000))
    app.run(host="0.0.0.0",port=port)
