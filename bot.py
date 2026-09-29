import os
import sys
import re
import json
import requests
import time
import threading
from datetime import datetime, timedelta
import pytz
from flask import Flask, render_template, jsonify

# =====================================================================
# НАСТРОЙКИ
# =====================================================================
BOT_TOKEN = os.getenv('BOT_TOKEN') or os.getenv('BOT_TOKEN_PROGNOZ')
CHAT_ID = os.getenv('CHAT_ID_HOCKEY') or os.getenv('CHAT_ID_FOOTBALL') or os.getenv('CHAT_ID')

if not BOT_TOKEN or not CHAT_ID:
    print("❌ BOT_TOKEN или CHAT_ID не заданы", flush=True)
    sys.exit(1)

MOSCOW_TZ = pytz.timezone('Europe/Moscow')
API = f"https://api.telegram.org/bot{BOT_TOKEN}"

print(f"✅ BOT_TOKEN: {BOT_TOKEN[:5]}...", flush=True)
print(f"✅ CHAT_ID: {CHAT_ID}", flush=True)

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
RUSCORE_API_KEY = "yAUBmZp9XJgh3US6bN1GZKtAYsFRKET6"
RUSCORE_SPORT = 5
RUSCORE_TIMEOUT = 15
RUSCORE_HISTORY_DAYS = 30
RUSCORE_CACHE_SEC = 3600

# =====================================================================
# 1WIN (для сбора кэфов)
# =====================================================================
WIN_URL = "https://1xlite-7936.pro/service-api/main-line-feed/v3/games1x2"
WIN_PARAMS = {
    "cfView": 3, "count": 40, "fcountry": 1,
    "gr": 2336, "grMode": 4, "lng": "ru", "ref": 1,
    "selectedMs": "1.2,2.2,10.2",
}
WIN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 YaBrowser/26.8.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru,en;q=0.9",
    "x-app-n": "__BETTING_APP__",
    "x-requested-with": "XMLHttpRequest",
    "x-svc-source": "__BETTING_APP__",
    "Referer": "https://1xlite-7936.pro/ru/line/ice-hockey",
}
WIN_CACHE_SEC = 240

# =====================================================================
# ЛОГИКА
# =====================================================================
HISTORY_COUNT    = 10
H2H_COUNT        = 10
PERIOD_THRESHOLD = 0.70
TOTAL_LINES      = [4.5, 5.5]
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

# =====================================================================
# РАБОТА С ФАЙЛАМИ
# =====================================================================
def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, data):
    try:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    old = f.read()
                if old.strip() and old.strip() not in ("[]", "{}"):
                    with open(path + ".bak", "w", encoding="utf-8") as f:
                        f.write(old)
            except Exception:
                pass
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ Не сохранил {path}: {e}", flush=True)

predictions = load_json(PREDICTIONS_FILE, {})
stats       = load_json(STATS_FILE, [])
state       = load_json(STATE_FILE, {"last_update_id": 0})

ruscore_history_cache = {"ts": 0, "events": []}
win_odds_cache = {"ts": 0, "games": []}

# =====================================================================
# ВРЕМЯ
# =====================================================================
def is_active_time():
    now = datetime.now(MOSCOW_TZ)
    h = now.hour
    if not (SLEEP_START <= h < SLEEP_END):
        return True
    return has_nhl_activity(now)


def has_nhl_activity(now):
    now_ts = now.timestamp()

    for p in predictions.values():
        if not p.get("message_id"):
            send_min = p.get("send_min_ts", 0)
            send_max = p.get("send_max_ts", 0)
            if send_min - 300 <= now_ts <= send_max + 300:
                return True

    for p in predictions.values():
        if p.get("message_id"):
            start_dt = p.get("start_dt_dt")
            if isinstance(start_dt, str):
                try:
                    start_dt = datetime.fromisoformat(start_dt)
                except Exception:
                    continue
            if start_dt is None:
                continue
            check_after = start_dt + timedelta(hours=CHECK_AFTER_HOURS)
            if now >= check_after - timedelta(minutes=10):
                return True

    try:
        matches = get_today_matches()
        for m in matches:
            league = m["league"].upper()
            if "NHL" not in league and "НХЛ" not in league:
                continue
            diff_min = (m["start_dt"] - now).total_seconds() / 60
            if -10 <= diff_min <= 90:
                return True
    except Exception:
        pass

    return False

# =====================================================================
# ХЕЛПЕРЫ ДЛЯ АЗИАТСКОГО ТОТАЛА
# =====================================================================
def asian_line(original_line):
    if original_line is None:
        return None
    return int(original_line - 0.5)

