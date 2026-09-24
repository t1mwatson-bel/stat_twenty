import os
import sys
import requests
import json
import time
from datetime import datetime, timedelta
from collections import deque
import pytz

# =====================================================================
# НАСТРОЙКИ
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
# ЛИГИ (топ хоккей)
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
# ПОРОГИ СТРАТЕГИЙ (хоккей)
# =====================================================================
S1_ATT_DIFF      = 40
S1_MIN_ODD       = 1.4

S2_POSSESSION    = 25
S2_MIN_ODD       = 1.4

S3_PENALTY_DIFF  = 4
S3_MIN_ODD       = 1.4

S4_COMBO_ATT     = 35
S4_COMBO_POSS    = 20
S4_MIN_ODD       = 1.4

# Дроп 1X2 (live)
DROP_PCT      = -10.0
DROP_WINDOW   = 180
DROP_ANTISPAM = 900

# Прематч-дроп
PREMATCH_INTERVAL    = 300
PREMATCH_ANTISPAM    = 1800
PREMATCH_DROP_PCT    = -10.0
PREMATCH_DROP_WINDOW = 180

# Общие
MAX_MINUTE        = 55
UPDATE_INTERVAL   = 60
ANTISPAM_SEC      = 900
GOAL_COOLDOWN_SEC = 300

SLEEP_HOUR_START = 1
SLEEP_HOUR_END   = 12

# =====================================================================
# НОВОЕ: настройки «после 1-го периода 0:0»
# =====================================================================
P1_ZERO_ZERO_TOTALS = [2.5, 3.5]     # какие тоталы проверяем в линии
P1_RESULT_CHECK_DELAY = 3 * 60 * 60  # через сколько секунд проверять результат (3ч)
# =====================================================================

# =====================================================================
# ЗАГОЛОВКИ
# =====================================================================
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 YaBrowser/26.8.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Content-Type": "application/json",
    "Referer": f"{BASE_URL}/ru/live/ice-hockey",
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

print("✅ Настройки загружены", flush=True)

# =====================================================================
# СОСТОЯНИЕ
# =====================================================================
sent_signals = {}
last_scores = {}
odds_history = {}
sent_drops = {}
p_game_cache = {}

prematch_odds_history = {}
sent_prematch_drops   = {}
prematch_updated_at   = 0

# ← НОВОЕ: состояние прогнозов «после 1-го периода 0:0»
p1_zero_zero_sent = {}   # game_id -> {message_id, total, base_text, sent_ts, checked}
# =====================================================================

# =====================================================================
# ВРЕМЯ
# =====================================================================
def is_active_time():
    h = datetime.now(MOSCOW_TZ).hour
    return not (SLEEP_HOUR_START <= h < SLEEP_HOUR_END)

# =====================================================================
# API
# =====================================================================
def get_live_games():
    url = f"{BASE_URL}/service-api/main-live-feed/v3/games1x2"
    params = {"cfView": 3, "count": 40, "fcountry": 1,
              "gr": 2336, "grMode": 4, "lng": "ru", "ref": 1,
              "selectedMs": "2.2"}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=15)
        if r.status_code != 200:
            return []
        data = r.json()
        if not isinstance(data, list):
            return []
        return [g for g in data if isinstance(g, dict) and (g.get("sport") or {}).get("id") == 2]
    except Exception as e:
        print(f"   ❌ Live: {e}", flush=True)
        return []

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

# ← НОВОЕ: получить конкретный матч по id (для проверки результата)
def get_game_by_id(game_id):
    """Пробуем найти матч в live, потом в прематче (историю)."""
    for g in get_live_games():
        if g.get("id") == game_id:
            return g
    for g in get_prematch_games():
        if g.get("id") == game_id:
            return g
    return None
# =====================================================================

# =====================================================================
# ПАРСИНГ
# =====================================================================
def parse_stats(game):
    stats = {}
    tablo = ((game.get("scores") or {}).get("tabloStats")) or {}
    for key, items in tablo.items():
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and item.get("name"):
                    stats[item["name"]] = item
    return stats

