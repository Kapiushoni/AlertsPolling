import json
import os
import threading
import time
import requests
from flask import Flask

app = Flask(__name__)

API_URL = "https://api.alerts.in.ua/v1/iot/active_air_raids.json"
API_TOKEN = os.environ.get("ALERTS_API_TOKEN")
CHECK_INTERVAL = 15
STATE_FILE = "last_state.json"
HEADERS = {"Authorization": f"Bearer {API_TOKEN}"}


def load_last_state():
  if os.path.exists(STATE_FILE):
    try:
      with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)
    except Exception:
      return {}
  return {}


def save_state(state):
  with open(STATE_FILE, "w", encoding="utf-8") as f:
    json.dump(state, f, ensure_ascii=False, indent=2)


def fetch_alerts():
  try:
    response = requests.get(API_URL, headers=HEADERS, timeout=10)
    if response.status_code == 200:
      return response.json()
    elif response.status_code == 429:
      print(
          "[!] Превышен лимит запросов (Rate Limit). Ждем перед повтором..."
      )
      time.sleep(30)
    else:
      print(f"[!] Ошибка API: {response.status_code} - {response.text}")
  except requests.exceptions.RequestException as e:
    print(f"[!] Ошибка сети: {e}")
  return None


def background_worker():
  print("[*] Фоновый монитор alerts.in.ua запущен...")
  last_data = load_last_state()

  while True:
    current_data = fetch_alerts()

    if current_data:
      if current_data != last_data:
        print(
            f"[*] Статус изменился! Время:"
            f" {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )

        # Отправка в n8n (раскомментируйте, когда будет готов вебхук)
        requests.post("https://alexn8n12345.app.n8n.cloud/webhook/air-alert", json=current_data)

        last_data = current_data
        save_state(last_data)

    time.sleep(CHECK_INTERVAL)


@app.route("/")
def home():
  return "Alerts Poller is running!", 200


if __name__ == "__main__":
  t = threading.Thread(target=background_worker, daemon=True)
  t.start()

  port = int(os.environ.get("PORT", 10000))
  app.run(host="0.0.0.0", port=port)
