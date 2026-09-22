import json
import os
import threading
import time
import datetime
import requests
from flask import Flask

app = Flask(__name__)

API_URL = "https://api.alerts.in.ua/v1/alerts/active.json"
API_TOKEN = os.environ.get("ALERTS_API_TOKEN")
CHECK_INTERVAL = 45
STATE_FILE = "last_state.json"
STATS_FILE = "daily_stats.json"
HEADERS = {"Authorization": f"Bearer {API_TOKEN}"}

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage" if TELEGRAM_BOT_TOKEN else None


def get_adjusted_time():
    """Возвращает текущее время, смещенное вперед на 3 часа."""
    return datetime.datetime.now() + datetime.timedelta(hours=3)


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


def load_daily_stats():
    """Завантажує або ініціалізує статистику за день тільки для м. Київ"""
    today_str = get_adjusted_time().strftime("%Y-%m-%d")
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("date") == today_str:
                    return data
        except Exception:
            pass
    
    return {
        "date": today_str,
        "red_count": 0,
        "yellow_count": 0,
        "red_duration_seconds": 0,
        "yellow_duration_seconds": 0,
        "active_alerts_tracker": {}  # Зберігає інформацію про активну тривогу: {region_title: {"start": iso_time, "level": level}}
    }


def save_daily_stats(stats):
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)


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
    """Возвращает словарь активных тревог ТОЛЬКО для города Киев в формате {region_name: alert_info}"""
    if not raw_data or "alerts" not in raw_data:
        return {}

    active_kyiv_alerts = {}
    for item in raw_data["alerts"]:
        if item.get("alert_type") == "air_raid" and item.get("finished_at") is None:
            title = item.get("location_title") or ""

            # Фільтруємо виключно місто Київ (ігноруємо Київську область)
            if title == "м. Київ":
                
                raw_level = item.get("alert_level")
                if raw_level == "yellow":
                    level_display = "🟡 Жовтий рівень"
                    level_key = "yellow"
                elif raw_level == "red":
                    level_display = "🔴 Червоний рівень"
                    level_key = "red"
                elif raw_level:
                    level_display = f"⚪ {raw_level} рівень"
                    level_key = "other"
                else:
                    level_display = "⚪ Рівень не вказаний"
                    level_key = "other"

                threats_data = item.get("threats", [])
                threat_types = []
                
                threat_mapping = {
                    "tactic_aircraft_activity": "Активність тактичної авіації",
                    "strategic_aircraft_activity": "Активність стратегічної авіації",
                    "mig31k_departure": "Взліт Міг-31К",
                    "ballistic_missiles": "Балістика",
                    "cruise_missiles": "Крилаті ракети",
                    "unspecified_missiles": "Ракети",
                    "drones": "Дроны (БПЛА)",
                    "guided_aerial_bombs": "КАБи",
                    "air_defense": "Працює ППО",
                    "unknown": "Невідома загроза"
                }
                
                for t in threats_data:
                    ttype = t.get("threat_type")
                    if ttype:
                        threat_types.append(threat_mapping.get(ttype, ttype))
                
                threats_display = ", ".join(threat_types) if threat_types else "Невідома загроза"

                active_kyiv_alerts[title] = {
                    "region": title,
                    "type": item.get("location_type"),
                    "started_at": item.get("started_at"),
                    "alert_level": level_display,
                    "level_key": level_key,  # Зберігаємо ключ рівня для статистики
                    "threats": threats_display
                }

    return active_kyiv_alerts


def format_telegram_message(event):
    """Форматирует событие тревоги в красивое текстовое сообщение для Telegram."""
    lines = [f"<b>{event['title']}</b>", f"🕒 <code>{get_adjusted_time().strftime('%Y-%m-%d %H:%M:%S')}</code>\n"]
    
    for reg in event["regions"]:
        region_name = reg.get("region", "Невідомо")
        lines.append(f"📍 <b>{region_name}</b>")
        if event["status"] == "started":
            if "alert_level" in reg:
                lines.append(f"    • Рівень: {reg['alert_level']}")
            if "threats" in reg:
                lines.append(f"    • Загроза: {reg['threats']}")
        lines.append("")
        
    return "\n".join(lines)


