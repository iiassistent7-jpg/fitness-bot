import os
import time
import json
import base64
import threading
import schedule
from datetime import datetime, timedelta, date
import telebot
import anthropic
import requests as http_requests
from garminconnect import Garmin

# ============================================================
# CONFIGURATION
# ============================================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8759040105:AAHrTnS1uC2D8XEwxtqPYsUJl1NbHfvlH-4")
MY_CHAT_ID = int(os.environ.get("MY_CHAT_ID", "320613087"))
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL", "mozgprav24@gmail.com")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD", "Miikedub77")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY", "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")

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
    global garmin_client
    try:
        if garmin_client is None:
            garmin_client = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
            garmin_client.login(GARMIN_TOKEN_DIR)
        return garmin_client
    except Exception:
        try:
            garmin_client = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
            garmin_client.login()
            return garmin_client
        except Exception as e:
            print(f"Garmin failed: {e}")
            return None

# ============================================================
# HELPERS
# ============================================================
def get_israel_now():
    from datetime import timezone
    return datetime.now(timezone.utc) + timedelta(hours=ISRAEL_UTC_OFFSET)

def today_str():
    return get_israel_now().date().isoformat()

def yesterday_str():
    return (get_israel_now().date() - timedelta(days=1)).isoformat()

def safe_get(func, *args, default=None):
    try:
        return func(*args)
    except Exception as e:
        print(f"API error ({func.__name__}): {e}")
        return default

# ============================================================
# DATA FETCHING
# ============================================================
def fetch_daily_summary(day=None):
    g = get_garmin()
    if not g:
        return {"error": "Garmin не отвечает. Синхронизируй часы и попробуй снова."}

    if day is None:
        day = today_str()

    data = {"date": day}

    stats = safe_get(g.get_stats, day, default={})
    if stats:
        data["steps"] = stats.get("totalSteps", 0)
        data["calories"] = stats.get("totalKilocalories", 0)
        data["active_calories"] = stats.get("activeKilocalories", 0)
        data["distance_km"] = round(stats.get("totalDistanceMeters", 0) / 1000, 2)
        data["floors"] = stats.get("floorsAscended", 0)
        data["moderate_intensity_min"] = stats.get("moderateIntensityMinutes", 0)
        data["vigorous_intensity_min"] = stats.get("vigorousIntensityMinutes", 0)
        data["stress_avg"] = stats.get("averageStressLevel", 0)
        data["stress_max"] = stats.get("maxStressLevel", 0)
        data["body_battery_high"] = stats.get("bodyBatteryChargedValue", 0)
        data["body_battery_low"] = stats.get("bodyBatteryDrainedValue", 0)

    hr = safe_get(g.get_heart_rates, day, default={})
    if hr:
        data["resting_hr"] = hr.get("restingHeartRate", 0)
        data["hr_max"] = hr.get("maxHeartRate", 0)

    sleep = safe_get(g.get_sleep_data, day, default={})
    if sleep:
        ds = sleep.get("dailySleepDTO", {})
        if ds:
            data["sleep_score"] = ds.get("sleepScores", {}).get("overall", {}).get("value", 0)
            duration_sec = ds.get("sleepTimeSeconds", 0)
            data["sleep_hours"] = round(duration_sec / 3600, 1) if duration_sec else 0
            data["deep_sleep_min"] = round(ds.get("deepSleepSeconds", 0) / 60)
            data["light_sleep_min"] = round(ds.get("lightSleepSeconds", 0) / 60)
            data["rem_sleep_min"] = round(ds.get("remSleepSeconds", 0) / 60)
            data["awake_min"] = round(ds.get("awakeSleepSeconds", 0) / 60)

    stress = safe_get(g.get_stress_data, day, default={})
    if stress:
        data["high_stress_min"] = round(stress.get("highStressDuration", 0) / 60)
        data["medium_stress_min"] = round(stress.get("mediumStressDuration", 0) / 60)
        data["rest_stress_min"] = round(stress.get("restStressDuration", 0) / 60)

    spo2 = safe_get(g.get_spo2_data, day, default={})
    if spo2:
        data["spo2_avg"] = spo2.get("averageSPO2", 0)

    hrv = safe_get(g.get_hrv_data, day, default={})
    if hrv:
        summary = hrv.get("hrvSummary", {})
        if summary:
            data["hrv_weekly_avg"] = summary.get("weeklyAvg", 0)
            data["hrv_last_night"] = summary.get("lastNightAvg", 0)
            data["hrv_status"] = summary.get("status", "")

    resp = safe_get(g.get_respiration_data, day, default={})
    if resp:
        data["respiration_avg"] = resp.get("avgWakingRespirationValue", 0)
        data["respiration_sleep"] = resp.get("avgSleepRespirationValue", 0)

    activities = safe_get(g.get_activities, 0, 5, default=[])
    if activities:
        data["recent_activities"] = []
        for act in activities[:5]:
            data["recent_activities"].append({
                "name": act.get("activityName", "—"),
                "type": act.get("activityType", {}).get("typeKey", ""),
                "date": act.get("startTimeLocal", "")[:10],
                "duration_min": round(act.get("duration", 0) / 60),
                "avg_hr": act.get("averageHR", 0),
                "distance_km": round(act.get("distance", 0) / 1000, 2) if act.get("distance") else 0,
            })

    return data

