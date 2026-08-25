import os
import sys
import requests
import json
import re
import time
from datetime import datetime
import pytz

# =====================================================================
# ЧАСОВОЙ ПОЯС (МОСКВА)
# =====================================================================
MOSCOW_TZ = pytz.timezone('Europe/Moscow')
sys.stdout.flush()

# =====================================================================
# ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ
# =====================================================================
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    BOT_TOKEN = os.getenv('BOT_TOKEN_PROGNOZ')

CHANNEL_STATS = os.getenv('CHANNEL_STATS_ID')
CHANNEL_PROGNOZ = os.getenv('CHANNEL_PROGNOZ_ID')

print("🔍 ДИАГНОСТИКА ПЕРЕМЕННЫХ:", flush=True)
print(f"   BOT_TOKEN: {BOT_TOKEN[:5] if BOT_TOKEN else '❌ НЕ ЗАДАН'}", flush=True)
print(f"   CHANNEL_STATS_ID: {CHANNEL_STATS if CHANNEL_STATS else '❌ НЕ ЗАДАН'}", flush=True)
print(f"   CHANNEL_PROGNOZ_ID: {CHANNEL_PROGNOZ if CHANNEL_PROGNOZ else '❌ НЕ ЗАДАН'}", flush=True)
print("", flush=True)

if not BOT_TOKEN or not CHANNEL_STATS or not CHANNEL_PROGNOZ:
    print("❌ КРИТИЧЕСКАЯ ОШИБКА: переменные окружения не заданы!", flush=True)
    exit(1)

print("✅ Все переменные успешно загружены!", flush=True)
print("", flush=True)

# =====================================================================
# НАСТРОЙКИ
# =====================================================================
HISTORY_FILE = "history_digits.json"
OFFSET_FILE = "offset_digits.txt"
STATS_FILE = "stats_digits.json"
MAX_HISTORY = 200
PROCESSED_GAMES = set()
LAST_PREDICT_TIME = 0
PREDICT_INTERVAL = 3
CLEANUP_INTERVAL = 3600
TIMEOUT_SECONDS = 300  # 5 минут
MAX_GAME_GAP = 15      # если разница номеров больше 15, сбрасываем

POSITION_SUITS = {1: "♣️", 2: "♦️", 3: "♥️", 4: "♠️"}
DIGIT_VALUES = {'6': 6, '7': 7, '8': 8, '9': 9, '10': 10}

# =====================================================================
# СТАТИСТИКА
# =====================================================================
def load_stats():
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {"total": 0, "win": 0, "lose": 0, "by_dogon": {0: 0, 1: 0, 2: 0, 3: 0}}
    return {"total": 0, "win": 0, "lose": 0, "by_dogon": {0: 0, 1: 0, 2: 0, 3: 0}}

def save_stats(stats):
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)

def update_stats(dogon_number, result):
    stats = load_stats()
    stats["total"] += 1
    if result == "win":
        stats["win"] += 1
        if dogon_number in stats["by_dogon"]:
            stats["by_dogon"][dogon_number] += 1
        else:
            stats["by_dogon"][dogon_number] = 1
    else:
        stats["lose"] += 1
    save_stats(stats)
    return stats

