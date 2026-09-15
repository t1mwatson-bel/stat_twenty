import os
import sys
import re
import json
import time
import requests
import pytz

from datetime import datetime, timedelta


# =====================================================================
# ENV
# =====================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    BOT_TOKEN = os.getenv("BOT_TOKEN_PROGNOZ")

CHANNEL_PROGNOZ = os.getenv("CHAT_ID_21")
if not CHANNEL_PROGNOZ:
    CHANNEL_PROGNOZ = os.getenv("CHANNEL_PROGNOZ")

CHANNEL_STATS = os.getenv("CHANNEL_STATS")
CHANNEL_STAT = os.getenv("CHANNEL_STAT")


if not BOT_TOKEN:
    print("❌ BOT_TOKEN не задан", flush=True)
    sys.exit(1)

if not CHANNEL_PROGNOZ:
    print("❌ CHANNEL_PROGNOZ не задан", flush=True)
    sys.exit(1)

if not CHANNEL_STATS:
    print("❌ CHANNEL_STATS не задан", flush=True)
    sys.exit(1)

if not CHANNEL_STAT:
    print("❌ CHANNEL_STAT не задан", flush=True)
    sys.exit(1)


# =====================================================================
# CONFIG
# =====================================================================

MOSCOW_TZ = pytz.timezone("Europe/Moscow")

PREDICTIONS_FILE = "premium_predictions.json"
QUEUED_FILE = "queued_predictions.json"
OFFSET_FILE = "telegram_offset.txt"

POLL_INTERVAL = 2.0

FINALIZE_WAIT_SECONDS = 30

DOGON_GAMES = 3

PREDICTION_TIMEOUT_HOURS = 24

SEND_HOUR = 9
SEND_MINUTE = 0


# =====================================================================
# TELEGRAM
# =====================================================================

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
})


# =====================================================================
# GLOBALS
# =====================================================================

games_cache = {}
pending_games = {}
processed_triggers = set()

games_cache_stat = {}
pending_games_stat = {}

predictions = []
queued_predictions = []

telegram_offset = 0

last_send_date = None


# =====================================================================
# CARD NORMALIZATION
# =====================================================================

SUITS = {
    "\u2660": "\u2660\ufe0f",
    "\u2663": "\u2663\ufe0f",
    "\u2666": "\u2666\ufe0f",
    "\u2665": "\u2665\ufe0f",
}

MIRROR_SUIT = {
    "\u2666\ufe0f": "\u2665\ufe0f",
    "\u2665\ufe0f": "\u2666\ufe0f",
    "\u2663\ufe0f": "\u2660\ufe0f",
    "\u2660\ufe0f": "\u2663\ufe0f",
}


def normalize_suit(suit):
    if suit is None:
        return None

    suit = str(suit).strip()
    suit = suit.replace("\ufe0f", "")

    return SUITS.get(suit)


def normalize_rank(rank):
    if rank is None:
        return None

    rank = str(rank).strip().upper()

    if rank == "А":
        rank = "A"

    if rank in {
        "6", "7", "8", "9", "10",
        "J", "Q", "K", "A",
    }:
        return rank

    return None


def card_to_text(card):
    if not card:
        return ""

    rank = normalize_rank(card.get("rank"))
    suit = normalize_suit(card.get("suit"))

    if not rank or not suit:
        return ""

    return f"{rank}{suit}"


def cards_to_text(cards):
    result = []

    for card in cards:
        value = card_to_text(card)
        if value:
            result.append(value)

    return " ".join(result)


# =====================================================================
# CYBER 21 SCORE
# =====================================================================

CARD_VALUES = {
    "6": 6, "7": 7, "8": 8, "9": 9, "10": 10,
    "J": 2, "Q": 3, "K": 4, "A": 11,
}


def cyber21_score(cards):
    total = 0

    for card in cards:
        rank = normalize_rank(card.get("rank"))
        if rank in CARD_VALUES:
            total += CARD_VALUES[rank]

    return total


# =====================================================================
# PARSE CARDS
# =====================================================================

