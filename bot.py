import os
import sys
import re
import json
import time
from pathlib import Path

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

PATTERNS = []
for s in _data.get("survivors", []):
    gap, feature = s["key"]
    PATTERNS.append({
        "gap": int(gap),
        "feature": feature,
        "target": s["target"],
    })

print(f"✅ Загружено паттернов: {len(PATTERNS)}", flush=True)


# =====================================================================
# CONFIG
# =====================================================================

POLL_INTERVAL = 2.0
FINALIZE_WAIT_SECONDS = 30
OFFSET_FILE = "tg_offset.txt"


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
                "allowed_updates": json.dumps(["channel_post", "edited_channel_post"]),
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

# pending_games: {game_number: {"first_seen": ts, "text": str}}
pending_games = {}

# games_cache: {game_number: parsed_game}
games_cache = {}

# predictions: list of dict
predictions = []

# processed triggers: set of (game_number, feature, target)
processed_triggers = set()


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

        # не спамим одинаковыми прогнозами
        already = any(
            pr["target_game"] == target_game and pr["card"] == card
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
        })
        processed_triggers.add(key)

        print(f"🔮 #{target_game} {card}  (триггер #{gn} {p['feature']})", flush=True)


def check_predictions(current_game_number):
    for pr in predictions:
        if pr["status"] != "pending":
            continue

        target = pr["target_game"]
        card = pr["card"]

        # если текущая игра уже за пределами 4-игрового окна — минус
        if current_game_number > target + 3:
            # прогоняем по всем 4 играм ещё раз (вдруг карта была, но мы пропустили)
            for dogon in range(0, 4):
                g_num = target + dogon
                g = games_cache.get(g_num)
                if not g:
                    continue
                if card in g["all_cards"]:
                    pr["status"] = "win"
                    tg_edit(pr["message_id"], f"{target}: {card} ✅")
                    print(f"✅ #{target} {card} — сбылось (догон {dogon})", flush=True)
                    break
            else:
                pr["status"] = "lose"
                tg_edit(pr["message_id"], f"{target}: {card} ❌")
                print(f"❌ #{target} {card} — не сбылось", flush=True)
            continue


def check_pending_on_new_game(game):
    """Когда пришла новая игра — проверяем pending по этой игре."""
    for pr in predictions:
        if pr["status"] != "pending":
            continue
        if pr["card"] in game["all_cards"]:
            # проверяем что игра в допустимом окне
            if pr["target_game"] <= game["game_number"] <= pr["target_game"] + 3:
                pr["status"] = "win"
                tg_edit(pr["message_id"], f"{pr['target_game']}: {pr['card']} ✅")
                print(f"✅ #{pr['target_game']} {pr['card']} — сбылось", flush=True)


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
        print(f"🎮 #{gn}  P:{game['player_cards']}  D:{game['dealer_cards']}", flush=True)

        # 1. проверяем pending по этой игре
        check_pending_on_new_game(game)

        # 2. таймауты
        check_predictions(gn)

        # 3. новые триггеры
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

        # только завершённые игры (✅ или 🔰)
        if not re.search(r"[✅🔰]", text):
            continue

        if gn in pending_games:
            # обновляем текст (Telegram может дописать карты)
            pending_games[gn]["text"] = text
            continue

        if gn in games_cache:
            # уже обработана, но текст мог обновиться
            new_game = parse_game(text)
            if new_game:
                games_cache[gn] = new_game
            continue

        pending_games[gn] = {
            "first_seen": time.time(),
            "text": text,
        }
        print(f"👀 Новая игра #N{gn}, жду {FINALIZE_WAIT_SECONDS} сек", flush=True)

    return offset


# =====================================================================
# MAIN
# =====================================================================

def main():
    print("=" * 60, flush=True)
    print("🚀 PATTERN FORECAST BOT (requests)", flush=True)
    print("=" * 60, flush=True)
    print(f"📥 CHANNEL_STAT: {CHANNEL_STAT}", flush=True)
    print(f"📤 CHANNEL_PROGNOZ: {CHANNEL_PROGNOZ}", flush=True)
    print(f"🧩 Паттернов: {len(PATTERNS)}", flush=True)
    print("=" * 60, flush=True)

    offset = load_offset()
    print(f"📌 Offset: {offset}", flush=True)

    while True:
        try:
            offset = process_updates(offset)
            finalize_pending_games()
            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            print("🛑 Остановлен", flush=True)
            break
        except Exception as e:
            print(f"❌ Ошибка: {e}", flush=True)
            time.sleep(3)


if __name__ == "__main__":
    main()