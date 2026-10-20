# -*- coding: utf-8 -*-
"""Настройки хоккейного бота OLD."""
import os
import pytz

BOT_TOKEN = os.getenv('BOT_TOKEN') or os.getenv('BOT_TOKEN_PROGNOZ')
CHAT_ID = os.getenv('CHAT_ID_HOCKEY') or os.getenv('CHAT_ID_FOOTBALL') or os.getenv('CHAT_ID')
if not BOT_TOKEN or not CHAT_ID:
    raise RuntimeError("Задайте BOT_TOKEN (или BOT_TOKEN_PROGNOZ) и CHAT_ID_HOCKEY (или CHAT_ID)")


MOSCOW_TZ = pytz.timezone('Europe/Moscow')
API = f"https://api.telegram.org/bot{BOT_TOKEN}"

STATS_SITE_URL = os.getenv("STATS_SITE_URL") or "https://bot-1787342419-7555-timwatgrz.bothost.tech"

print(f"✅ Статистика: {STATS_SITE_URL}", flush=True)

# =====================================================================
# ЛИГИ
# =====================================================================
LEAGUE_KEYWORDS = [
    "Беларусь", "Экстралига",
    "НХЛ", "NHL", "АХЛ", "AHL",
    "Liiga", "Финляндия",
    "DEL", "Германия",
    "SHL", "Швеция", "Аллсвенскан",
    "NL", "Швейцария",
    "Чехия", "Университетская",
    "Австрия", "ICEHL",
    "Словакия", "Tipsport",
    "Норвегия", "Дания", "Франция", "Великобритания",
]

# =====================================================================
# RUSCORE
# =====================================================================
RUSCORE_URL = "https://api-statistics.ruscore.ru/v1/events"
RUSCORE_APP_ID = "ruscore"
RUSCORE_API_KEY = os.getenv("RUSCORE_API_KEY", "")
RUSCORE_SPORT = 5
RUSCORE_TIMEOUT = 15
RUSCORE_HISTORY_DAYS = 30
RUSCORE_CACHE_SEC = 3600

# =====================================================================
# 1XBET (для сбора кэфов)
# =====================================================================
XBET_URL = "https://1xlite-8150.pro/service-api/main-line-feed/v3/games1x2"

# ID лиг в 1xBet (формат: 2.2.{liga_id})
XBET_LEAGUE_IDS = {
    "NHL": 30619,
    "Czech": 1329569,
    "Belarus": 1706943,
    "Slovakia": 26279,
}

XBET_PARAMS = {
    "cfView": 3, "count": 40, "fcountry": 1,
    "gr": 2336, "grMode": 4, "lng": "ru", "ref": 1,
    "selectedMs": ",".join([f"2.2.{lid}" for lid in XBET_LEAGUE_IDS.values()]),
}

XBET_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 YaBrowser/26.8.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru,en;q=0.9",
    "x-app-n": "__BETTING_APP__",
    "x-requested-with": "XMLHttpRequest",
    "x-svc-source": "__BETTING_APP__",
    "Referer": "https://1xlite-7936.pro/ru/line/ice-hockey",
}
XBET_CACHE_SEC = 240

# =====================================================================
# ЛОГИКА
# =====================================================================
HISTORY_COUNT    = 10
H2H_COUNT        = 10
PERIOD_THRESHOLD = 0.70
TOTAL_LINES      = [4.5, 5.5, 6.5]
TOTAL_MARGIN     = 1.0

SEND_BEFORE_MAX = 60
SEND_BEFORE_MIN = 25

CHECK_AFTER_HOURS = 3
MAX_WAIT_HOURS    = 6

UPDATE_INTERVAL = 300
SLEEP_START = 1
SLEEP_END   = 12

# =====================================================================
# ФАЙЛЫ
# =====================================================================
PREDICTIONS_FILE = "predictions.json"
STATS_FILE       = "stats.json"
REPORT_FILE      = "report.txt"
STATE_FILE       = "state.json"

# =====================================================================
# ЗАГОЛОВКИ
# =====================================================================
RUSCORE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 YaBrowser/26.8.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://ruscore.ru",
    "Referer": "https://ruscore.ru/",
}

print("✅ Настройки загружены", flush=True)

