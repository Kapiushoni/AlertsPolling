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

# ЗАМЕНИТЕ НА ВАШ PRODUCTION URL (без слова -test)
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
    print(f"[*] Запрос к API с токеном: {API_TOKEN[:5]}...")
    response = requests.get(API_URL, headers=HEADERS, timeout=10)
    print(f"[*] Статус ответа API alerts.in.ua: {response.status_code}")
    if response.status_code == 200:
      return response.json()
    else:
      print(f"[!] Ошибка API текст: {response.text}")
  except Exception as e:
    print(f"[!] Исключение при запросе к API: {e}")
  return None

def background_worker():
  print("[*] Фоновый монитор alerts.in.ua запущен...")
  last_data = load_last_state()

  while True:
    current_data = fetch_alerts()

    if current_data:
      print(
          f"[*] Отправка данных в n8n... Время:"
          f" {time.strftime('%Y-%m-%d %H:%M:%S')}"
      )

      try:
        # Отправляем данные на вебхук n8n при каждом цикле
        response = requests.post(
            N8N_WEBHOOK_URL, json=current_data, timeout=10
        )
        print(f"[*] Ответ от n8n: {response.status_code}")
      except Exception as e:
        print(f"[!] Ошибка отправки в n8n: {e}")

      # Сохраняем состояние
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