def check_asian_over(total_goals, original_line):
    N = asian_line(original_line)
    if N is None:
        return None
    if total_goals > N:
        return "win"
    if total_goals == N:
        return "refund"
    return "lose"

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
        r = requests.get(RUSCORE_URL, params=params, headers=RUSCORE_HEADERS, timeout=RUSCORE_TIMEOUT)
        if r.status_code != 200:
            return []
        data = r.json()
        events = []
        for league in data.get("data", []) or []:
            if not isinstance(league, dict):
                continue
            league_name = league.get("name", "")
            for event in league.get("events", []) or []:
                if not isinstance(event, dict):
                    continue
                event["_league_name"] = league_name
                events.append(event)
        return events
    except Exception as e:
        print(f"⚠️ Ошибка Ruscore: {e}", flush=True)
        return []

def ruscore_get_today():
    today = datetime.now(MOSCOW_TZ).date()
    return ruscore_get_events(today)

def ruscore_get_history():
    now_ts = time.time()
    if ruscore_history_cache["events"] and now_ts - ruscore_history_cache["ts"] < RUSCORE_CACHE_SEC:
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
    text = (str(status.get("label", "")).lower() + " " + str(status.get("shortName", "")).lower())
    return any(x in text for x in ("ended", "finished", "completed", "final",
                                    "заверш", "окончен", "закончен"))

def ruscore_get_score(event):
    """
    Возвращает (overall, periods):
      overall = (home, away) — итоговый счёт (с учётом OT/буллитов)
      periods = {1: (h, a), 2: (h, a), 3: (h, a)} — счёт по периодам
    В JSON ruscore НЕТ поля period — считаем по порядку (period_idx).
    """
    scores = event.get("score") or []
    overall = None
    periods = {}
    period_idx = 0

    for item in scores:
        if not isinstance(item, dict):
            continue
        t = item.get("type")
        if t == "overall":
            try:
                overall = (int(item.get("home", 0)), int(item.get("away", 0)))
            except Exception:
                pass
        elif t == "regular_period":
            period_idx += 1
            try:
                periods[period_idx] = (int(item.get("home", 0)), int(item.get("away", 0)))
            except Exception:
                pass
        # overtime / penalties — игнорируем
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
    s = str(name or "").lower().replace("ё", "е")
    return re.sub(r"[^a-zа-я0-9]+", "", s)

# =====================================================================
# 1WIN — СБОР КЭФОВ
# =====================================================================
def win_get_games():
    now_ts = time.time()
    if win_odds_cache["games"] and now_ts - win_odds_cache["ts"] < WIN_CACHE_SEC:
        return win_odds_cache["games"]

    try:
        r = requests.get(WIN_URL, params=WIN_PARAMS, headers=WIN_HEADERS, timeout=15)
        if r.status_code != 200:
            return win_odds_cache.get("games", [])
        data = r.json()
        if not isinstance(data, list):
            return win_odds_cache.get("games", [])
        win_odds_cache["ts"] = now_ts
        win_odds_cache["games"] = data
        return data
    except Exception as e:
        print(f"⚠️ 1win: {e}", flush=True)
        return win_odds_cache.get("games", [])


def win_extract_1x2(game):
    result = {}
    for grp in game.get("eventGroups", []) or []:
        if grp.get("groupId") != 1:
            continue
        events = grp.get("events", [])
        if len(events) >= 1:
            for item in events[0]:
                if item.get("type") == 1:
                    result["П1"] = item.get("cf")
        if len(events) >= 2:
            for item in events[1]:
                if item.get("type") == 2:
                    result["X"] = item.get("cf")
        if len(events) >= 3:
            for item in events[2]:
                if item.get("type") == 3:
                    result["П2"] = item.get("cf")
    return result


def win_extract_total(game, line):
    if line is None:
        return None
    for grp in game.get("centralBlockEventGroups", []) or []:
        if grp.get("groupId") != 17:
            continue
        events = grp.get("events", [])
        if not events:
            continue
        for item in events[0]:
            if item.get("type") != 9:
                continue
            param = item.get("parameter")
            if param is None:
                continue
            try:
                if abs(float(param) - float(line)) < 0.01:
                    return item.get("cf")
            except Exception:
                continue
    return None


def win_find_game_by_teams(team1, team2):
    games = win_get_games()
    if not games:
        return None

    n1 = ruscore_normalize_team(team1)
    n2 = ruscore_normalize_team(team2)
    if not n1 or not n2:
        return None

    for g in games:
        if not isinstance(g, dict):
            continue
        o1 = (g.get("opponent1") or {}).get("fullName", "")
        o2 = (g.get("opponent2") or {}).get("fullName", "")
        gn1 = ruscore_normalize_team(o1)
        gn2 = ruscore_normalize_team(o2)
        if not gn1 or not gn2:
            continue
        if (gn1 == n1 and gn2 == n2) or (gn1 == n2 and gn2 == n1):
            return g
        f1 = n1.split()[0] if n1.split() else ""
        f2 = n2.split()[0] if n2.split() else ""
        gf1 = gn1.split()[0] if gn1.split() else ""
        gf2 = gn2.split()[0] if gn2.split() else ""
        if len(f1) >= 4 and len(f2) >= 4 and f1 == gf1 and f2 == gf2:
            return g
    return None


