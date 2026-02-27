import os
import time
import json
import base64
import tempfile
import threading
import schedule
from datetime import datetime, timedelta, date
import telebot
import anthropic
import requests as http_requests
from garminconnect import Garmin
from openai import OpenAI

# ============================================================
# CONFIGURATION
# ============================================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
MY_CHAT_ID = int(os.environ.get("MY_CHAT_ID", "0"))
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL", "")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OURA_TOKEN = os.environ.get("OURA_TOKEN", "")

ISRAEL_UTC_OFFSET = 2
GARMIN_TOKEN_DIR = os.path.expanduser("~/.garminconnect")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
claude = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
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
# OURA RING 4 API
# ============================================================
def oura_request(endpoint, params=None):
    """Make request to Oura API v2."""
    if not OURA_TOKEN:
        return None
    url = f"https://api.ouraring.com/v2/usercollection/{endpoint}"
    headers = {"Authorization": f"Bearer {OURA_TOKEN}"}
    try:
        resp = http_requests.get(url, headers=headers, params=params or {}, timeout=30)
        if resp.status_code == 200:
            return resp.json()
        else:
            print(f"Oura API error {resp.status_code}: {resp.text[:200]}")
            return None
    except Exception as e:
        print(f"Oura request error: {e}")
        return None

def fetch_oura_data(day=None):
    """Fetch sleep, readiness, activity, and HRV from Oura Ring 4."""
    if not OURA_TOKEN:
        return {}

    if day is None:
        day = today_str()

    data = {}
    params = {"start_date": day, "end_date": day}

    # Sleep
    sleep = oura_request("sleep", params)
    if sleep and sleep.get("data"):
        s = sleep["data"][-1]  # Last sleep session
        data["oura_sleep_score"] = s.get("score", 0)
        data["oura_sleep_efficiency"] = s.get("efficiency", 0)
        total_sec = s.get("total_sleep_duration", 0)
        data["oura_sleep_hours"] = round(total_sec / 3600, 1) if total_sec else 0
        data["oura_deep_sleep_min"] = round(s.get("deep_sleep_duration", 0) / 60)
        data["oura_rem_sleep_min"] = round(s.get("rem_sleep_duration", 0) / 60)
        data["oura_light_sleep_min"] = round(s.get("light_sleep_duration", 0) / 60)
        data["oura_awake_min"] = round(s.get("awake_time", 0) / 60)
        data["oura_avg_hr_sleep"] = s.get("average_heart_rate", 0)
        data["oura_lowest_hr"] = s.get("lowest_heart_rate", 0)
        data["oura_avg_hrv_sleep"] = s.get("average_hrv", 0)
        data["oura_restless_periods"] = s.get("restless_periods", 0)
        data["oura_sleep_latency_min"] = round(s.get("latency", 0) / 60)
        # Temperature deviation
        temp = s.get("readiness", {})
        if "temperature_deviation" in s:
            data["oura_temp_deviation"] = s["temperature_deviation"]

    # Daily Readiness
    readiness = oura_request("daily_readiness", params)
    if readiness and readiness.get("data"):
        r = readiness["data"][-1]
        data["oura_readiness_score"] = r.get("score", 0)
        contributors = r.get("contributors", {})
        data["oura_recovery_index"] = contributors.get("recovery_index", 0)
        data["oura_resting_hr_score"] = contributors.get("resting_heart_rate", 0)
        data["oura_hrv_balance"] = contributors.get("hrv_balance", 0)
        data["oura_body_temp_score"] = contributors.get("body_temperature", 0)
        data["oura_sleep_balance"] = contributors.get("sleep_balance", 0)
        data["oura_previous_night"] = contributors.get("previous_night", 0)
        data["oura_activity_balance"] = contributors.get("activity_balance", 0)

    # Daily Activity
    activity = oura_request("daily_activity", params)
    if activity and activity.get("data"):
        a = activity["data"][-1]
        data["oura_activity_score"] = a.get("score", 0)
        data["oura_steps"] = a.get("steps", 0)
        data["oura_active_calories"] = a.get("active_calories", 0)
        data["oura_total_calories"] = a.get("total_calories", 0)
        data["oura_sedentary_min"] = round(a.get("sedentary_time", 0) / 60)
        data["oura_high_activity_min"] = round(a.get("high_activity_time", 0) / 60)
        data["oura_medium_activity_min"] = round(a.get("medium_activity_time", 0) / 60)
        data["oura_low_activity_min"] = round(a.get("low_activity_time", 0) / 60)
        data["oura_inactivity_alerts"] = a.get("inactivity_alerts", 0)

    # Heart Rate
    hr = oura_request("heartrate", {"start_datetime": f"{day}T00:00:00+02:00", "end_datetime": f"{day}T23:59:59+02:00"})
    if hr and hr.get("data"):
        hr_values = [h.get("bpm", 0) for h in hr["data"] if h.get("bpm")]
        if hr_values:
            data["oura_hr_min"] = min(hr_values)
            data["oura_hr_max"] = max(hr_values)
            data["oura_hr_avg"] = round(sum(hr_values) / len(hr_values))

    # SpO2
    spo2 = oura_request("daily_spo2", params)
    if spo2 and spo2.get("data"):
        sp = spo2["data"][-1]
        data["oura_spo2_avg"] = sp.get("spo2_percentage", {}).get("average", 0)

    # Resilience (Oura 4 feature)
    try:
        resilience = oura_request("daily_resilience", params)
        if resilience and resilience.get("data"):
            res = resilience["data"][-1]
            data["oura_resilience_level"] = res.get("level", "")
            contributors = res.get("contributors", {})
            data["oura_sleep_recovery"] = contributors.get("sleep_recovery", 0)
            data["oura_daytime_recovery"] = contributors.get("daytime_recovery", 0)
    except:
        pass

    return data

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
# DATA FETCHING — Garmin + Oura combined
# ============================================================
def fetch_daily_summary(day=None):
    g = get_garmin()
    if not g:
        return {"error": "Garmin не отвечает. Синхронизируй часы и попробуй снова."}

    if day is None:
        day = today_str()

    data = {"date": day}

    # ---- GARMIN DATA ----
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

    # ---- OURA RING 4 DATA ----
    try:
        oura_data = fetch_oura_data(day)
        if oura_data:
            data.update(oura_data)
            data["_sources"] = "Garmin + Oura Ring 4"
        else:
            data["_sources"] = "Garmin"
    except Exception as e:
        print(f"Oura fetch error: {e}")
        data["_sources"] = "Garmin"

    return data

