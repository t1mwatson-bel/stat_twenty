import os
import sys
import requests
import json
import re
import time
from datetime import datetime, timedelta

# =====================================================================
# ПРИНУДИТЕЛЬНЫЙ ВЫВОД ЛОГОВ
# =====================================================================
sys.stdout.flush()
print("=" * 60, flush=True)
print("🃏 ПРОГНОЗИСТ 21 CLASSICS", flush=True)
print("=" * 60, flush=True)

# =====================================================================
# ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ
# =====================================================================
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    BOT_TOKEN = os.getenv('BOT_TOKEN_PROGNOZ')

CHANNEL_STATS = os.getenv('CHANNEL_STATS_ID')
CHANNEL_PROGNOZ = os.getenv('CHANNEL_PROGNOZ_ID')

print("🔍 ДИАГНОСТИКА ПЕРЕМЕННЫХ:", flush=True)
print(f"BOT_TOKEN: {BOT_TOKEN[:5] if BOT_TOKEN else 'НЕ ЗАДАН'}", flush=True)
print(f"CHANNEL_STATS_ID: {CHANNEL_STATS if CHANNEL_STATS else 'НЕ ЗАДАН'}", flush=True)
print(f"CHANNEL_PROGNOZ_ID: {CHANNEL_PROGNOZ if CHANNEL_PROGNOZ else 'НЕ ЗАДАН'}", flush=True)
print("=" * 60, flush=True)

if not BOT_TOKEN or not CHANNEL_STATS or not CHANNEL_PROGNOZ:
    print("❌ ОШИБКА: переменные окружения не заданы!", flush=True)
    exit(1)

print("✅ Все переменные заданы!", flush=True)

# =====================================================================
# НАСТРОЙКИ
# =====================================================================
HISTORY_FILE = "history.json"
OFFSET_FILE = "offset.txt"
MAX_HISTORY = 200
PROCESSED_GAMES = set()
LAST_PREDICT_TIME = 0
PREDICT_INTERVAL = 120  # 2 минуты

# Масти с эмодзи для 21 Classics
SUITS = {
    "♠": "♠️", "♣": "♣️", "♦": "♦️", "♥": "♥️",
    "♠️": "♠️", "♣️": "♣️", "♦️": "♦️", "♥️": "♥️"
}
POSITION_SUITS = {1: "♣️", 2: "♦️", 3: "♥️", 4: "♠️"}

# =====================================================================
# ФУНКЦИИ
# =====================================================================
def is_skip_game(text, game_data=None):
    """Проверяет, нужно ли пропустить игру"""
    if "21" in text:
        return True
    if "🔰" in text:
        return True
    return False

def get_updates(offset):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"offset": offset, "timeout": 30}
    try:
        response = requests.get(url, params=params, timeout=35)
        return response.json()
    except Exception as e:
        print(f"❌ Ошибка getUpdates: {e}", flush=True)
        return {}

def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHANNEL_PROGNOZ, "text": text, "parse_mode": "HTML"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            return response.json()["result"]["message_id"]
        else:
            print(f"❌ Ошибка отправки: {response.status_code} - {response.text}", flush=True)
            return None
    except Exception as e:
        print(f"❌ Ошибка отправки: {e}", flush=True)
        return None

