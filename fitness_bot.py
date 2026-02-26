import os
import time
import json
import threading
import schedule
from datetime import datetime, timedelta, date
import telebot
import anthropic
from garminconnect import Garmin

# ============================================================
# CONFIGURATION
# ============================================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8759040105:AAHrTnS1uC2D8XEwxtqPYsUJl1NbHfvlH-4")
MY_CHAT_ID = int(os.environ.get("MY_CHAT_ID", "320613087"))
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL", "mozgprav24@gmail.com")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD", "Miikedub77")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY", "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")

# Oura (for future use)
OURA_CLIENT_ID = os.environ.get("OURA_CLIENT_ID", "5449e251-03d9-4c92-b2bf-d0a53a790595")
OURA_CLIENT_SECRET = os.environ.get("OURA_CLIENT_SECRET", "Mvdha6wIqrvUlIa3YONjvWITExFuHUFvdEfA32I-sSg")

ISRAEL_UTC_OFFSET = 2
GARMIN_TOKEN_DIR = os.path.expanduser("~/.garminconnect")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
claude = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
garmin_client = None

# ============================================================
# GARMIN CONNECTION
# ============================================================
def get_garmin():
    """Get or create Garmin client with token caching."""
    global garmin_client
    try:
        if garmin_client is None:
            garmin_client = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
            garmin_client.login(GARMIN_TOKEN_DIR)
            print("✅ Garmin: fresh login")
        return garmin_client
    except Exception as e:
        print(f"Garmin login error: {e}")
        # Try fresh login
        try:
            garmin_client = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
            garmin_client.login()
            print("✅ Garmin: re-login successful")
            return garmin_client
        except Exception as e2:
            print(f"Garmin re-login failed: {e2}")
            return None

# ============================================================
# HELPERS
# ============================================================
def get_israel_now():
    return datetime.utcnow() + timedelta(hours=ISRAEL_UTC_OFFSET)

def today_str():
    return get_israel_now().date().isoformat()

def yesterday_str():
    return (get_israel_now().date() - timedelta(days=1)).isoformat()

def safe_get(func, *args, default=None):
    """Safely call a Garmin API function."""
    try:
        return func(*args)
    except Exception as e:
        print(f"Garmin API error ({func.__name__}): {e}")
        return default

