import json
import os
import threading
import time
import requests
from flask import Flask

app = Flask(__name__)

API_URL = "https://api.alerts.in.ua/v1/alerts/active.json"
API_TOKEN = os.environ.get("ALERTS_API_TOKEN")
CHECK_INTERVAL = 45
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
    elif response.status_code == 429:
      print("[!] Превышен лимит (429). Ждем 30 секунд...", flush=True)
      time.sleep(30)
      return None
    else:
      print(
          f"[!] Ошибка API статус {response.status_code}: {response.text}",
          flush=True,
      )
  except Exception as e:
    print(f"[!] Исключение при запросе к API: {e}", flush=True)
  return None


def get_kyiv_active_alerts(raw_data):
  """Возвращает словарь активных тревог Киевщины в формате {region_name: alert_info}"""
  if not raw_data or "alerts" not in raw_data:
    return {}

  active_kyiv_alerts = {}
  for item in raw_data["alerts"]:
    if item.get("alert_type") == "air_raid" and item.get("finished_at") is None:
      oblast = item.get("location_oblast") or ""
      title = item.get("location_title") or ""

      if "Київська область" in oblast or "Київська область" in title:
        active_kyiv_alerts[title] = {
            "region": title,
            "type": item.get("location_type"),
            "started_at": item.get("started_at"),
        }

  return active_kyiv_alerts


def background_worker():
  print("[*] Фоновый монитор alerts.in.ua запущен...", flush=True)
  last_filtered_data = get_kyiv_active_alerts(load_last_state())

  while True:
    raw_data = fetch_alerts()

    if raw_data:
      current_filtered_data = get_kyiv_active_alerts(raw_data)

      if current_filtered_data != last_filtered_data:
        print(
            f"[*] Изменилась ситуация в Киевской области! Время:"
            f" {time.strftime('%Y-%m-%d %H:%M:%S')}",
            flush=True,
        )

        # Вычисляем, где началась тревога, а где прошел отбой
        last_regions = set(last_filtered_data.keys())
        current_regions = set(current_filtered_data.keys())

        started_regions = list(current_regions - last_regions)
        ended_regions = list(last_regions - current_regions)

        events = []

        if started_regions:
          events.append({
              "status": "started",
              "title": "🚨 Повітряна тривога!",
              "regions": [current_filtered_data[r] for r in started_regions],
          })

        if ended_regions:
          events.append({
              "status": "ended",
              "title": "✅ Відбій тривоги!",
              "regions": [{"region": r} for r in ended_regions],
          })

        # Отправляем каждое событие в n8n
        for event in events:
          payload = {
              "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
              "status": event["status"],
              "event_title": event["title"],
              "alerts": event["regions"],
          }

          try:
            response = requests.post(
                N8N_WEBHOOK_URL, json=payload, timeout=10
            )
            print(
                f"[*] Отправлено в n8n ({event['status']}):"
                f" {response.status_code}",
                flush=True,
            )
          except Exception as e:
            print(f"[!] Ошибка отправки в n8n: {e}", flush=True)

        last_filtered_data = current_filtered_data
        save_state(raw_data)
      else:
        print(
            f"[-] Изменений по Киевщине нет ({time.strftime('%H:%M:%S')})",
            flush=True,
        )

    time.sleep(CHECK_INTERVAL)


t = threading.Thread(target=background_worker, daemon=True)
t.start()


@app.route("/")
def home():
  return "Alerts Poller is running!", 200


if __name__ == "__main__":
  port = int(os.environ.get("PORT", 10000))
  app.run(host="0.0.0.0", port=port)