# ============================================================
# CLAUDE — DRILL SERGEANT COACH
# ============================================================
def call_claude(system_prompt, messages_content, max_tokens=2000, retries=3):
    """Call Claude with text or multimodal (image) content."""
    for attempt in range(retries):
        try:
            print(f"🤖 Claude запрос (попытка {attempt+1}/{retries})...")
            response = claude.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": messages_content}]
            )
            print(f"✅ Claude ответил! ({len(response.content[0].text)} символов)")
            return response.content[0].text
        except anthropic.APIStatusError as e:
            print(f"❌ Claude API ошибка: {e.status_code} — {e.message}")
            if e.status_code == 529 and attempt < retries - 1:
                wait = (attempt + 1) * 10
                print(f"⏳ Жду {wait} сек перед повтором...")
                time.sleep(wait)
                continue
            return None
        except Exception as e:
            print(f"❌ Claude исключение: {type(e).__name__}: {e}")
            return None

# ============================================================
# THE PERSONALITY
# ============================================================
COACH_PROMPT = """Ты — ФИЗРУК. Жёсткий персональный тренер в Telegram. Бывший военный инструктор, который стал фитнес-тренером.

ХАРАКТЕР:
- Ты ЖЁСТКИЙ. Говоришь как сержант, но который реально заботится
- Используешь сарказм, подколки, иногда откровенные наезды — но всё с любовью
- Если человек облажался (мало спал, не тренируется, жрёт мусор) — даёшь ЛЮЛЕЙ
- Если хорошие показатели — скупая мужская похвала: "Ну, не полный позор. Уважаю."
- ПРИКАЗЫВАЕШЬ, а не просишь
- Используешь капс для усиления, но не постоянно
- Эмодзи — умеренно

ФОРМАТ:
- Пишешь как в мессенджере — рублеными фразами
- Начинай с главного — оценки состояния
- 2-4 конкретных ПРИКАЗА: тренировка, еда, режим
- Заканчивай мотивацией или подколкой

ИСТОЧНИКИ ДАННЫХ:
У пользователя Garmin Fenix 6X Pro (часы) и Oura Ring 4 (кольцо).
- Данные с префиксом "oura_" — это с кольца Oura
- Данные без префикса — с часов Garmin
- Если есть данные с обоих источников — используй ОБА для более точной оценки
- Oura точнее меряет сон (фазы, HRV ночью, температуру тела) и готовность
- Garmin точнее меряет активность (шаги, тренировки, Body Battery, стресс)
- Если данные расходятся — упомяни это: "Garmin говорит X, Oura говорит Y"

АНАЛИТИКА:
Сон (используй Oura если есть, он точнее):
- oura_sleep_score / sleep_score: <50 = дерьмо, 50-69 = так себе, ≥70 = сойдёт
- Глубокий сон <60мин = мало
- oura_sleep_latency_min >30 = долго засыпал — стресс или телефон перед сном
- oura_temp_deviation: отклонение температуры тела, >0.5 = возможно заболевает

Готовность (Oura Readiness):
- oura_readiness_score: <60 = убит, 60-74 = средне, ≥75 = готов к бою
- oura_recovery_index: показывает как восстановился
- oura_resilience_level: устойчивость организма

Пульс и HRV:
- resting_hr / oura_lowest_hr: <60 = зверь, 60-70 = норм, >75 = тревога
- HRV (oura_avg_hrv_sleep точнее): ≥50 = готов, 30-49 = средне, <30 = убитый
- Body Battery (Garmin): ≥70 = заряжен, 40-69 = средне, <40 = труп

Стресс и активность:
- stress_avg (Garmin): <30 = дзен, 30-50 = рабочий, >50 = горишь
- Шаги: <3000 = позор, <5000 = слабо, 5-10к = нормально, >10к = красавчик
- oura_sedentary_min >600 = слишком много сидел
- oura_inactivity_alerts > 3 = овощ

ПИТАНИЕ — КОНКРЕТНЫЕ ПРИКАЗЫ:
- Плохой сон: "Яйца + авокадо утром. Рыба + гречка обед. Кофе ТОЛЬКО до 12:00. Магний на ночь."
- Высокий стресс: "Omega-3 обязательно. Убери сахар. Горсть орехов вместо печенья."
- Мало энергии: "Овсянка + банан + мёд утром. Не обсуждается."
- После тренировки: "В течение 45 минут — белок. Творог, протеин, курица."

ТРЕНИРОВКИ — ПРИКАЗЫ (учитывай ОБА источника):
- BB <30 ИЛИ oura_readiness <60 ИЛИ сон <50: "СТОП. Отдых. Максимум прогулка 20 минут."
- BB 30-60 ИЛИ readiness 60-74: "Лёгкая тренировка. Растяжка + прогулка."
- BB >60 И readiness >74: "Можешь тренироваться. Давай!"

ВАЖНО: Ты НЕ врач. Если пульс покоя >85 или SpO2 <92% или oura_temp_deviation >1.0 — скажи проверить у врача.

ФОРМАТИРОВАНИЕ: НИКОГДА не используй **звёздочки**, __подчёркивания__, ## заголовки или другую Markdown-разметку. Только чистый текст и эмодзи."""

