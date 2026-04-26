import os
import requests
import schedule
import time
from datetime import datetime

TELEGRAM_TOKEN = os.environ.get(“TELEGRAM_TOKEN”, “”)
CHAT_ID = os.environ.get(“CHAT_ID”, “”)
BACKEND_URL = os.environ.get(“BACKEND_URL”, “https://nifty-ai-backend-es7z.onrender.com”)
DASHBOARD_URL = os.environ.get(“DASHBOARD_URL”, “https://sree789p.github.io/nifty-ai-backend”)

def fetch_signal():
try:
res = requests.get(BACKEND_URL + “/api/signal”, timeout=60)
if res.status_code == 200:
return res.json()
return None
except Exception as e:
print(“Fetch error: “ + str(e))
return None

def format_message(data, session):
if not data or data.get(“status”) != “ok”:
return “Error fetching signal. Please check backend.”

```
sig = data.get("signal", {})
mkt = data.get("market", {})
drivers = data.get("drivers", [])
regime = data.get("regime", "range")

direction = sig.get("direction", "neutral").upper()
confidence = sig.get("confidence", 0)
nifty = mkt.get("nifty", {})
nifty_price = nifty.get("price", 0)
nifty_chg = nifty.get("change_pct", 0) or 0
india_vix = mkt.get("india_vix", {}).get("price", 0)

if direction == "BULLISH":
    signal_emoji = "🟢"
elif direction == "BEARISH":
    signal_emoji = "🔴"
else:
    signal_emoji = "🟡"

if regime == "volatile":
    regime_text = "⚡ HIGH VOLATILITY"
elif regime == "trending":
    regime_text = "↗ TRENDING"
else:
    regime_text = "↔ RANGE BOUND"

chg_arrow = "▲" if nifty_chg >= 0 else "▼"

if session == "morning":
    session_line = "🌅 MORNING SIGNAL — Pre-Market"
else:
    session_line = "🌆 EVENING SIGNAL — Post-Market"

date_str = datetime.now().strftime("%d %b %Y")

driver_lines = ""
for d in drivers[:4]:
    driver_lines += d.get("icon", "") + " " + d.get("label", "") + "\n"

msg = "🧠 *NIFTY AI SIGNAL ENGINE*\n"
msg += session_line + " | " + date_str + "\n"
msg += "━━━━━━━━━━━━━━━━━━━━\n\n"
msg += "📊 *Nifty 50:* " + str(nifty_price) + "  " + chg_arrow + " " + str(abs(nifty_chg)) + "%\n"
msg += "⚡ *Regime:* " + regime_text + "\n"
msg += "🎯 *Signal:* " + signal_emoji + " *" + direction + "* (" + str(confidence) + "%)\n"
msg += "📉 *India VIX:* " + str(india_vix) + "\n\n"
msg += "📌 *Key Drivers:*\n"
msg += driver_lines
msg += "\n🔗 [Open Dashboard](" + DASHBOARD_URL + ")\n"
msg += "━━━━━━━━━━━━━━━━━━━━\n"
msg += "_Not financial advice_"

return msg
```

def send_telegram(message):
if not TELEGRAM_TOKEN or not CHAT_ID:
print(“Missing TELEGRAM_TOKEN or CHAT_ID”)
return False
url = “https://api.telegram.org/bot” + TELEGRAM_TOKEN + “/sendMessage”
payload = {
“chat_id”: CHAT_ID,
“text”: message,
“parse_mode”: “Markdown”,
“disable_web_page_preview”: False
}
try:
res = requests.post(url, json=payload, timeout=30)
if res.status_code == 200:
print(“Message sent successfully”)
return True
else:
print(“Telegram error: “ + str(res.text))
return False
except Exception as e:
print(“Send error: “ + str(e))
return False

def send_morning_signal():
print(“Sending morning signal at “ + datetime.now().strftime(”%H:%M IST”))
data = fetch_signal()
msg = format_message(data, “morning”)
send_telegram(msg)

def send_evening_signal():
print(“Sending evening signal at “ + datetime.now().strftime(”%H:%M IST”))
data = fetch_signal()
msg = format_message(data, “evening”)
send_telegram(msg)

def send_test():
print(“Sending test message…”)
data = fetch_signal()
msg = format_message(data, “morning”)
msg = “✅ *TEST MESSAGE*\n\n” + msg
send_telegram(msg)

if **name** == “**main**”:
print(“🤖 Nifty AI Telegram Bot starting…”)
print(“Backend: “ + BACKEND_URL)

```
send_test()

schedule.every().day.at("03:30").do(send_morning_signal)
schedule.every().day.at("10:30").do(send_evening_signal)

print("Scheduled: 9:00 AM IST and 4:00 PM IST daily")
print("Bot is running... (Ctrl+C to stop)")

while True:
    schedule.run_pending()
    time.sleep(60)