def send_stats_report():
    stats = load_stats()
    if stats["total"] == 0:
        msg = "📊 <b>СТАТИСТИКА ПРОГНОЗОВ (ЦИФРЫ)</b>\n\nПока нет прогнозов."
        send_message(msg)
        return
    win_rate = (stats["win"] / stats["total"] * 100) if stats["total"] > 0 else 0
    msg = f"📊 <b>СТАТИСТИКА ПРОГНОЗОВ (ЦИФРЫ)</b>\n{'=' * 30}\n\n"
    msg += f"📈 <b>Всего прогнозов:</b> {stats['total']}\n"
    msg += f"✅ <b>Зашло:</b> {stats['win']} ({win_rate:.1f}%)\n"
    msg += f"❌ <b>Не зашло:</b> {stats['lose']} ({100 - win_rate:.1f}%)\n\n"
    msg += f"{'=' * 30}\n<b>По догонам:</b>\n"
    msg += f"🎯 Целевая игра: {stats['by_dogon'].get(0, 0)}\n"
    msg += f"🔄 Догон 1: {stats['by_dogon'].get(1, 0)}\n"
    msg += f"🔄 Догон 2: {stats['by_dogon'].get(2, 0)}\n"
    msg += f"🔄 Догон 3: {stats['by_dogon'].get(3, 0)}\n"
    msg += f"{'=' * 30}\n⏰ {datetime.now(MOSCOW_TZ).strftime('%d.%m.%Y %H:%M:%S')}"
    send_message(msg)

# =====================================================================
# ФУНКЦИИ ТЕЛЕГРАМ
# =====================================================================
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

def send_startup_message():
    msg = "✅ ОБНОВЛЕНИЕ ЗАВЕРШЕНО\n📦 Версия: 2.3.0\n✅ Переменные установлены\n🤖 Бот активен 💪"
    send_message(msg)

# =====================================================================
# ПАРСИНГ
# =====================================================================
def parse_game(text):
    try:
        game_match = re.search(r'#N(\d+)', text)
        if not game_match:
            return None
        game_number = int(game_match.group(1))
        parts = None
        if '◀️' in text:
            parts = text.split('◀️')
        elif '▶️' in text:
            parts = text.split('▶️')
        elif '-' in text:
            parts = text.split('-')
        elif '—' in text:
            parts = text.split('—')
        else:
            return None
        if not parts or len(parts) < 2:
            return None
        player_part = parts[0].strip()
        dealer_part = parts[1].strip()
        player_score_match = re.search(r'(\d+)\s*\(', player_part)
        player_score = int(player_score_match.group(1)) if player_score_match else 0
        dealer_score_match = re.search(r'(\d+)\s*\(', dealer_part)
        dealer_score = int(dealer_score_match.group(1)) if dealer_score_match else 0
        player_cards_match = re.search(r'\(([^)]+)\)', player_part)
        if not player_cards_match:
            return None
        player_cards_str = player_cards_match.group(1).strip()
        dealer_cards_match = re.search(r'\(([^)]+)\)', dealer_part)
        dealer_cards_str = dealer_cards_match.group(1).strip() if dealer_cards_match else ""

        def parse_cards(cards_str):
            cards = []
            i = 0
            while i < len(cards_str):
                if cards_str[i] == ' ':
                    i += 1
                    continue
                rank = ''
                if i + 1 < len(cards_str) and cards_str[i:i+2] == '10':
                    rank = '10'
                    i += 2
                elif cards_str[i] in 'AKQJ':
                    rank = cards_str[i]
                    i += 1
                elif cards_str[i].isdigit():
                    rank = cards_str[i]
                    i += 1
                else:
                    i += 1
                    continue
                suit = ''
                if i < len(cards_str):
                    if cards_str[i:i+2] == '♠️':
                        suit = '♠️'
                        i += 2
                    elif cards_str[i:i+2] == '♣️':
                        suit = '♣️'
                        i += 2
                    elif cards_str[i:i+2] == '♦️':
                        suit = '♦️'
                        i += 2
                    elif cards_str[i:i+2] == '♥️':
                        suit = '♥️'
                        i += 2
                    elif cards_str[i] in '♠♣♦♥':
                        suit = cards_str[i].replace('♠', '♠️').replace('♣', '♣️').replace('♦', '♦️').replace('♥', '♥️')
                        i += 1
                    else:
                        i += 1
                        continue
                if rank and suit:
                    cards.append({"rank": rank, "suit": suit})
            return cards

        player_cards = parse_cards(player_cards_str)
        dealer_cards = parse_cards(dealer_cards_str) if dealer_cards_str else []
        print(f"✅ #N{game_number}: игрок {player_score} очков ({len(player_cards)} карт), дилер {dealer_score} очков ({len(dealer_cards)} карт)", flush=True)
        if player_cards:
            cards_str = ', '.join([f"{c['rank']}{c['suit']}" for c in player_cards])
            print(f"   🃏 Игрок: {cards_str}", flush=True)
        if dealer_cards:
            cards_str = ', '.join([f"{c['rank']}{c['suit']}" for c in dealer_cards])
            print(f"   🃏 Дилер: {cards_str}", flush=True)
        return {
            "number": game_number,
            "player_cards": player_cards,
            "dealer_cards": dealer_cards,
            "player_score": player_score,
            "dealer_score": dealer_score,
            "text": text
        }
    except Exception as e:
        print(f"❌ Ошибка парсинга: {e}", flush=True)
        return None

