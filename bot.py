import os
import sys
import re
import requests
import json
import time
from datetime import datetime, timedelta
from collections import deque
import pytz

# =====================================================================
# НАСТРОЙКИ (переменные как в hockey.py)
# =====================================================================
BOT_TOKEN = os.getenv('BOT_TOKEN') or os.getenv('BOT_TOKEN_PROGNOZ')
CHAT_ID = os.getenv('CHAT_ID_HOCKEY') or os.getenv('CHAT_ID_FOOTBALL') or os.getenv('CHAT_ID')

if not BOT_TOKEN or not CHAT_ID:
    print("❌ BOT_TOKEN или CHAT_ID не заданы", flush=True)
    sys.exit(1)

print(f"✅ BOT_TOKEN: {BOT_TOKEN[:5]}...", flush=True)
print(f"✅ CHAT_ID: {CHAT_ID}", flush=True)

MOSCOW_TZ = pytz.timezone('Europe/Moscow')
BASE_URL = "https://1xlite-7720.pro"
API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# =====================================================================
# ЛИГИ ХОККЕЯ
# =====================================================================
LEAGUE_IDS = {
    3355:    "🏒 КХЛ",
    101763:  "🏒 ВХЛ",
    104781:  "🏒 МХЛ",
    34833:   "🇸🇪 Чемпионат Швеции. Аллсвенскан",
    1706943: "🇧🇾 Чемпионат Беларуси. Экстралига",
    2007383: "🇨🇿 Чехия. Университетская лига",
}

# =====================================================================
# RUSCORE
# =====================================================================
RUSCORE_URL = "https://api-statistics.ruscore.ru/v1/events"
RUSCORE_APP_ID = "ruscore"
RUSCORE_API_KEY = "yAUBmZp9XJgh3US6bN1GZKtAYsFRKET6"
RUSCORE_SPORT = 5
RUSCORE_TIMEOUT = 15

RUSCORE_HISTORY_DAYS = 14
RUSCORE_CACHE_SEC = 3600

# =====================================================================
# ПРЕМАТЧ
# =====================================================================
PREMATCH_INTERVAL = 300          # раз в 5 мин
PREMATCH_MINUTES_FROM = 55
PREMATCH_MINUTES_TO = 65

# =====================================================================
# ПОРОГИ
# =====================================================================
MIN_HISTORY_MATCHES = 10
GOAL_EACH_PERIOD_THRESHOLD = 0.70    # 70%
TOTAL_MIN_SCORE = 70                  # для тотала
TOTAL_LINES = (4.5, 5.5, 6.5, 7.5)

# =====================================================================
# ОБЩИЕ
# =====================================================================
UPDATE_INTERVAL = 60
SLEEP_HOUR_START = 1
SLEEP_HOUR_END   = 12

# =====================================================================
# ЗАГОЛОВКИ
# =====================================================================
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 YaBrowser/26.8.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Content-Type": "application/json",
    "Referer": f"{BASE_URL}/ru/line/ice-hockey",
    "Origin": BASE_URL,
    "is-srv": "false",
    "priority": "u=1, i",
    "sec-ch-ua": '"Not;A=Brand";v="8", "Chromium";v="150", "YaBrowser";v="26.8", "Yowser";v="2.5"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "x-app-n": "__BETTING_APP__",
    "x-hd": "owR7kNTG7qqNQtxninDXQBLer79M6KT0KQD9+7BfJAwOaJJtL5t8vWkY96ZhOCZmO2AbNvlPzNkx5DAfv0rEuhm4FMhuvnj044szUW5eKpGx9bWd0Hcb7s1oWJFhdS8DylBXbdgyWBSD6HxMcGW6yaXvCLQ3MCmMcHRLCT3JDCclW8NAkY7GPko8FJICathgP7k1x0GGTcHCtYEc0sxZUhmZsbYWBpBTDXPPavqWgwf3utt40Uyuwq5JnCgxwaUGAz7/umvF9fsDXiB42g==",
    "x-requested-with": "XMLHttpRequest",
    "x-svc-source": "__BETTING_APP__",
    "Cookie": "platform_type=desktop; lng=ru; cookies_agree_type=3; tzo=3; is12h=0; fatman_uuid=49ad8be1-0777-45e9-8d1f-5f5c3eb16b79; che_g=1459a921-4f77-4465-bfda-d092eb1f1f74; sh.session.id=35558d78-509c-488c-9ff5-f1b3511f8c73; _ga=GA1.1.1492770073.1789849171; _gcl_au=1.1.126561714.1789849171; auid=ua+l62qxaoiWeF0kAxuSAg==; SESSION=5000cc91cc6a5330be04376abc6a9813; window_width=1101; _ga_7JGWL9SV66=GS2.1.s1790012066$o2$g1$t1790013997$j48$l0$h535646863",
}