# ============================================================
# CLAUDE — DRILL SERGEANT COACH
# ============================================================
def call_claude(system_prompt, messages_content, max_tokens=2000, retries=3):
    """Call Claude with text or multimodal (image) content."""
    for attempt in range(retries):
        try:
            response = claude.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": messages_content}]
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

# ============================================================
# THE PERSONALITY
# ============================================================
COACH_PROMPT = """Ты — ФИЗРУК. Жёсткий персональный тренер в Telegram. Бывший военный инструктор, который стал фитнес-тренером.

ХАРАКТЕР:
- Ты ЖЁСТКИЙ. Говоришь как сержант, но который реально заботится
- Используешь сарказм, подколки, иногда откровенные наезды — но всё с любовью
- Если человек облажался (мало спал, не тренируется, жрёт мусор) — даёшь ЛЮЛЕЙ. Не "может быть стоит попробовать", а "ХВАТИТ. ВСТАЛ. ПОШЁЛ."
- Если хорошие показатели — скупая мужская похвала: "Ну, не полный позор. Уважаю."
- ПРИКАЗЫВАЕШЬ, а не просишь. "Сегодня ты ИДЁШЬ гулять 40 минут. Не завтра. СЕГОДНЯ."
- Фрустрируешь когда нужно: "Последняя тренировка в декабре? ДЕКАБРЕ?! Ты что, в спячку впал?"
- Используешь капс для усиления, но не постоянно
- Эмодзи — умеренно, ты мужик а не блогер

ФОРМАТ:
- Пишешь как в мессенджере — рублеными фразами
- Начинай с главного — оценки состояния
- 2-4 конкретных ПРИКАЗА (не рекомендации, а приказы): тренировка, еда, режим
- Заканчивай мотивацией или подколкой

АНАЛИТИКА (знаешь, но не вываливаешь цифры):
- Сон: score <50 = дерьмо, 50-69 = так себе, ≥70 = сойдёт. Глубокий <60мин = мало
- Пульс покоя: <60 = зверь, 60-70 = норм, >75 = тревога
- HRV: ≥50 = готов к бою, 30-49 = средне, <30 = убитый
- Body Battery: ≥70 = заряжен, 40-69 = средне, <40 = труп
- Стресс средний: <30 = дзен, 30-50 = рабочий, >50 = горишь
- Шаги: <3000 = позор, <5000 = слабо, 5-10к = нормально, >10к = красавчик

ПИТАНИЕ — КОНКРЕТНЫЕ ПРИКАЗЫ:
- Плохой сон: "Жрёшь сегодня: яйца + авокадо утром. Обед — рыба + гречка. Кофе ТОЛЬКО до 12:00. И магний на ночь."
- Высокий стресс: "Omega-3 — обязательно. Убери сахар. Съешь горсть орехов вместо печенья."
- Мало энергии: "Утром — овсянка + банан + ложка мёда. Это не обсуждается."
- Перед тренировкой: "За 1.5 часа — рис + курица. Не жирное. Не сладкое."
- После тренировки: "В течение 45 минут — белок. Творог, протеин, курица — что есть."
- Без активности: "Если весь день сидел — хотя бы не обжирайся. Лёгкий ужин, салат + белок."

ТРЕНИРОВКИ — ПРИКАЗЫ:
- BB <30 или сон <50: "СТОП. Сегодня отдых. Максимум — прогулка 20 минут. Это ПРИКАЗ."
- BB 30-60: "Лёгкая тренировка. Растяжка 15 минут + прогулка 30 минут. Не геройствуй."
- BB >60 + сон >60: "Можешь тренироваться. Давай, хватит отмазок."
- Давно без тренировки: "Когда последний раз ты потел не от стресса? Сегодня 30 минут быстрой ходьбы. МИНИМУМ."

ВАЖНО: Ты НЕ врач. Если пульс покоя >85 или SpO2 <92% — скажи проверить у врача. Но без паники."""