def get_highest_digit(cards):
    if not cards:
        return None, None
    highest_value = -1
    highest_card = None
    highest_position = None
    count_highest = 0
    for idx, card in enumerate(cards, start=1):
        rank = card.get("rank", "")
        value = DIGIT_VALUES.get(rank, 0)
        if value > highest_value:
            highest_value = value
            highest_card = card
            highest_position = idx
            count_highest = 1
        elif value == highest_value:
            count_highest += 1
    if count_highest > 1:
        return None, None
    return highest_card, highest_position

def get_suit_by_position(position):
    return POSITION_SUITS.get(position, None)

def is_valid_game(game_data):
    player_cards = game_data.get("player_cards", [])
    dealer_cards = game_data.get("dealer_cards", [])
    player_score = game_data.get("player_score", 0)
    dealer_score = game_data.get("dealer_score", 0)
    print(f"   Проверка для прогноза: игрок={len(player_cards)} карт, дилер={len(dealer_cards)} карт, очки: {player_score}/{dealer_score}", flush=True)
    if player_score == 21 or dealer_score == 21:
        print(f"⏭️ Пропускаем #N{game_data['number']}: у кого-то 21 очко", flush=True)
        return False
    if len(player_cards) not in [3, 4]:
        print(f"⏭️ Пропускаем #N{game_data['number']}: у игрока {len(player_cards)} карт (нужно 3 или 4)", flush=True)
        return False
    if len(dealer_cards) == 0:
        print(f"⏭️ Пропускаем #N{game_data['number']}: у дилера 0 карт", flush=True)
        return False
    if len(player_cards) == 3:
        return True
    if len(player_cards) == 4:
        has_digit = False
        for card in player_cards:
            if card.get("rank") in DIGIT_VALUES:
                has_digit = True
                break
        if not has_digit:
            print(f"⏭️ Пропускаем #N{game_data['number']}: нет цифр (6-10) у игрока", flush=True)
            return False
        return True
    return False

