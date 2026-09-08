import os
import sys
import json
import time
import requests
import re
from datetime import datetime, timedelta

import pytz


# ==================================================
# ENV
# ==================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    BOT_TOKEN = os.getenv("BOT_TOKEN_PROGNOZ")

CHANNEL_PROGNOZ = os.getenv("CHAT_ID_21")
if not CHANNEL_PROGNOZ:
    CHANNEL_PROGNOZ = os.getenv("CHANNEL_PROGNOZ")

CHANNEL_STATISTICS = os.getenv("CHANNEL_STATISTICS")
if not CHANNEL_STATISTICS:
    CHANNEL_STATISTICS = os.getenv("CHAT_ID_STATISTICS")

if not CHANNEL_STATISTICS:
    CHANNEL_STATISTICS = os.getenv("CHANNEL_STAT")

if not BOT_TOKEN:
    print("❌ BOT_TOKEN не задан!", flush=True)
    sys.exit(1)

if not CHANNEL_PROGNOZ:
    print("❌ CHANNEL_PROGNOZ не задан!", flush=True)
    sys.exit(1)

if not CHANNEL_STATISTICS:
    print("❌ CHANNEL_STATISTICS не задан!", flush=True)
    sys.exit(1)


# ==================================================
# CONFIG
# ==================================================

MOSCOW_TZ = pytz.timezone("Europe/Moscow")

DOGON_GAMES = 4
MAX_PREDICTIONS = 1000

BASE_URL = "https://1xlite-36553.pro"
LEAGUE_ID = 1643503

POLL_INTERVAL = 2.0

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ==================================================
# HTTP
# ==================================================

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": f"{BASE_URL}/ru/live/twentyone/1643503-twentyone-game"
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ==================================================
# GLOBALS
# ==================================================

predictions = []
processed_games = set()
active_games_cache = {}
telegram_update_offset = 0
statistics_games = {}


# ==================================================
# JSON
# ==================================================

PREDICTIONS_FILE = "twentyone_predictions.json"
OFFSET_FILE = "telegram_updates_offset.json"


def load_json_file(filename, default):
    try:
        if not os.path.exists(filename):
            return default
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ Ошибка чтения {filename}: {e}", flush=True)
        return default


def atomic_save_json(filename, data):
    tmp = filename + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, filename)
        return True
    except Exception as e:
        print(f"⚠️ Ошибка сохранения {filename}: {e}", flush=True)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False


def load_predictions():
    data = load_json_file(PREDICTIONS_FILE, [])
    return data if isinstance(data, list) else []


def save_predictions():
    global predictions
    if len(predictions) > MAX_PREDICTIONS:
        predictions = predictions[-MAX_PREDICTIONS:]
    atomic_save_json(PREDICTIONS_FILE, predictions)


def load_telegram_offset():
    data = load_json_file(OFFSET_FILE, {})
    try:
        return int(data.get("offset", 0))
    except Exception:
        return 0


def save_telegram_offset(offset):
    atomic_save_json(OFFSET_FILE, {"offset": int(offset)})


# ==================================================
# CARD HELPERS
# ==================================================

def normalize_suit(suit):
    if not suit:
        return None
    suit = str(suit).replace("\ufe0f", "")
    return suit if suit in "♠♣♦♥" else None


def normalize_rank(rank):
    if not rank:
        return None
    rank = str(rank).strip().upper().replace("А", "A")
    return rank if rank in {"2","3","4","5","6","7","8","9","10","J","Q","K","A"} else None


def normalize_card_string(card):
    if not card:
        return None
    card = str(card).replace("\ufe0f", "")
    match = re.search(r"(10|[2-9AJQKА])([♠♣♦♥])", card)
    if not match:
        return None
    rank = normalize_rank(match.group(1))
    suit = normalize_suit(match.group(2))
    return f"{rank}{suit}" if rank and suit else None


def get_opposite_suit(suit):
    suit_map = {
        "♠": "♣",
        "♣": "♠",
        "♦": "♥",
        "♥": "♦"
    }
    return suit_map.get(suit, suit)


def get_game_number_fallback():
    now = datetime.now(MOSCOW_TZ)
    start = now.replace(hour=3, minute=0, second=0, microsecond=0)
    if now < start:
        start -= timedelta(days=1)
    return int((now - start).total_seconds() / 60) % 1440 + 1