def win_get_odds_for_bet(match_team1, match_team2, bet_type, line=None):
    game = win_find_game_by_teams(match_team1, match_team2)
    if not game:
        return None

    if bet_type == "total":
        return win_extract_total(game, line)

    if bet_type == "period":
        # Реального кэфа нет, сайт возьмёт оценочный
        return None

    return None

# =====================================================================
# ИНДЕКСЫ
# =====================================================================
def build_name_index(events):
    index = {}
    for event in events:
        for side in ("home", "away"):
            team = event.get(side) or {}
            name = team.get("name", "")
            tid = team.get("id")
            if not name or tid is None:
                continue
            norm = ruscore_normalize_team(name)
            if norm and norm not in index:
                index[norm] = (str(tid), name)
    return index

def find_team(name, index):
    norm = ruscore_normalize_team(name)
    if norm in index:
        return index[norm]
    first = norm.split()[0] if norm else ""
    if len(first) >= 4:
        for k, v in index.items():
            k_first = k.split()[0] if k else ""
            if k_first == first:
                return v
    return None, None

def build_team_history(events):
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
        hs, aws = overall
        total = hs + aws

        p1, p2, p3 = periods.get(1), periods.get(2), periods.get(3)
        all_three = False
        if p1 and p2 and p3:
            all_three = (p1[0] + p1[1] > 0) and (p2[0] + p2[1] > 0) and (p3[0] + p3[1] > 0)

        row = {"total": total, "all_three": all_three, "date": event_time}
        history.setdefault(str(home_id), []).append(row)
        history.setdefault(str(away_id), []).append(row)

    for tid in history:
        history[tid].sort(key=lambda x: (x["date"] or datetime(1970, 1, 1, tzinfo=MOSCOW_TZ)), reverse=True)
    return history

def build_h2h_index(events):
    index = {}
    for event in events:
        if not ruscore_is_finished(event):
            continue
        home = event.get("home") or {}
        away = event.get("away") or {}
        h = home.get("id")
        a = away.get("id")
        if h is None or a is None:
            continue
        key = tuple(sorted([str(h), str(a)]))
        index.setdefault(key, []).append(event)
    return index

def get_h2h_rows(id1, id2, h2h_index, count):
    key = tuple(sorted([str(id1), str(id2)]))
    events = h2h_index.get(key, [])
    events = sorted(events,
                    key=lambda e: ruscore_get_datetime(e) or datetime(1970, 1, 1, tzinfo=MOSCOW_TZ),
                    reverse=True)
    rows = []
    for event in events[:count]:
        overall, periods = ruscore_get_score(event)
        if overall is None or not periods:
            continue
        hs, aws = overall
        total = hs + aws
        p1, p2, p3 = periods.get(1), periods.get(2), periods.get(3)
        all_three = False
        if p1 and p2 and p3:
            all_three = (p1[0] + p1[1] > 0) and (p2[0] + p2[1] > 0) and (p3[0] + p3[1] > 0)
        rows.append({"total": total, "all_three": all_three})
    return rows

# =====================================================================
# АНАЛИЗ
# =====================================================================
def analyze(team1_name, team2_name, history, h2h_index, name_index):
    id1, real1 = find_team(team1_name, name_index)
    id2, real2 = find_team(team2_name, name_index)
    if not id1 or not id2:
        return None

    rows1 = history.get(str(id1), [])[:HISTORY_COUNT]
    rows2 = history.get(str(id2), [])[:HISTORY_COUNT]
    rows_h2h = get_h2h_rows(id1, id2, h2h_index, H2H_COUNT)

    if len(rows1) < 5 or len(rows2) < 5:
        return None

    def avg_total(rows):
        return sum(r["total"] for r in rows) / len(rows) if rows else 0

    def pct_all_three(rows):
        return sum(1 for r in rows if r["all_three"]) / len(rows) if rows else 0

    avg1, avg2 = avg_total(rows1), avg_total(rows2)
    avg_h2h = avg_total(rows_h2h) if rows_h2h else None

    if avg_h2h is not None:
        expected_total = (avg1 + avg2 + avg_h2h) / 3
    else:
        expected_total = (avg1 + avg2) / 2

    p1, p2 = pct_all_three(rows1), pct_all_three(rows2)
    p_h2h = pct_all_three(rows_h2h) if rows_h2h else None

    if p_h2h is not None:
        period_pct = (p1 + p2 + p_h2h) / 3
    else:
        period_pct = (p1 + p2) / 2

    return {
        "real1": real1, "real2": real2,
        "avg1": avg1, "avg2": avg2, "avg_h2h": avg_h2h,
        "expected_total": expected_total,
        "period_pct": period_pct,
        "h2h_matches": len(rows_h2h),
        "team1_matches": len(rows1),
        "team2_matches": len(rows2),
    }