RUSCORE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 YaBrowser/26.8.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://ruscore.ru",
    "Referer": "https://ruscore.ru/",
}

print("✅ Настройки загружены", flush=True)

# =====================================================================
# СОСТОЯНИЕ
# =====================================================================
sent_prematch = {}          # {gid: ts} — уже отправленные прогнозы

ruscore_history_cache = {
    "ts": 0,
    "events": []
}

# =====================================================================
# ВРЕМЯ
# =====================================================================
def is_active_time():
    h = datetime.now(MOSCOW_TZ).hour
    return not (SLEEP_HOUR_START <= h < SLEEP_HOUR_END)

# =====================================================================
# 1XLITE API (только прематч)
# =====================================================================
def get_prematch_games():
    url = f"{BASE_URL}/service-api/main-line-feed/v3/games1x2"
    params = {
        "cfView": 3, "count": 40, "fcountry": 1,
        "gr": 2336, "grMode": 4, "lng": "ru", "ref": 1,
        "selectedMs": "2.2",
    }
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=15)
        if r.status_code != 200:
            return []
        data = r.json()
        if not isinstance(data, list):
            return []
        return [g for g in data if isinstance(g, dict) and (g.get("sport") or {}).get("id") == 2]
    except Exception as e:
        print(f"   ❌ Prematch: {e}", flush=True)
        return []

# =====================================================================
# RUSCORE API
# =====================================================================
def ruscore_get_events(date_obj):
    params = {
        "app_id": RUSCORE_APP_ID,
        "api_key": RUSCORE_API_KEY,
        "lang": "ru",
        "tz": "Europe/Moscow",
        "sport": RUSCORE_SPORT,
        "date": date_obj.strftime("%Y-%m-%d"),
    }
    try:
        r = requests.get(
            RUSCORE_URL,
            params=params,
            headers=RUSCORE_HEADERS,
            timeout=RUSCORE_TIMEOUT
        )
        if r.status_code != 200:
            print(f"⚠️ Ruscore HTTP {r.status_code}", flush=True)
            return []
        data = r.json()
        events = []
        for league in data.get("data", []) or []:
            if not isinstance(league, dict):
                continue
            league_id = league.get("id")
            league_name = league.get("name", "")
            for event in league.get("events", []) or []:
                if not isinstance(event, dict):
                    continue
                event["_league_id"] = league_id
                event["_league_name"] = league_name
                events.append(event)
        return events
    except Exception as e:
        print(f"⚠️ Ошибка Ruscore: {e}", flush=True)
        return []

def ruscore_get_history():
    now_ts = time.time()
    if (ruscore_history_cache["events"]
            and now_ts - ruscore_history_cache["ts"] < RUSCORE_CACHE_SEC):
        return ruscore_history_cache["events"]

    today = datetime.now(MOSCOW_TZ).date()
    result = []
    for i in range(1, RUSCORE_HISTORY_DAYS + 1):
        day = today - timedelta(days=i)
        events = ruscore_get_events(day)
        if events:
            result.extend(events)

    ruscore_history_cache["ts"] = now_ts
    ruscore_history_cache["events"] = result
    print(f"📚 Ruscore история: {len(result)} матчей", flush=True)
    return result

def ruscore_is_finished(event):
    status = event.get("status") or {}
    if status.get("isLive"):
        return False
    text = (str(status.get("label", "")).lower() + " "
            + str(status.get("shortName", "")).lower())
    return any(x in text for x in (
        "ended", "finished", "completed", "final",
        "заверш", "окончен", "закончен",
    ))

