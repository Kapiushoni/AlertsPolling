import json
import os
import threading
import time
import requests
from flask import Flask

app = Flask(__name__)

API_URL = "https://api.alerts.in.ua/v1/alerts/active.json"
API_TOKEN = os.environ.get("ALERTS_API_TOKEN")
CHECK_INTERVAL = 30
STATE_FILE = "last_state.json"
HEADERS = {"Authorization": f"Bearer {API_TOKEN}"}

N8N_WEBHOOK_URL = "https://alexn8n12345.app.n8n.cloud/webhook/air-alert"


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
    else:
      print(f"[!] Ошибка API текст: {response.text}", flush=True)
  except Exception as e:
    print(f"[!] Исключение при запросе к API: {e}", flush=True)
  return None


def background_worker():
  print("[*] Фоновый монитор alerts.in.ua запущен...", flush=True)
  last_data = load_last_state()

  while True:
    current_data = fetch_alerts()

    if current_data:
      # Проверяем, изменились ли данные по сравнению с последним разом
      if current_data != last_data:
        print(
            f"[*] Статус изменился! Время:"
            f" {time.strftime('%Y-%m-%d %H:%M:%S')}",
            flush=True,
        )
        try:
          response = requests.post(
              N8N_WEBHOOK_URL, json=current_data, timeout=10
          )
          print(f"[*] Ответ от n8n: {response.status_code}", flush=True)
        except Exception as e:
          print(f"[!] Ошибка отправки в n8n: {e}", flush=True)

        last_data = current_data
        save_state(last_data)
      else:
        print(f"[-] Изменений нет ({time.strftime('%H:%M:%S')})", flush=True)

    time.sleep(CHECK_INTERVAL)


t = threading.Thread(target=background_worker, daemon=True)
t.start()


@app.route("/")
def home():
  return "Alerts Poller is running!", 200


if __name__ == "__main__":
  port = int(os.environ.get("PORT", 10000))
  app.run(host="0.0.0.0", port=port)
