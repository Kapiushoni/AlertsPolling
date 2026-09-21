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
STATS_FILE = "daily_stats.json"  # Файл для збереження статистики за поточний день
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
    """Завантажує або ініціалізує статистику за день"""
    today_str = get_adjusted_time().strftime("%Y-%m-%d")
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("date") == today_str:
                    return data
        except Exception:
            pass
    
    # Якщо файлу немає або це новий день — створюємо порожню структуру
    return {
        "date": today_str,
        "count": 0,
        "total_duration_seconds": 0,
        "active_alerts_tracker": {}  # Зберігає час початку {region: start_timestamp}
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
    """Возвращает словарь активных тревог Киева и Киевщины в формате {region_name: alert_info}"""
    if not raw_data or "alerts" not in raw_data:
        return {}

    active_kyiv_alerts = {}
    for item in raw_data["alerts"]:
        if item.get("alert_type") == "air_raid" and item.get("finished_at") is None:
            oblast = item.get("location_oblast") or ""
            title = item.get("location_title") or ""

            if "Київська область" in oblast or "Київська область" in title or "м. Київ" in title:
                
                raw_level = item.get("alert_level")
                if raw_level == "yellow":
                    level_display = "🟡 Жовтий рівень"
                elif raw_level == "red":
                    level_display = "🔴 Червоний рівень"
                elif raw_level:
                    level_display = f"⚪ {raw_level} рівень"
                else:
                    level_display = "⚪ Рівень не вказаний"

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


def send_daily_report(stats):
    """Формує та надсилає звіт за минулу добу"""
    total_seconds = stats.get("total_duration_seconds", 0)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    
    report_date = stats.get("date", get_adjusted_time().strftime('%Y-%m-%d'))
    
    text = (
        f"📊 <b>Статистика повітряних тривог за добу</b>\n"
        f"📅 Дата: <code>{report_date}</code>\n"
        f"📍 Регіон: <b>Київ та Київська область</b>\n\n"
        f"🚨 Кількість тривог: <b>{stats.get('count', 0)}</b>\n"
        f"⏳ Сумарний час тривог: <b>{hours} год. {minutes} хв.</b>"
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

        # Перевірка на зміну доби (якщо настав новий день, надсилаємо звіт за вчора)
        if daily_stats.get("date") != current_date_str:
            # Закриваємо активні тривоги перед зміною дня, щоб зарахувати час до кінця доби
            for region in list(daily_stats["active_alerts_tracker"].keys()):
                start_str = daily_stats["active_alerts_tracker"][region]
                try:
                    start_dt = datetime.datetime.fromisoformat(start_str)
                    # Вважаємо тривалість до 00:00 нового дня
                    midnight_dt = datetime.datetime.combine(now.date(), datetime.time.min) # це вже новий день, тому беремо вчорашню північ
                    # Простіше: вважаємо до поточного часу або кінця доби
                except Exception:
                    pass
            
            # Надсилаємо звіт
            send_daily_report(daily_stats)
            
            # Скидаємо статистику на новий день
            daily_stats = {
                "date": current_date_str,
                "count": 0,
                "total_duration_seconds": 0,
                "active_alerts_tracker": {}
            }
            # Якщо в цей момент у Києві горять тривоги, переносимо їх на новий день
            for region in last_filtered_data.keys():
                daily_stats["active_alerts_tracker"][region] = now.isoformat()
            save_daily_stats(daily_stats)

        raw_data = fetch_alerts()

        if raw_data:
            current_filtered_data = get_kyiv_active_alerts(raw_data)

            if current_filtered_data != last_filtered_data:
                current_time_str = now.strftime('%Y-%m-%d %H:%M:%S')
                print(
                    f"[*] Змінилася ситуація в Києві та Області! Час: {current_time_str}",
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
                    # Оновлюємо статистику (збільшуємо лічильник тривог та фіксуємо час початку)
                    daily_stats["count"] += len(started_regions)
                    for r in started_regions:
                        daily_stats["active_alerts_tracker"][r] = now.isoformat()

                if ended_regions:
                    events.append({
                        "status": "ended",
                        "title": "✅ Відбій тривоги!",
                        "regions": [{"region": r} for r in ended_regions],
                    })
                    # Рахуємо тривалість закінчених тривог
                    for r in ended_regions:
                        if r in daily_stats["active_alerts_tracker"]:
                            try:
                                start_dt = datetime.datetime.fromisoformat(daily_stats["active_alerts_tracker"][r])
                                duration = (now - start_dt).total_seconds()
                                daily_stats["total_duration_seconds"] += int(duration)
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
                f"[-] Изменений по Киеву и области нет ({now.strftime('%H:%M:%S')})",
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