def ruscore_get_score(event):
    """Возвращает overall и словарь периодов."""
    scores = event.get("score") or []
    overall = None
    periods = {}

    for item in scores:
        if not isinstance(item, dict):
            continue

        if item.get("type") == "overall":
            try:
                overall = (
                    int(item.get("home", 0)),
                    int(item.get("away", 0))
                )
            except Exception:
                pass

        elif item.get("type") == "regular_period":
            p = item.get("period")
            try:
                periods[p] = (
                    int(item.get("home", 0)),
                    int(item.get("away", 0))
                )
            except Exception:
                pass

    return overall, periods

def ruscore_get_datetime(event):
    value = event.get("time")
    try:
        if isinstance(value, (int, float)):
            ts = float(value)
            if ts > 10_000_000_000:
                ts /= 1000
            return datetime.fromtimestamp(ts, MOSCOW_TZ)
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = MOSCOW_TZ.localize(dt)
        return dt.astimezone(MOSCOW_TZ)
    except Exception:
        return None

def ruscore_normalize_team(name):
    s = str(name or "").lower()
    s = s.replace("ё", "е")
    s = re.sub(r"[^a-zа-я0-9]+", "", s)
    return s

# =====================================================================
# ИСТОРИЯ КОМАНД
# =====================================================================
def build_team_history(events):
    """{team_id: [список матчей]}"""
    history = {}

    for event in events:
        if not ruscore_is_finished(event):
            continue

        overall, periods = ruscore_get_score(event)
        if overall is None or not periods:
            continue

        home = event.get("home") or {}
        away = event.get("away") or {}
        home_id = home.get("id")
        away_id = away.get("id")
        if home_id is None or away_id is None:
            continue

        event_time = ruscore_get_datetime(event)
        home_score, away_score = overall
        total = home_score + away_score

        # Голы по периодам
        period_goals = {}
        for p in (1, 2, 3):
            if p in periods:
                ph, pa = periods[p]
                period_goals[p] = ph + pa
            else:
                period_goals[p] = None

        # Гол во всех 3 периодах?
        all_three = (
            period_goals.get(1) is not None and period_goals[1] > 0
            and period_goals.get(2) is not None and period_goals[2] > 0
            and period_goals.get(3) is not None and period_goals[3] > 0
        )

        row_home = {
            "total": total,
            "scored": home_score,
            "conceded": away_score,
            "period_goals": period_goals,
            "all_three_periods": all_three,
            "date": event_time,
        }
        row_away = {
            "total": total,
            "scored": away_score,
            "conceded": home_score,
            "period_goals": period_goals,
            "all_three_periods": all_three,
            "date": event_time,
        }

        history.setdefault(str(home_id), []).append(row_home)
        history.setdefault(str(away_id), []).append(row_away)

    for team_id in history:
        history[team_id].sort(
            key=lambda x: (x["date"] or datetime(1970, 1, 1, tzinfo=MOSCOW_TZ)),
            reverse=True
        )

    return history

def get_team_stats(rows, count):
    rows = rows[:count]
    if not rows:
        return None

    n = len(rows)
    over45 = sum(x["total"] > 4.5 for x in rows) / n
    over55 = sum(x["total"] > 5.5 for x in rows) / n
    over65 = sum(x["total"] > 6.5 for x in rows) / n
    under45 = sum(x["total"] < 4.5 for x in rows) / n
    under55 = sum(x["total"] < 5.5 for x in rows) / n
    under65 = sum(x["total"] < 6.5 for x in rows) / n

    all_three = sum(x["all_three_periods"] for x in rows) / n

    return {
        "count": n,
        "avg_total": sum(x["total"] for x in rows) / n,
        "avg_scored": sum(x["scored"] for x in rows) / n,
        "avg_conceded": sum(x["conceded"] for x in rows) / n,
        "over45": over45, "over55": over55, "over65": over65,
        "under45": under45, "under55": under55, "under65": under65,
        "all_three_periods": all_three,
    }