# ==================================================
# API - GET ACTIVE GAMES
# ==================================================

def get_active_games():
    try:
        url = (
            f"{BASE_URL}/service-api/main-live-feed/v3/games1x2"
            "?cfView=3&count=40&fcountry=190&gr=415&grMode=4&lng=ru&ref=7&selectedMs=10.146.1643503"
        )
        response = SESSION.get(url, timeout=10)
        if response.status_code != 200:
            return []
        data = response.json()
        if isinstance(data, list):
            games = data
        elif isinstance(data, dict) and isinstance(data.get("Value"), list):
            games = data.get("Value", [])
        else:
            return []
        result = []
        for game in games:
            if not isinstance(game, dict):
                continue
            league = game.get("liga", {})
            league_id = league.get("id") if isinstance(league, dict) else None
            if league_id == LEAGUE_ID:
                game_id = game.get("id")
                if game_id:
                    result.append(game)
        return result
    except Exception as e:
        print(f"❌ Ошибка получения игр: {e}", flush=True)
        return []


# ==================================================
# API - GET GAME DATA
# ==================================================

def get_game_data(game_id):
    url = f"{BASE_URL}/service-api/LiveFeed/GetGameZip"
    params = {
        "id": game_id,
        "isSubGames": "true",
        "GroupEvents": "true",
        "countevents": 250,
        "grMode": 4,
        "partner": 7,
        "topGroups": "",
        "country": 190,
        "marketType": 1,
        "isNewBuilder": "true"
    }
    try:
        response = SESSION.get(url, params=params, timeout=8)
        if response.status_code != 200:
            return None
        return response.json()
    except Exception as e:
        print(f"❌ Ошибка игры {game_id}: {e}", flush=True)
        return None


# ==================================================
# PARSE CARDS & SCORE
# ==================================================

def get_cards(value_str):
    if not value_str or value_str == "[]":
        return []
    try:
        if isinstance(value_str, str):
            cards = json.loads(value_str)
        elif isinstance(value_str, list):
            cards = value_str
        else:
            return []
        suit_map = {0: "♠", 1: "♣", 2: "♦", 3: "♥"}
        rank_map = {"1": "A", "2": "2", "3": "3", "4": "4", "5": "5", "6": "6", 
                    "7": "7", "8": "8", "9": "9", "10": "10", "11": "J", "12": "Q", "13": "K", "14": "A"}
        result = []
        for card in cards:
            if not isinstance(card, dict):
                continue
            cs = card.get("CS")
            cv = card.get("CV")
            try:
                cv = int(cv)
            except Exception:
                pass
            rank = rank_map.get(str(cv), str(cv))
            suit = suit_map.get(cs)
            if rank and suit:
                result.append(f"{rank}{suit}")
        return result
    except Exception:
        return []


def calculate_score(cards):
    if not cards:
        return 0
    if len(cards) == 2 and all(c.startswith("A") for c in cards):
        return 21
    score = 0
    for card in cards:
        if not card:
            continue
        if card.startswith("10"):
            score += 10
        elif card.startswith("2"):
            score += 2
        elif card.startswith("3"):
            score += 3
        elif card.startswith("4"):
            score += 4
        elif card.startswith("5"):
            score += 5
        elif card.startswith("6"):
            score += 6
        elif card.startswith("7"):
            score += 7
        elif card.startswith("8"):
            score += 8
        elif card.startswith("9"):
            score += 9
        elif card.startswith("J"):
            score += 2
        elif card.startswith("Q"):
            score += 3
        elif card.startswith("K"):
            score += 4
        elif card.startswith("A"):
            score += 11
    return score


# ==================================================
# PARSE API GAME
# ==================================================

