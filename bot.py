import os
import sys
import re
import json
import time
from pathlib import Path
from datetime import datetime
from collections import defaultdict

import requests


# =====================================================================
# ENV
# =====================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_STAT = os.getenv("CHANNEL_STAT")
CHANNEL_PROGNOZ = os.getenv("CHANNEL_PROGNOZ")

if not BOT_TOKEN:
    print("❌ BOT_TOKEN не задан", flush=True)
    sys.exit(1)
if not CHANNEL_STAT:
    print("❌ CHANNEL_STAT не задан", flush=True)
    sys.exit(1)
if not CHANNEL_PROGNOZ:
    print("❌ CHANNEL_PROGNOZ не задан", flush=True)
    sys.exit(1)

CHANNEL_STAT = str(CHANNEL_STAT).strip()
CHANNEL_PROGNOZ = str(CHANNEL_PROGNOZ).strip()


# =====================================================================
# PATTERNS
# =====================================================================

PATTERNS_FILE = Path(__file__).parent / "pattern_results.json"

if not PATTERNS_FILE.exists():
    print(f"❌ Не найден {PATTERNS_FILE}", flush=True)
    sys.exit(1)

with open(PATTERNS_FILE, "r", encoding="utf-8") as f:
    _data = json.load(f)

# ---- ФИЛЬТР ПАТТЕРНОВ ----
MIN_LIFT = 1.6
MIN_RETENTION = 0.85
MIN_OCC = 30
MIN_HOLDOUT_HITS = 8
MAX_GAP = 6

PATTERNS = []
for s in _data.get("survivors", []):
    gap, feature = s["key"]
    gap = int(gap)

    if gap > MAX_GAP:
        continue
    if s.get("holdout_lift", 0) < MIN_LIFT:
        continue
    if s.get("lift_retention", 0) < MIN_RETENTION:
        continue
    if s.get("holdout_occ", 0) < MIN_OCC:
        continue
    if s.get("holdout_hits", 0) < MIN_HOLDOUT_HITS:
        continue

    PATTERNS.append({
        "gap": gap,
        "feature": feature,
        "target": s["target"],
    })

print(f"✅ Загружено паттернов (после фильтра): {len(PATTERNS)}", flush=True)


# =====================================================================
# CONFIG
# =====================================================================

POLL_INTERVAL = 2.0
FINALIZE_WAIT_SECONDS = 30
OFFSET_FILE = "tg_offset.txt"

STATS_INTERVAL = 6 * 60 * 60  # 6 часов


# =====================================================================
# TELEGRAM HTTP
# =====================================================================

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
SESSION = requests.Session()