def predict(game_data):
    game_num = game_data["number"]
    player_cards = game_data["player_cards"]
    dealer_cards = game_data.get("dealer_cards", [])
    if len(player_cards) == 3:
        if dealer_cards:
            four_cards = player_cards + [dealer_cards[0]]
        else:
            print(f"⚠️ Игра #{game_num}: у дилера нет карт", flush=True)
            return None
    elif len(player_cards) == 4:
        four_cards = player_cards
    else:
        print(f"⚠️ Игра #{game_num}: {len(player_cards)} карт — не подходит", flush=True)
        return None
    has_digit = False
    for card in four_cards:
        if card.get("rank") in DIGIT_VALUES:
            has_digit = True
            break
    if not has_digit:
        print(f"⏭️ Пропускаем #N{game_num}: нет цифр (6-10) в 4 картах", flush=True)
        return None
    digit_ranks = []
    for card in four_cards:
        rank = card.get("rank", "")
        if rank in DIGIT_VALUES:
            digit_ranks.append(rank)
    if len(digit_ranks) != len(set(digit_ranks)):
        print(f"⏭️ Пропускаем #N{game_num}: повторяющиеся цифры {digit_ranks}", flush=True)
        return None
    highest_card, highest_position = get_highest_digit(four_cards)
    if not highest_card or not highest_position:
        print(f"⚠️ Игра #{game_num}: не удалось определить старшую цифру", flush=True)
        return None
    predicted_suit = get_suit_by_position(highest_position)
    if not predicted_suit:
        print(f"⚠️ Игра #{game_num}: позиция {highest_position} не определена", flush=True)
        return None
    if highest_card.get("suit") == predicted_suit:
        print(f"⏭️ Пропускаем #N{game_num}: масть старшей карты {highest_card['rank']}{highest_card['suit']} совпадает с предсказанием {predicted_suit}", flush=True)
        return None
    rank = highest_card["rank"]
    target_game = game_num + 1
    print(f"🔍 #N{game_num}: старшая цифра {rank} (поз. {highest_position}) → {predicted_suit} (прогноз на дилера)", flush=True)
    return {
        "from_game": game_num,
        "target": target_game,
        "suit": predicted_suit,
        "rank": rank,
        "card": f"{rank}{predicted_suit}",
        "position": highest_position,
        "games": [target_game, target_game + 1, target_game + 2, target_game + 3]
    }