def decide_bet(analysis):
    candidates = []

    period_conf = analysis["period_pct"] * 100
    if analysis["period_pct"] >= PERIOD_THRESHOLD:
        candidates.append({
            "bet_type": "period",
            "confidence": period_conf,
        })

    expected = analysis["expected_total"]
    line_chosen = None
    for line in sorted(TOTAL_LINES, reverse=True):
        if expected - line >= TOTAL_MARGIN:
            line_chosen = line
            break

    if line_chosen:
        margin = expected - line_chosen
        total_conf = min(95, 55 + margin * 20)
        candidates.append({
            "bet_type": "total",
            "line": line_chosen,
            "confidence": total_conf,
            "margin": margin,
        })

    if not candidates:
        return None

    return max(candidates, key=lambda x: x["confidence"])

# =====================================================================
# ФОРМАТЫ
# =====================================================================
def format_prediction(p, analysis, bet):
    lines = [
        f"🏒 <b>ПРЕМАТЧ ПРОГНОЗ</b>",
        p["league"],
        f"🏒 <b>{p['match']}</b>",
        f"🕐 Старт: <b>{p['start_str']}</b>",
        "",
    ]
    if bet["bet_type"] == "period":
        lines.append(f"💡 <b>Ставка: Гол в каждом периоде — ДА</b>")
        lines.append(f"🔥 Уверенность: <b>{bet['confidence']:.0f}%</b>")
        lines.append("")
        lines.append(f"📊 За последние {HISTORY_COUNT} матчей + личные встречи:")
        lines.append(f"   • Вероятность гола во всех 3 периодах: <b>{analysis['period_pct']*100:.0f}%</b>")
    else:
        al = asian_line(bet["line"])
        lines.append(f"💡 <b>Ставка: ТБ {al} (азиатский)</b>")
        lines.append(f"🔥 Уверенность: <b>{bet['confidence']:.0f}%</b>")
        lines.append("")
        lines.append(f"📊 Средний тотал по последним матчам: <b>{analysis['expected_total']:.2f}</b>")
        lines.append(f"   • Запас над линией: <b>+{bet['margin']:.2f}</b>")
    return "\n".join(lines)


def format_result_msg(base_text, final_score, total_goals, period_goals_arr, outcome, bet_type, line=None):
    if outcome == "win":
        emoji = "✅"
        res = "ЗАШЛА"
        bar = "🟢"
    elif outcome == "refund":
        emoji = "🔄"
        res = "ВОЗВРАТ"
        bar = "🟡"
    else:
        emoji = "❌"
        res = "НЕ ЗАШЛА"
        bar = "🔴"

    p1 = period_goals_arr[0] if len(period_goals_arr) > 0 else "?"
    p2 = period_goals_arr[1] if len(period_goals_arr) > 1 else "?"
    p3 = period_goals_arr[2] if len(period_goals_arr) > 2 else "?"

    def mark(goals):
        if goals == "?":
            return "❔"
        try:
            return "✅" if int(goals) > 0 else "❌"
        except Exception:
            return "❔"

    periods_line = (
        f"   1-й: {mark(p1)} <b>{p1}</b>   "
        f"2-й: {mark(p2)} <b>{p2}</b>   "
        f"3-й: {mark(p3)} <b>{p3}</b>"
    )

    if bet_type == "period":
        detail = f"⚽ <b>Голы по периодам:</b>\n{periods_line}"
        tail = f"📈 Итог: {emoji} <b>{res}</b>"
    else:
        al = asian_line(line)
        detail = f"⚽ <b>Голы по периодам:</b>\n{periods_line}\n   Всего голов: <b>{total_goals}</b>"
        tail = f"📈 Итог: {emoji} <b>{res}</b> (азиатский ТБ {al})"

    return (
        base_text
        + f"\n\n━━━━━━━━━━━━━━━━━━\n"
        + f"🏁 Итоговый счёт: <b>{final_score}</b>\n"
        + f"{bar} {detail}\n"
        + f"{tail}"
    )

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

def edit_telegram(message_id, text):
    try:
        r = requests.post(API + "/editMessageText",
                          json={"chat_id": CHAT_ID, "message_id": message_id,
                                "text": text, "parse_mode": "HTML"})
        return r.status_code == 200
    except Exception as e:
        print(f"❌ TG edit: {e}", flush=True)
        return False

def get_updates():
    try:
        r = requests.get(API + "/getUpdates",
                         params={"offset": state.get("last_update_id", 0) + 1, "timeout": 5},
                         timeout=15)
        if r.status_code != 200:
            return []
        data = r.json()
        if not data.get("ok"):
            return []
        return data.get("result", [])
    except Exception as e:
        print(f"❌ TG updates: {e}", flush=True)
        return []