def sv(stats, name, side="s1"):
    d = stats.get(name, {})
    try:
        return float(d.get(side, 0) or 0)
    except (ValueError, TypeError):
        return 0.0

def get_1x2_odds(game):
    odds = {}
    for grp in (game.get("eventGroups") or []):
        if grp.get("groupId") != 1:
            continue
        for e in (grp.get("events") or []):
            if not isinstance(e, list) or not e:
                continue
            item = e[0]
            if not isinstance(item, dict):
                continue
            t = item.get("T")
            c = item.get("C")
            if t is None:
                t = item.get("type")
            if c is None:
                c = item.get("cf")
            if t == 1:   odds["П1"] = c
            elif t == 2: odds["X"] = c
            elif t == 3: odds["П2"] = c
        break
    return odds

def get_odd_total(game, total_goals):
    target = total_goals + 0.5
    for grp in (game.get("centralBlockEventGroups") or []):
        if grp.get("groupId") != 17:
            continue
        events = grp.get("events") or []
        if len(events) < 2:
            continue
        tb_list = events[0] if isinstance(events[0], list) else []
        for item in tb_list:
            if (isinstance(item, dict) and item.get("parameter") == target
                    and item.get("type") == 9):
                return item.get("cf")
    return None

# ← НОВОЕ: получить чистый тотал (без +0.5), т.е. ищем ТБ 2.5 / ТБ 3.5
def get_total_odds_raw(game, total_goals):
    """Возвращает кэф ТБ для конкретного тотала (2.5, 3.5 и т.п.)."""
    return get_odd_total(game, total_goals)
# =====================================================================

# =====================================================================
# ДРОП 1X2
# =====================================================================
def save_odds(gid, now_ts, odds):
    if gid not in odds_history:
        odds_history[gid] = deque(maxlen=30)
    odds_history[gid].append({"ts": now_ts, "odds": odds})

def check_drops(gid, now_ts):
    if gid not in odds_history or len(odds_history[gid]) < 2:
        return None
    cur = odds_history[gid][-1]["odds"]
    prev = None
    for h in reversed(list(odds_history[gid])[:-1]):
        if now_ts - h["ts"] >= DROP_WINDOW:
            prev = h
            break
    if prev is None:
        return None
    prev_odds = prev["odds"]
    res = []
    for key in ("П1", "X", "П2"):
        c = cur.get(key)
        p = prev_odds.get(key)
        if not c or not p:
            continue
        try:
            change = (float(c) - float(p)) / float(p) * 100
        except (ValueError, TypeError, ZeroDivisionError):
            continue
        if change <= DROP_PCT:
            res.append({
                "market": key, "from": p, "to": c,
                "change_pct": round(change, 1),
                "window": now_ts - prev["ts"],
            })
    if not res:
        return None
    res.sort(key=lambda x: x["change_pct"])
    return res

def drop_to_bet(market, p):
    if market == "П1":
        return ("ИТ1 Б 0.5 (хозяева забьют)", "Дроп П1 → хозяева побеждают → забьют")
    if market == "П2":
        return ("ИТ2 Б 0.5 (гости забьют)", "Дроп П2 → гости побеждают → забьют")
    if market == "X":
        return ("Ничья в основное время", "Дроп X → ждут ничью")
    return (market, "—")

def format_drop_signal(p, drops):
    lines = [
        "📉 <b>ДРОП 1X2 (ХОККЕЙ)</b>",
        p["league"],
        f"🏒 <b>{p['match']}</b>",
        f"📊 Счёт: <b>{p['score']}</b> | ⏱ {p['period_str']}",
        "",
    ]
    for d in drops[:3]:
        lines.append(f"🔻 <b>{d['market']}</b>: {d['from']} → {d['to']} "
                     f"({d['change_pct']}% за {d['window']}с)")
    best = drops[0]
    bet, reason = drop_to_bet(best["market"], p)
    lines.append("")
    lines.append(f"💡 <b>Ставка: {bet}</b>")
    lines.append(f"<i>{reason}</i>")
    lines.append(f"📌 Кэф 1X2 сейчас: {best['to']}")
    return "\n".join(lines)

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