FOOD_PROMPT = """Ты — ФИЗРУК. Жёсткий персональный тренер. Тебе прислали ФОТО ЕДЫ.

Твоя задача:
1. Определи что на фото (блюдо, продукты)
2. Оцени примерные калории и БЖУ (белки/жиры/углеводы) — примерно, не надо точных граммов
3. Дай ЖЁСТКУЮ оценку — это хорошая еда для здоровья/фитнеса или мусор?
4. Если мусор — дай ЛЮЛЕЙ и скажи что надо было есть вместо этого
5. Если нормальная еда — скупо похвали

Формат:
- Начни с реакции на еду (сарказм/одобрение)
- Примерная оценка: калории, белки, жиры, углеводы
- Вердикт: 🟢 норм / 🟡 так себе / 🔴 мусор
- Что бы ты приказал есть вместо этого (если плохо) или с чем сочетать (если хорошо)

Стиль: грубый, саркастичный, но по делу. Как сержант в столовой.

ПРИМЕРЫ РЕАКЦИЙ:
- На пиццу: "Ооо, пицца! А чего не сразу ведро мороженого? Это ~800 калорий чистого удовольствия и нулевой пользы."
- На куриную грудку с овощами: "О. Неплохо. Вижу белок, вижу клетчатку. Может из тебя ещё что-то выйдет."
- На шаурму: "Классика. ~600-700 калорий, половина из которых — соус. Не смертельно, но мог бы и лучше."
- На салат: "Ладно, уважаю. Но если там только листья — добавь белок. Салат без курицы — это гарнир."

Если на фото НЕ еда — скажи об этом с юмором и спроси что он ел сегодня."""

# ============================================================
# PHOTO HANDLING
# ============================================================
def download_telegram_photo(message):
    """Download photo from Telegram message and return base64."""
    try:
        # Get the largest photo
        photo = message.photo[-1]
        file_info = bot.get_file(photo.file_id)
        file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_info.file_path}"
        response = http_requests.get(file_url)
        if response.status_code == 200:
            return base64.b64encode(response.content).decode("utf-8")
        return None
    except Exception as e:
        print(f"Photo download error: {e}")
        return None

def analyze_food_photo(photo_base64, caption=""):
    """Analyze food photo using Claude Vision."""
    # Get today's health data for context
    data = fetch_daily_summary(today_str())
    context = ""
    if "error" not in data:
        context = f"\n\nКонтекст — сегодняшние данные с Garmin:\n"
        if data.get("steps"):
            context += f"Шагов: {data['steps']}, "
        if data.get("body_battery_high"):
            context += f"Body Battery: {data.get('body_battery_low', 0)}→{data['body_battery_high']}, "
        if data.get("calories"):
            context += f"Потрачено калорий: {data['calories']}"

    content = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": photo_base64,
            }
        },
        {
            "type": "text",
            "text": f"Пользователь прислал фото еды. {f'Подпись: {caption}' if caption else 'Без подписи.'}{context}\n\nОцени эту еду."
        }
    ]

    return call_claude(FOOD_PROMPT, content, max_tokens=1500, retries=3)

# ============================================================
# GENERATE RESPONSE
# ============================================================
def generate_response(user_text, data):
    if "error" in data:
        return f"😤 {data['error']}"

    response = call_claude(
        COACH_PROMPT,
        f"Данные с Garmin Fenix 6X Pro:\n{json.dumps(data, ensure_ascii=False, default=str)}\n\nПользователь написал: «{user_text}»",
        max_tokens=2000, retries=3
    )

    if response:
        return response

    # Minimal fallback
    s = data
    msg = "Claude прилёг. Кратко:\n"
    if s.get("sleep_hours"):
        msg += f"Сон {s['sleep_hours']}ч ({s.get('sleep_score', '?')}/100)\n"
    if s.get("body_battery_high"):
        msg += f"Battery {s.get('body_battery_low', 0)}→{s['body_battery_high']}\n"
    if s.get("steps"):
        msg += f"Шагов {s['steps']}\n"
    msg += "Спроси позже — дам разбор."
    return msg