# =====================================================================
# /stats
# =====================================================================
def build_stats_short():
    if not stats:
        return "📊 Статистика пока пуста."

    total = len(stats)
    wins    = sum(1 for s in stats if s.get("outcome") == "win")
    loses   = sum(1 for s in stats if s.get("outcome") == "lose")
    refunds = sum(1 for s in stats if s.get("outcome") == "refund")
    winrate = wins / total * 100 if total else 0

    period_stats = [s for s in stats if s["bet_type"] == "period"]
    total_stats  = [s for s in stats if s["bet_type"] == "total"]

    def wr(arr):
        if not arr:
            return "—"
        w = sum(1 for s in arr if s.get("outcome") == "win")
        r = sum(1 for s in arr if s.get("outcome") == "refund")
        return f"{w}/{len(arr)} (+{r} возвр.)"

    return "\n".join([
        f"📊 <b>СТАТИСТИКА</b>",
        f"Всего прогнозов: <b>{total}</b>",
        f"✅ Зашло: <b>{wins}</b>",
        f"🔄 Возврат: <b>{refunds}</b>",
        f"❌ Не зашло: <b>{loses}</b>",
        f"🔥 Winrate: <b>{winrate:.1f}%</b>",
        "",
        f"🏒 Гол в каждом периоде: {wr(period_stats)}",
        f"🎯 Тотал: {wr(total_stats)}",
    ])

# =====================================================================
# ОТЧЁТ
# =====================================================================
def build_report():
    if not stats:
        return "📊 Статистика пуста.\n"

    total   = len(stats)
    wins    = sum(1 for s in stats if s.get("outcome") == "win")
    loses   = sum(1 for s in stats if s.get("outcome") == "lose")
    refunds = sum(1 for s in stats if s.get("outcome") == "refund")
    winrate = wins / total * 100 if total else 0

    lines = []
    lines.append("=" * 60)
    lines.append(f"ОТЧЁТ {datetime.now(MOSCOW_TZ).strftime('%d.%m.%Y %H:%M МСК')}")
    lines.append("=" * 60)
    lines.append(f"Всего прогнозов: {total}")
    lines.append(f"Зашло: {wins} ({winrate:.1f}%)")
    lines.append(f"Возврат: {refunds}")
    lines.append(f"Не зашло: {loses}")
    lines.append("")

    for bt, name in [("period", "ГОЛ В КАЖДОМ ПЕРИОДЕ"), ("total", "ТОТАЛ")]:
        arr = [s for s in stats if s["bet_type"] == bt]
        if not arr:
            continue
        w = sum(1 for s in arr if s.get("outcome") == "win")
        r = sum(1 for s in arr if s.get("outcome") == "refund")
        lines.append(f"{name}: {w}/{len(arr)} ({w/len(arr)*100:.0f}%), возврат {r}")
    lines.append("")

    lines.append("--- ПО ЛИГАМ ---")
    by_league = {}
    for s in stats:
        by_league.setdefault(s.get("league", "?"), []).append(s)
    for league, arr in sorted(by_league.items()):
        w = sum(1 for s in arr if s.get("outcome") == "win")
        lines.append(f"  {league}: {w}/{len(arr)} ({w/len(arr)*100:.0f}%)")
    lines.append("")

    lines.append("--- ПО УВЕРЕННОСТИ ---")
    for lo, hi in [(60, 70), (70, 80), (80, 90), (90, 101)]:
        arr = [s for s in stats if lo <= s.get("confidence", 0) < hi]
        if not arr:
            continue
        w = sum(1 for s in arr if s.get("outcome") == "win")
        lines.append(f"  {lo}-{hi-1}%: {w}/{len(arr)} ({w/len(arr)*100:.0f}%)")
    lines.append("")

    lines.append("--- ПО ЛИНИЯМ ТОТАЛА ---")
    by_line = {}
    for s in stats:
        if s["bet_type"] == "total":
            by_line.setdefault(s.get("line_asian", s.get("line")), []).append(s)
    for line, arr in sorted(by_line.items(), key=lambda x: (x[0] or 0)):
        w = sum(1 for s in arr if s.get("outcome") == "win")
        lines.append(f"  ТБ {line} (азиатский): {w}/{len(arr)} ({w/len(arr)*100:.0f}%)")
    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)

def save_report():
    try:
        with open(REPORT_FILE, "w", encoding="utf-8") as f:
            f.write(build_report())
    except Exception as e:
        print(f"⚠️ Не сохранил отчёт: {e}", flush=True)

# =====================================================================
# МАТЧИ
# =====================================================================
def get_today_matches():
    events = ruscore_get_today()
    matches = []
    now = datetime.now(MOSCOW_TZ)

    for event in events:
        league_name = str(event.get("_league_name", ""))
        if not any(kw.lower() in league_name.lower() for kw in LEAGUE_KEYWORDS):
            continue

        start_dt = ruscore_get_datetime(event)
        if start_dt is None:
            continue

        diff_min = (start_dt - now).total_seconds() / 60
        if diff_min < -120:
            continue

        if ruscore_is_finished(event):
            continue

        home = event.get("home") or {}
        away = event.get("away") or {}
        team1 = home.get("name", "?")
        team2 = away.get("name", "?")

        gid = f"{ruscore_normalize_team(team1)}|{ruscore_normalize_team(team2)}|{int(start_dt.timestamp())}"

        matches.append({
            "gid": gid,
            "league": league_name,
            "match": f"{team1} — {team2}",
            "team1": team1,
            "team2": team2,
            "start_dt": start_dt,
            "start_str": start_dt.strftime("%d.%m %H:%M МСК"),
        })

    return matches