# =====================================================================
# ПАРСИНГ LIVE-МАТЧА
# =====================================================================
def parse_game(game):
    if not isinstance(game, dict):
        return None
    if (game.get("sport") or {}).get("id") != 2:
        return None
    liga_id = (game.get("liga") or {}).get("id")
    if liga_id not in LEAGUE_IDS:
        return None

    scores = game.get("scores") or {}
    if scores.get("isBreak"):
        return None
    if scores.get("timer", {}).get("timeDirection") == -1:
        return None

    time_sec = (scores.get("timer") or {}).get("timeSec", 0)
    minute = time_sec // 60
    score = scores.get("fullScore", "0-0")
    try:
        s1, s2 = map(int, score.split("-"))
    except ValueError:
        s1, s2 = 0, 0

    current_period = scores.get("currentPeriod", 1)
    period_str = f"{current_period}-й период, {minute}'"

    # ← НОВОЕ: определяем, закончился ли 1-й период
    period_ended = False
    try:
        # если currentPeriod >= 2, значит 1-й уже закончился
        if current_period is not None and int(current_period) >= 2:
            period_ended = True
    except (ValueError, TypeError):
        pass

    stats = parse_stats(game)
    att1 = int(sv(stats, "Атаки", "s1"))
    att2 = int(sv(stats, "Атаки", "s2"))
    pen1 = int(sv(stats, "Штрафы", "s1"))
    pen2 = int(sv(stats, "Штрафы", "s2"))
    poss1 = int(sv(stats, "Владение %", "s1"))
    poss2 = int(sv(stats, "Владение %", "s2"))
    maj1 = int(sv(stats, "Голы в большинстве", "s1"))
    maj2 = int(sv(stats, "Голы в большинстве", "s2"))

    o1 = (game.get("opponent1") or {}).get("fullName", "?")
    o2 = (game.get("opponent2") or {}).get("fullName", "?")

    return {
        "game_id": game.get("id"),
        "liga_id": liga_id,
        "league": LEAGUE_IDS.get(liga_id, ""),
        "team1": o1, "team2": o2,
        "match": f"{o1} — {o2}",
        "minute": minute, "score": score,
        "s1": s1, "s2": s2,
        "period": current_period,
        "period_str": period_str,
        "period_ended": period_ended,   # ← НОВОЕ
        "att1": att1, "att2": att2,
        "pen1": pen1, "pen2": pen2,
        "poss1": poss1, "poss2": poss2,
        "maj1": maj1, "maj2": maj2,
    }

# =====================================================================
# ФИЛЬТРЫ
# =====================================================================
def common_ok(p, gid, now_ts):
    if p["minute"] > MAX_MINUTE:
        return False
    prev = last_scores.get(gid, {})
    if prev.get("score") and prev["score"] != p["score"]:
        last_scores[gid] = {"score": p["score"], "changed_at": now_ts}
        return False
    if prev.get("changed_at"):
        if now_ts - prev["changed_at"] < GOAL_COOLDOWN_SEC:
            return False
    return True

# =====================================================================
# СТРАТЕГИИ ХОККЕЯ
# =====================================================================
def strategy_attacks(p):
    att_diff = abs(p["att1"] - p["att2"])
    if att_diff < S1_ATT_DIFF:
        return None
    side = "home" if p["att1"] > p["att2"] else "away"
    dominant = p["team1"] if side == "home" else p["team2"]
    return {"strategy": "Атаки", "emoji": "⚔️", "dominant": dominant,
            "key": f"атаки {att_diff}", "min_odd": S1_MIN_ODD,
            "side": side}