CARD_RE = re.compile(
    r"(10|[6-9AJQK])\s*"
    r"(\u2660|\u2663|\u2666|\u2665)"
    r"\ufe0f?"
)


def parse_cards(text):
    result = []

    if not text:
        return result

    for match in CARD_RE.finditer(text):
        rank = normalize_rank(match.group(1))
        suit = normalize_suit(match.group(2))

        if not rank or not suit:
            continue

        result.append({
            "rank": rank,
            "suit": suit,
        })

    return result


# =====================================================================
# PARSE GAME MESSAGE
# =====================================================================

def parse_game_message(text):
    if not text:
        return None

    number_match = re.search(r"#N(\d+)", text)
    if not number_match:
        return None

    game_number = int(number_match.group(1))

    groups = re.findall(r"\(([^()]*)\)", text)
    if len(groups) < 2:
        return None

    player_text = groups[0]
    dealer_text = groups[1]

    player_cards = parse_cards(player_text)
    dealer_cards = parse_cards(dealer_text)

    if not player_cards:
        return None

    player_score = cyber21_score(player_cards)
    dealer_score = cyber21_score(dealer_cards)

    id_match = re.search(r"ID:\s*(\d+)", text)
    game_id = id_match.group(1) if id_match else None

    is_draw = bool(re.search(r"#X\b", text))
    is_ochko = bool(re.search(r"#O\b", text))

    return {
        "game_number": game_number,
        "game_id": game_id,

        "player_cards": player_cards,
        "dealer_cards": dealer_cards,

        "player_score": player_score,
        "dealer_score": dealer_score,

        "is_draw": is_draw,
        "is_ochko": is_ochko,

        "raw_text": text,
    }


# =====================================================================
# LOG GAME
# =====================================================================

def log_game(game, source="trigger"):
    player = game.get("player_cards", [])
    dealer = game.get("dealer_cards", [])

    tag = "TRIGGER" if source == "trigger" else "CHECK"

    print("", flush=True)
    print("────────────────────────────────────", flush=True)
    print(
        f"🔒 ИГРА ЗАФИКСИРОВАНА [{tag}] "
        f"#N{game['game_number']}",
        flush=True,
    )
    print(f"👤 P: {game['player_score']} ({cards_to_text(player)})", flush=True)
    print(f"🎰 D: {game['dealer_score']} ({cards_to_text(dealer)})", flush=True)

    if game.get("is_draw"):
        print("🔰 #X — НИЧЬЯ", flush=True)

    if game.get("is_ochko"):
        print("⭕ #O — ОЧКО (21), пропускаем триггер", flush=True)

    print("────────────────────────────────────", flush=True)


# =====================================================================
# OFFSET
# =====================================================================

def load_offset():
    try:
        if os.path.exists(OFFSET_FILE):
            with open(OFFSET_FILE, "r", encoding="utf-8") as f:
                return int(f.read().strip())
    except Exception:
        pass
    return 0


def save_offset(offset):
    try:
        with open(OFFSET_FILE, "w", encoding="utf-8") as f:
            f.write(str(offset))
    except Exception as e:
        print(f"⚠️ Ошибка сохранения offset: {e}", flush=True)


# =====================================================================
# PREDICTIONS JSON
# =====================================================================