def parse_api_game(game_id, data):
    if not isinstance(data, dict):
        return None
    value = data.get("Value")
    if not isinstance(value, dict):
        return None
    sc = value.get("SC", {})
    if not isinstance(sc, dict):
        return None
    player_cards = []
    dealer_cards = []
    state = None
    for item in sc.get("S", []):
        if not isinstance(item, dict):
            continue
        key = item.get("Key")
        item_value = item.get("Value", "[]")
        if key == "P1":
            player_cards = get_cards(item_value)
        elif key == "P2":
            dealer_cards = get_cards(item_value)
        elif key == "STATE":
            state = str(item_value)
    if not player_cards:
        return None
    raw_game_num = value.get("DI") or value.get("TN")
    if raw_game_num:
        match = re.search(r"\d+", str(raw_game_num))
        game_number = int(match.group()) if match else get_game_number_fallback()
    else:
        game_number = get_game_number_fallback()
    return {
        "game_id": str(game_id),
        "game_number": game_number,
        "player_cards": player_cards,
        "dealer_cards": dealer_cards,
        "player_score": calculate_score(player_cards),
        "dealer_score": calculate_score(dealer_cards),
        "state": state,
        "updated_at": datetime.now(MOSCOW_TZ).isoformat()
    }


# ==================================================
# GAME FINISHED
# ==================================================

def is_game_finished(state, player_cards, dealer_cards, p_score, d_score):
    if len(player_cards) == 2 and p_score == 21:
        return True
    if dealer_cards and len(dealer_cards) == 2 and d_score == 21:
        return True
    if state == "5":
        return True
    if state == "4":
        if p_score == 21:
            return True
        if dealer_cards and d_score in (20, 21):
            return True
        return False
    if p_score > 21:
        return True
    if dealer_cards and d_score > 21:
        return True
    if len(player_cards) >= 5:
        return True
    if dealer_cards and len(dealer_cards) >= 5:
        return True
    return False


# ==================================================
# TRIGGER LOGIC
# ==================================================

def build_trigger_predictions(game):
    if not game:
        return []
    
    player_cards = game.get("player_cards", [])
    dealer_cards = game.get("dealer_cards", [])
    
    if not player_cards or not dealer_cards:
        return []
    
    # НЕ ДАЁМ ПРОГНОЗ ЕСЛИ 21 ИЛИ НИЧЬЯ
    if game.get("player_score") == 21:
        return []
    if game.get("dealer_score") == 21:
        return []
    if game.get("player_score") == game.get("dealer_score"):
        return []
    
    player_first = normalize_card_string(player_cards[0])
    dealer_first = normalize_card_string(dealer_cards[0])
    
    if not player_first or not dealer_first:
        return []
    
    player_match = re.match(r"(10|[2-9AJQK])([♠♣♦♥])", player_first)
    dealer_match = re.match(r"(10|[2-9AJQK])([♠♣♦♥])", dealer_first)
    
    if not player_match or not dealer_match:
        return []
    
    player_rank = player_match.group(1)
    player_suit = player_match.group(2)
    dealer_rank = dealer_match.group(1)
    
    allowed_digits = {"6", "7", "8", "9"}
    if player_rank not in allowed_digits or dealer_rank not in allowed_digits:
        return []
    
    rank_map = {"6": "J", "7": "Q", "8": "K", "9": "A"}
    predicted_rank = rank_map.get(dealer_rank)
    
    if not predicted_rank:
        return []
    
    try:
        source_number = int(game.get("game_number"))
        offset = int(dealer_rank)
    except Exception:
        return []
    
    target_number = source_number + offset
    
    return [{
        "prediction_type": "dealer_digit",
        "offset": offset,
        "target_number": target_number,
        "predicted_card": f"{predicted_rank}{player_suit}",
        "source_player_rank": player_rank,
        "source_player_suit": player_suit,
        "source_dealer_rank": dealer_rank,
        "predicted_rank": predicted_rank
    }]


def prediction_exists(source_game_id, target_number):
    for entry in predictions:
        if str(entry.get("source_game_id")) == str(source_game_id) and int(entry.get("target_number", 0)) == int(target_number):
            return True
    return False


# ==================================================
# TELEGRAM
# ==================================================

def telegram_send(text):
    try:
        response = SESSION.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": CHANNEL_PROGNOZ, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=10
        )
        data = response.json()
        if data.get("ok"):
            return data["result"].get("message_id")
        print(f"❌ Telegram send: {data}", flush=True)
    except Exception as e:
        print(f"❌ Telegram send error: {e}", flush=True)
    return None


def telegram_edit(message_id, text):
    if not message_id:
        return False
    try:
        response = SESSION.post(
            f"{TELEGRAM_API}/editMessageText",
            json={"chat_id": CHANNEL_PROGNOZ, "message_id": message_id, "text": text, "parse_mode": "HTML"},
            timeout=10
        )
        return bool(response.json().get("ok"))
    except Exception as e:
        print(f"❌ Telegram edit error: {e}", flush=True)
        return False