# =====================================================================
# ПРОВЕРКА РЕЗУЛЬТАТА
# =====================================================================
def check_results():
    now = datetime.now(MOSCOW_TZ)
    today = now.date()
    yesterday = today - timedelta(days=1)

    events = ruscore_get_events(today) + ruscore_get_events(yesterday)

    by_key = {}
    for event in events:
        home = event.get("home") or {}
        away = event.get("away") or {}
        h = ruscore_normalize_team(home.get("name", ""))
        a = ruscore_normalize_team(away.get("name", ""))
        if not h or not a:
            continue
        by_key[f"{h}|{a}"] = event
        by_key[f"{a}|{h}"] = event

    checked = 0
    for gid in list(predictions.keys()):
        pred = predictions[gid]
        start_dt = pred.get("start_dt_dt")
        if isinstance(start_dt, str):
            try:
                start_dt = datetime.fromisoformat(start_dt)
            except Exception:
                start_dt = None

        if start_dt is None:
            predictions.pop(gid, None)
            continue

        check_after = start_dt + timedelta(hours=CHECK_AFTER_HOURS)
        max_wait    = start_dt + timedelta(hours=MAX_WAIT_HOURS)

        if now < check_after:
            continue

        event = by_key.get(pred["lookup_key"])

        if event is None:
            if now > max_wait:
                predictions.pop(gid, None)
            continue

        if not ruscore_is_finished(event):
            if now > max_wait:
                pass
            else:
                continue

        overall, periods = ruscore_get_score(event)
        if overall is None:
            continue

        hs, aws = overall
        total_goals = hs + aws
        final_score = f"{hs}-{aws}"

        def period_total(p):
            if p is None:
                return "?"
            return p[0] + p[1]
        period_arr = [
            period_total(periods.get(1)),
            period_total(periods.get(2)),
            period_total(periods.get(3)),
        ]

        bet_type = pred["bet_type"]
        line = pred.get("line")
        outcome = "lose"

        if bet_type == "period":
            p1, p2, p3 = periods.get(1), periods.get(2), periods.get(3)
            if not (p1 and p2 and p3):
                print(f"   ⚠️ {pred['match']}: нет всех 3 периодов, пропуск", flush=True)
                predictions.pop(gid, None)
                continue
            ok = (p1[0]+p1[1] > 0) and (p2[0]+p2[1] > 0) and (p3[0]+p3[1] > 0)
            outcome = "win" if ok else "lose"

        elif bet_type == "total":
            outcome = check_asian_over(total_goals, line)
            if outcome is None:
                outcome = "lose"

        result_text = format_result_msg(
            pred["base_text"], final_score, total_goals,
            period_arr, outcome, bet_type, line
        )
        edit_telegram(pred["message_id"], result_text)

        entry = {
            "date": now.strftime("%Y-%m-%d %H:%M"),
            "gid": gid,
            "league": pred["league"],
            "match": pred["match"],
            "team1": pred["team1"],
            "team2": pred["team2"],
            "bet_type": bet_type,
            "line": line,
            "confidence": pred["confidence"],
            "period_pct": pred.get("period_pct"),
            "expected_total": pred.get("expected_total"),
            "h2h_matches": pred.get("h2h_matches"),
            "start_str": pred["start_str"],
            "final_score": final_score,
            "total_goals": total_goals,
            "period_goals": period_arr,
            "outcome": outcome,
            "odds": pred.get("odds"),
        }
        if bet_type == "total":
            entry["line_original"] = line
            entry["line_asian"] = asian_line(line)

        stats.append(entry)
        save_json(STATS_FILE, stats)

        predictions.pop(gid, None)
        checked += 1

        emoji = {"win": "✅", "lose": "❌", "refund": "🔄"}.get(outcome, "?")
        print(f"   🏁 {pred['match']} | {final_score} | {emoji} ({outcome})", flush=True)

    if checked:
        save_json(PREDICTIONS_FILE, predictions)
        save_report()