# ============================================================
# DATA FETCHING
# ============================================================
def fetch_daily_summary(day=None):
    """Fetch comprehensive health data for a given day."""
    g = get_garmin()
    if not g:
        return {"error": "Не удалось подключиться к Garmin Connect"}

    if day is None:
        day = today_str()

    data = {}

    # Basic stats (steps, calories, distance, etc.)
    stats = safe_get(g.get_stats, day, default={})
    if stats:
        data["steps"] = stats.get("totalSteps", 0)
        data["calories"] = stats.get("totalKilocalories", 0)
        data["active_calories"] = stats.get("activeKilocalories", 0)
        data["distance_km"] = round(stats.get("totalDistanceMeters", 0) / 1000, 2)
        data["floors_climbed"] = stats.get("floorsAscended", 0)
        data["intensity_minutes"] = stats.get("intensityMinutesGoal", 0)
        data["moderate_intensity"] = stats.get("moderateIntensityMinutes", 0)
        data["vigorous_intensity"] = stats.get("vigorousIntensityMinutes", 0)
        data["stress_avg"] = stats.get("averageStressLevel", 0)
        data["stress_max"] = stats.get("maxStressLevel", 0)
        data["body_battery_high"] = stats.get("bodyBatteryChargedValue", 0)
        data["body_battery_low"] = stats.get("bodyBatteryDrainedValue", 0)

    # Heart rate
    hr = safe_get(g.get_heart_rates, day, default={})
    if hr:
        data["resting_hr"] = hr.get("restingHeartRate", 0)
        data["hr_min"] = hr.get("minHeartRate", 0)
        data["hr_max"] = hr.get("maxHeartRate", 0)

    # Sleep
    sleep = safe_get(g.get_sleep_data, day, default={})
    if sleep:
        ds = sleep.get("dailySleepDTO", {})
        if ds:
            data["sleep_score"] = ds.get("sleepScores", {}).get("overall", {}).get("value", 0)
            data["sleep_start"] = ds.get("sleepStartTimestampLocal")
            data["sleep_end"] = ds.get("sleepEndTimestampLocal")
            # Duration in hours
            duration_sec = ds.get("sleepTimeSeconds", 0)
            if duration_sec:
                data["sleep_hours"] = round(duration_sec / 3600, 1)
            else:
                data["sleep_hours"] = 0
            data["deep_sleep_min"] = round(ds.get("deepSleepSeconds", 0) / 60)
            data["light_sleep_min"] = round(ds.get("lightSleepSeconds", 0) / 60)
            data["rem_sleep_min"] = round(ds.get("remSleepSeconds", 0) / 60)
            data["awake_min"] = round(ds.get("awakeSleepSeconds", 0) / 60)

    # Stress
    stress = safe_get(g.get_stress_data, day, default={})
    if stress:
        data["stress_qualifier"] = stress.get("stressQualifier", "")
        data["rest_stress_duration_min"] = round(stress.get("restStressDuration", 0) / 60)
        data["low_stress_duration_min"] = round(stress.get("lowStressDuration", 0) / 60)
        data["medium_stress_duration_min"] = round(stress.get("mediumStressDuration", 0) / 60)
        data["high_stress_duration_min"] = round(stress.get("highStressDuration", 0) / 60)

    # SpO2
    spo2 = safe_get(g.get_spo2_data, day, default={})
    if spo2:
        data["spo2_avg"] = spo2.get("averageSPO2", 0)
        data["spo2_min"] = spo2.get("lowestSPO2", 0)

    # HRV
    hrv = safe_get(g.get_hrv_data, day, default={})
    if hrv:
        summary = hrv.get("hrvSummary", {})
        if summary:
            data["hrv_weekly_avg"] = summary.get("weeklyAvg", 0)
            data["hrv_last_night"] = summary.get("lastNightAvg", 0)
            data["hrv_status"] = summary.get("status", "")

    # Respiration
    resp = safe_get(g.get_respiration_data, day, default={})
    if resp:
        data["respiration_avg"] = resp.get("avgWakingRespirationValue", 0)
        data["respiration_sleep"] = resp.get("avgSleepRespirationValue", 0)

    # Recent activities (last 5)
    activities = safe_get(g.get_activities, 0, 5, default=[])
    if activities:
        data["recent_activities"] = []
        for act in activities[:5]:
            data["recent_activities"].append({
                "name": act.get("activityName", "—"),
                "type": act.get("activityType", {}).get("typeKey", ""),
                "date": act.get("startTimeLocal", "")[:10],
                "duration_min": round(act.get("duration", 0) / 60),
                "calories": act.get("calories", 0),
                "avg_hr": act.get("averageHR", 0),
                "max_hr": act.get("maxHR", 0),
                "distance_km": round(act.get("distance", 0) / 1000, 2) if act.get("distance") else 0,
            })

    data["date"] = day
    return data

