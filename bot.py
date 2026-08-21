import os
import requests
import json
import re
import time
from datetime import datetime, timedelta
import pytz
import sys

# =====================================================================
# ПРИНУДИТЕЛЬНЫЙ ВЫВОД ЛОГОВ
# =====================================================================
sys.stdout.flush()
print("=" * 60, flush=True)
print("🔮 БОТ-ПРОГНОЗИСТ 21 CLASSICS", flush=True)
print("=" * 60, flush=True)

# =====================================================================
# ПЕРЕМЕННЫЕ ИЗ ОКРУЖЕНИЯ
# =====================================================================
BOT_TOKEN = os.getenv('BOT_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')  # Канал с играми
PREDICT_CHAT_ID = os.getenv('PREDICT_CHAT_ID')  # Канал для прогнозов (может быть тот же)

if not BOT_TOKEN or not CHAT_ID:
    print("❌ Ошибка: BOT_TOKEN или CHAT_ID не найдены в переменных окружения", flush=True)
    sys.exit(1)

MOSCOW_TZ = pytz.timezone('Europe/Moscow')
API = f"https://api.telegram.org/bot{BOT_TOKEN}"

SUITS_NAMES = {0: "♠️", 1: "♣️", 2: "♦️", 3: "♥️"}
RANKS = {2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8", 9: "9", 10: "10", 11: "J", 12: "Q", 13: "K", 14: "A"}
POSITION_SUITS = {1: "♣️", 2: "♦️", 3: "♥️", 4: "♠️"}

print("✅ Настройки загружены", flush=True)

# =====================================================================
# ХРАНИЛИЩЕ ПРОГНОЗОВ
# =====================================================================
predictions = {}  # {game_number: {"suit": "♥️", "target": 594, "games": [594,595,596], "status": "waiting"}}

# =====================================================================
# ФУНКЦИИ
# =====================================================================
def get_game_number_from_message(text):
    """Извлекает номер игры из сообщения"""
    match = re.search(r'#N(\d+)', text)
    if match:
        return int(match.group(1))
    return None

def get_player_cards_from_message(text):
    """Извлекает карты игрока из сообщения (до ' - ')"""
    try:
        # Находим часть с картами игрока
        match = re.search(r'#N\d+\.\s+(\d+)\(([^)]+)\)\s+-\s+', text)
        if match:
            cards_str = match.group(2)
            # Разбиваем карты
            cards = []
            # Ищем все карты в формате: A♠️, K♦️, 10♥️ и т.д.
            card_pattern = re.compile(r'([AJQK]|10|\d+)([♠️♣️♦️♥️])')
            for match_card in card_pattern.finditer(cards_str):
                rank = match_card.group(1)
                suit = match_card.group(2)
                cards.append({"rank": rank, "suit": suit})
            return cards
    except Exception as e:
        print(f"❌ Ошибка парсинга карт: {e}", flush=True)
    return []

def find_highest_card(cards):
    """Находит самую старшую карту и её позицию"""
    if not cards:
        return None, None
    
    # Ранги для сравнения
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

def get_predicted_suit(position):
    """По позиции определяет прогнозируемую масть"""
    return POSITION_SUITS.get(position, None)

def is_game_finished(text):
    """Проверяет, завершена ли игра (есть ✅ или 🔰)"""
    return "✅" in text or "🔰" in text

def check_prediction_in_game(text, predicted_suit):
    """Проверяет, есть ли прогнозируемая масть среди карт игрока"""
    cards = get_player_cards_from_message(text)
    for card in cards:
        if card.get("suit") == predicted_suit:
            return True
    return False

def send_message(chat_id, text):
    """Отправляет сообщение в Telegram"""
    try:
        url = f"{API}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        response = requests.post(url, json=payload, timeout=5)
        if response.status_code == 200:
            return response.json().get("result", {}).get("message_id")
        else:
            print(f"⚠️ Ошибка отправки: {response.status_code}", flush=True)
    except Exception as e:
        print(f"❌ Ошибка отправки: {e}", flush=True)
    return None

def get_updates(offset=None):
    """Получает новые сообщения из канала"""
    try:
        url = f"{API}/getUpdates"
        params = {"chat_id": CHAT_ID, "timeout": 30}
        if offset:
            params["offset"] = offset
        response = requests.get(url, params=params, timeout=35)
        if response.status_code == 200:
            data = response.json()
            if data.get("ok"):
                return data.get("result", [])
    except Exception as e:
        print(f"❌ Ошибка получения обновлений: {e}", flush=True)
    return []

# =====================================================================
# ОСНОВНАЯ ЛОГИКА
# =====================================================================
def process_prediction(game_number, text):
    """Обрабатывает завершенную игру и создает прогноз"""
    if game_number in predictions:
        return
    
    cards = get_player_cards_from_message(text)
    if not cards:
        return
    
    highest_card, position = find_highest_card(cards)
    if not highest_card or not position:
        print(f"⚠️ Игра #{game_number}: неопределенность (несколько старших карт или 5+ карт)", flush=True)
        return
    
    predicted_suit = get_predicted_suit(position)
    if not predicted_suit:
        print(f"⚠️ Игра #{game_number}: позиция {position} - прогноза нет", flush=True)
        return
    
    # Создаем прогноз на 3 игры
    target_game = game_number + 1
    predictions[game_number] = {
        "suit": predicted_suit,
        "target": target_game,
        "games": [target_game, target_game + 1, target_game + 2],
        "status": "waiting",
        "result": None,
        "checked": []
    }
    
    # Отправляем прогноз
    msg = f"""🔮 ПРОГНОЗ
📊 От игры: #{game_number}
🃏 Игрок масть: {predicted_suit}
🎯 Целевая игра: #{target_game}
📈 2 игры догон"""
    
    send_message(PREDICT_CHAT_ID or CHAT_ID, msg)
    print(f"📊 Прогноз на игру #{game_number}: {predicted_suit} (позиция {position})", flush=True)

def check_predictions(game_number, text):
    """Проверяет прогнозы по завершенной игре"""
    if not is_game_finished(text):
        return
    
    # Проверяем все активные прогнозы
    for pred_game, pred_data in list(predictions.items()):
        if pred_data["status"] != "waiting":
            continue
        
        if game_number in pred_data["games"]:
            # Проверяем, есть ли нужная масть
            found = check_prediction_in_game(text, pred_data["suit"])
            pred_data["checked"].append(game_number)
            
            if found:
                # Прогноз зашел
                pred_data["status"] = "completed"
                pred_data["result"] = "win"
                
                msg = f"""🔮 ПРОГНОЗ
📊 От игры: #{pred_game}
🃏 Игрок масть: {pred_data['suit']}
🎯 Целевая игра: #{pred_data['target']}
📈 2 игры догон
✅✅✅ ПРОГНОЗ ЗАШЕЛ"""
                
                send_message(PREDICT_CHAT_ID or CHAT_ID, msg)
                print(f"✅ Прогноз #{pred_game} ЗАШЕЛ на игре #{game_number}", flush=True)
                break
            else:
                # Проверяем, все ли игры проверены
                if len(pred_data["checked"]) >= 3:
                    # Не зашло
                    pred_data["status"] = "completed"
                    pred_data["result"] = "lose"
                    
                    msg = f"""🔮 ПРОГНОЗ
📊 От игры: #{pred_game}
🃏 Игрок масть: {pred_data['suit']}
🎯 Целевая игра: #{pred_data['target']}
📈 2 игры догон
❌❌❌ ПРОГНОЗ НЕ ЗАШЕЛ"""
                    
                    send_message(PREDICT_CHAT_ID or CHAT_ID, msg)
                    print(f"❌ Прогноз #{pred_game} НЕ ЗАШЕЛ", flush=True)
                    break

# =====================================================================
# ОСНОВНОЙ ЦИКЛ
# =====================================================================
def main():
    print("🔄 БОТ-ПРОГНОЗИСТ ЗАПУЩЕН...", flush=True)
    print(f"📢 Канал: {CHAT_ID}", flush=True)
    print(f"📢 Канал прогнозов: {PREDICT_CHAT_ID or CHAT_ID}", flush=True)
    print("=" * 60, flush=True)
    
    last_update_id = 0
    processed_messages = set()
    
    while True:
        try:
            updates = get_updates(last_update_id + 1 if last_update_id else None)
            
            for update in updates:
                last_update_id = update.get("update_id", 0)
                
                # Проверяем, что сообщение из нужного канала
                message = update.get("message", {})
                chat = message.get("chat", {})
                chat_id = str(chat.get("id", ""))
                
                if chat_id != str(CHAT_ID):
                    continue
                
                text = message.get("text", "")
                if not text or not text.startswith("#N"):
                    continue
                
                # Проверяем, не обрабатывали ли уже это сообщение
                message_id = message.get("message_id", 0)
                if message_id in processed_messages:
                    continue
                processed_messages.add(message_id)
                
                game_number = get_game_number_from_message(text)
                if not game_number:
                    continue
                
                print(f"📩 Получена игра #{game_number}", flush=True)
                
                # Проверяем завершенные игры для прогнозов
                if is_game_finished(text):
                    # Создаем новый прогноз
                    process_prediction(game_number, text)
                    # Проверяем существующие прогнозы
                    check_predictions(game_number, text)
            
            # Очищаем обработанные сообщения (не больше 1000)
            if len(processed_messages) > 1000:
                processed_messages.clear()
            
            time.sleep(2)
            
        except Exception as e:
            print(f"❌ Критическая ошибка: {e}", flush=True)
            time.sleep(10)

if __name__ == "__main__":
    main()