# =====================================================================
# ЦИКЛ
# =====================================================================
def monitor():
    now = datetime.now(MOSCOW_TZ)
    print(f"🔄 {now.strftime('%H:%M:%S')}", flush=True)

    updates = get_updates()
    if updates:
        for u in updates:
            state["last_update_id"] = u.get("update_id", state.get("last_update_id", 0))
            msg = u.get("message") or {}
            text = (msg.get("text") or "").strip().lower()
            if text.startswith("/stats"):
                send_telegram(build_stats_short())
        save_json(STATE_FILE, state)

    check_results()

    matches = get_today_matches()
    print(f"   📋 Матчей в лигах: {len(matches)}", flush=True)

    if matches:
        history_events = ruscore_get_history()
        if history_events:
            history = build_team_history(history_events)
            h2h_index = build_h2h_index(history_events)
            name_index = build_name_index(history_events)

            new_preds = 0
            for m in matches:
                gid = m["gid"]
                if gid in predictions:
                    continue

                analysis = analyze(m["team1"], m["team2"], history, h2h_index, name_index)
                if not analysis:
                    continue

                bet = decide_bet(analysis)
                if not bet:
                    continue

                odds = None
                try:
                    odds = win_get_odds_for_bet(
                        m["team1"], m["team2"],
                        bet["bet_type"], bet.get("line")
                    )
                except Exception as e:
                    print(f"   ⚠️ 1win odds: {e}", flush=True)

                predictions[gid] = {
                    "gid": gid,
                    "league": m["league"],
                    "match": m["match"],
                    "team1": m["team1"],
                    "team2": m["team2"],
                    "start_str": m["start_str"],
                    "start_dt_dt": m["start_dt"].isoformat(),
                    "send_min_ts": (m["start_dt"] - timedelta(minutes=SEND_BEFORE_MAX)).timestamp(),
                    "send_max_ts": (m["start_dt"] - timedelta(minutes=SEND_BEFORE_MIN)).timestamp(),
                    "lookup_key": f"{ruscore_normalize_team(m['team1'])}|{ruscore_normalize_team(m['team2'])}",
                    "bet_type": bet["bet_type"],
                    "line": bet.get("line"),
                    "confidence": bet["confidence"],
                    "period_pct": analysis["period_pct"],
                    "expected_total": analysis["expected_total"],
                    "h2h_matches": analysis["h2h_matches"],
                    "base_text": format_prediction(m, analysis, bet),
                    "message_id": None,
                    "odds": odds,
                }
                new_preds += 1

                if bet["bet_type"] == "period":
                    bet_desc = f"Гол в каждом периоде {bet['confidence']:.0f}%"
                else:
                    bet_desc = f"ТБ {asian_line(bet['line'])} азиатский ({bet['confidence']:.0f}%)"
                odds_str = f" | кэф {odds}" if odds else ""
                print(f"   📝 {m['match']} | {bet_desc}{odds_str}", flush=True)

            if new_preds:
                save_json(PREDICTIONS_FILE, predictions)

    now_ts = time.time()
    sent_now = 0
    for gid, p in list(predictions.items()):
        if p.get("message_id"):
            continue
        if p["send_min_ts"] <= now_ts <= p["send_max_ts"]:
            msg_id = send_telegram(p["base_text"])
            if msg_id:
                p["message_id"] = msg_id
                sent_now += 1
                print(f"   📤 {p['match']}", flush=True)
                time.sleep(1)
        elif now_ts > p["send_max_ts"]:
            predictions.pop(gid, None)

    if sent_now:
        save_json(PREDICTIONS_FILE, predictions)

    print(f"   ✅ активных: {len(predictions)}, отправлено: {sent_now}", flush=True)

# =====================================================================
# ВЕБ-ПАНЕЛЬ (Flask)
# =====================================================================
web_app = Flask(__name__)

STATS_FILE_WEB = "stats.json"
START_BANK = 100_000
FLAT_BET = 1000

ESTIMATED_ODDS = {
    "total": 1.90,
    "period": 1.75,
}