# ============================================================
# FORMAT REPORT (without Claude)
# ============================================================
def format_daily_report(data):
    """Format health data into emoji report."""
    if "error" in data:
        return f"❌ {data['error']}"

    day = data.get("date", today_str())
    report = f"🏋️ Отчёт за {day}\n{'─' * 30}\n\n"

    # Sleep
    sleep_h = data.get("sleep_hours", 0)
    sleep_score = data.get("sleep_score", 0)
    if sleep_h > 0:
        sleep_emoji = "🟢" if sleep_score >= 70 else "🟡" if sleep_score >= 50 else "🔴"
        report += f"😴 Сон: {sleep_h}ч | Оценка: {sleep_emoji} {sleep_score}/100\n"
        report += f"   🟦 Глубокий: {data.get('deep_sleep_min', 0)} мин | 💜 REM: {data.get('rem_sleep_min', 0)} мин\n"
        report += f"   🟩 Лёгкий: {data.get('light_sleep_min', 0)} мин | ⬜ Бодрств: {data.get('awake_min', 0)} мин\n\n"

    # Heart
    rhr = data.get("resting_hr", 0)
    if rhr:
        hr_emoji = "🟢" if rhr < 65 else "🟡" if rhr < 75 else "🔴"
        report += f"❤️ Пульс покоя: {hr_emoji} {rhr} уд/мин"
        if data.get("hr_max"):
            report += f" | Макс: {data['hr_max']}"
        report += "\n"

    # HRV
    hrv = data.get("hrv_last_night", 0)
    if hrv:
        hrv_emoji = "🟢" if hrv >= 50 else "🟡" if hrv >= 30 else "🔴"
        report += f"📊 HRV: {hrv_emoji} {hrv} мс"
        if data.get("hrv_weekly_avg"):
            report += f" (сред. за неделю: {data['hrv_weekly_avg']})"
        report += "\n"

    # Body Battery
    bb_high = data.get("body_battery_high", 0)
    bb_low = data.get("body_battery_low", 0)
    if bb_high:
        bb_emoji = "🟢" if bb_high >= 70 else "🟡" if bb_high >= 40 else "🔴"
        report += f"🔋 Body Battery: {bb_emoji} {bb_low}→{bb_high}\n"

    # Stress
    stress_avg = data.get("stress_avg", 0)
    if stress_avg:
        stress_emoji = "🟢" if stress_avg < 30 else "🟡" if stress_avg < 50 else "🔴"
        report += f"😤 Стресс: {stress_emoji} {stress_avg} (сред.)"
        if data.get("high_stress_duration_min", 0) > 0:
            report += f" | Высокий: {data['high_stress_duration_min']} мин"
        report += "\n"

    # SpO2
    spo2 = data.get("spo2_avg", 0)
    if spo2:
        report += f"🫁 SpO2: {spo2}%"
        if data.get("spo2_min"):
            report += f" (мин: {data['spo2_min']}%)"
        report += "\n"

    report += "\n"

    # Activity
    steps = data.get("steps", 0)
    dist = data.get("distance_km", 0)
    cal = data.get("calories", 0)
    active_cal = data.get("active_calories", 0)
    steps_emoji = "🟢" if steps >= 10000 else "🟡" if steps >= 5000 else "🔴"
    report += f"🚶 Шаги: {steps_emoji} {steps:,} | {dist} км\n"
    report += f"🔥 Калории: {cal:,} (активные: {active_cal})\n"

    mod = data.get("moderate_intensity", 0)
    vig = data.get("vigorous_intensity", 0)
    if mod or vig:
        report += f"⚡ Интенсивность: {mod} мин умеренная + {vig} мин высокая\n"

    floors = data.get("floors_climbed", 0)
    if floors:
        report += f"🏢 Этажей: {floors}\n"

    # Respiration
    resp_avg = data.get("respiration_avg", 0)
    resp_sleep = data.get("respiration_sleep", 0)
    if resp_avg:
        report += f"🌬️ Дыхание: {resp_avg} вд/мин (во сне: {resp_sleep})\n"

    # Recent activities
    activities = data.get("recent_activities", [])
    if activities:
        report += f"\n🏃 Последние тренировки:\n"
        for a in activities:
            report += f"   • {a['date']} {a['name']} — {a['duration_min']} мин"
            if a.get("avg_hr"):
                report += f" | ❤️ {a['avg_hr']}"
            if a.get("distance_km"):
                report += f" | {a['distance_km']} км"
            report += "\n"

    return report

# ============================================================
# CLAUDE AI
# ============================================================
def call_claude(system_prompt, user_content, max_tokens=2000, retries=3):
    for attempt in range(retries):
        try:
            response = claude.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_content}]
            )
            return response.content[0].text
        except anthropic.APIStatusError as e:
            if e.status_code == 529 and attempt < retries - 1:
                time.sleep((attempt + 1) * 10)
                continue
            return None
        except Exception as e:
            print(f"Claude error: {e}")
            return None