# =====================================================================
# ПОИСК МАТЧА В RUSCORE
# =====================================================================
def ruscore_find_game(p, history):
    """Находит матч в истории Ruscore по именам команд + времени старта."""
    team1 = ruscore_normalize_team(p["team1"])
    team2 = ruscore_normalize_team(p["team2"])

    target_time = None
    if p.get("start_ts"):
        try:
            target_time = datetime.fromtimestamp(int(p["start_ts"]), MOSCOW_TZ)
        except (ValueError, TypeError):
            pass

    best = None
    best_diff = None

    for event in history:
        home = event.get("home") or {}
        away = event.get("away") or {}
        h = ruscore_normalize_team(home.get("name", ""))
        a = ruscore_normalize_team(away.get("name", ""))

        same_teams = (
            (h == team1 and a == team2)
            or (h == team2 and a == team1)
        )
        if not same_teams:
            continue

        if target_time is None:
            return event

        event_time = ruscore_get_datetime(event)
        if event_time is None:
            continue

        diff = abs((event_time - target_time).total_seconds())
        if diff > 15 * 60:
            continue

        if best_diff is None or diff < best_diff:
            best = event
            best_diff = diff

    return best

# =====================================================================
# ПРОГНОЗ: ГОЛ В КАЖДОМ ПЕРИОДЕ
# =====================================================================
def predict_goal_each_period(p, history):
    """
    Считает P(гол во всех 3 периодах) по последним N матчам
    обеих команд. Возвращает словарь или None.
    """
    team1 = ruscore_normalize_team(p["team1"])
    team2 = ruscore_normalize_team(p["team2"])

    # Ищем ID команд в истории
    team1_id = None
    team2_id = None
    team1_name = None
    team2_name = None

    for team_id, rows in history.items():
        for row in rows:
            pass  # ID уже есть в ключе

    # Более правильно: сопоставляем по именам из Ruscore
    for event in history:
        home = event.get("home") or {}
        away = event.get("away") or {}
        h = ruscore_normalize_team(home.get("name", ""))
        a = ruscore_normalize_team(away.get("name", ""))

        if h == team1 and team1_id is None:
            team1_id = str(home.get("id"))
            team1_name = home.get("name")
        if a == team1 and team1_id is None:
            team1_id = str(away.get("id"))
            team1_name = away.get("name")
        if h == team2 and team2_id is None:
            team2_id = str(home.get("id"))
            team2_name = home.get("name")
        if a == team2 and team2_id is None:
            team2_id = str(away.get("id"))
            team2_name = away.get("name")

        if team1_id and team2_id:
            break

    if not team1_id or not team2_id:
        return None

    rows1 = history.get(team1_id, [])
    rows2 = history.get(team2_id, [])

    if len(rows1) < MIN_HISTORY_MATCHES or len(rows2) < MIN_HISTORY_MATCHES:
        return None

    st1 = get_team_stats(rows1, MIN_HISTORY_MATCHES)
    st2 = get_team_stats(rows2, MIN_HISTORY_MATCHES)

    p1 = st1["all_three_periods"]
    p2 = st2["all_three_periods"]
    p_final = (p1 + p2) / 2

    return {
        "team1_name": team1_name,
        "team2_name": team2_name,
        "team1_matches": st1["count"],
        "team2_matches": st2["count"],
        "team1_p": p1,
        "team2_p": p2,
        "p_final": p_final,
    }