def _load_stats_web():
    try:
        with open(STATS_FILE_WEB, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _calc_metrics(stats_web):
    total = len(stats_web)
    if total == 0:
        return {
            "total": 0, "wins": 0, "loses": 0, "refunds": 0,
            "winrate": 0, "roi": 0, "profit": 0, "bank": START_BANK,
        }

    wins    = sum(1 for s in stats_web if s.get("outcome") == "win")
    loses   = sum(1 for s in stats_web if s.get("outcome") == "lose")
    refunds = sum(1 for s in stats_web if s.get("outcome") == "refund")

    profit = 0
    for s in stats_web:
        odds = s.get("odds") or ESTIMATED_ODDS.get(s.get("bet_type"), 1.90)
        oc = s.get("outcome")
        if oc == "win":
            profit += FLAT_BET * (odds - 1)
        elif oc == "lose":
            profit -= FLAT_BET

    total_staked = FLAT_BET * total
    roi = (profit / total_staked * 100) if total_staked else 0

    return {
        "total": total,
        "wins": wins,
        "loses": loses,
        "refunds": refunds,
        "winrate": round(wins / total * 100, 1) if total else 0,
        "roi": round(roi, 1),
        "profit": round(profit),
        "bank": round(START_BANK + profit),
    }


def _by_league(stats_web):
    result = {}
    for s in stats_web:
        result.setdefault(s.get("league", "?"), []).append(s)

    out = []
    for league, arr in result.items():
        total = len(arr)
        wins = sum(1 for s in arr if s.get("outcome") == "win")
        profit = 0
        for s in arr:
            odds = s.get("odds") or ESTIMATED_ODDS.get(s.get("bet_type"), 1.90)
            if s.get("outcome") == "win":
                profit += FLAT_BET * (odds - 1)
            elif s.get("outcome") == "lose":
                profit -= FLAT_BET
        out.append({
            "league": league,
            "total": total,
            "wins": wins,
            "winrate": round(wins / total * 100, 1) if total else 0,
            "profit": round(profit),
            "roi": round(profit / (FLAT_BET * total) * 100, 1) if total else 0,
        })
    out.sort(key=lambda x: x["total"], reverse=True)
    return out


def _by_bet_type(stats_web):
    result = {}
    for s in stats_web:
        result.setdefault(s.get("bet_type", "?"), []).append(s)

    out = []
    for bt, arr in result.items():
        total = len(arr)
        wins = sum(1 for s in arr if s.get("outcome") == "win")
        name = "Гол в каждом периоде" if bt == "period" else "Тотал (азиат)"
        out.append({
            "bet_type": bt,
            "name": name,
            "total": total,
            "wins": wins,
            "winrate": round(wins / total * 100, 1) if total else 0,
        })
    return out


def _by_confidence(stats_web):
    out = []
    for lo, hi in [(60, 70), (70, 80), (80, 90), (90, 101)]:
        arr = [s for s in stats_web if lo <= s.get("confidence", 0) < hi]
        if not arr:
            continue
        total = len(arr)
        wins = sum(1 for s in arr if s.get("outcome") == "win")
        out.append({
            "range": f"{lo}-{hi-1}%",
            "total": total,
            "wins": wins,
            "winrate": round(wins / total * 100, 1) if total else 0,
        })
    return out


def _daily_series(stats_web):
    by_date = {}
    for s in stats_web:
        date = (s.get("date") or "")[:10]
        if date:
            by_date.setdefault(date, []).append(s)

    out = []
    cum = 0
    for date in sorted(by_date.keys()):
        profit = 0
        for s in by_date[date]:
            odds = s.get("odds") or ESTIMATED_ODDS.get(s.get("bet_type"), 1.90)
            if s.get("outcome") == "win":
                profit += FLAT_BET * (odds - 1)
            elif s.get("outcome") == "lose":
                profit -= FLAT_BET
        cum += profit
        out.append({
            "date": date,
            "profit": round(profit),
            "cumulative": round(cum),
        })
    return out


@web_app.route("/")
def web_index():
    return render_template("index.html")


@web_app.route("/api/stats")
def web_api_stats():
    stats_web = _load_stats_web()
    return jsonify({
        "metrics": _calc_metrics(stats_web),
        "by_league": _by_league(stats_web),
        "by_bet_type": _by_bet_type(stats_web),
        "by_confidence": _by_confidence(stats_web),
        "daily": _daily_series(stats_web),
        "recent": stats_web[-30:][::-1],
        "config": {"flat_bet": FLAT_BET, "start_bank": START_BANK},
    })


def run_web_panel():
    port = int(os.getenv("PORT", 3000))
    web_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# =====================================================================
# MAIN
# =====================================================================
def main():
    print("🚀 ХОККЕЙ ПРЕМАТЧ-БОТ ЗАПУЩЕН", flush=True)
    print(f"🏒 Лиг: {len(LEAGUE_KEYWORDS)}", flush=True)
    print(f"📊 История: {HISTORY_COUNT} матчей + {H2H_COUNT} личных ({RUSCORE_HISTORY_DAYS} дней)", flush=True)
    print(f"🎯 Порог 'гол в каждом периоде': {PERIOD_THRESHOLD*100:.0f}%", flush=True)
    print(f"⏰ Отправка: за {SEND_BEFORE_MIN}–{SEND_BEFORE_MAX} мин до старта", flush=True)
    print(f"🏁 Проверка: через {CHECK_AFTER_HOURS} ч после старта", flush=True)
    print(f"🌙 Ночью: просыпается под NHL", flush=True)
    print(f"💰 Кэфы: с 1win", flush=True)
    print("=" * 60, flush=True)

    # Запускаем веб-панель в отдельном потоке
    try:
        threading.Thread(target=run_web_panel, daemon=True).start()
        print(f"🌐 Веб-панель: http://0.0.0.0:{os.getenv('PORT', 3000)}", flush=True)
    except Exception as e:
        print(f"⚠️ Веб-панель не запустилась: {e}", flush=True)

    if predictions:
        save_json(PREDICTIONS_FILE, predictions)
    if stats:
        save_json(STATS_FILE, stats)
    if state:
        save_json(STATE_FILE, state)
    if stats:
        save_report()

    while True:
        try:
            now_str = datetime.now(MOSCOW_TZ).strftime('%H:%M')
            if not is_active_time():
                print(f"😴 Ночь ({now_str})", flush=True)
                time.sleep(300)
                continue

            monitor()
            time.sleep(UPDATE_INTERVAL)

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