def strategy_possession(p):
    poss_diff = abs(p["poss1"] - p["poss2"])
    if poss_diff < S2_POSSESSION:
        return None
    side = "home" if p["poss1"] > p["poss2"] else "away"
    dominant = p["team1"] if side == "home" else p["team2"]
    return {"strategy": "Владение", "emoji": "🎯", "dominant": dominant,
            "key": f"владение {poss_diff}%", "min_odd": S2_MIN_ODD,
            "side": side}

def strategy_penalties(p):
    pen_diff = abs(p["pen1"] - p["pen2"])
    if pen_diff < S3_PENALTY_DIFF:
        return None
    side = "away" if p["pen1"] > p["pen2"] else "home"
    dominant = p["team2"] if side == "home" else p["team1"]
    return {"strategy": "Штрафы", "emoji": "🚨", "dominant": dominant,
            "key": f"штрафы {pen_diff}", "min_odd": S3_MIN_ODD,
            "side": side}

def strategy_combo(p):
    att_diff = abs(p["att1"] - p["att2"])
    poss_diff = abs(p["poss1"] - p["poss2"])
    if att_diff < S4_COMBO_ATT or poss_diff < S4_COMBO_POSS:
        return None
    if p["att1"] > p["att2"] and p["poss1"] > p["poss2"]:
        side = "home"
        dominant = p["team1"]
    elif p["att2"] > p["att1"] and p["poss2"] > p["poss1"]:
        side = "away"
        dominant = p["team2"]
    else:
        return None
    return {"strategy": "Комбо (атаки+владение)", "emoji": "💡", "dominant": dominant,
            "key": f"атаки {att_diff}, владение {poss_diff}%",
            "min_odd": S4_MIN_ODD, "side": side}

# =====================================================================
# ФОРМАТЫ
# =====================================================================
def format_signal(p, strategy, odd):
    side = strategy["side"]
    at_dom = p["att1"] if side == "home" else p["att2"]
    at_opp = p["att2"] if side == "home" else p["att1"]
    po_dom = p["poss1"] if side == "home" else p["poss2"]
    po_opp = p["poss2"] if side == "home" else p["poss1"]
    pe_dom = p["pen1"] if side == "home" else p["pen2"]
    pe_opp = p["pen2"] if side == "home" else p["pen1"]

    total = p["s1"] + p["s2"]
    tb1 = total + 0.5
    tb2 = total + 1.5
    odd_str = f"💰 Кэф ТБ {tb1}: <b>{odd}</b>" if odd else "💰 Кэф: —"

    return (
        f"{strategy['emoji']} <b>СИГНАЛ: {strategy['strategy']}</b>\n"
        f"{p['league']}\n"
        f"🏒 <b>{p['match']}</b>\n"
        f"📊 Счёт: <b>{p['score']}</b> | ⏱ {p['period_str']}\n"
        f"⚔️ Атаки: {p['att1']} — {p['att2']}\n"
        f"🎯 Владение: {p['poss1']}% — {p['poss2']}%\n"
        f"🚨 Штрафы: {p['pen1']} — {p['pen2']}\n"
        f"👉 Давит: <b>{strategy['dominant']}</b>\n"
        f"🔑 {strategy['key']}\n"
        f"{odd_str}\n"
        f"💡 <b>Ожидается гол — ТБ {tb1} / ТБ {tb2}</b>"
    )