FOOD_PROMPT = """Ты — ФИЗРУК. Жёсткий персональный тренер. Тебе прислали ФОТО ЕДЫ.

Твоя задача:
1. Определи что на фото
2. Оцени примерные калории и БЖУ
3. Дай ЖЁСТКУЮ оценку
4. Если мусор — дай ЛЮЛЕЙ и скажи что есть вместо
5. Если норм — скупо похвали

Формат:
- Реакция (сарказм/одобрение)
- Калории, белки, жиры, углеводы (примерно)
- Вердикт: 🟢 норм / 🟡 так себе / 🔴 мусор
- Что приказал бы есть вместо (если плохо) или с чем сочетать (если хорошо)

Если на фото НЕ еда — скажи с юмором и спроси что он ел.

ФОРМАТИРОВАНИЕ: НИКОГДА не используй **звёздочки**, __подчёркивания__ или Markdown. Только чистый текст и эмодзи."""

# ============================================================
# PHOTO HANDLING
# ============================================================
def download_telegram_photo(message):
    """Download photo from Telegram message and return base64."""
    try:
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
    data = fetch_daily_summary(today_str())
    context = ""
    if "error" not in data:
        context = f"\n\nКонтекст — сегодняшние данные:\n"
        if data.get("steps"):
            context += f"Шагов: {data['steps']}, "
        if data.get("body_battery_high"):
            context += f"Body Battery: {data.get('body_battery_low', 0)}→{data['body_battery_high']}, "
        if data.get("calories"):
            context += f"Потрачено: {data['calories']} ккал, "
        if data.get("oura_readiness_score"):
            context += f"Готовность Oura: {data['oura_readiness_score']}"

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

    sources = data.get("_sources", "Garmin")
    response = call_claude(
        COACH_PROMPT,
        f"Источники данных: {sources}\nДанные:\n{json.dumps(data, ensure_ascii=False, default=str)}\n\nПользователь написал: «{user_text}»",
        max_tokens=2000, retries=3
    )

    if response:
        return response

    s = data
    msg = "Claude прилёг. Кратко:\n"
    if s.get("sleep_hours"):
        msg += f"Сон {s['sleep_hours']}ч ({s.get('sleep_score', '?')}/100)\n"
    if s.get("oura_readiness_score"):
        msg += f"Готовность Oura: {s['oura_readiness_score']}/100\n"
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

    sources = data.get("_sources", "Garmin")
    response = call_claude(
        COACH_PROMPT,
        f"Утренний брифинг (07:00). Источники: {sources}.\nВчерашние данные:\n{json.dumps(data, ensure_ascii=False, default=str)}\n\n"
        "ПОДЪЁМ! Поприветствуй, разнеси или похвали за вчера, и дай ПРИКАЗЫ на сегодня: "
        "тренировка или отдых, что жрать, во сколько спать. Учитывай данные с Garmin И Oura.",
        max_tokens=2000, retries=3
    )

    if not response:
        response = "🌅 ПОДЪЁМ! Claude спит, но ты — нет. Встал и пошёл. /today когда очнусь."

    try:
        bot.send_message(MY_CHAT_ID, response)
    except Exception as e:
        print(f"Morning report error: {e}")

