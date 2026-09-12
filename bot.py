import os
import sys
import requests
import json
import re
import time
from datetime import datetime, timedelta
import pytz

# =====================================================================
# НАСТРОЙКИ
# =====================================================================
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    BOT_TOKEN = os.getenv('BOT_TOKEN_PROGNOZ')

CHAT_ID = os.getenv('CHAT_ID')
if not CHAT_ID:
    CHAT_ID = os.getenv('CHAT_ID_21')

if not BOT_TOKEN or not CHAT_ID:
    print("❌ Ошибка: BOT_TOKEN или CHAT_ID не заданы!", flush=True)
    sys.exit(1)

print(f"✅ BOT_TOKEN: {BOT_TOKEN[:5]}...", flush=True)
print(f"✅ CHAT_ID: {CHAT_ID}", flush=True)

MOSCOW_TZ = pytz.timezone('Europe/Moscow')

# =====================================================================
# ⚠️ ЗДЕСЬ ТОЛЬКО ОДНА СТРОКА МЕНЯЕТСЯ — BASE_URL
# =====================================================================
BASE_URL = "https://1xlite-6021.pro"  # ← АКТУАЛЬНОЕ ЗЕРКАЛО
# =====================================================================

API = f"https://api.telegram.org/bot{BOT_TOKEN}"
messages = {}
processed_games = set()
game_numbers = {}
player_cards_history = {}
dealer_cards_history = {}
game_state_history = {}

SUITS_NAMES = {0: "♠️", 1: "♣️", 2: "♦️", 3: "♥️"}
RANKS = {1: "A", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8", 9: "9", 10: "10", 11: "J", 12: "Q", 13: "K"}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": f"{BASE_URL}/ru/live/baccarat/",
    "Cookie": "platform_type=desktop; lng=ru; cookies_agree_type=3; tzo=3; is12h=0; referral_values=%7B%22type%22%3A%22reflinkid%22%2C%22val%22%3A%22s_50970m_355c_%22%2C%22additional%22%3A%7B%22name_tag%22%3A%22tag%22%7D%7D; reflinkid=s_50970m_355c_; auid=uaJb+WqQFLEHP+WbAwdUAg==; fatman_uuid=6dac517c-7199-1491-828a-723ace371af0; che_g=3741ad9b-2648-4e11-b16e-55cbdda04b42; SESSION=ae9f1b4deac37d41be6873b1acf03cf4; sh.session.id=1e645679-820b-4250-86f5-bf39161d311d; _ga=GA1.1.103981619.1787827389; _ym_uid=1787827389562709649; _ym_d=1787827389; _ym_isad=2; _ym_visorc=b; mdd=1; _ga_7JGWL9SV66=GS2.1.s1787827388$o1$g1$t1787827414$j34$l0$h1219464045; window_width=150"
}

# ===== НАСТРОЙКИ ЛИГ ИЗ РАБОЧЕГО КОДА =====
LIST_URL = f"{BASE_URL}/service-api/LiveFeed/Get1x2_VZip?sports=236&champs=2050671&count=40&gr=1521&mode=4&country=192&partner=8&getEmpty=true&virtualSports=true&noFilterBlockEvent=true"
DETAIL_URL_TEMPLATE = f"{BASE_URL}/service-api/LiveFeed/GetGameZip?id={{game_id}}&isSubGames=true&GroupEvents=true&countevents=250&grMode=4&partner=8&topGroups=&country=192&marketType=1&isNewBuilder=true"

print("✅ Настройки для Baccarat загружены", flush=True)

# =====================================================================
# ФУНКЦИИ
# =====================================================================
def get_game_number():
    """Номер игры от 1 до 720 (каждые 2 минуты, старт в 03:00)"""
    now = datetime.now(MOSCOW_TZ)
    start = now.replace(hour=3, minute=0, second=0, microsecond=0)
    if now < start:
        start = start - timedelta(days=1)
    diff_minutes = (now - start).total_seconds() / 60
    return int(diff_minutes) % 720 + 1