def tg_send(text):
    """Отправляет сообщение в канал прогнозов. Возвращает message_id."""
    try:
        r = SESSION.post(
            f"{TELEGRAM_API}/sendMessage",
            json={
                "chat_id": CHANNEL_PROGNOZ,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        data = r.json()
        if data.get("ok"):
            return data["result"]["message_id"]
        print(f"❌ sendMessage: {data}", flush=True)
    except Exception as e:
        print(f"❌ sendMessage error: {e}", flush=True)
    return None


def tg_edit(message_id, text):
    """Редактирует сообщение в канале прогнозов."""
    if not message_id:
        return False
    try:
        r = SESSION.post(
            f"{TELEGRAM_API}/editMessageText",
            json={
                "chat_id": CHANNEL_PROGNOZ,
                "message_id": message_id,
                "text": text,
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        return bool(r.json().get("ok"))
    except Exception as e:
        print(f"⚠️ editMessageText error: {e}", flush=True)
    return False


def tg_get_updates(offset):
    try:
        r = SESSION.get(
            f"{TELEGRAM_API}/getUpdates",
            params={
                "offset": offset,
                "timeout": 3,
                "limit": 50,
                "allowed_updates": json.dumps(
                    ["channel_post", "edited_channel_post"]
                ),
            },
            timeout=15,
        )
        data = r.json()
        if not data.get("ok"):
            print(f"❌ getUpdates: {data}", flush=True)
            return []
        return data.get("result", [])
    except Exception as e:
        print(f"⚠️ getUpdates error: {e}", flush=True)
        return []


# =====================================================================
# OFFSET
# =====================================================================

def load_offset():
    try:
        if os.path.exists(OFFSET_FILE):
            with open(OFFSET_FILE, "r") as f:
                return int(f.read().strip())
    except Exception:
        pass
    return 0


def save_offset(offset):
    try:
        with open(OFFSET_FILE, "w") as f:
            f.write(str(offset))
    except Exception as e:
        print(f"⚠️ save offset: {e}", flush=True)


# =====================================================================
# PARSING
# =====================================================================

CARD_RE = re.compile(r"(10|[2-9]|[AJQK])([♠♣♦♥])")
HAND_RE = re.compile(r"\(([^)]*)\)")
NUMBER_RE = re.compile(r"#N(\d+)")


def clean_text(t):
    return t.replace("✅", "").replace("🔰", "")


def extract_cards(s):
    return [f"{r}{su}" for r, su in CARD_RE.findall(s)]


def parse_game(text):
    """Возвращает dict или None."""
    if not text:
        return None

    m = NUMBER_RE.search(text)
    if not m:
        return None
    game_number = int(m.group(1))

    clean = clean_text(text)
    hands = HAND_RE.findall(clean)
    if len(hands) < 2:
        return None

    player_cards = extract_cards(hands[0])
    dealer_cards = extract_cards(hands[1])

    if not player_cards and not dealer_cards:
        return None

    return {
        "game_number": game_number,
        "player_cards": player_cards,
        "dealer_cards": dealer_cards,
        "all_cards": player_cards + dealer_cards,
    }


# =====================================================================
# FEATURES (совпадают со сканером)
# =====================================================================

def ranks(cards):
    return [c[:-1] for c in cards]


def suits(cards):
    return [c[-1] for c in cards]


def rank_seq(cards):
    return "-".join(ranks(cards))


def suit_seq(cards):
    return "-".join(suits(cards))


def exact_seq(cards):
    return "-".join(cards)


def game_features(player_cards, dealer_cards):
    result = []

    for side_name, cards in (("P", player_cards), ("D", dealer_cards)):
        if not cards:
            continue

        result.append(f"{side_name}:COUNT={len(cards)}")
        result.append(f"{side_name}:FIRST={cards[0]}")
        result.append(f"{side_name}:FIRST_RANK={ranks(cards)[0]}")
        result.append(f"{side_name}:FIRST_SUIT={suits(cards)[0]}")

        if len(cards) >= 2:
            result.append(f"{side_name}:FIRST2={exact_seq(cards[:2])}")
            result.append(f"{side_name}:FIRST2_RANKS={rank_seq(cards[:2])}")
            result.append(f"{side_name}:FIRST2_SUITS={suit_seq(cards[:2])}")

        if len(cards) >= 3:
            result.append(f"{side_name}:FIRST3_RANKS={rank_seq(cards[:3])}")
            result.append(f"{side_name}:FIRST3_SUITS={suit_seq(cards[:3])}")

        result.append(f"{side_name}:RANKS={rank_seq(cards)}")
        result.append(f"{side_name}:SUITS={suit_seq(cards)}")
        result.append(f"{side_name}:EXACT={exact_seq(cards)}")

        rc = {}
        for r in ranks(cards):
            rc[r] = rc.get(r, 0) + 1
        for rank, count in sorted(rc.items()):
            result.append(f"{side_name}:RANKCOUNT:{rank}={count}")

        for rank in ("J", "Q", "K", "A"):
            if rank in ranks(cards):
                result.append(f"{side_name}:HAS_{rank}")

    return result


# =====================================================================
# STATE
# =====================================================================

pending_games = {}
games_cache = {}
predictions = []
processed_triggers = set()

stats_last_sent = time.time()


# =====================================================================
# PREDICTION LOGIC
# =====================================================================

def create_predictions(game):
    gn = game["game_number"]
    feats = set(game_features(game["player_cards"], game["dealer_cards"]))

    for p in PATTERNS:
        if p["feature"] not in feats:
            continue

        target_game = gn + p["gap"]
        card = p["target"]

        key = (gn, p["feature"], card)
        if key in processed_triggers:
            continue

        # один прогноз на одну целевую игру
        already = any(
            pr["target_game"] == target_game
            and pr["status"] == "pending"
            for pr in predictions
        )
        if already:
            processed_triggers.add(key)
            continue

        mid = tg_send(f"{target_game}: {card}")
        if not mid:
            continue

        predictions.append({
            "message_id": mid,
            "target_game": target_game,
            "card": card,
            "status": "pending",
            "trigger_game": gn,
            "feature": p["feature"],
        })
        processed_triggers.add(key)

        print(
            f"🔮 #{target_game} {card}  (триггер #{gn} {p['feature']})",
            flush=True,
        )


def check_pending_on_new_game(game):
    """Когда пришла новая игра — проверяем pending по этой игре."""
    for pr in predictions:
        if pr["status"] != "pending":
            continue
        if pr["card"] in game["all_cards"]:
            if pr["target_game"] <= game["game_number"] <= pr["target_game"] + 3:
                pr["status"] = "win"
                tg_edit(pr["message_id"], f"{pr['target_game']}: {pr['card']} ✅")
                feat = pr.get("feature", "?")
                trig = pr.get("trigger_game", "?")
                print(
                    f"✅ #{pr['target_game']} {pr['card']} — СБЫЛОСЬ "
                    f"(паттерн: {feat}, триггер: #{trig})",
                    flush=True,
                )


def check_predictions(current_game_number):
    for pr in predictions:
        if pr["status"] != "pending":
            continue

        target = pr["target_game"]
        card = pr["card"]
        feat = pr.get("feature", "?")
        trig = pr.get("trigger_game", "?")

        if current_game_number > target + 3:
            for dogon in range(0, 4):
                g_num = target + dogon
                g = games_cache.get(g_num)
                if not g:
                    continue
                if card in g["all_cards"]:
                    pr["status"] = "win"
                    tg_edit(pr["message_id"], f"{target}: {card} ✅")
                    print(
                        f"✅ #{target} {card} — СБЫЛОСЬ, догон {dogon} "
                        f"(паттерн: {feat}, триггер: #{trig})",
                        flush=True,
                    )
                    break
            else:
                pr["status"] = "lose"
                tg_edit(pr["message_id"], f"{target}: {card} ❌")
                print(
                    f"❌ #{target} {card} — НЕ СБЫЛОСЬ "
                    f"(паттерн: {feat}, триггер: #{trig})",
                    flush=True,
                )
            continue


# =====================================================================
# FINALIZE
# =====================================================================

def finalize_pending_games():
    now = time.time()
    ready = [
        gn for gn, info in pending_games.items()
        if now - info["first_seen"] >= FINALIZE_WAIT_SECONDS
    ]

    for gn in ready:
        info = pending_games.pop(gn, None)
        if not info:
            continue

        game = parse_game(info["text"])
        if not game:
            print(f"⚠️ #N{gn}: не удалось распарсить", flush=True)
            continue

        games_cache[gn] = game
        print(
            f"🎮 #{gn}  P:{game['player_cards']}  D:{game['dealer_cards']}",
            flush=True,
        )

        check_pending_on_new_game(game)
        check_predictions(gn)
        create_predictions(game)


# =====================================================================
# UPDATES
# =====================================================================

def process_updates(offset):
    updates = tg_get_updates(offset)

    for u in updates:
        uid = u.get("update_id")
        if uid is not None:
            offset = uid + 1
            save_offset(offset)

        post = u.get("channel_post") or u.get("edited_channel_post")
        if not post:
            continue

        chat_id = str(post.get("chat", {}).get("id", ""))
        if chat_id != CHANNEL_STAT:
            continue

        text = post.get("text", "")
        if not text:
            continue

        m = NUMBER_RE.search(text)
        if not m:
            continue
        gn = int(m.group(1))

        if not re.search(r"[✅🔰]", text):
            continue

        if gn in pending_games:
            pending_games[gn]["text"] = text
            continue

        if gn in games_cache:
            new_game = parse_game(text)
            if new_game:
                games_cache[gn] = new_game
            continue

        pending_games[gn] = {
            "first_seen": time.time(),
            "text": text,
        }
        print(
            f"👀 Новая игра #N{gn}, жду {FINALIZE_WAIT_SECONDS} сек",
            flush=True,
        )

    return offset


# =====================================================================
# STATS
# =====================================================================

def build_stats_message():
    resolved = [p for p in predictions if p["status"] in ("win", "lose")]

    if not resolved:
        return None

    total = len(resolved)
    wins = sum(1 for p in resolved if p["status"] == "win")
    loses = total - wins
    rate = wins / total * 100 if total else 0

    by_feature = defaultdict(lambda: {"wins": 0, "loses": 0})
    for p in resolved:
        feat = p.get("feature", "?")
        if p["status"] == "win":
            by_feature[feat]["wins"] += 1
        else:
            by_feature[feat]["loses"] += 1

    feature_stats = []
    for feat, st in by_feature.items():
        tot = st["wins"] + st["loses"]
        if tot < 2:
            continue
        feature_stats.append({
            "feature": feat,
            "wins": st["wins"],
            "loses": st["loses"],
            "total": tot,
            "rate": st["wins"] / tot * 100,
        })

    feature_stats.sort(key=lambda x: (x["rate"], x["total"]), reverse=True)

    top = feature_stats[:5]
    worst = [f for f in feature_stats if f["rate"] < 40][:5]

    pending = sum(1 for p in predictions if p["status"] == "pending")

    now_str = datetime.now().strftime("%d.%m %H:%M")

    lines = []
    lines.append(f"📊 <b>СТАТИСТИКА ({now_str})</b>")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"Всего прогнозов: {total}")
    lines.append(f"✅ Сбылось: {wins} ({rate:.1f}%)")
    lines.append(f"❌ Не сбылось: {loses} ({100 - rate:.1f}%)")
    lines.append("")

    if top:
        lines.append("🏆 <b>ТОП-5 ПАТТЕРНОВ:</b>")
        for i, f in enumerate(top, 1):
            lines.append(
                f"{i}. {f['feature']}  {f['wins']}/{f['total']} ({f['rate']:.0f}%)"
            )
        lines.append("")

    if worst:
        lines.append("💀 <b>ХУДШИЕ:</b>")
        for i, f in enumerate(worst, 1):
            lines.append(
                f"{i}. {f['feature']}  {f['wins']}/{f['total']} ({f['rate']:.0f}%)"
            )
        lines.append("")

    lines.append(f"⏳ В ожидании: {pending}")

    return "\n".join(lines)


def maybe_send_stats():
    global stats_last_sent

    if time.time() - stats_last_sent < STATS_INTERVAL:
        return

    msg = build_stats_message()
    if msg:
        mid = tg_send(msg)
        if mid:
            print("📊 Статистика отправлена", flush=True)
        else:
            print("⚠️ Не смог отправить статистику", flush=True)
    else:
        print("📊 Статистики нет (нет resolved прогнозов)", flush=True)

    stats_last_sent = time.time()


# =====================================================================
# MAIN
# =====================================================================

def main():
    print("=" * 60, flush=True)
    print("🚀 PATTERN FORECAST BOT", flush=True)
    print("=" * 60, flush=True)
    print(f"📥 CHANNEL_STAT: {CHANNEL_STAT}", flush=True)
    print(f"📤 CHANNEL_PROGNOZ: {CHANNEL_PROGNOZ}", flush=True)
    print(f"🧩 Паттернов: {len(PATTERNS)}", flush=True)
    print(f"📊 Статистика раз в {STATS_INTERVAL // 3600} ч", flush=True)
    print("=" * 60, flush=True)

    offset = load_offset()
    print(f"📌 Offset: {offset}", flush=True)

    global stats_last_sent
    stats_last_sent = time.time()

    while True:
        try:
            offset = process_updates(offset)
            finalize_pending_games()
            maybe_send_stats()
            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            print("🛑 Остановлен", flush=True)
            break
        except Exception as e:
            print(f"❌ Ошибка: {e}", flush=True)
            time.sleep(3)


if __name__ == "__main__":
    main()