# ============================================================
# PERIODIC CHECKIN
# ============================================================
CHECKIN_PROMPTS = {
    10: (
        "Чекин 10:00 утра. Источники: {sources}. Данные:\n{data}\n\n"
        "Утро в разгаре. Шаги, стресс, Battery, готовность Oura. "
        "Напомни про завтрак. Дай пинка если сидит. Коротко 3-5 предложений."
    ),
    13: (
        "Чекин 13:00 — обед. Источники: {sources}. Данные:\n{data}\n\n"
        "Полдень. Прогресс по шагам и активности. "
        "ПРИКАЖИ что есть на обед. Напомни про тренировку. Коротко 3-5 предложений."
    ),
    16: (
        "Чекин 16:00 — послеобед. Источники: {sources}. Данные:\n{data}\n\n"
        "Вторая половина дня. Шаги, стресс, энергия. "
        "Не тренировался — последний шанс. Устал — как восстановиться. Коротко 3-5 предложений."
    ),
    19: (
        "Чекин 19:00 — вечер. Источники: {sources}. Данные:\n{data}\n\n"
        "Вечер. Итог дня. Что есть на ужин. Когда спать. Коротко 3-5 предложений."
    ),
    22: (
        "Чекин 22:00 — отбой. Источники: {sources}. Данные:\n{data}\n\n"
        "ОТБОЙ. Итог дня — разнеси или похвали. Убери телефон. Спать. Коротко 3-5 предложений."
    ),
}