# =====================================================================
# ПРОВЕРКА РЕЗУЛЬТАТОВ (С ТАЙМАУТОМ И ЗАЩИТОЙ ОТ УСТАРЕВАНИЯ)
# =====================================================================
def check_results(history, all_messages):
    for entry in history:
        if entry.get("status") != "pending":
            continue
        target = entry.get("target")
        predicted_suit = entry.get("suit")
        from_game = entry.get("from_game")
        message_id = entry.get("message_id")
        created_time = entry.get("time", "")
        if not predicted_suit or not message_id:
            continue

        # === ТАЙМАУТ 5 МИНУТ ===
        try:
            created_dt = datetime.fromisoformat(created_time)
            time_diff = (datetime.now(MOSCOW_TZ) - created_dt).total_seconds()
        except:
            time_diff = 0
        if time_diff > TIMEOUT_SECONDS:
            print(f"⏰ Таймаут! Прогноз #N{from_game} → #N{target} висит {int(time_diff // 60)} мин", flush=True)
            update_stats(0, "lose")
            original_text = f"🔮 <b>ПРОГНОЗ (ЦИФРЫ) - ДИЛЕР</b>\n📊 От игры: #N{from_game}\n🃏 Масть: {predicted_suit}\n🎯 Целевая игра: #N{target}\n📈 3 игры догон\n⏰ {entry.get('time', '')[:16]}"
            result_text = f"\n\n⏰ <b>ТАЙМАУТ</b> (не дождались завершения)"
            edit_message(message_id, original_text + result_text)
            entry["status"] = "lose"
            save_history(history)
            print(f"❌ Прогноз #N{from_game} → #N{target} НЕ ЗАШЕЛ (таймаут)", flush=True)
            continue

        # === ПРОВЕРКА НА УСТАРЕВАНИЕ НОМЕРА ===
        if all_messages:
            max_game = 0
            for msg in all_messages:
                match = re.search(r'#N(\d+)', msg)
                if match:
                    max_game = max(max_game, int(match.group(1)))
            if max_game - target > MAX_GAME_GAP:
                print(f"⏰ Игра #N{target} сильно устарела (текущая ~#N{max_game})", flush=True)
                update_stats(0, "lose")
                original_text = f"🔮 <b>ПРОГНОЗ (ЦИФРЫ) - ДИЛЕР</b>\n📊 От игры: #N{from_game}\n🃏 Масть: {predicted_suit}\n🎯 Целевая игра: #N{target}\n📈 3 игры догон\n⏰ {entry.get('time', '')[:16]}"
                result_text = f"\n\n❌ <b>НЕ ЗАШЛО</b> (игра устарела)"
                edit_message(message_id, original_text + result_text)
                entry["status"] = "lose"
                save_history(history)
                print(f"❌ Прогноз #N{from_game} → #N{target} НЕ ЗАШЕЛ (устарел)", flush=True)
                continue

        # === ОСНОВНАЯ ПРОВЕРКА (ПО ДИЛЕРУ) ===
        max_games_to_check = 4
        for i in range(max_games_to_check):
            game_to_check = target + i
            game_msg = None
            for msg in all_messages:
                if f"#N{game_to_check}" in msg and ('✅' in msg or '🔰' in msg):
                    game_msg = msg
                    break
            if not game_msg:
                print(f"⏳ Ждем завершенную игру #N{game_to_check} для проверки масти {predicted_suit} у дилера", flush=True)
                break
            game_data = parse_game(game_msg)
            if not game_data:
                print(f"⚠️ Не удалось распарсить #N{game_to_check}", flush=True)
                continue

            # === ПРОВЕРКА МАСТИ У ДИЛЕРА ===
            suit_found = False
            dealer_cards = game_data.get("dealer_cards", [])
            if not dealer_cards:
                print(f"⚠️ Нет карт дилера в #N{game_to_check}", flush=True)
                continue
            print(f"   Проверка #N{game_to_check}: {len(dealer_cards)} карт дилера", flush=True)
            for card in dealer_cards:
                print(f"      Карта: {card['rank']}{card['suit']}", flush=True)
                if card.get("suit") == predicted_suit:
                    suit_found = True
                    print(f"   ✅ Найдена масть {predicted_suit} у дилера в карте {card['rank']}{card['suit']}", flush=True)
                    break
            if suit_found:
                print(f"🎯 МАСТЬ {predicted_suit} НАЙДЕНА у дилера в игре #N{game_to_check}!", flush=True)
                dogon_number = i
                update_stats(dogon_number, "win")
                original_text = f"🔮 <b>ПРОГНОЗ (ЦИФРЫ) - ДИЛЕР</b>\n📊 От игры: #N{from_game}\n🃏 Масть: {predicted_suit}\n🎯 Целевая игра: #N{target}\n📈 3 игры догон\n⏰ {entry.get('time', '')[:16]}"
                if dogon_number == 0:
                    result_text = f"\n\n✅ <b>ЗАШЛО</b> у дилера в целевой игре: #N{game_to_check}"
                else:
                    result_text = f"\n\n✅ <b>ЗАШЛО</b> у дилера на догоне {dogon_number}: #N{game_to_check}"
                edit_message(message_id, original_text + result_text)
                entry["status"] = "win"
                entry["result_game"] = game_to_check
                entry["dogon"] = dogon_number
                save_history(history)
                print(f"✅ Прогноз #N{from_game} → #N{target} ЗАШЕЛ у дилера на игре #N{game_to_check}", flush=True)
                return
            if i == max_games_to_check - 1:
                print(f"❌ Масть {predicted_suit} НЕ НАЙДЕНА у дилера за {max_games_to_check} игр", flush=True)
                update_stats(0, "lose")
                original_text = f"🔮 <b>ПРОГНОЗ (ЦИФРЫ) - ДИЛЕР</b>\n📊 От игры: #N{from_game}\n🃏 Масть: {predicted_suit}\n🎯 Целевая игра: #N{target}\n📈 3 игры догон\n⏰ {entry.get('time', '')[:16]}"
                result_text = f"\n\n❌ <b>НЕ ЗАШЛО</b> у дилера (проверено {max_games_to_check} игр)"
                edit_message(message_id, original_text + result_text)
                entry["status"] = "lose"
                save_history(history)
                print(f"❌ Прогноз #N{from_game} → #N{target} НЕ ЗАШЕЛ у дилера", flush=True)
                return