# =====================================================================
# ПРОГНОЗ: ТОТАЛ
# =====================================================================
def predict_total(p, history):
    """Считает тотал ТБ/ТМ."""
    team1 = ruscore_normalize_team(p["team1"])
    team2 = ruscore_normalize_team(p["team2"])

    team1_id = team2_id = None
    for event in history:
        home = event.get("home") or {}
        away = event.get("away") or {}
        h = ruscore_normalize_team(home.get("name", ""))
        a = ruscore_normalize_team(away.get("name", ""))

        if h == team1 and team1_id is None:
            team1_id = str(home.get("id"))
        if a == team1 and team1_id is None:
            team1_id = str(away.get("id"))
        if h == team2 and team2_id is None:
            team2_id = str(home.get("id"))
        if a == team2 and team2_id is None:
            team2_id = str(away.get("id"))

        if team1_id and team2_id:
            break

    if not team1_id or not team2_id:
        return None

    rows1 = history.get(team1_id, [])
    rows2 = history.get(team2_id, [])

    if len(rows1) < MIN_HISTORY_MATCHES or len(rows2) < MIN_HISTORY_MATCHES:
        return None

    st1 = get_team_stats(rows1, MIN_HISTORY_MATCHES)
    st2 = get_team_stats(rows2, MIN_HISTORY_MATCHES)

    # Ожидаемый тотал: среднее по обеим командам
    expected_total = (st1["avg_total"] + st2["avg_total"]) / 2

    candidates = []
    for line in TOTAL_LINES:
        if expected_total > line:
            support = (st1["over55"] + st2["over55"]) / 2
            side = "ТБ"
        else:
            support = (st1["under55"] + st2["under55"]) / 2
            side = "ТМ"

        edge = abs(expected_total - line)
        score = min(edge * 20, 30) + support * 70

        if edge < 0.30:
            score -= 15

        if score < TOTAL_MIN_SCORE:
            continue

        candidates.append({
            "line": line, "side": side,
            "expected": expected_total,
            "score": score, "support": support * 100,
        })

    if not candidates:
        return None

    return max(candidates, key=lambda x: x["score"])