FITNESS_PROMPT = """Ты — персональный фитнес-тренер и нутрициолог. Анализируешь данные с Garmin Fenix 6X Pro.

ПРАВИЛА:
1. Отвечай ТОЛЬКО на основе данных. Не придумывай.
2. Используй эмодзи. Кратко но информативно.
3. Давай КОНКРЕТНЫЕ рекомендации по:
   - Тренировкам (что делать сегодня с учётом восстановления)
   - Питанию (что и когда есть)
   - Восстановлению (сон, стресс, отдых)
4. Если Body Battery низкий или сон плохой — рекомендуй лёгкий день
5. Если HRV высокий и Body Battery хороший — можно нагрузку
6. Учитывай стресс — если высокий, рекомендуй дыхательные практики
7. Не задавай вопросов в конце.

Оценки:
- Сон: 🟢 ≥70, 🟡 50-69, 🔴 <50
- Пульс покоя: 🟢 <65, 🟡 65-75, 🔴 >75
- HRV: 🟢 ≥50мс, 🟡 30-49, 🔴 <30
- Body Battery: 🟢 ≥70, 🟡 40-69, 🔴 <40
- Шаги: 🟢 ≥10000, 🟡 5000-9999, 🔴 <5000"""

INTENT_PROMPT = """Парсер запросов фитнес-бота. Ответь ТОЛЬКО JSON:
{"period": "today", "type": "summary"}

period: today | yesterday
type: summary | sleep | stress | activity | heart | advice

- "как дела", "статус", "сводка", "отчёт" → today, summary
- "вчера" → yesterday, summary
- "сон", "как спал" → today, sleep
- "стресс" → today, stress
- "тренировка", "активность" → today, activity
- "пульс", "сердце", "HRV" → today, heart
- "что делать", "рекомендации", "совет", "питание" → today, advice
- по умолчанию → today, summary"""

def detect_intent(user_text):
    raw = call_claude(INTENT_PROMPT, user_text, max_tokens=100, retries=2)
    if raw:
        try:
            clean = raw.replace("```json", "").replace("```", "").strip()
            return json.loads(clean)
        except:
            pass
    # Fallback
    text = user_text.lower()
    period = "today"
    qtype = "summary"
    if "вчера" in text:
        period = "yesterday"
    if any(w in text for w in ["сон", "спал"]):
        qtype = "sleep"
    elif any(w in text for w in ["стресс"]):
        qtype = "stress"
    elif any(w in text for w in ["трениров", "активност", "занят"]):
        qtype = "activity"
    elif any(w in text for w in ["пульс", "сердц", "hrv"]):
        qtype = "heart"
    elif any(w in text for w in ["делать", "рекоменд", "совет", "питани", "еда", "есть"]):
        qtype = "advice"
    return {"period": period, "type": qtype}

def generate_response(user_text, data):
    """Generate AI response with fallback to format_daily_report."""
    if "error" in data:
        return f"❌ {data['error']}"

    response = call_claude(
        FITNESS_PROMPT,
        f"Данные Garmin:\n{json.dumps(data, ensure_ascii=False, default=str)}\n\nЗапрос: {user_text}",
        max_tokens=2000, retries=2
    )
    if response:
        return response
    return format_daily_report(data)

# ============================================================
# MORNING REPORT
# ============================================================
def send_morning_report():
    """Send morning health briefing."""
    data = fetch_daily_summary(yesterday_str())
    report = f"🌅 Доброе утро!\n\n"

    # Try Claude for smart analysis
    ai_response = call_claude(
        FITNESS_PROMPT,
        f"Утренний брифинг. Данные за вчера:\n{json.dumps(data, ensure_ascii=False, default=str)}\n\n"
        "Дай краткую сводку за вчера и рекомендации на сегодня: тренировка, питание, восстановление.",
        max_tokens=2000, retries=2
    )

    if ai_response:
        report += ai_response
    else:
        report += format_daily_report(data)

    try:
        bot.send_message(MY_CHAT_ID, report)
    except Exception as e:
        print(f"Morning report error: {e}")

