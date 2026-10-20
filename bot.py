# -*- coding: utf-8 -*-
"""Основной цикл хоккейного Telegram-бота OLD."""
import os, sys, re, json, hashlib, requests, time, threading
from datetime import datetime, timedelta
import pytz
from config import *
from web_server import run_web_panel

API = f"https://api.telegram.org/bot{BOT_TOKEN}"
print(f"✅ CHAT_ID: {CHAT_ID}", flush=True)
print(f"✅ Статистика: {STATS_SITE_URL}", flush=True)
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

# ✅ НЕ обнуляем: stats.json, predictions.json, state.json загружаются из существующих
predictions = load_json(PREDICTIONS_FILE, {})
if not isinstance(predictions, dict):
    predictions = {}

stats = load_json(STATS_FILE, [])
if not isinstance(stats, list):
    stats = []

state = load_json(STATE_FILE, {"last_update_id": 0})
if not isinstance(state, dict):
    state = {"last_update_id": 0}
if "last_update_id" not in state:
    state["last_update_id"] = 0

ruscore_history_cache = {"ts": 0, "events": []}
xbet_odds_cache = {"ts": 0, "games": []}

# =====================================================================
# ОЦЕНОЧНЫЙ КЭФ (рандом 1.5–1.7, стабильный по gid)
# =====================================================================
def get_estimated_odds(gid, bet_type=None):
    """Рандом 1.5–1.7, стабильный по gid (не меняется при обновлении)."""
    try:
        h = int(hashlib.md5(str(gid).encode()).hexdigest(), 16)
    except Exception:
        h = 12345
    r = (h % 1000) / 1000.0
    return round(1.5 + r * 0.2, 2)

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
# ПРОВЕРКА ТОТАЛА (обычный, без азиатского)
# =====================================================================
def check_total(total_goals, line):
    """Обычный тотал (4.5, 5.5, 6.5). Без возврата."""
    if line is None:
        return None
    if total_goals > line:
        return "win"
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
# 1XBET — СБОР КЭФОВ
# =====================================================================
def xbet_get_games():
    now_ts = time.time()
    if xbet_odds_cache["games"] and now_ts - xbet_odds_cache["ts"] < XBET_CACHE_SEC:
        return xbet_odds_cache["games"]

    all_games = []
    for league_name, lid in XBET_LEAGUE_IDS.items():
        params = dict(XBET_PARAMS)
        params["selectedMs"] = f"2.2.{lid}"
        try:
            r = requests.get(XBET_URL, params=params, headers=XBET_HEADERS, timeout=15)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    all_games.extend(data)
                    print(f"   💰 1xBet {league_name}: {len(data)} матчей", flush=True)
            else:
                print(f"   ⚠️ 1xBet {league_name}: {r.status_code}", flush=True)
        except Exception as e:
            print(f"   ⚠️ 1xBet {league_name}: {e}", flush=True)

    xbet_odds_cache["ts"] = now_ts
    xbet_odds_cache["games"] = all_games
    return all_games


def xbet_extract_total(game, line):
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


def xbet_find_game_by_teams(team1, team2):
    games = xbet_get_games()
    if not games:
        return None

    n1 = ruscore_normalize_team(team1)
    n2 = ruscore_normalize_team(team2)
    if not n1 or not n2:
        return None

    for g in games:
        if not isinstance(g, dict):
            continue
        o1_data = g.get("opponent1") or {}
        o2_data = g.get("opponent2") or {}
        candidates1 = [o1_data.get("fullName", ""), o1_data.get("fullNameEng", "")]
        candidates2 = [o2_data.get("fullName", ""), o2_data.get("fullNameEng", "")]
        for o1 in candidates1:
            for o2 in candidates2:
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


def xbet_get_odds_for_bet(match_team1, match_team2, bet_type, line=None):
    game = xbet_find_game_by_teams(match_team1, match_team2)
    if not game:
        return None
    if bet_type == "total":
        return xbet_extract_total(game, line)
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

# Анализ матча и выбор прогноза находятся в model.py
from model import analyze, decide_bet, train_from_events

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
        lines.append(f"💡 <b>Ставка: ТБ {bet['line']}</b>")
        lines.append(f"🔥 Уверенность: <b>{bet['confidence']:.0f}%</b>")
        lines.append("")
        lines.append(f"📊 Средний тотал по последним матчам: <b>{analysis['expected_total']:.2f}</b>")
        lines.append(f"   • Запас над линией: <b>+{bet['margin']:.2f}</b>")

    lines.append("")
    lines.append(f'📊 <a href="{STATS_SITE_URL}">Статистика бота</a>')

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
        detail = f"⚽ <b>Голы по периодам:</b>\n{periods_line}\n   Всего голов: <b>{total_goals}</b>"
        tail = f"📈 Итог: {emoji} <b>{res}</b> (ТБ {line})"

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
        last_id = state.get("last_update_id", 0) if isinstance(state, dict) else 0
        r = requests.get(API + "/getUpdates",
                         params={"offset": last_id + 1, "timeout": 5},
                         timeout=15)
        if r.status_code != 200:
            return []
        data = r.json()
        if not isinstance(data, dict) or not data.get("ok"):
            return []
        result = data.get("result", [])
        if not isinstance(result, list):
            return []
        return result
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
            by_line.setdefault(s.get("line"), []).append(s)
    for line, arr in sorted(by_line.items(), key=lambda x: (x[0] or 0)):
        w = sum(1 for s in arr if s.get("outcome") == "win")
        lines.append(f"  ТБ {line}: {w}/{len(arr)} ({w/len(arr)*100:.0f}%)")
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
            outcome = check_total(total_goals, line)
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
        # ✅ line_asian убран — только реальная line

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
    if updates and isinstance(updates, list):
        for u in updates:
            if not isinstance(u, dict):
                continue
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
            train_from_events(history_events)
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
                    odds = xbet_get_odds_for_bet(
                        m["team1"], m["team2"],
                        bet["bet_type"], bet.get("line")
                    )
                except Exception as e:
                    print(f"   ⚠️ 1xBet odds: {e}", flush=True)

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
                    bet_desc = f"ТБ {bet['line']} ({bet['confidence']:.0f}%)"
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
    print(f"💰 Кэфы: с 1xBet", flush=True)
    print(f"📈 Тоталы: {TOTAL_LINES}", flush=True)
    print("=" * 60, flush=True)

    try:
        threading.Thread(target=run_web_panel, daemon=True).start()
        print(f"🌐 Веб-панель: http://0.0.0.0:{os.getenv('PORT', 3000)}", flush=True)
    except Exception as e:
        print(f"⚠️ Веб-панель не запустилась: {e}", flush=True)

    # ✅ НЕ обнуляем stats.json и state.json — загружаем существующие
    print(f"📊 Загружено: {len(stats)} записей статистики", flush=True)
    print(f"📋 Активных прогнозов: {len(predictions)}", flush=True)

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