def format_signal_multi(p, strategies, odd):
    total = p["s1"] + p["s2"]
    tb1 = total + 0.5
    tb2 = total + 1.5
    odd_str = f"💰 Кэф ТБ {tb1}: <b>{odd}</b>" if odd else "💰 Кэф: —"

    lines = [
        f"🎯 <b>СИГНАЛ ХОККЕЙ</b>",
        f"{p['league']}",
        f"🏒 <b>{p['match']}</b>",
        f"📊 Счёт: <b>{p['score']}</b> | ⏱ {p['period_str']}",
        f"⚔️ Атаки: {p['att1']} — {p['att2']}",
        f"🎯 Владение: {p['poss1']}% — {p['poss2']}%",
        f"🚨 Штрафы: {p['pen1']} — {p['pen2']}",
    ]

    if len(strategies) > 1:
        lines.append("")
        lines.append("✅ <b>Подтвердилось:</b>")
        for s in strategies:
            lines.append(f"   • {s}")

    lines.append("")
    lines.append(f"{odd_str}")
    lines.append(f"💡 <b>Ожидается гол — ТБ {tb1} / ТБ {tb2}</b>")

    return "\n".join(lines)

# ← НОВОЕ: формат сообщения для прогноза «после 1-го периода 0:0»
def format_p1_zero_zero_signal(p, total_line, odd):
    return (
        f"🥅 <b>ПРОГНОЗ НА МАТЧ (после 1-го периода 0:0)</b>\n"
        f"{p['league']}\n"
        f"🏒 <b>{p['match']}</b>\n"
        f"📊 1-й период: <b>0:0</b> | ⏱ {p['period_str']}\n"
        f"⚔️ Атаки: {p['att1']} — {p['att2']}\n"
        f"🎯 Владение: {p['poss1']}% — {p['poss2']}%\n"
        f"🚨 Штрафы: {p['pen1']} — {p['pen2']}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💡 <b>Ставка на матч: ТБ {total_line} </b>\n"
        f"💰 Кэф: <b>{odd}</b>\n"
        f"⏳ Проверка результата после матча…"
    )

def format_p1_zero_zero_result(p, total_line, final_score, won):
    emoji = "✅" if won else "❌"
    res = "ЗАШЛА" if won else "НЕ ЗАШЛА"
    return (
        f"{emoji} <b>РЕЗУЛЬТАТ: {res}</b>\n"
        f"{p['league']}\n"
        f"🏒 <b>{p['match']}</b>\n"
        f"📊 1-й период: <b>0:0</b>\n"
        f"🏁 Итоговый счёт: <b>{final_score}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💡 Ставка была: <b>ТБ {total_line}</b>\n"
        f"📈 Итог: {emoji} <b>{res}</b>"
    )
# =====================================================================

# =====================================================================
# ОТПРАВКА СИГНАЛА (стратегии)
# =====================================================================
def try_send_signal(p, strategy, now_ts):
    gid = p["game_id"]
    key = str(gid)

    odd = get_odd_total(p_game_cache.get(gid, {}), p["s1"] + p["s2"])

    if odd is not None and odd < strategy["min_odd"]:
        return False

    existing = sent_signals.get(key)

    if not existing:
        text = format_signal(p, strategy, odd)
        msg_id = send_telegram(text)
        if not msg_id:
            return False
        sent_signals[key] = {
            "ts": now_ts,
            "message_id": msg_id,
            "base_text": text,
            "strategies": [f"{strategy['emoji']} {strategy['strategy']}"],
        }
        print(f"    📤 {p['match']} | {strategy['emoji']} {strategy['strategy']} | кэф {odd}", flush=True)
        time.sleep(1)
        return True

    full_name = f"{strategy['emoji']} {strategy['strategy']}"

    if full_name in existing["strategies"]:
        return False

    if (now_ts - existing["ts"]) > ANTISPAM_SEC:
        del sent_signals[key]
        return try_send_signal(p, strategy, now_ts)

    existing["strategies"].append(full_name)
    existing["ts"] = now_ts

    new_text = format_signal_multi(p, existing["strategies"], odd)
    edit_telegram(existing["message_id"], new_text)
    existing["base_text"] = new_text
    print(f"    ✏️ {p['match']} | + {full_name}", flush=True)
    time.sleep(1)
    return True