def get_active_games():
    try:
        response = requests.get(LIST_URL, headers=HEADERS, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list):
                games = data
            elif isinstance(data, dict) and "Value" in data:
                games = data.get("Value", [])
            else:
                return []
            active_games = []
            for game in games:
                game_id = game.get("I")
                if game_id and str(game_id) not in processed_games:
                    active_games.append(game)
            return active_games
        else:
            print(f"⚠️ Статус API: {response.status_code}", flush=True)
    except Exception as e:
        print(f"❌ Ошибка: {e}", flush=True)
    return []

def get_game_data(game_id):
    url = DETAIL_URL_TEMPLATE.format(game_id=game_id)
    try:
        response = requests.get(url, headers=HEADERS, timeout=5)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"⚠️ Статус игры {game_id}: {response.status_code}", flush=True)
    except Exception as e:
        print(f"❌ Ошибка игры {game_id}: {e}", flush=True)
    return None

def parse_cards(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if not isinstance(value, str):
        return []
    try:
        result = json.loads(value)
        if isinstance(result, list):
            return result
    except:
        pass
    return []

def format_cards(cards):
    if not cards:
        return ""
    output = []
    for card in cards:
        if not isinstance(card, dict):
            return None
        cs = card.get("CS", 0)
        cv = card.get("CV", 0)
        if not cs or not cv:
            return None
        suit = SUITS_NAMES.get(cs, "?")
        rank = RANKS.get(cv, str(cv))
        output.append(f"{rank}{suit}")
    return "".join(output)

def calculate_score(cards):
    if not cards:
        return 0
    score = 0
    for card in cards:
        if not isinstance(card, dict):
            return -1
        cv = card.get("CV", 0)
        if cv == 0:
            return -1
        if cv == 1:
            score += 1
        elif 2 <= cv <= 9:
            score += cv
        elif 10 <= cv <= 13:
            score += 0
    return score % 10

def is_game_finished(state, player_cards, dealer_cards, p_score, d_score):
    if state:
        state_str = str(state).lower().strip()
        finished_states = {
            "игра завершена", "завершена", "завершено",
            "finished", "finish", "ended", "closed"
        }
        if state_str in finished_states:
            return True
    # Ранняя победа (8 или 9)
    if p_score in [8, 9] or d_score in [8, 9]:
        return True
    return False

def get_arrow(state):
    if not state:
        return ""
    state_str = str(state).lower().strip()
    if state_str in ("игра завершена", "завершена", "finished"):
        return ""
    return "▶️"

def build_message(game_num, game_id, player_cards, dealer_cards, p_score, d_score, state):
    p_hand = format_cards(player_cards)
    d_hand = format_cards(dealer_cards)
    
    if p_hand is None:
        return None
    if dealer_cards and d_hand is None:
        return None
    
    total = p_score + d_score
    finished = is_game_finished(state, player_cards, dealer_cards, p_score, d_score)
    
    if finished:
        tags = []
        if len(player_cards) == 2 and len(dealer_cards) == 2:
            tags.append("#R")
        tag_str = " " + " ".join(tags) if tags else ""
        
        if p_score > d_score:
            return f"#N{game_num} ✅{p_score} ({p_hand}) - {d_score} ({d_hand}) #П1 #T{total}{tag_str} (ID: {game_id})"
        elif d_score > p_score:
            return f"#N{game_num} {p_score} ({p_hand}) - ✅{d_score} ({d_hand}) #П2 #T{total}{tag_str} (ID: {game_id})"
        else:
            return f"#N{game_num} {p_score} ({p_hand}) - 🔰{d_score} ({d_hand}) #X #T{total}{tag_str} (ID: {game_id})"
    
    arrow = get_arrow(state)
    return f"#N{game_num}. {p_score}({p_hand}) {arrow} {d_score}({d_hand}) #T{total} (ID: {game_id})"

def send_message(text):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": text}
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            result = response.json()
            if result.get("ok"):
                return result["result"]["message_id"]
    except Exception as e:
        print(f"❌ Ошибка отправки: {e}", flush=True)
    return None

def edit_message(msg_id, text):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
        payload = {"chat_id": CHAT_ID, "message_id": msg_id, "text": text}
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Ошибка редактирования: {e}", flush=True)
        return False

# =====================================================================
# ОБРАБОТКА ОДНОЙ ИГРЫ
# =====================================================================
def process_game(game):
    gid = str(game.get("I"))
    if not gid or gid in processed_games:
        return
    
    data = get_game_data(gid)
    if not data:
        return
    
    value = data.get("Value", {})
    if not isinstance(value, dict):
        return
    
    sc = value.get("SC", {})
    if not isinstance(sc, dict):
        return
    
    state = sc.get("CPS", "")
    
    player_cards = []
    dealer_cards = []
    
    sections = sc.get("S", [])
    if isinstance(sections, list):
        for item in sections:
            if not isinstance(item, dict):
                continue
            key = item.get("Key")
            if key == "P":
                player_cards = parse_cards(item.get("Value"))
            elif key == "B":
                dealer_cards = parse_cards(item.get("Value"))
    
    # Ожидание
    if not player_cards and not dealer_cards:
        if gid not in game_numbers:
            game_numbers[gid] = get_game_number()
        game_number = game_numbers[gid]
        if gid not in messages:
            msg = f"⏳ Ожидание игры #N{game_number} (ID: {gid})"
            msg_id = send_message(msg)
            if msg_id:
                messages[gid] = msg_id
                print(f"📤 Ожидание {gid} (№{game_number})", flush=True)
        return
    
    if gid not in game_numbers:
        game_numbers[gid] = get_game_number()
    game_number = game_numbers[gid]
    
    p_score = calculate_score(player_cards)
    d_score = calculate_score(dealer_cards)
    
    if p_score < 0 or d_score < 0:
        return
    
    p1_str = json.dumps(player_cards)
    p2_str = json.dumps(dealer_cards)
    
    cards_changed = (gid not in player_cards_history or player_cards_history[gid] != p1_str or
                     gid not in dealer_cards_history or dealer_cards_history[gid] != p2_str)
    state_changed = (gid not in game_state_history or game_state_history[gid] != state)
    
    if not cards_changed and not state_changed:
        return
    
    player_cards_history[gid] = p1_str
    dealer_cards_history[gid] = p2_str
    game_state_history[gid] = state
    
    msg = build_message(game_number, gid, player_cards, dealer_cards, p_score, d_score, state)
    if not msg:
        return
    
    if gid in messages:
        edit_message(messages[gid], msg)
        print(f"🔄 Обновлена {gid}: {msg}", flush=True)
    else:
        msg_id = send_message(msg)
        if msg_id:
            messages[gid] = msg_id
            print(f"📤 Новая {gid}: {msg}", flush=True)
    
    if is_game_finished(state, player_cards, dealer_cards, p_score, d_score):
        processed_games.add(gid)
        for d in (messages, game_numbers, player_cards_history, dealer_cards_history, game_state_history):
            if gid in d:
                del d[gid]
        print(f"🏁 Завершена {gid}", flush=True)

# =====================================================================
# ОСНОВНОЙ ЦИКЛ
# =====================================================================
def main():
    global processed_games, messages, game_numbers, player_cards_history, dealer_cards_history, game_state_history
    print("🚀 ПАРСЕР БАККАРА ЗАПУЩЕН", flush=True)
    print("=" * 60, flush=True)
    
    while True:
        try:
            games = get_active_games()
            if not games:
                time.sleep(3)
                continue
            
            for game in games:
                try:
                    process_game(game)
                except Exception as e:
                    print(f"❌ Ошибка обработки: {e}", flush=True)
                time.sleep(0.3)
            
            if len(processed_games) > 500:
                processed_games.clear()
                messages.clear()
                game_numbers.clear()
                player_cards_history.clear()
                dealer_cards_history.clear()
                game_state_history.clear()
                print("🧹 Кэш очищен", flush=True)
            
            time.sleep(1)
            
        except KeyboardInterrupt:
            print("🛑 Остановка...", flush=True)
            break
        except Exception as e:
            print(f"❌ Ошибка главного цикла: {e}", flush=True)
            time.sleep(5)

if __name__ == "__main__":
    main()