def send_telegram_message(message_text):
    """Допоміжна функція для відправки повідомлень у Telegram"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[!] Ошибка: Не заданы TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID в переменных окружения!", flush=True)
        return False

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message_text,
        "parse_mode": "HTML"
    }

    try:
        response = requests.post(TELEGRAM_API_URL, json=payload, timeout=10)
        if response.status_code == 200:
            return True
        else:
            print(f"[!] Ошибка отправки в Telegram: {response.status_code} - {response.text}", flush=True)
    except Exception as e:
        print(f"[!] Исключение при отправке в Telegram: {e}", flush=True)
    return False


def format_duration(total_seconds):
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    return f"{hours} год. {minutes} хв."


def send_daily_report(stats):
    """Формує та надсилає звіт за минулу добу виключно по місту Київ"""
    report_date = stats.get("date", get_adjusted_time().strftime('%Y-%m-%d'))
    
    red_time_str = format_duration(stats.get("red_duration_seconds", 0))
    yellow_time_str = format_duration(stats.get("yellow_duration_seconds", 0))
    
    total_seconds = stats.get("red_duration_seconds", 0) + stats.get("yellow_duration_seconds", 0)
    total_time_str = format_duration(total_seconds)
    
    text = (
        f"📊 <b>Статистика повітряних тривог за добу</b>\n"
        f"📅 Дата: <code>{report_date}</code>\n"
        f"📍 Регіон: <b>м. Київ</b>\n\n"
        f"🔴 Червоних тривог: <b>{stats.get('red_count', 0)}</b> (Час: {red_time_str})\n"
        f"🟡 Жовтих тривог: <b>{stats.get('yellow_count', 0)}</b> (Час: {yellow_time_str})\n\n"
        f"⏳ Сумарний час усіх тривог: <b>{total_time_str}</b>"
    )
    
    print(f"[*] Відправляємо нічний звіт за {report_date}...", flush=True)
    send_telegram_message(text)


def background_worker():
    print("[*] Фоновый монитор alerts.in.ua запущен...", flush=True)
    last_filtered_data = load_last_state()
    daily_stats = load_daily_stats()

    while True:
        now = get_adjusted_time()
        current_date_str = now.strftime("%Y-%m-%d")

        # Перевірка на зміну доби
        if daily_stats.get("date") != current_date_str:
            send_daily_report(daily_stats)
            
            daily_stats = {
                "date": current_date_str,
                "red_count": 0,
                "yellow_count": 0,
                "red_duration_seconds": 0,
                "yellow_duration_seconds": 0,
                "active_alerts_tracker": {}
            }
            # Якщо тривога триває у новий день, переносимо її
            for region, info in last_filtered_data.items():
                daily_stats["active_alerts_tracker"][region] = {
                    "start": now.isoformat(),
                    "level_key": info.get("level_key", "red")
                }
            save_daily_stats(daily_stats)

        raw_data = fetch_alerts()

        if raw_data:
            current_filtered_data = get_kyiv_active_alerts(raw_data)

            if current_filtered_data != last_filtered_data:
                current_time_str = now.strftime('%Y-%m-%d %H:%M:%S')
                print(
                    f"[*] Змінилася ситуація в місті Київ! Час: {current_time_str}",
                    flush=True,
                )

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
                    for r in started_regions:
                        level_key = current_filtered_data[r].get("level_key", "red")
                        if level_key == "yellow":
                            daily_stats["yellow_count"] += 1
                        else:
                            daily_stats["red_count"] += 1
                            
                        daily_stats["active_alerts_tracker"][r] = {
                            "start": now.isoformat(),
                            "level_key": level_key
                        }

                if ended_regions:
                    events.append({
                        "status": "ended",
                        "title": "✅ Відбій тривоги!",
                        "regions": [{"region": r} for r in ended_regions],
                    })
                    for r in ended_regions:
                        if r in daily_stats["active_alerts_tracker"]:
                            try:
                                tracker_info = daily_stats["active_alerts_tracker"][r]
                                start_dt = datetime.datetime.fromisoformat(tracker_info["start"])
                                duration = int((now - start_dt).total_seconds())
                                level_key = tracker_info.get("level_key", "red")
                                
                                if level_key == "yellow":
                                    daily_stats["yellow_duration_seconds"] += duration
                                else:
                                    daily_stats["red_duration_seconds"] += duration
                                    
                                del daily_stats["active_alerts_tracker"][r]
                            except Exception as e:
                                print(f"[!] Помилка розрахунку часу тривалості: {e}", flush=True)

                save_daily_stats(daily_stats)

                for event in events:
                    message_text = format_telegram_message(event)
                    success = send_telegram_message(message_text)
                    if success:
                        print(f"[*] Отправлено в Telegram ({event['status']}): Успешно", flush=True)

                last_filtered_data = current_filtered_data
                save_state(current_filtered_data)
        else:
            print(
                f"[-] Изменений по Киеву нет ({now.strftime('%H:%M:%S')})",
                flush=True,
            )

        time.sleep(CHECK_INTERVAL)


t = threading.Thread(target=background_worker, daemon=True)
t.start()


@app.route("/")
def home():
    return "Alerts Poller is running!", 200


@app.route("/test-voice")
def test_voice():
    """Завантажує ogg-файл у пам'ять і відправляє як справжнє голосове повідомлення."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return "Помилка: Не задані TELEGRAM_BOT_TOKEN або TELEGRAM_CHAT_ID", 400

    voice_api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVoice"
    audio_url = "https://raw.githubusercontent.com/Kapiushoni/AlertsPolling/main/gordon.ogg"

    try:
        file_response = requests.get(audio_url, timeout=15)
        if file_response.status_code != 200:
            return f"Не вдалося завантажити аудіо з GitHub: {file_response.status_code}", 400

        files = {
            "voice": ("gordon.ogg", file_response.content, "audio/ogg")
        }
        data = {
            "chat_id": TELEGRAM_CHAT_ID,
        }

        response = requests.post(voice_api_url, data=data, files=files, timeout=20)
        
        if response.status_code == 200:
            return "Голосове повідомлення успішно надіслано!", 200
        else:
            return f"Помилка від Telegram API: {response.status_code} - {response.text}", 400
            
    except Exception as e:
        return f"Виняток при відправці: {e}", 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