def load_predictions():
    global predictions

    try:
        if not os.path.exists(PREDICTIONS_FILE):
            predictions = []
            return

        with open(PREDICTIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        predictions = data if isinstance(data, list) else []

    except Exception as e:
        print(f"⚠️ Ошибка чтения {PREDICTIONS_FILE}: {e}", flush=True)
        predictions = []


def save_predictions():
    try:
        tmp = PREDICTIONS_FILE + ".tmp"

        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(predictions, f, ensure_ascii=False, indent=2)

        os.replace(tmp, PREDICTIONS_FILE)

    except Exception as e:
        print(f"⚠️ Ошибка сохранения прогнозов: {e}", flush=True)


def load_queued():
    global queued_predictions

    try:
        if not os.path.exists(QUEUED_FILE):
            queued_predictions = []
            return

        with open(QUEUED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        queued_predictions = data if isinstance(data, list) else []

    except Exception as e:
        print(f"⚠️ Ошибка чтения {QUEUED_FILE}: {e}", flush=True)
        queued_predictions = []


def save_queued():
    try:
        tmp = QUEUED_FILE + ".tmp"

        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(queued_predictions, f, ensure_ascii=False, indent=2)

        os.replace(tmp, QUEUED_FILE)

    except Exception as e:
        print(f"⚠️ Ошибка сохранения очереди: {e}", flush=True)


# =====================================================================
# TELEGRAM SEND
# =====================================================================

def telegram_send(text):
    try:
        response = SESSION.post(
            f"{TELEGRAM_API}/sendMessage",
            json={
                "chat_id": CHANNEL_PROGNOZ,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )

        data = response.json()

        if data.get("ok"):
            return data["result"]["message_id"]

        print(f"❌ Telegram sendMessage: {data}", flush=True)

    except Exception as e:
        print(f"❌ Ошибка отправки Telegram: {e}", flush=True)

    return None


def telegram_edit(message_id, text):
    if not message_id:
        return False

    try:
        response = SESSION.post(
            f"{TELEGRAM_API}/editMessageText",
            json={
                "chat_id": CHANNEL_PROGNOZ,
                "message_id": message_id,
                "text": text,
                "parse_mode": "HTML",
            },
            timeout=10,
        )

        return bool(response.json().get("ok"))

    except Exception as e:
        print(f"⚠️ Ошибка редактирования Telegram: {e}", flush=True)

    return False


# =====================================================================
# GAME NUMBER
# =====================================================================

def add_game_offset(number, offset):
    return ((int(number) - 1 + int(offset)) % 1440) + 1


# =====================================================================
# ALGORITHM: ВЫСШИЙ РАНГ + ПОСЛЕДНЯЯ 10 → ТОЧНАЯ КАРТА
# =====================================================================

HIGH_RANKS = {"J", "Q", "K", "A"}


def get_premium_prediction(game):
    player = game.get("player_cards", [])
    dealer = game.get("dealer_cards", [])

    if not player:
        return None

    if dealer:
        return None

    if game.get("is_ochko"):
        print(
            f"⭕ #N{game['game_number']}: #O (очко) — пропуск триггера",
            flush=True,
        )
        return None

    if "✅" not in game.get("raw_text", ""):
        return None

    first_card = player[0]
    first_rank = normalize_rank(first_card.get("rank"))

    if first_rank not in HIGH_RANKS:
        return None

    last_card = player[-1]
    last_rank = normalize_rank(last_card.get("rank"))

    if last_rank != "10":
        return None

    tens_count = sum(
        1 for c in player
        if normalize_rank(c.get("rank")) == "10"
    )

    if tens_count != 1:
        return None

    suit_first = normalize_suit(first_card.get("suit"))
    suit_last = normalize_suit(last_card.get("suit"))

    if not suit_first or not suit_last:
        return None

    if suit_first == suit_last:
        suit_2 = MIRROR_SUIT.get(suit_first)
        if not suit_2:
            return None
    else:
        suit_2 = suit_last

    predicted_rank = first_rank
    predicted_suit_1 = suit_first
    predicted_suit_2 = suit_2

    target_number = add_game_offset(game["game_number"] * 2, 0)

    return {
        "algorithm": "высший ранг + последняя 10",
        "trigger_number": game["game_number"],
        "trigger_game_id": game.get("game_id"),
        "target_number": target_number,
        "predicted_rank": predicted_rank,
        "predicted_suit_1": predicted_suit_1,
        "predicted_suit_2": predicted_suit_2,
        "trigger_player": [card_to_text(c) for c in player],
        "trigger_dealer": [card_to_text(c) for c in dealer],
        "trigger_player_score": game["player_score"],
        "trigger_dealer_score": game["dealer_score"],
        "status": "queued",
        "created_at": datetime.now(MOSCOW_TZ).isoformat(),
        "sent_at": None,
        "result_game": None,
        "found_card": None,
        "dogon": None,
        "message_id": None,
    }


# =====================================================================
# CREATE PREDICTION → в очередь
# =====================================================================

def create_predictions(game):
    game_number = game["game_number"]

    prediction = get_premium_prediction(game)

    if not prediction:
        return

    algorithm = prediction["algorithm"]
    trigger_key = (algorithm, game_number)

    if trigger_key in processed_triggers:
        return

    target_number = prediction["target_number"]

    already_exists = any(
        entry.get("algorithm") == algorithm
        and entry.get("target_number") == target_number
        for entry in queued_predictions + predictions
    )

    if already_exists:
        processed_triggers.add(trigger_key)
        return

    queued_predictions.append(prediction)
    processed_triggers.add(trigger_key)
    save_queued()

    print("", flush=True)
    print("📥 ПРОГНОЗ В ОЧЕРЕДЬ", flush=True)
    print(f"🧠 Алгоритм: {algorithm}", flush=True)
    print(f"🎯 Цель: #N{target_number}", flush=True)
    print(
        f"🃏 Прогноз: "
        f"{prediction['predicted_rank']}"
        f"{prediction['predicted_suit_1']}"
        f"{prediction['predicted_suit_2']}",
        flush=True,
    )
    print(f"📌 Триггер: #N{game_number}", flush=True)


def create_prediction(game):
    create_predictions(game)


# =====================================================================
# PREDICTION MESSAGE
# =====================================================================

def format_card_prediction(prediction):
    rank = prediction["predicted_rank"]
    s1 = prediction["predicted_suit_1"]
    s2 = prediction["predicted_suit_2"]

    return f"{rank}{s1}{s2}"


def make_prediction_message(prediction):
    target = prediction["target_number"]
    card_text = format_card_prediction(prediction)

    return f"🎯 Игра: <b>#N{target}</b> {card_text}"


def make_result_message(prediction, result):
    target = prediction["target_number"]
    card_text = format_card_prediction(prediction)

    if result == "win":
        mark = "✅"
    elif result == "lose":
        mark = "❌"
    else:
        mark = "⚠️"

    return f"🎯 Игра: <b>#N{target}</b> {card_text}{mark}"


# =====================================================================
# SEND QUEUE (09:00)
# =====================================================================

def send_queued_predictions():
    global queued_predictions, predictions

    if not queued_predictions:
        print("📭 Очередь пуста — нечего отправлять", flush=True)
        return

    print(
        f"📤 Отправка пачки: {len(queued_predictions)} прогнозов",
        flush=True,
    )

    sent = []

    for prediction in queued_predictions:
        message = make_prediction_message(prediction)
        message_id = telegram_send(message)

        if not message_id:
            print(
                f"❌ Не удалось отправить прогноз на "
                f"#N{prediction['target_number']}",
                flush=True,
            )
            continue

        prediction["message_id"] = message_id
        prediction["status"] = "pending"
        prediction["sent_at"] = datetime.now(MOSCOW_TZ).isoformat()

        predictions.append(prediction)
        sent.append(prediction)

        print(
            f"✅ Отправлен прогноз #N{prediction['target_number']} "
            f"({format_card_prediction(prediction)})",
            flush=True,
        )

    unsent = [
        p for p in queued_predictions
        if p not in sent
    ]

    queued_predictions = unsent

    save_predictions()
    save_queued()


def should_send_now():
    global last_send_date

    now = datetime.now(MOSCOW_TZ)

    if now.hour != SEND_HOUR or now.minute != SEND_MINUTE:
        return False

    today = now.date()

    if last_send_date == today:
        return False

    return True


def mark_send_done():
    global last_send_date

    last_send_date = datetime.now(MOSCOW_TZ).date()


# =====================================================================
# CHECK PREDICTION CARD
# =====================================================================

def check_prediction_card(game, prediction):
    rank = prediction["predicted_rank"]
    s1 = prediction["predicted_suit_1"]
    s2 = prediction["predicted_suit_2"]

    player_cards = game.get("player_cards", [])
    dealer_cards = game.get("dealer_cards", [])

    all_cards = list(player_cards) + list(dealer_cards)

    for card in all_cards:
        c_rank = normalize_rank(card.get("rank"))
        c_suit = normalize_suit(card.get("suit"))

        if c_rank != rank:
            continue

        if c_suit == s1 or c_suit == s2:
            return card_to_text(card)

    return None


# =====================================================================
# CHECK PREDICTIONS (по CHANNEL_STAT)
# =====================================================================

def check_predictions():
    changed = False

    now = datetime.now(MOSCOW_TZ)

    max_stat_number = max(games_cache_stat.keys()) if games_cache_stat else 0

    for prediction in predictions:

        if prediction.get("status") != "pending":
            continue

        target = prediction.get("target_number")
        if not target:
            continue

        sent_at_str = prediction.get("sent_at")
        if sent_at_str:
            try:
                sent_at = datetime.fromisoformat(sent_at_str)
                if now - sent_at > timedelta(
                    hours=PREDICTION_TIMEOUT_HOURS
                ):
                    prediction["status"] = "void"
                    telegram_edit(
                        prediction.get("message_id"),
                        make_result_message(prediction, "void"),
                    )
                    print(
                        f"⚠️ VOID (timeout) #N{target}",
                        flush=True,
                    )
                    changed = True
                    continue
            except Exception:
                pass

        checked_all = True
        found_any = False

        for dogon in range(0, DOGON_GAMES + 1):

            game_number = add_game_offset(target, dogon)
            game = games_cache_stat.get(game_number)

            if game:
                found_card = check_prediction_card(game, prediction)

                if found_card:
                    prediction["status"] = "win"
                    prediction["result_game"] = game_number
                    prediction["found_card"] = found_card
                    prediction["dogon"] = dogon

                    telegram_edit(
                        prediction.get("message_id"),
                        make_result_message(prediction, "win"),
                    )

                    print("", flush=True)
                    print(f"✅ PLUS #N{target}", flush=True)
                    print(
                        f"🎯 Карта найдена в #N{game_number} "
                        f"({found_card})",
                        flush=True,
                    )
                    print(f"🔄 Догон: {dogon}", flush=True)

                    changed = True
                    found_any = True
                    checked_all = False
                    break

                print(
                    f"🔍 #N{game_number} — карты нет → дальше",
                    flush=True,
                )
                continue

            if max_stat_number >= game_number:
                print(
                    f"⏭️ #N{game_number} — пропущена",
                    flush=True,
                )
                continue

            checked_all = False
            print(
                f"⏳ #N{target}: ждём #N{game_number} "
                f"(догон {dogon})",
                flush=True,
            )
            break

        if found_any:
            continue

        if not checked_all:
            continue

        any_checked = False

        for dogon in range(0, DOGON_GAMES + 1):
            game_number = add_game_offset(target, dogon)
            if game_number in games_cache_stat:
                any_checked = True
                break

        if any_checked:
            prediction["status"] = "lose"
            prediction["result_game"] = add_game_offset(
                target, DOGON_GAMES
            )
            prediction["dogon"] = DOGON_GAMES

            telegram_edit(
                prediction.get("message_id"),
                make_result_message(prediction, "lose"),
            )

            print("", flush=True)
            print(f"❌ MINUS #N{target}", flush=True)
            print(
                f"🏁 Проверены все игры "
                f"#N{target} — "
                f"#N{add_game_offset(target, DOGON_GAMES)}",
                flush=True,
            )
        else:
            prediction["status"] = "void"

            telegram_edit(
                prediction.get("message_id"),
                make_result_message(prediction, "void"),
            )

            print("", flush=True)
            print(f"⚠️ VOID #N{target}", flush=True)
            print(
                "🏁 Все 4 игры пропущены — прогноз аннулирован",
                flush=True,
            )

        changed = True

    if changed:
        save_predictions()


# =====================================================================
# FINALIZE PENDING GAMES (TRIGGERS)
# =====================================================================

def finalize_pending_games():
    now = time.time()
    ready = []

    for game_number, info in list(pending_games.items()):
        first_seen = info.get("first_seen", now)

        if now - first_seen >= FINALIZE_WAIT_SECONDS:
            ready.append(game_number)

    for game_number in ready:
        info = pending_games.pop(game_number, None)
        if not info:
            continue

        text = info.get("text", "")
        game = parse_game_message(text)

        if not game:
            print(
                f"⚠️ #N{game_number} не удалось разобрать "
                f"после 30 секунд",
                flush=True,
            )
            continue

        games_cache[game_number] = game
        log_game(game, source="trigger")
        create_prediction(game)


# =====================================================================
# FINALIZE PENDING GAMES (CHECK)
# =====================================================================

def finalize_pending_games_stat():
    now = time.time()
    ready = []

    for game_number, info in list(pending_games_stat.items()):
        first_seen = info.get("first_seen", now)

        if now - first_seen >= FINALIZE_WAIT_SECONDS:
            ready.append(game_number)

    for game_number in ready:
        info = pending_games_stat.pop(game_number, None)
        if not info:
            continue

        text = info.get("text", "")
        game = parse_game_message(text)

        if not game:
            print(
                f"⚠️ [STAT] #N{game_number} не удалось разобрать "
                f"после 30 секунд",
                flush=True,
            )
            continue

        games_cache_stat[game_number] = game
        log_game(game, source="check")


# =====================================================================
# TELEGRAM UPDATES
# =====================================================================

def process_telegram_updates(offset):
    try:
        response = SESSION.get(
            f"{TELEGRAM_API}/getUpdates",
            params={
                "offset": offset,
                "timeout": 3,
                "limit": 50,
                "allowed_updates": json.dumps([
                    "channel_post",
                    "edited_channel_post",
                ]),
            },
            timeout=10,
        )

        data = response.json()

        if not data.get("ok"):
            print(f"❌ Telegram getUpdates: {data}", flush=True)
            return offset

        updates = data.get("result", [])

        for update in updates:
            update_id = update.get("update_id")

            if update_id is not None:
                offset = update_id + 1
                save_offset(offset)

            post = (
                update.get("channel_post")
                or update.get("edited_channel_post")
            )

            if not post:
                continue

            chat = post.get("chat", {})
            chat_id = str(chat.get("id", ""))

            is_trigger_channel = (chat_id == str(CHANNEL_STATS))
            is_check_channel = (chat_id == str(CHANNEL_STAT))

            if not is_trigger_channel and not is_check_channel:
                continue

            text = post.get("text", "")
            if not text:
                continue

            number_match = re.search(r"#N(\d+)", text)
            if not number_match:
                continue

            game_number = int(number_match.group(1))
            game = parse_game_message(text)

            if is_trigger_channel:
                if game_number in pending_games:
                    pending_games[game_number]["text"] = text
                    print(
                        f"🔄 [TRIG] Обновлена #N{game_number}",
                        flush=True,
                    )
                    continue

                if game_number in games_cache:
                    if game:
                        games_cache[game_number] = game
                        print(
                            f"🔄 [TRIG] Обновлена завершённая "
                            f"#N{game_number}",
                            flush=True,
                        )
                    continue

                if re.search(r"[✅🔰]", text):
                    if game_number in processed_triggers:
                        continue

                    pending_games[game_number] = {
                        "first_seen": time.time(),
                        "text": text,
                    }

                    print("", flush=True)
                    print(
                        f"👀 [TRIG] НОВАЯ ИГРА #N{game_number}",
                        flush=True,
                    )
                    print(
                        f"⏳ Ждём {FINALIZE_WAIT_SECONDS} секунд",
                        flush=True,
                    )

                continue

            if is_check_channel:
                if game_number in pending_games_stat:
                    pending_games_stat[game_number]["text"] = text
                    print(
                        f"🔄 [STAT] Обновлена #N{game_number}",
                        flush=True,
                    )
                    continue

                if game_number in games_cache_stat:
                    if game:
                        games_cache_stat[game_number] = game
                        print(
                            f"🔄 [STAT] Обновлена завершённая "
                            f"#N{game_number}",
                            flush=True,
                        )
                    continue

                if re.search(r"[✅🔰]", text):
                    pending_games_stat[game_number] = {
                        "first_seen": time.time(),
                        "text": text,
                    }

                    print("", flush=True)
                    print(
                        f"👀 [STAT] НОВАЯ ИГРА #N{game_number}",
                        flush=True,
                    )
                    print(
                        f"⏳ Ждём {FINALIZE_WAIT_SECONDS} секунд",
                        flush=True,
                    )

                continue

    except Exception as e:
        print(f"⚠️ Updates error: {e}", flush=True)

    return offset


# =====================================================================
# CLEANUP
# =====================================================================

def cleanup_games_cache():
    if len(games_cache) <= 100:
        return

    numbers = sorted(games_cache.keys())
    keep = set(numbers[-100:])

    for number in list(games_cache.keys()):
        if number not in keep:
            del games_cache[number]


def cleanup_games_cache_stat():
    if len(games_cache_stat) <= 200:
        return

    numbers = sorted(games_cache_stat.keys())
    keep = set(numbers[-200:])

    for number in list(games_cache_stat.keys()):
        if number not in keep:
            del games_cache_stat[number]


def cleanup_predictions():
    global predictions

    if len(predictions) > 1000:
        predictions = predictions[-1000:]
        save_predictions()


# =====================================================================
# MAIN
# =====================================================================

def main():
    global telegram_offset

    print("", flush=True)
    print("==================================================", flush=True)
    print("🚀 CYBER 21 — PREMIUM CARD FORECAST", flush=True)
    print("==================================================", flush=True)
    print("📡 Триггеры: CHANNEL_STATS", flush=True)
    print("📡 Проверка: CHANNEL_STAT", flush=True)
    print(f"⏳ Финализация: {FINALIZE_WAIT_SECONDS} сек", flush=True)
    print(
        "🧠 Алгоритм: высший ранг + последняя 10 → точная карта",
        flush=True,
    )
    print(
        "😴 Сон: НЕТ (проверка круглосуточно)",
        flush=True,
    )
    print(
        f"📤 Отправка пачки: {SEND_HOUR:02d}:{SEND_MINUTE:02d} МСК",
        flush=True,
    )
    print(
        f"🔄 Догонов: {DOGON_GAMES} "
        f"(0, 1, 2, ..., {DOGON_GAMES})",
        flush=True,
    )
    print(
        f"⏱ Таймаут прогноза: {PREDICTION_TIMEOUT_HOURS} ч",
        flush=True,
    )
    print(
        "🎯 Прогноз: ранг + 2 масти, проверка у игрока И у дилера",
        flush=True,
    )
    print("==================================================", flush=True)

    load_predictions()
    load_queued()
    telegram_offset = load_offset()

    print(f"📌 Telegram offset: {telegram_offset}", flush=True)
    print(f"📊 Загружено прогнозов: {len(predictions)}", flush=True)
    print(f"📥 В очереди: {len(queued_predictions)}", flush=True)
    print("==================================================", flush=True)

    while True:
        try:
            if should_send_now():
                send_queued_predictions()
                mark_send_done()

            telegram_offset = process_telegram_updates(telegram_offset)

            finalize_pending_games()
            finalize_pending_games_stat()

            check_predictions()

            cleanup_games_cache()
            cleanup_games_cache_stat()
            cleanup_predictions()

            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            print("\n🛑 Бот остановлен", flush=True)
            break

        except Exception as e:
            print(f"❌ Критическая ошибка: {e}", flush=True)
            time.sleep(3)


# =====================================================================
# START
# =====================================================================

if __name__ == "__main__":
    main()