# =====================================================================
# НОВОЕ: ЛОГИКА ПРОГНОЗА ПОСЛЕ 1-ГО ПЕРИОДА 0:0
# =====================================================================
def try_p1_zero_zero_signal(p, game, now_ts):
    """
    Если 1-й период закончился 0:0 → даём прогноз на весь матч ТБ 2.5 / ТБ 3.5.
    Проверяем, есть ли в линии соответствующие тоталы.
    """
    gid = p["game_id"]
    if gid in p1_zero_zero_sent:
        return False  # уже отправляли по этому матчу

    # Условие: 1-й период закончился, счёт 0:0 (именно после 1-го периода)
    if not p.get("period_ended"):
        return False
    if p["s1"] != 0 or p["s2"] != 0:
        return False
    # Период должен быть >= 2 (значит 1-й закончился) и счёт 0:0
    # (если уже 2-й период, а счёт всё ещё 0:0 — ок; но если во 2-м уже забили — не подходит)
    if p["period"] is None or int(p["period"]) < 2:
        return False

    # Ищем доступные тоталы в линии
    chosen_total = None
    chosen_odd = None
    for t in P1_ZERO_ZERO_TOTALS:
        odd = get_total_odds_raw(game, t)
        if odd is not None:
            chosen_total = t
            chosen_odd = odd
            break

    if chosen_total is None:
        # нет в линии ни 2.5, ни 3.5 — пропускаем
        return False

    text = format_p1_zero_zero_signal(p, chosen_total, chosen_odd)
    msg_id = send_telegram(text)
    if not msg_id:
        return False

    p1_zero_zero_sent[gid] = {
        "message_id": msg_id,
        "total_line": chosen_total,
        "base_text": text,
        "sent_ts": now_ts,
        "checked": False,
    }
    print(f"    🥅 P1 0:0 → {p['match']} | ТБ {chosen_total} @ {chosen_odd}", flush=True)
    time.sleep(1)
    return True


def check_p1_zero_zero_results():
    """
    Проверка результатов прогнозов «после 1-го периода 0:0».
    Проходим по всем отправленным, у которых ещё не проверено и прошло время.
    """
    now_ts = int(time.time())
    to_delete = []

    for gid, info in list(p1_zero_zero_sent.items()):
        if info.get("checked"):
            continue
        if now_ts - info["sent_ts"] < P1_RESULT_CHECK_DELAY:
            continue

        # Ищем матч
        game = get_game_by_id(gid)
        if not game:
            # матч уже не в линии — оставляем на следующий круг
            continue

        scores = game.get("scores") or {}
        full_score = scores.get("fullScore", "")
        if not full_score or "-" not in full_score:
            continue

        try:
            fs1, fs2 = map(int, full_score.split("-"))
        except ValueError:
            continue

        # Проверяем, закончился ли матч
        # Признак: есть period == 3 и время идёт к концу, либо статус finished
        is_finished = False
        cur_period = scores.get("currentPeriod")
        try:
            if cur_period is not None and int(cur_period) >= 3:
                # если 3-й период и время близко к 60 мин или isFinished
                timer = scores.get("timer") or {}
                time_sec = timer.get("timeSec", 0) or 0
                if time_sec >= 20 * 60:  # 20 минут в 3-м периоде = конец
                    is_finished = True
        except (ValueError, TypeError):
            pass

        # Дополнительный признак: статус
        status = (scores.get("status") or "").lower()
        if status in ("finished", "ended", "final"):
            is_finished = True

        if not is_finished:
            # матч ещё идёт — не проверяем
            # но если прошло слишком много времени (например 5 часов), всё равно проверим
            if now_ts - info["sent_ts"] < 5 * 60 * 60:
                continue

        total_goals = fs1 + fs2
        total_line = info["total_line"]
        won = total_goals > total_line

        final_score = f"{fs1}-{fs2}"
        new_text = format_p1_zero_zero_result(
            {"league": LEAGUE_IDS.get((game.get("liga") or {}).get("id"), ""),
             "match": f"{(game.get('opponent1') or {}).get('fullName','?')} — "
                      f"{(game.get('opponent2') or {}).get('fullName','?')}"},
            total_line, final_score, won
        )

        edit_telegram(info["message_id"], new_text)
        info["checked"] = True
        print(f"    🏁 P1 0:0 результат: {final_score} | ТБ {total_line} → "
              f"{'✅' if won else '❌'}", flush=True)
        to_delete.append(gid)

    for gid in to_delete:
        p1_zero_zero_sent.pop(gid, None)