# ==================================================
# MAKE PREDICTION MESSAGE
# ==================================================

def make_prediction_message(entry):
    predicted_card = entry.get("predicted_card", "")
    
    card_match = re.match(r"(10|[2-9AJQK])([♠♣♦♥])", predicted_card)
    if card_match:
        rank = card_match.group(1)
        suit = card_match.group(2)
        opposite_suit = get_opposite_suit(suit)
        opposite_card = f"{rank}{opposite_suit}"
        display_cards = f"{predicted_card} / {opposite_card}"
    else:
        display_cards = predicted_card
    
    return f"🎯 <b>Игра: #N{entry['target_number']}</b>\n\n🃏 <b>{display_cards}</b>"


def create_trigger_predictions(game):
    global predictions
    
    patterns = build_trigger_predictions(game)
    if not patterns:
        return
    
    source_number = int(game.get("game_number"))
    source_game_id = str(game.get("game_id"))
    
    print(f"🔥 Триггер: #N{source_number} -> #N{patterns[0]['target_number']} | {patterns[0]['predicted_card']}", flush=True)
    
    for pattern in patterns:
        entry = {
            "source_number": source_number,
            "source_game_id": source_game_id,
            "player_score": game.get("player_score"),
            "dealer_score": game.get("dealer_score"),
            "player_cards": game.get("player_cards", []),
            "dealer_cards": game.get("dealer_cards", []),
            "source_player_card": game.get("player_cards", ["?"])[0],
            "source_dealer_card": game.get("dealer_cards", ["?"])[0],
            "source_player_rank": pattern["source_player_rank"],
            "source_player_suit": pattern["source_player_suit"],
            "source_dealer_rank": pattern["source_dealer_rank"],
            "predicted_rank": pattern["predicted_rank"],
            "prediction_type": pattern["prediction_type"],
            "offset": pattern["offset"],
            "target_number": pattern["target_number"],
            "predicted_card": pattern["predicted_card"],
            "status": "pending",
            "message_id": None,
            "original_text": "",
            "result_game": None,
            "found_card": None,
            "current_dogon": 0,
            "created_at": datetime.now(MOSCOW_TZ).isoformat()
        }
        
        if prediction_exists(source_game_id, entry["target_number"]):
            continue
        
        message = make_prediction_message(entry)
        entry["original_text"] = message
        message_id = telegram_send(message)
        if message_id:
            entry["message_id"] = message_id
        
        # ВСЕГДА ДОБАВЛЯЕМ В СПИСОК
        predictions.append(entry)
        print(f"🔮 Прогноз: #N{entry['target_number']} -> {entry['predicted_card']}", flush=True)
        
        # ПРОВЕРЯЕМ СРАЗУ, ЕСЛИ ИГРА УЖЕ ЕСТЬ В СТАТИСТИКЕ
        target_number = entry["target_number"]
        if int(target_number) in statistics_games:
            print(f"🔍 Игра #N{target_number} уже есть в статистике, проверяем...", flush=True)
            check_single_prediction(entry)
    
    save_predictions()


# ==================================================
# CHECK SINGLE PREDICTION
# ==================================================