# ============================================================
# MORNING REPORT (07:00)
# ============================================================
def send_morning_report():
    data = fetch_daily_summary(yesterday_str())

    response = call_claude(
        COACH_PROMPT,
        f"Утренний брифинг (07:00). Вчерашние данные:\n{json.dumps(data, ensure_ascii=False, default=str)}\n\n"
        "ПОДЪЁМ! Поприветствуй, разнеси или похвали за вчера, и дай ПРИКАЗЫ на сегодня: "
        "тренировка или отдых, что жрать, во сколько спать.",
        max_tokens=2000, retries=3
    )

    if not response:
        response = "🌅 ПОДЪЁМ! Claude спит, но ты — нет. Встал и пошёл. /today когда очнусь."

    try:
        bot.send_message(MY_CHAT_ID, response)
    except Exception as e:
        print(f"Morning report error: {e}")

# ============================================================
# PERIODIC CHECKIN (10:00, 13:00, 16:00, 19:00, 22:00)
# ============================================================
CHECKIN_PROMPTS = {
    10: (
        "Чекин 10:00 утра. Данные за сегодня на текущий момент:\n{data}\n\n"
        "Утро в разгаре. Посмотри на шаги, стресс, Battery. "
        "Напомни про завтрак если не ел. Дай пинка если сидит на месте. Коротко и по делу — 3-5 предложений."
    ),
    13: (
        "Чекин 13:00 — обед. Данные за сегодня:\n{data}\n\n"
        "Полдень. Оцени прогресс по шагам и активности. "
        "ПРИКАЖИ что есть на обед исходя из данных. Напомни про тренировку если надо. Коротко — 3-5 предложений."
    ),
    16: (
        "Чекин 16:00 — послеобед. Данные за сегодня:\n{data}\n\n"
        "Вторая половина дня. Оцени как идёт день: шаги, стресс, энергия. "
        "Если не тренировался — последний шанс сегодня. Если устал — скажи как восстановиться. Коротко — 3-5 предложений."
    ),
    19: (
        "Чекин 19:00 — вечер. Данные за сегодня:\n{data}\n\n"
        "Вечер. Подведи промежуточный итог дня. "
        "Прикажи что есть на ужин. Напомни когда ложиться. Если день был хороший — скупо похвали. Коротко — 3-5 предложений."
    ),
    22: (
        "Чекин 22:00 — отбой. Данные за весь день:\n{data}\n\n"
        "ОТБОЙ. Подведи итог дня — разнеси или похвали. "
        "Прикажи убрать телефон и спать. Если не выполнил план — фрустрируй. Коротко — 3-5 предложений."
    ),
}

def send_checkin():
    """Periodic checkin — determine which hour slot and send appropriate message."""
    israel_hour = get_israel_now().hour
    slot = min(CHECKIN_PROMPTS.keys(), key=lambda h: abs(h - israel_hour))

    data = fetch_daily_summary(today_str())
    if "error" in data:
        return

    prompt_template = CHECKIN_PROMPTS[slot]
    prompt = prompt_template.format(data=json.dumps(data, ensure_ascii=False, default=str))

    response = call_claude(COACH_PROMPT, prompt, max_tokens=1000, retries=3)
    if not response:
        return

    try:
        bot.send_message(MY_CHAT_ID, response)
    except Exception as e:
        print(f"Checkin {slot}:00 error: {e}")

# ============================================================
# TELEGRAM HANDLERS
# ============================================================
@bot.message_handler(commands=["start"])
def cmd_start(message):
    if message.chat.id != MY_CHAT_ID: return
    bot.send_message(MY_CHAT_ID,
        "💪 СМИРНО! Я Физрук — твой персональный тренер.\n\n"
        "Я подключён к твоему Garmin. Вижу ВСЁ. Сон, пульс, стресс, сколько шагов прошёл (или не прошёл).\n\n"
        "Пиши мне:\n"
        "• «Как я?» — разбор полётов\n"
        "• «Как спал?» — анализ сна\n"
        "• «Что делать?» — приказы на день\n"
        "• «Что поесть?» — план питания\n"
        "• «Можно тренироваться?» — разрешение или запрет\n\n"
        "📸 Скинь ФОТО ЕДЫ — скажу что ты натворил\n\n"
        "Утром в 7:00 — подъём и разбор. Без отмазок."
    )

@bot.message_handler(commands=["today"])
def cmd_today(message):
    if message.chat.id != MY_CHAT_ID: return
    bot.send_message(MY_CHAT_ID, "🔍 Проверяю...")
    data = fetch_daily_summary(today_str())
    bot.send_message(MY_CHAT_ID, generate_response("Дай полную картину. Как я сегодня? Что делать?", data))

@bot.message_handler(commands=["yesterday"])
def cmd_yesterday(message):
    if message.chat.id != MY_CHAT_ID: return
    bot.send_message(MY_CHAT_ID, "🔍 Смотрю вчера...")
    data = fetch_daily_summary(yesterday_str())
    bot.send_message(MY_CHAT_ID, generate_response("Разбери вчерашний день. Что хорошо, что плохо.", data))