def send_checkin():
    israel_hour = get_israel_now().hour
    slot = min(CHECKIN_PROMPTS.keys(), key=lambda h: abs(h - israel_hour))

    data = fetch_daily_summary(today_str())
    if "error" in data:
        return

    sources = data.get("_sources", "Garmin")
    prompt_template = CHECKIN_PROMPTS[slot]
    prompt = prompt_template.format(
        data=json.dumps(data, ensure_ascii=False, default=str),
        sources=sources,
    )

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
    oura_status = "✅ Oura Ring 4" if OURA_TOKEN else "❌ Oura не подключён"
    bot.send_message(MY_CHAT_ID,
        "💪 СМИРНО! Я Физрук — твой персональный тренер.\n\n"
        f"📡 Источники: Garmin Fenix 6X Pro + {oura_status}\n\n"
        "Вижу ВСЁ. Сон, пульс, стресс, готовность, температуру тела.\n\n"
        "Пиши или говори голосом:\n"
        "• «Как я?» — разбор полётов\n"
        "• «Как спал?» — анализ сна (Garmin + Oura)\n"
        "• «Что делать?» — приказы на день\n"
        "• «Что поесть?» — план питания\n"
        "• «Можно тренироваться?» — разрешение или запрет\n\n"
        "📸 Скинь ФОТО ЕДЫ — скажу что ты натворил\n"
        "🎙 Отправь ГОЛОСОВОЕ — пойму\n\n"
        "Утром в 7:00 — подъём и разбор. Без отмазок."
    )

@bot.message_handler(commands=["today"])
def cmd_today(message):
    if message.chat.id != MY_CHAT_ID: return
    bot.send_message(MY_CHAT_ID, "🔍 Проверяю Garmin + Oura...")
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
    bot.send_message(MY_CHAT_ID, generate_response("Как я спал? Данные с Garmin и Oura. Разнеси или похвали.", data))

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
# VOICE MESSAGE HANDLER
# ============================================================
def transcribe_voice(message):
    """Download voice and transcribe with OpenAI Whisper."""
    if not openai_client:
        return None
    try:
        file_info = bot.get_file(message.voice.file_id)
        file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_info.file_path}"
        response = http_requests.get(file_url)
        if response.status_code != 200:
            return None
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp.write(response.content)
            tmp_path = tmp.name
        with open(tmp_path, "rb") as audio_file:
            transcript = openai_client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language="ru",
            )
        os.unlink(tmp_path)
        return transcript.text
    except Exception as e:
        print(f"Voice error: {e}")
        try:
            os.unlink(tmp_path)
        except:
            pass
        return None

@bot.message_handler(content_types=["voice", "video_note"])
def handle_voice(message):
    if message.chat.id != MY_CHAT_ID:
        return
    bot.send_message(MY_CHAT_ID, "🎙 Слушаю...")
    text = transcribe_voice(message)
    if not text:
        bot.send_message(MY_CHAT_ID, "Не разобрал. Скажи ещё раз или напиши текстом.")
        return
    bot.send_message(MY_CHAT_ID, f"✅ Понял: «{text}»")
    day = yesterday_str() if any(w in text.lower() for w in ["вчера", "yesterday"]) else today_str()
    data = fetch_daily_summary(day)
    bot.send_message(MY_CHAT_ID, generate_response(text, data))

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
    print(f"📡 Oura Ring 4: {'✅ token set' if OURA_TOKEN else '❌ no token'}")
    print(f"🎙 Voice: {'✅ OpenAI Whisper' if OPENAI_API_KEY else '❌ no key'}")

    # Wait for old instance to stop
    print("⏳ Жду 15 сек...")
    time.sleep(15)

    print("🔄 Сбрасываю webhook...")
    bot.remove_webhook()
    time.sleep(2)

    for attempt in range(10):
        try:
            bot.get_updates(offset=-1, timeout=1)
            print("✅ Telegram API свободен!")
            break
        except Exception as e:
            if "409" in str(e):
                wait = (attempt + 1) * 5
                print(f"⚠️ 409 conflict, жду {wait} сек ({attempt+1}/10)...")
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