def check_single_prediction(entry):
    """Проверяет один прогноз по уже имеющейся статистике"""
    if entry.get("status") != "pending":
        return
    
    target = entry.get("target_number")
    predicted_card = entry.get("predicted_card")
    
    if not target or not predicted_card:
        return
    
    card_match = re.match(r"(10|[2-9AJQK])([♠♣♦♥])", predicted_card)
    if not card_match:
        return
    
    predicted_rank = card_match.group(1)
    predicted_suit = card_match.group(2)
    opposite_suit = get_opposite_suit(predicted_suit)
    opposite_card = f"{predicted_rank}{opposite_suit}"
    
    found = None
    all_available = True
    
    for dogon in range(DOGON_GAMES + 1):
        check_number = int(target) + dogon
        game = statistics_games.get(int(check_number))
        
        if not game:
            all_available = False
            break
        
        actual_cards = game.get("player_cards", []) + game.get("dealer_cards", [])
        
        if predicted_card in actual_cards or opposite_card in actual_cards:
            found_card = predicted_card if predicted_card in actual_cards else opposite_card
            found = {
                "num": check_number,
                "dogon": dogon,
                "card": found_card,
                "is_opposite": found_card == opposite_card
            }
            break
    
    if found:
        entry["status"] = "win"
        entry["result_game"] = found["num"]
        entry["found_card"] = found["card"]
        entry["current_dogon"] = found["dogon"]
        print(f"✅ Прогноз #N{target} ЗАШЁЛ на #N{found['num']} (догон {found['dogon']})", flush=True)
        if found.get("is_opposite"):
            print(f"🔄 Выпала противоположная масть: {found['card']}", flush=True)
        update_prediction_status(entry, True, found)
    elif all_available:
        entry["status"] = "lose"
        entry["current_dogon"] = DOGON_GAMES
        print(f"❌ Прогноз #N{target} НЕ ЗАШЁЛ", flush=True)
        update_prediction_status(entry, False)
    else:
        print(f"⏳ Прогноз #N{target} ожидает статистики", flush=True)
    
    save_predictions()


# ==================================================
# PARSE STATISTICS CHANNEL
# ==================================================

def parse_statistics_message(text):
    if not text:
        return None
    
    text = str(text).replace("\ufe0f", "")
    
    number_match = re.search(r"#N\s*(\d+)", text, re.IGNORECASE)
    if not number_match:
        return None
    
    game_number = int(number_match.group(1))
    
    pattern = r"(?:✅)?\s*(\d+)\s*\(([^)]+)\)"
    matches = re.findall(pattern, text)
    
    if len(matches) < 2:
        return None
    
    player_cards_str = matches[0][1]
    dealer_cards_str = matches[1][1]
    
    player_cards = []
    card_pattern = r"(10|[2-9AJQK])([♠♣♦♥])"
    
    for rank, suit in re.findall(card_pattern, player_cards_str):
        rank = normalize_rank(rank)
        suit = normalize_suit(suit)
        if rank and suit:
            player_cards.append(f"{rank}{suit}")
    
    dealer_cards = []
    for rank, suit in re.findall(card_pattern, dealer_cards_str):
        rank = normalize_rank(rank)
        suit = normalize_suit(suit)
        if rank and suit:
            dealer_cards.append(f"{rank}{suit}")
    
    if not player_cards and not dealer_cards:
        return None
    
    return {
        "game_number": game_number,
        "player_cards": player_cards,
        "dealer_cards": dealer_cards,
        "raw_text": text
    }


# ==================================================
# LOAD RECENT STATISTICS (ПРИ ЗАПУСКЕ)
# ==================================================

def load_recent_statistics():
    """Загружает последние 50 игр из канала статистики при запуске"""
    global statistics_games
    
    try:
        print("📡 Загрузка последних игр из канала статистики...", flush=True)
        
        params = {
            "limit": 50,
            "allowed_updates": json.dumps(["channel_post", "edited_channel_post"])
        }
        
        response = SESSION.get(f"{TELEGRAM_API}/getUpdates", params=params, timeout=10)
        data = response.json()
        
        if not data.get("ok"):
            print(f"❌ Ошибка getUpdates: {data}", flush=True)
            return
        
        count = 0
        for update in data.get("result", []):
            message = update.get("channel_post") or update.get("edited_channel_post")
            if not message:
                continue
            
            chat_id = str(message.get("chat", {}).get("id", ""))
            if chat_id != str(CHANNEL_STATISTICS):
                continue
            
            text = message.get("text") or message.get("caption")
            if not text:
                continue
            
            parsed = parse_statistics_message(text)
            if not parsed:
                continue
            
            game_number = parsed["game_number"]
            statistics_games[int(game_number)] = parsed
            count += 1
        
        print(f"📊 Загружено игр из статистики: {count}", flush=True)
        
    except Exception as e:
        print(f"⚠️ Ошибка загрузки статистики: {e}", flush=True)


# ==================================================
# FETCH STATISTICS FROM CHANNEL
# ==================================================