# =====================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =====================================================================
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
        print(f"🧹 Очистка кэша: оставлено {len(history)} записей", flush=True)
    return history

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

def load_recent_messages():
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"chat_id": CHANNEL_STATS, "limit": 100}
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            messages = []
            for update in data.get("result", []):
                post = update.get("channel_post")
                if post and post.get("text"):
                    messages.append(post.get("text"))
            return messages
    except Exception as e:
        print(f"❌ Ошибка загрузки истории: {e}", flush=True)
    return []

def check_bot_status():
    print("╔═══════════════════════════════════════════════════════════════╗", flush=True)
    print("║              🔍 ДИАГНОСТИКА СТАТУСА БОТА                    ║", flush=True)
    print("╠═══════════════════════════════════════════════════════════════╣", flush=True)
    print(f"║  📊 Кэш: {len(load_history())} записей", flush=True)
    print(f"║  🔄 Обработано игр: {len(PROCESSED_GAMES)}", flush=True)
    print(f"║  ⏰ Последний прогноз: {datetime.fromtimestamp(LAST_PREDICT_TIME).strftime('%H:%M:%S') if LAST_PREDICT_TIME > 0 else 'Нет'}", flush=True)
    print(f"║  📤 Канал отправки: {CHANNEL_PROGNOZ}", flush=True)
    print(f"║  📥 Канал чтения: {CHANNEL_STATS}", flush=True)
    print("╚═══════════════════════════════════════════════════════════════╝", flush=True)
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getMe"
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            bot_info = response.json()
            print(f"✅ Бот активен: @{bot_info['result']['username']}", flush=True)
        else:
            print(f"❌ Бот недоступен: {response.status_code}", flush=True)
    except Exception as e:
        print(f"❌ Ошибка проверки бота: {e}", flush=True)
    print("", flush=True)