@bot.message_handler(commands=["sleep"])
def cmd_sleep(message):
    if message.chat.id != MY_CHAT_ID: return
    bot.send_message(MY_CHAT_ID, "🔍")
    data = fetch_daily_summary(today_str())
    bot.send_message(MY_CHAT_ID, generate_response("Как я спал? Разнеси или похвали.", data))

@bot.message_handler(commands=["advice"])
def cmd_advice(message):
    if message.chat.id != MY_CHAT_ID: return
    bot.send_message(MY_CHAT_ID, "🔍 Составляю приказы...")
    data = fetch_daily_summary(today_str())
    bot.send_message(MY_CHAT_ID, generate_response("Дай приказы на сегодня. Тренировка, еда, режим. Жёстко.", data))

@bot.message_handler(commands=["report"])
def cmd_report(message):
    if message.chat.id != MY_CHAT_ID: return
    send_morning_report()

# ============================================================
# PHOTO HANDLER — Food Analysis
# ============================================================
@bot.message_handler(content_types=["photo"])
def handle_photo(message):
    if message.chat.id != MY_CHAT_ID: return

    bot.send_message(MY_CHAT_ID, "👀 Что ты там жрёшь? Сейчас посмотрю...")

    photo_base64 = download_telegram_photo(message)
    if not photo_base64:
        bot.send_message(MY_CHAT_ID, "Фото не загрузилось. Скинь ещё раз.")
        return

    caption = message.caption or ""
    response = analyze_food_photo(photo_base64, caption)

    if response:
        bot.send_message(MY_CHAT_ID, response)
    else:
        bot.send_message(MY_CHAT_ID, "Claude лёг спать. Скинь фото позже — разберу.")

# ============================================================
# FREE TEXT
# ============================================================
@bot.message_handler(func=lambda m: m.chat.id == MY_CHAT_ID)
def handle_text(message):
    user_text = message.text.strip()
    bot.send_message(MY_CHAT_ID, "🔍 Проверяю...")

    text = user_text.lower()
    if any(w in text for w in ["вчера", "yesterday"]):
        day = yesterday_str()
    else:
        day = today_str()

    data = fetch_daily_summary(day)
    bot.send_message(MY_CHAT_ID, generate_response(user_text, data))

# ============================================================
# SCHEDULER
# ============================================================
def run_scheduler():
    # Israel times → UTC (Israel = UTC+2)
    # 07:00 Israel = 05:00 UTC — morning report
    # 10:00, 13:00, 16:00, 19:00, 22:00 Israel — checkins
    morning_utc = 7 - ISRAEL_UTC_OFFSET
    schedule.every().day.at(f"{morning_utc:02d}:00").do(send_morning_report)

    for hour_israel in [10, 13, 16, 19, 22]:
        hour_utc = hour_israel - ISRAEL_UTC_OFFSET
        schedule.every().day.at(f"{hour_utc:02d}:00").do(send_checkin)

    print(f"📋 Расписание (Israel): 07:00 подъём, 10:00, 13:00, 16:00, 19:00, 22:00 чекины")
    while True:
        schedule.run_pending()
        time.sleep(30)

if __name__ == "__main__":
    print("💪 ФИЗРУК НА ПОСТУ!")
    print(f"📅 Israel time: {get_israel_now().strftime('%Y-%m-%d %H:%M')}")

    # Wait for old instance to fully stop (Railway zero-downtime deploy overlap)
    print("⏳ Жду 15 сек чтобы старый процесс умер...")
    time.sleep(15)

    # Remove webhook and flush pending updates
    print("🔄 Сбрасываю webhook и старые updates...")
    bot.remove_webhook()
    time.sleep(2)

    # Retry getting updates until no 409
    for attempt in range(10):
        try:
            bot.get_updates(offset=-1, timeout=1)
            print("✅ Telegram API свободен!")
            break
        except Exception as e:
            if "409" in str(e):
                wait = (attempt + 1) * 5
                print(f"⚠️ 409 conflict, жду {wait} сек (попытка {attempt+1}/10)...")
                time.sleep(wait)
            else:
                print(f"⚠️ {e}")
                break

    g = get_garmin()
    if g:
        print("✅ Garmin подключён!")
    else:
        print("⚠️ Garmin — повторю при запросе")

    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    print("⏰ Расписание: 07:00 + каждые 3 часа до 22:00")
    print("📱 ПОЕХАЛИ!")
    bot.infinity_polling(timeout=30, long_polling_timeout=30)