def fetch_statistics_channel():
    global telegram_update_offset, statistics_games
    
    try:
        params = {"timeout": 2, "allowed_updates": json.dumps(["channel_post", "edited_channel_post"])}
        if telegram_update_offset > 0:
            params["offset"] = telegram_update_offset
        
        response = SESSION.get(f"{TELEGRAM_API}/getUpdates", params=params, timeout=5)
        data = response.json()
        
        if not data.get("ok"):
            return
        
        updates = data.get("result", [])
        if not updates:
            save_telegram_offset(telegram_update_offset)
            return
        
        for update in updates:
            try:
                update_id = update.get("update_id")
                if update_id is not None:
                    telegram_update_offset = int(update_id) + 1
                
                message = update.get("channel_post") or update.get("edited_channel_post")
                if not message:
                    continue
                
                chat_id = str(message.get("chat", {}).get("id", ""))
                if chat_id != str(CHANNEL_STATISTICS):
                    continue
                
                text = message.get("text") or message.get("caption")
                if not text:
                    continue
                
                parsed = parse_statistics_message(text)
                if not parsed:
                    continue
                
                game_number = parsed["game_number"]
                statistics_games[int(game_number)] = parsed
                
                print(f"📊 Статистика: #N{game_number}", flush=True)
                
                # ===== СРАЗУ ПРОВЕРЯЕМ ПРОГНОЗЫ =====
                check_predictions()
                
            except Exception as e:
                print(f"⚠️ Ошибка обработки: {e}", flush=True)
        
        save_telegram_offset(telegram_update_offset)
        
    except Exception as e:
        print(f"❌ Ошибка канала статистики: {e}", flush=True)


# ==================================================
# UPDATE PREDICTION STATUS
# ==================================================

def update_prediction_status(entry, success, found=None):
    message_id = entry.get("message_id")
    original_text = entry.get("original_text", "")
    if not message_id or not original_text:
        return
    
    lines = original_text.split("\n")
    target = entry.get("target_number")
    
    if success:
        lines[0] = f"🎯 <b>Игра: #N{target} ✅</b>"
        if found and found.get("is_opposite"):
            lines.append("")
            lines.append(f"✅ ЗАШЛО: #N{found['num']}")
            lines.append(f"🃏 Выпало: {found['card']} 🔄")
            lines.append(f"🔁 Догон: {found['dogon']}")
            lines.append("⚡ Противоположная масть")
        else:
            lines.append("")
            lines.append(f"✅ ЗАШЛО: #N{found['num']}")
            lines.append(f"🃏 Выпало: {found['card']}")
            lines.append(f"🔁 Догон: {found['dogon']}")
    else:
        lines[0] = f"🎯 <b>Игра: #N{target} ❌</b>"
        lines.append("")
        lines.append(f"❌ Не зашло за {DOGON_GAMES + 1} игр")
    
    telegram_edit(message_id, "\n".join(lines))


# ==================================================
# CHECK PREDICTIONS (ОСНОВНАЯ ПРОВЕРКА)
# ==================================================

def check_predictions():
    global predictions
    changed = False
    
    for entry in predictions:
        if entry.get("status") != "pending":
            continue
        
        target = entry.get("target_number")
        predicted_card = entry.get("predicted_card")
        
        if not target or not predicted_card:
            continue
        
        card_match = re.match(r"(10|[2-9AJQK])([♠♣♦♥])", predicted_card)
        if not card_match:
            continue
        
        predicted_rank = card_match.group(1)
        predicted_suit = card_match.group(2)
        opposite_suit = get_opposite_suit(predicted_suit)
        opposite_card = f"{predicted_rank}{opposite_suit}"
        
        found = None
        all_available = True
        
        for dogon in range(DOGON_GAMES + 1):
            check_number = int(target) + dogon
            game = statistics_games.get(int(check_number))
            
            if not game:
                all_available = False
                break
            
            actual_cards = game.get("player_cards", []) + game.get("dealer_cards", [])
            
            if predicted_card in actual_cards or opposite_card in actual_cards:
                found_card = predicted_card if predicted_card in actual_cards else opposite_card
                found = {
                    "num": check_number,
                    "dogon": dogon,
                    "card": found_card,
                    "is_opposite": found_card == opposite_card
                }
                break
        
        if found:
            entry["status"] = "win"
            entry["result_game"] = found["num"]
            entry["found_card"] = found["card"]
            entry["current_dogon"] = found["dogon"]
            changed = True
            print(f"✅ Прогноз #N{target} ЗАШЁЛ на #N{found['num']} (догон {found['dogon']})", flush=True)
            if found.get("is_opposite"):
                print(f"🔄 Выпала противоположная масть: {found['card']}", flush=True)
            update_prediction_status(entry, True, found)
            continue
        
        if not all_available:
            continue
        
        entry["status"] = "lose"
        entry["current_dogon"] = DOGON_GAMES
        changed = True
        print(f"❌ Прогноз #N{target} НЕ ЗАШЁЛ", flush=True)
        update_prediction_status(entry, False)
    
    if changed:
        save_predictions()