# ============================================================
# TELEGRAM HANDLERS
# ============================================================
@bot.message_handler(commands=["start"])
def cmd_start(message):
    if message.chat.id != MY_CHAT_ID: return
    bot.send_message(MY_CHAT_ID,
        "💪 Привет! Я твой фитнес-тренер.\n\n"
        "Спрашивай:\n"
        "• «Как дела?» — полная сводка\n"
        "• «Как спал?» — анализ сна\n"
        "• «Стресс» — уровень стресса\n"
        "• «Пульс / HRV» — сердце\n"
        "• «Что делать сегодня?» — рекомендации\n"
        "• «Что вчера?» — данные за вчера\n\n"
        "/today /yesterday /sleep /stress /advice"
    )

@bot.message_handler(commands=["today"])
def cmd_today(message):
    if message.chat.id != MY_CHAT_ID: return
    bot.send_message(MY_CHAT_ID, "⏳ Собираю данные с Garmin...")
    data = fetch_daily_summary(today_str())
    bot.send_message(MY_CHAT_ID, format_daily_report(data))

@bot.message_handler(commands=["yesterday"])
def cmd_yesterday(message):
    if message.chat.id != MY_CHAT_ID: return
    bot.send_message(MY_CHAT_ID, "⏳ Собираю данные...")
    data = fetch_daily_summary(yesterday_str())
    bot.send_message(MY_CHAT_ID, format_daily_report(data))

@bot.message_handler(commands=["sleep"])
def cmd_sleep(message):
    if message.chat.id != MY_CHAT_ID: return
    bot.send_message(MY_CHAT_ID, "⏳")
    data = fetch_daily_summary(today_str())
    bot.send_message(MY_CHAT_ID, generate_response("анализ сна и рекомендации", data))

@bot.message_handler(commands=["stress"])
def cmd_stress(message):
    if message.chat.id != MY_CHAT_ID: return
    bot.send_message(MY_CHAT_ID, "⏳")
    data = fetch_daily_summary(today_str())
    bot.send_message(MY_CHAT_ID, generate_response("анализ стресса", data))

@bot.message_handler(commands=["advice"])
def cmd_advice(message):
    if message.chat.id != MY_CHAT_ID: return
    bot.send_message(MY_CHAT_ID, "⏳ Анализирую...")
    data = fetch_daily_summary(today_str())
    bot.send_message(MY_CHAT_ID, generate_response(
        "Дай рекомендации на сегодня: тренировка, питание, восстановление", data
    ))

@bot.message_handler(commands=["report"])
def cmd_report(message):
    if message.chat.id != MY_CHAT_ID: return
    send_morning_report()

# ============================================================
# FREE TEXT
# ============================================================
@bot.message_handler(func=lambda m: m.chat.id == MY_CHAT_ID)
def handle_text(message):
    user_text = message.text.strip()
    bot.send_message(MY_CHAT_ID, "🤔 Анализирую...")

    intent = detect_intent(user_text)
    print(f"Intent: {intent}")

    day = yesterday_str() if intent.get("period") == "yesterday" else today_str()
    data = fetch_daily_summary(day)

    bot.send_message(MY_CHAT_ID, generate_response(user_text, data))

# ============================================================
# SCHEDULER
# ============================================================
def run_scheduler():
    utc_hour = 7 - ISRAEL_UTC_OFFSET  # 07:00 Israel time
    schedule.every().day.at(f"{utc_hour:02d}:00").do(send_morning_report)
    while True:
        schedule.run_pending()
        time.sleep(30)

if __name__ == "__main__":
    print("💪 Fitness Bot starting...")
    print(f"📅 Israel time: {get_israel_now().strftime('%Y-%m-%d %H:%M')}")

    # Test Garmin connection
    g = get_garmin()
    if g:
        print("✅ Garmin connected!")
    else:
        print("⚠️ Garmin connection failed — will retry on first request")

    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    print("⏰ Morning report at 07:00 Israel time")

    print("📱 Polling...")
    bot.infinity_polling()