# =====================================================================

# =====================================================================
# ПРЕМАТЧ-ДРОП
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
    }

def save_prematch_odds(gid, now_ts, odds):
    if gid not in prematch_odds_history:
        prematch_odds_history[gid] = deque(maxlen=30)
    prematch_odds_history[gid].append({"ts": now_ts, "odds": odds})

def check_prematch_drops(gid, now_ts):
    if gid not in prematch_odds_history or len(prematch_odds_history[gid]) < 2:
        return None
    cur = prematch_odds_history[gid][-1]["odds"]
    prev = None
    for h in reversed(list(prematch_odds_history[gid])[:-1]):
        if now_ts - h["ts"] >= PREMATCH_DROP_WINDOW:
            prev = h
            break
    if prev is None:
        return None
    prev_odds = prev["odds"]
    res = []
    for key in ("П1", "X", "П2"):
        c = cur.get(key)
        p = prev_odds.get(key)
        if not c or not p:
            continue
        try:
            change = (float(c) - float(p)) / float(p) * 100
        except (ValueError, TypeError, ZeroDivisionError):
            continue
        if change <= PREMATCH_DROP_PCT:
            res.append({
                "market": key, "from": p, "to": c,
                "change_pct": round(change, 1),
                "window": now_ts - prev["ts"],
            })
    if not res:
        return None
    res.sort(key=lambda x: x["change_pct"])
    return res

def format_prematch_signal(p, drops):
    lines = [
        "🕐 <b>ДРОП ПРЕМАТЧ (ХОККЕЙ)</b>",
        p["league"],
        f"🏒 <b>{p['match']}</b>",
        f"🕐 Старт: <b>{p['start_str']}</b>",
        "",
    ]
    for d in drops[:3]:
        lines.append(f"🔻 <b>{d['market']}</b>: {d['from']} → {d['to']} "
                     f"({d['change_pct']}% за {d['window']}с)")
    best = drops[0]
    bet, reason = drop_to_bet(best["market"], p)
    lines.append("")
    lines.append(f"💡 <b>Ставка: {bet}</b>")
    lines.append(f"<i>{reason}</i>")
    lines.append(f"📌 Кэф 1X2 сейчас: {best['to']}")
    return "\n".join(lines)

def monitor_prematch():
    global prematch_updated_at, sent_prematch_drops
    now = int(time.time())
    if now - prematch_updated_at < PREMATCH_INTERVAL:
        return
    prematch_updated_at = now

    print(f"🕐 Прематч: {datetime.now(MOSCOW_TZ).strftime('%H:%M:%S')}", flush=True)
    games = get_prematch_games()
    if not games:
        print("   ⚠️ Прематч пуст", flush=True)
        return

    total = 0
    total_drops = 0
    for game in games:
        p = parse_prematch_game(game)
        if not p:
            continue
        gid = p["game_id"]
        odds_1x2 = get_1x2_odds(game)
        if not odds_1x2:
            continue
        save_prematch_odds(gid, now, odds_1x2)
        total += 1
        drops = check_prematch_drops(gid, now)
        if not drops:
            continue
        prev = sent_prematch_drops.get(gid)
        if prev and (now - prev) < PREMATCH_ANTISPAM:
            continue
        text = format_prematch_signal(p, drops)
        if send_telegram(text):
            sent_prematch_drops[gid] = now
            total_drops += 1
            print(f"    🕐 {p['match']} | {drops[0]['market']} {drops[0]['change_pct']}%", flush=True)
            time.sleep(1)

    print(f"✅ Прематч: {total} матчей, {total_drops} дропов", flush=True)
    sent_prematch_drops = {k: v for k, v in sent_prematch_drops.items() if now - v < 7200}