def edit_message(message_id, text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    payload = {"chat_id": CHANNEL_PROGNOZ, "message_id": message_id, "text": text, "parse_mode": "HTML"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            return True
        else:
            print(f"❌ Ошибка редактирования: {response.status_code} - {response.text}", flush=True)
            return False
    except Exception as e:
        print(f"❌ Ошибка редактирования: {e}", flush=True)
        return False

def is_final_game(text):
    return "✅" in text or "🔰" in text

def parse_game(text):
    try:
        game_match = re.search(r'#N(\d+)', text)
        if not game_match:
            return None
        game_number = int(game_match.group(1))
        
        # Очищаем текст от лишних символов
        clean_text = text.replace('✅', '').replace('🔰', '').replace('▶️', '').replace('◀️', '').replace('⚠️', '')
        
        parts = clean_text.split('-')
        if len(parts) < 2:
            return None
        
        player_part = parts[0].strip()
        player_match = re.search(r'(\d+)\(([^)]+)\)', player_part)
        if not player_match:
            return None
        player_cards_str = player_match.group(2).strip()
        
        dealer_part = parts[1].strip()
        dealer_match = re.search(r'(\d+)\(([^)]+)\)', dealer_part)
        if not dealer_match:
            return None
        dealer_cards_str = dealer_match.group(2).strip() if dealer_match else ""
        
        # Парсим карты с учётом эмодзи мастей (♠️♣️♦️♥️)
        player_cards = []
        for card in re.findall(r'([AKQJ]|10|\d)([♠♣♦♥]|♠️|♣️|♦️|♥️)', player_cards_str):
            rank, suit = card
            # Нормализуем масть
            if suit in ["♠️", "♠"]:
                suit = "♠️"
            elif suit in ["♣️", "♣"]:
                suit = "♣️"
            elif suit in ["♦️", "♦"]:
                suit = "♦️"
            elif suit in ["♥️", "♥"]:
                suit = "♥️"
            player_cards.append({"rank": rank, "suit": suit})
        
        dealer_cards = []
        for card in re.findall(r'([AKQJ]|10|\d)([♠♣♦♥]|♠️|♣️|♦️|♥️)', dealer_cards_str):
            rank, suit = card
            if suit in ["♠️", "♠"]:
                suit = "♠️"
            elif suit in ["♣️", "♣"]:
                suit = "♣️"
            elif suit in ["♦️", "♦"]:
                suit = "♦️"
            elif suit in ["♥️", "♥"]:
                suit = "♥️"
            dealer_cards.append({"rank": rank, "suit": suit})
        
        return {
            "number": game_number,
            "player_cards": player_cards,
            "dealer_cards": dealer_cards,
            "text": text
        }
    except Exception as e:
        print(f"❌ Ошибка парсинга: {e}", flush=True)
        return None

def get_highest_card(cards):
    """Находит самую старшую карту и её позицию"""
    if not cards:
        return None, None
    
    rank_order = {"A": 14, "K": 13, "Q": 12, "J": 11}
    for i in range(10, 1, -1):
        rank_order[str(i)] = i
    
    highest_rank = -1
    highest_card = None
    highest_position = None
    count_highest = 0
    
    for idx, card in enumerate(cards, start=1):
        rank = card.get("rank", "")
        rank_value = rank_order.get(rank, 0)
        
        if rank_value > highest_rank:
            highest_rank = rank_value
            highest_card = card
            highest_position = idx
            count_highest = 1
        elif rank_value == highest_rank:
            count_highest += 1
    
    # Если несколько одинаковых старших карт - пропускаем
    if count_highest > 1:
        return None, None
    
    return highest_card, highest_position

def get_suit_by_position(position):
    """По позиции определяет прогнозируемую масть"""
    return POSITION_SUITS.get(position, None)

def predict(game_data):
    """Делает прогноз для 21 Classics"""
    game_num = game_data["number"]
    
    # Находим старшую карту у игрока
    player_highest, player_position = get_highest_card(game_data["player_cards"])
    if not player_highest or not player_position:
        print(f"⚠️ Игра #{game_num}: неопределенность (несколько старших карт или 5+ карт)", flush=True)
        return None
    
    predicted_suit = get_suit_by_position(player_position)
    if not predicted_suit:
        return None
    
    # Получаем ранг старшей карты
    rank = player_highest["rank"]
    
    # Формируем прогноз
    target_game = game_num + 1
    
    result = {
        "from_game": game_num,
        "target": target_game,
        "suit": predicted_suit,
        "rank": rank,
        "card": f"{rank}{predicted_suit}",
        "position": player_position,
        "games": [target_game, target_game + 1, target_game + 2]
    }
    
    print(f"🔍 Прогноз для #N{game_num}: {result}", flush=True)
    return result

def check_results(history, all_messages):
    """Проверяет результаты прогнозов"""
    for entry in history:
        if entry.get("status") != "pending":
            continue
        
        target = entry.get("target")
        predicted_suit = entry.get("suit")
        from_game = entry.get("from_game")
        message_id = entry.get("message_id")
        predicted_card = entry.get("card")
        
        if not predicted_suit or not message_id:
            continue
        
        found = False
        found_game = None
        found_dogon = None
        
        # Проверяем игры с N+1 по N+3
        for i in range(3):
            game_to_check = target + i
            
            for msg in all_messages:
                if f"#N{game_to_check}" in msg:
                    # Проверяем наличие прогнозируемой масти у игрока
                    game_data = parse_game(msg)
                    if game_data:
                        for card in game_data["player_cards"]:
                            if card.get("suit") == predicted_suit:
                                found = True
                                found_game = game_to_check
                                found_dogon = i + 1
                                break
                    if found:
                        break
            if found:
                break
        
        # Проверяем, все ли игры проверены
        all_games_present = True
        for i in range(3):
            game_to_check = target + i
            found_msg = False
            for msg in all_messages:
                if f"#N{game_to_check}" in msg:
                    found_msg = True
                    break
            if not found_msg:
                all_games_present = False
                break
        
        if not all_games_present:
            continue
        
        original_text = f"🔮 <b>ПРОГНОЗ</b>\n"
        original_text += f"📊 От игры: #N{from_game}\n"
        original_text += f"🃏 Игрок масть: {predicted_suit}\n"
        original_text += f"🎯 Целевая игра: #N{target}\n"
        original_text += f"📈 2 игры догон\n"
        original_text += f"⏰ {entry.get('time', '')[:16]}"
        
        if found:
            result_text = f"\n\n✅ <b>ЗАШЛО</b> на догоне {found_dogon}: #N{found_game}"
        else:
            result_text = f"\n\n❌ <b>НЕ ЗАШЛО</b> (2 догона проверены до #N{target+2})"
        
        edit_message(message_id, original_text + result_text)
        entry["status"] = "win" if found else "loss"

def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return []
    return []

def clean_memory(history):
    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]
    now = datetime.now()
    new_history = []
    for item in history:
        if "time" in item:
            try:
                item_time = datetime.fromisoformat(item["time"])
                if (now - item_time).days < 7:
                    new_history.append(item)
            except:
                new_history.append(item)
        else:
            new_history.append(item)
    return new_history

def get_offset():
    if os.path.exists(OFFSET_FILE):
        try:
            with open(OFFSET_FILE, "r") as f:
                return int(f.read().strip())
        except:
            return 0
    return 0

def save_offset(offset):
    with open(OFFSET_FILE, "w") as f:
        f.write(str(offset))

# =====================================================================
# ОСНОВНОЙ ЦИКЛ
# =====================================================================
def main():
    global LAST_PREDICT_TIME
    
    print("🔄 ПРОГНОЗИСТ 21 CLASSICS ЗАПУЩЕН", flush=True)
    print(f"📊 Читает канал: {CHANNEL_STATS}", flush=True)
    print(f"📤 Отправляет в: {CHANNEL_PROGNOZ}", flush=True)
    print("=" * 60, flush=True)
    print("📌 Правила прогноза:", flush=True)
    print("   - Ищем старшую карту у игрока", flush=True)
    print("   - По позиции определяем масть (1→♣️, 2→♦️, 3→♥️, 4→♠️)", flush=True)
    print("   - Если несколько старших карт - пропускаем", flush=True)
    print("   - Прогноз на 3 игры (целевая + 2 догона)", flush=True)
    print("=" * 60, flush=True)
    
    offset = get_offset()
    history = load_history()
    all_messages = []
    
    while True:
        try:
            updates = get_updates(offset)
            
            for update in updates.get("result", []):
                offset = update["update_id"] + 1
                save_offset(offset)
                
                channel_post = update.get("channel_post")
                edited_post = update.get("edited_channel_post")
                post = channel_post if channel_post else edited_post
                if not post:
                    continue
                
                chat_id = post.get("chat", {}).get("id")
                if str(chat_id) != str(CHANNEL_STATS):
                    continue
                
                text = post.get("text", "")
                if not text or "#N" not in text:
                    continue
                
                game_id_match = re.search(r'#N(\d+)', text)
                if not game_id_match:
                    continue
                game_number = int(game_id_match.group(1))
                
                all_messages.append(text)
                if len(all_messages) > 500:
                    all_messages = all_messages[-500:]
                
                if game_number in PROCESSED_GAMES:
                    continue
                
                if not is_final_game(text):
                    print(f"⏳ Ожидание финальной раздачи для #N{game_number}", flush=True)
                    continue
                
                print(f"📥 {text[:50]}...", flush=True)
                
                game_data = parse_game(text)
                if not game_data:
                    print(f"❌ Не удалось распарсить #N{game_number}", flush=True)
                    continue
                
                if is_skip_game(text, game_data):
                    reason = "21" if "21" in text else "🔰" if "🔰" in text else "фильтр"
                    print(f"⏭️ Пропускаем #N{game_number} (фильтр: {reason})", flush=True)
                    continue
                
                current_time = time.time()
                if current_time - LAST_PREDICT_TIME < PREDICT_INTERVAL:
                    print(f"⏳ Интервал: {int(current_time - LAST_PREDICT_TIME)} сек < {PREDICT_INTERVAL} сек", flush=True)
                    continue
                
                prognoz = predict(game_data)
                if prognoz:
                    msg = f"🔮 <b>ПРОГНОЗ</b>\n"
                    msg += f"📊 От игры: #N{game_data['number']}\n"
                    msg += f"🃏 Игрок масть: {prognoz['suit']}\n"
                    msg += f"🎯 Целевая игра: #N{prognoz['target']}\n"
                    msg += f"📈 2 игры догон\n"
                    msg += f"⏰ {datetime.now().strftime('%H:%M:%S')}"
                    
                    message_id = send_message(msg)
                    if message_id:
                        print(f"✅ Прогноз отправлен: #N{prognoz['target']}", flush=True)
                        LAST_PREDICT_TIME = current_time
                        PROCESSED_GAMES.add(game_number)
                        
                        history.append({
                            "from_game": game_data["number"],
                            "target": prognoz["target"],
                            "suit": prognoz["suit"],
                            "card": prognoz["card"],
                            "time": datetime.now().isoformat(),
                            "status": "pending",
                            "message_id": message_id
                        })
                        save_history(history)
            
            check_results(history, all_messages)
            history = clean_memory(history)
            save_history(history)
            
            if len(PROCESSED_GAMES) > 500:
                PROCESSED_GAMES.clear()
            
            time.sleep(5)
            
        except Exception as e:
            print(f"❌ Ошибка: {e}", flush=True)
            time.sleep(30)

if __name__ == "__main__":
    main()