# =====================================================================
# TELEGRAM
# =====================================================================
def send_telegram(text):
    try:
        r = requests.post(API + "/sendMessage",
                          json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"})
        if r.status_code == 200:
            return r.json()["result"]["message_id"]
    except Exception as e:
        print(f"❌ TG send: {e}", flush=True)
    return None

# =====================================================================
# ФОРМАТЫ
# =====================================================================
def format_goal_each_period_signal(p, pred):
    p1_pct = pred["team1_p"] * 100
    p2_pct = pred["team2_p"] * 100
    pf_pct = pred["p_final"] * 100

    return (
        f"🏒 <b>ГОЛ В КАЖДОМ ПЕРИОДЕ</b>\n"
        f"{p['league']}\n"
        f"🏒 <b>{p['match']}</b>\n"
        f"🕐 Старт: <b>{p['start_str']}</b>\n"
        f"\n"
        f"💡 <b>Ставка: Гол в каждом периоде — ДА</b>\n"
        f"🔥 Вероятность: <b>{pf_pct:.0f}%</b>\n"
        f"\n"
        f"📊 По последним {MIN_HISTORY_MATCHES} матчам:\n"
        f"   • {pred['team1_name']}: {p1_pct:.0f}%\n"
        f"   • {pred['team2_name']}: {p2_pct:.0f}%"
    )

def format_total_signal(p, pred):
    return (
        f"🎯 <b>ПРЕМАТЧ ТОТАЛ</b>\n"
        f"{p['league']}\n"
        f"🏒 <b>{p['match']}</b>\n"
        f"🕐 Старт: <b>{p['start_str']}</b>\n"
        f"\n"
        f"📊 Ожидаемый тотал: <b>{pred['expected']:.2f}</b>\n"
        f"💡 <b>Ставка: {pred['side']} {pred['line']}</b>\n"
        f"🔥 Уверенность: <b>{pred['score']:.0f}/100</b>\n"
        f"📈 Поддержка: {pred['support']:.0f}%"
    )

# =====================================================================
# ПАРСИНГ ПРЕМАТЧ-МАТЧА
# =====================================================================
def parse_prematch_game(game):
    if not isinstance(game, dict):
        return None
    if (game.get("sport") or {}).get("id") != 2:
        return None
    liga_id = (game.get("liga") or {}).get("id")
    if liga_id not in LEAGUE_IDS:
        return None

    o1 = (game.get("opponent1") or {}).get("fullName", "?")
    o2 = (game.get("opponent2") or {}).get("fullName", "?")

    start_ts = game.get("startTs")
    start_str = "?"
    if start_ts:
        try:
            start_dt = datetime.fromtimestamp(int(start_ts), MOSCOW_TZ)
            start_str = start_dt.strftime("%d.%m %H:%M МСК")
        except (ValueError, TypeError):
            pass

    return {
        "game_id": game.get("id"),
        "liga_id": liga_id,
        "league": LEAGUE_IDS.get(liga_id, ""),
        "team1": o1, "team2": o2,
        "match": f"{o1} — {o2}",
        "start_ts": start_ts,
        "start_str": start_str,
        "raw": game,
    }

# =====================================================================
# МОНИТОР ПРЕМАТЧА
# =====================================================================
def monitor_prematch():
    global sent_prematch

    now = int(time.time())
    print(f"🕐 Прематч: {datetime.now(MOSCOW_TZ).strftime('%H:%M:%S')}", flush=True)

    games = get_prematch_games()
    print(f"   🔍 Всего из фида: {len(games)}")
    if not games:
        print("   ⚠️ Прематч пуст", flush=True)
        return

    history = ruscore_get_history()
    if not history:
        print("   ⚠️ Ruscore история пуста", flush=True)
        return

    total_checked = 0
    total_sent = 0

    for game in games:
        p = parse_prematch_game(game)
        if not p:
            continue

        gid = p["game_id"]
        if gid in sent_prematch:
            continue

        # Проверяем время старта
        start_ts = p.get("start_ts")
        if not start_ts:
            continue

        try:
            start_dt = datetime.fromtimestamp(int(start_ts), MOSCOW_TZ)
            diff_min = (start_dt - datetime.now(MOSCOW_TZ)).total_seconds() / 60
        except (ValueError, TypeError):
            continue

        if not (PREMATCH_MINUTES_FROM <= diff_min <= PREMATCH_MINUTES_TO):
            continue

        # ===== ПРОГНОЗ: ГОЛ В КАЖДОМ ПЕРИОДЕ =====
        goal_pred = predict_goal_each_period(p, history)
        if goal_pred and goal_pred["p_final"] >= GOAL_EACH_PERIOD_THRESHOLD:
            text = format_goal_each_period_signal(p, goal_pred)
            if send_telegram(text):
                sent_prematch[gid] = now
                total_sent += 1
                print(f"    🏒 {p['match']} | ГОЛ В КАЖДОМ ПЕРИОДЕ "
                      f"{goal_pred['p_final']*100:.0f}%", flush=True)
                time.sleep(1)
                continue  # на один матч — один сигнал

        # ===== ПРОГНОЗ: ТОТАЛ =====
        total_pred = predict_total(p, history)
        if total_pred:
            text = format_total_signal(p, total_pred)
            if send_telegram(text):
                sent_prematch[gid] = now
                total_sent += 1
                print(f"    🎯 {p['match']} | ТОТАЛ {total_pred['side']} "
                      f"{total_pred['line']} | {total_pred['score']:.0f}", flush=True)
                time.sleep(1)

        total_checked += 1

    print(f"✅ Прематч: проверено {total_checked}, отправлено {total_sent}", flush=True)

    # Чистим старые (за 24 часа)
    sent_prematch = {k: v for k, v in sent_prematch.items() if now - v < 86400}

# =====================================================================
# MAIN
# =====================================================================
def main():
    print("🚀 ХОККЕЙ ПРЕМАТЧ-БОТ ЗАПУЩЕН", flush=True)
    print(f"📋 Лиг: {len(LEAGUE_IDS)}", flush=True)
    print(f"🏒 Гол в каждом периоде: порог {GOAL_EACH_PERIOD_THRESHOLD*100:.0f}%", flush=True)
    print(f"📊 История: последние {MIN_HISTORY_MATCHES} матчей", flush=True)
    print(f"🎯 Тотал: порог {TOTAL_MIN_SCORE}", flush=True)
    print(f"⏰ Окно: {PREMATCH_MINUTES_FROM}-{PREMATCH_MINUTES_TO} мин до старта", flush=True)
    print("=" * 60, flush=True)

    while True:
        try:
            now_str = datetime.now(MOSCOW_TZ).strftime('%H:%M')
            if not is_active_time():
                print(f"😴 Ночь ({now_str})", flush=True)
                time.sleep(600)
                continue

            monitor_prematch()
            time.sleep(PREMATCH_INTERVAL)

        except KeyboardInterrupt:
            print("⏹️", flush=True)
            break
        except Exception as e:
            print(f"❌ {e}", flush=True)
            import traceback
            traceback.print_exc()
            time.sleep(30)

if __name__ == "__main__":
    main()