# =====================================================================
# ОСНОВНОЙ ЦИКЛ
# =====================================================================
def main():
    global LAST_PREDICT_TIME
    print("🔄 ЗАПУСК ПРОГНОЗИСТА...", flush=True)
    print("", flush=True)
    check_bot_status()
    try:
        send_startup_message()
        print("✅ Приветственное сообщение отправлено в Telegram", flush=True)
    except Exception as e:
        print(f"⚠️ Не удалось отправить приветствие: {e}", flush=True)

    offset = get_offset()
    history = load_history()
    last_reset_date = datetime.now(MOSCOW_TZ).date()

    print("📥 Загрузка последних сообщений из канала...", flush=True)
    all_messages = load_recent_messages()
    print(f"📥 Загружено сообщений: {len(all_messages)}", flush=True)
    print("", flush=True)

    check_results(history, all_messages)

    last_cleanup_time = time.time()
    last_stats_time = time.time()
    last_forced_check = time.time()

    print("🚀 БОТ ГОТОВ К РАБОТЕ!", flush=True)
    print("=" * 60, flush=True)
    print("", flush=True)

    while True:
        try:
            current_time = time.time()
            current_date = datetime.now(MOSCOW_TZ).date()
            current_hour = datetime.now(MOSCOW_TZ).hour

            if current_date != last_reset_date and current_hour == 3:
                print("🔄 Ежедневный сброс кэша...", flush=True)
                history = []
                save_history(history)
                all_messages = []
                last_reset_date = current_date
                continue

            if current_time - last_cleanup_time > CLEANUP_INTERVAL:
                history = clean_memory(history)
                save_history(history)
                last_cleanup_time = current_time

            if current_time - last_stats_time > 3600:
                send_stats_report()
                last_stats_time = current_time

            # ПРИНУДИТЕЛЬНАЯ ПРОВЕРКА КАЖДЫЕ 30 СЕКУНД
            if current_time - last_forced_check > 30:
                print("🔄 Принудительная проверка ожидающих прогнозов...", flush=True)
                check_results(history, all_messages)
                last_forced_check = current_time

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

                # ЗАМЕНЯЕМ СТАРУЮ ВЕРСИЮ ИГРЫ НА НОВУЮ
                all_messages = [msg for msg in all_messages if f"#N{game_number}" not in msg]
                all_messages.append(text)
                if len(all_messages) > 500:
                    all_messages = all_messages[-500:]

                print(f"📥 Получена игра #N{game_number}", flush=True)
                print(f"📝 Текст: {text}", flush=True)

                # ЕСЛИ ИГРА ЗАВЕРШЕНА - ПРОВЕРЯЕМ РЕЗУЛЬТАТ
                if '✅' in text or '🔰' in text:
                    print(f"✅ #N{game_number} завершена - проверяем результаты", flush=True)
                    check_results(history, all_messages)
                    continue

                # НЕЗАВЕРШЕННАЯ ИГРА - ПРОВЕРКА НА СОЗДАНИЕ ПРОГНОЗА
                print(f"⏭️ #N{game_number} не завершена (нет ✅ или 🔰)", flush=True)

                # НЕ ДАЁМ НОВЫЙ ПРОГНОЗ, ПОКА ЕСТЬ PENDING
                pending_exists = any(h.get('status') == 'pending' for h in history)
                if pending_exists:
                    print(f"⏳ Есть ожидающий прогноз, новый не даём", flush=True)
                    continue

                if game_number in PROCESSED_GAMES:
                    print(f"⏭️ #N{game_number} уже обработана", flush=True)
                    continue

                game_data = parse_game(text)
                if not game_data:
                    print(f"❌ Не удалось распарсить #N{game_number}", flush=True)
                    continue

                if not is_valid_game(game_data):
                    print(f"⏭️ #N{game_number} не подходит по правилам", flush=True)
                    continue

                if current_time - LAST_PREDICT_TIME < PREDICT_INTERVAL:
                    print(f"⏳ Интервал: {int(current_time - LAST_PREDICT_TIME)} сек < {PREDICT_INTERVAL} сек", flush=True)
                    continue

                prognoz = predict(game_data)
                if prognoz:
                    msg = f"🔮 <b>ПРОГНОЗ (ЦИФРЫ) - ДИЛЕР</b>\n"
                    msg += f"📊 От игры: #N{game_data['number']}\n"
                    msg += f"🃏 Масть: {prognoz['suit']}\n"
                    msg += f"🎯 Целевая игра: #N{prognoz['target']}\n"
                    msg += f"📈 3 игры догон\n"
                    msg += f"⏰ {datetime.now(MOSCOW_TZ).strftime('%H:%M:%S')}"

                    message_id = send_message(msg)
                    if message_id:
                        print(f"✅ ПРОГНОЗ ОТПРАВЛЕН: #N{prognoz['target']} → масть {prognoz['suit']} (на дилера)", flush=True)
                        LAST_PREDICT_TIME = current_time
                        PROCESSED_GAMES.add(game_number)

                        history.append({
                            "from_game": game_data["number"],
                            "target": prognoz["target"],
                            "suit": prognoz["suit"],
                            "card": prognoz["card"],
                            "time": datetime.now(MOSCOW_TZ).isoformat(),
                            "status": "pending",
                            "message_id": message_id
                        })
                        save_history(history)

                        pending_count = len([h for h in history if h.get('status') == 'pending'])
                        print(f"📊 Ожидающих прогнозов: {pending_count}", flush=True)

            check_results(history, all_messages)
            history = clean_memory(history)
            save_history(history)

            if len(PROCESSED_GAMES) > 500:
                PROCESSED_GAMES.clear()

            time.sleep(1)

        except Exception as e:
            print(f"❌ Ошибка: {e}", flush=True)
            import traceback
            traceback.print_exc()
            time.sleep(30)

if __name__ == "__main__":
    main()