# =====================================================================
# ОСНОВНОЙ ЦИКЛ LIVE
# =====================================================================
def monitor():
    global sent_signals, sent_drops, p_game_cache
    print(f"🔄 {datetime.now(MOSCOW_TZ).strftime('%H:%M:%S')}", flush=True)
    games = get_live_games()
    if not games:
        print("   0 матчей", flush=True)
        return

    now_ts = int(time.time())
    p_game_cache = {g.get("id"): g for g in games if isinstance(g, dict)}

    total_our = 0
    total_signals = 0
    total_drops = 0
    total_p1 = 0

    by_league = {}
    for game in games:
        lid = (game.get("liga") or {}).get("id")
        if lid in LEAGUE_IDS:
            by_league[lid] = by_league.get(lid, 0) + 1
    for lid, cnt in by_league.items():
        print(f"  🏒 {LEAGUE_IDS[lid]}: {cnt}", flush=True)

    for game in games:
        p = parse_game(game)
        if not p:
            continue
        gid = p["game_id"]

        # ← НОВОЕ: проверка «после 1-го периода 0:0»
        if try_p1_zero_zero_signal(p, game, now_ts):
            total_p1 += 1

        # ДРОП 1X2
        odds_1x2 = get_1x2_odds(game)
        if odds_1x2:
            save_odds(gid, now_ts, odds_1x2)
            drops = check_drops(gid, now_ts)
            if drops:
                prev_drop = sent_drops.get(gid)
                if not (prev_drop and (now_ts - prev_drop) < DROP_ANTISPAM):
                    drop_text = format_drop_signal(p, drops)
                    if send_telegram(drop_text):
                        sent_drops[gid] = now_ts
                        total_drops += 1
                        print(f"    📉 ДРОП {p['match']} | {drops[0]['market']} {drops[0]['change_pct']}%", flush=True)
                        time.sleep(1)

        # СТРАТЕГИИ
        if not common_ok(p, gid, now_ts):
            continue
        total_our += 1
        for strategy_func in (strategy_attacks, strategy_possession,
                              strategy_penalties, strategy_combo):
            strat = strategy_func(p)
            if not strat:
                continue
            if try_send_signal(p, strat, now_ts):
                total_signals += 1
                break

    # ← НОВОЕ: проверяем результаты прогнозов P1 0:0
    check_p1_zero_zero_results()

    print(f"✅ {total_our} наших, {total_signals} сигналов, "
          f"{total_drops} дропов, {total_p1} P1-0:0", flush=True)
    sent_signals = {k: v for k, v in sent_signals.items() if now_ts - v["ts"] < 3600}
    sent_drops = {k: v for k, v in sent_drops.items() if now_ts - v < 3600}

# =====================================================================
# MAIN
# =====================================================================
def main():
    print("🚀 ХОККЕЙ-БОТ ЗАПУЩЕН", flush=True)
    print(f"📋 Лиг: {len(LEAGUE_IDS)}", flush=True)
    print(f"🎯 Стратегии: Атаки, Владение, Штрафы, Комбо", flush=True)
    print(f"📉 Дроп 1X2 (live): {DROP_PCT}% за {DROP_WINDOW}с", flush=True)
    print(f"🕐 Дроп 1X2 (прематч): {PREMATCH_DROP_PCT}% за {PREMATCH_DROP_WINDOW}с", flush=True)
    print(f"🥅 P1 0:0 → ТБ {P1_ZERO_ZERO_TOTALS}", flush=True)
    print("=" * 60, flush=True)

    while True:
        try:
            now_str = datetime.now(MOSCOW_TZ).strftime('%H:%M')
            if not is_active_time():
                print(f"😴 Ночь ({now_str})", flush=True)
                time.sleep(600)
                continue

            monitor_prematch()
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