# ==================================================
# PROCESS ACTIVE GAMES
# ==================================================

def update_active_game(game_id):
    data = get_game_data(game_id)
    if not data:
        return None
    parsed = parse_api_game(game_id, data)
    if parsed:
        active_games_cache[str(game_id)] = parsed
    return parsed


def process_active_games():
    global processed_games
    active_games = get_active_games()
    if not active_games:
        return
    current_ids = set()
    for game_info in active_games:
        game_id = str(game_info.get("id"))
        if not game_id or game_id in processed_games:
            continue
        current_ids.add(game_id)
        parsed = update_active_game(game_id)
        if not parsed:
            continue
        if is_game_finished(
            parsed.get("state"),
            parsed.get("player_cards", []),
            parsed.get("dealer_cards", []),
            parsed.get("player_score", 0),
            parsed.get("dealer_score", 0)
        ):
            print(f"💾 Игра завершена: #N{parsed['game_number']} | {parsed['player_cards']} vs {parsed['dealer_cards']}", flush=True)
            create_trigger_predictions(parsed)
            processed_games.add(game_id)
            active_games_cache.pop(game_id, None)
    for game_id in list(active_games_cache.keys()):
        if game_id in processed_games or game_id in current_ids:
            continue
        parsed = update_active_game(game_id)
        if not parsed:
            continue
        if is_game_finished(
            parsed.get("state"),
            parsed.get("player_cards", []),
            parsed.get("dealer_cards", []),
            parsed.get("player_score", 0),
            parsed.get("dealer_score", 0)
        ):
            print(f"💾 Игра завершена: #N{parsed['game_number']} | {parsed['player_cards']} vs {parsed['dealer_cards']}", flush=True)
            create_trigger_predictions(parsed)
            processed_games.add(game_id)
            active_games_cache.pop(game_id, None)


# ==================================================
# CLEANUP
# ==================================================

def cleanup():
    global predictions, processed_games, statistics_games
    if len(predictions) > MAX_PREDICTIONS:
        predictions = predictions[-MAX_PREDICTIONS:]
        save_predictions()
    if len(processed_games) > 2000:
        processed_games = set(list(processed_games)[-1000:])
    if len(statistics_games) > 500:
        keys = sorted(statistics_games.keys())[:-300]
        for key in keys:
            statistics_games.pop(key, None)


# ==================================================
# MAIN
# ==================================================

def main():
    global predictions, telegram_update_offset
    
    print("🚀 БОТ ЗАПУЩЕН", flush=True)
    print(f"📊 Канал статистики: {CHANNEL_STATISTICS}", flush=True)
    print(f"🎯 Канал прогнозов: {CHANNEL_PROGNOZ}", flush=True)
    print("=" * 50, flush=True)
    
    predictions = load_predictions()
    telegram_update_offset = load_telegram_offset()
    
    print(f"🔮 Загружено прогнозов: {len(predictions)}", flush=True)
    
    # ===== ЗАГРУЖАЕМ СТАТИСТИКУ ПРИ ЗАПУСКЕ =====
    load_recent_statistics()
    
    # ===== ПРОВЕРЯЕМ ВСЕ ПРОГНОЗЫ =====
    check_predictions()
    
    print("🤖 Бот работает...", flush=True)
    
    while True:
        started = time.time()
        try:
            fetch_statistics_channel()
            process_active_games()
            cleanup()
            sleep_time = max(0.2, POLL_INTERVAL - (time.time() - started))
            time.sleep(sleep_time)
        except KeyboardInterrupt:
            print("\n🛑 Бот остановлен", flush=True)
            break
        except Exception as e:
            print(f"❌ Ошибка: {e}", flush=True)
            import traceback
            traceback.print_exc()
            time.sleep(3)


if __name__ == "__main__":
    main()