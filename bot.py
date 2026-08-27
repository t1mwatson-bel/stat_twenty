import os
import sys
import requests
import json
import time
from datetime import datetime
import pytz

# =====================================================================
# ПРИНУДИТЕЛЬНЫЙ ВЫВОД ЛОГОВ
# =====================================================================
sys.stdout.flush()
print("=" * 60, flush=True)
print("🃏 АНАЛИЗАТОР ОБЫЧНОЙ 21 (TWENTYONE GAME)", flush=True)
print("=" * 60, flush=True)

# =====================================================================
# ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ
# =====================================================================
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    BOT_TOKEN = os.getenv('BOT_TOKEN_PROGNOZ')

print("🔍 ДИАГНОСТИКА ПЕРЕМЕННЫХ:", flush=True)
print(f"BOT_TOKEN: {BOT_TOKEN[:5] if BOT_TOKEN else 'НЕ ЗАДАН'}", flush=True)
print("=" * 60, flush=True)

if not BOT_TOKEN:
    print("❌ ОШИБКА: BOT_TOKEN не задан!", flush=True)
    exit(1)

print("✅ BOT_TOKEN задан!", flush=True)

# =====================================================================
# НАСТРОЙКИ
# =====================================================================
MOSCOW_TZ = pytz.timezone('Europe/Moscow')
BASE_URL = "https://1xlite-10691.pro"
DATA_FILE = "twentyone_data.json"
MAX_RECORDS = 20000
CHECK_INTERVAL = 5

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": f"{BASE_URL}/ru/live/twentyone/1643503-twentyone-game",
    "Cookie": "platform_type=desktop; lng=ru; cookies_agree_type=3; tzo=3; is12h=0; referral_values=%7B%22type%22%3A%22reflinkid%22%2C%22val%22%3A%22s_50970m_355c_%22%2C%22additional%22%3A%7B%22name_tag%22%3A%22tag%22%7D%7D; reflinkid=s_50970m_355c_; auid=uaJb+WqQFLEHP+WbAwdUAg==; fatman_uuid=6dac517c-7199-1491-828a-723ace371af0; che_g=3741ad9b-2648-4e11-b16e-55cbdda04b42; SESSION=ae9f1b4deac37d41be6873b1acf03cf4; sh.session.id=1e645679-820b-4250-86f5-bf39161d311d; _ga=GA1.1.103981619.1787827389; _ym_uid=1787827389562709649; _ym_d=1787827389; _ym_isad=2; _ym_visorc=b; mdd=1; _ga_7JGWL9SV66=GS2.1.s1787827388$o1$g1$t1787827414$j34$l0$h1219464045; window_width=150"
}

SUITS_NAMES = {0: "♠️", 1: "♣️", 2: "♦️", 3: "♥️"}
RANKS = {1: "A", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8", 9: "9", 10: "10", 11: "J", 12: "Q", 13: "K"}

finished_games = set()
last_card_data = {}

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return []
    return []

def save_data(record):
    data = load_data()
    existing_index = None
    for i, r in enumerate(data):
        if r.get("game_id") == record["game_id"]:
            existing_index = i
            break
    if existing_index is not None:
        data[existing_index] = record
        print(f"🔄 Обновлена запись для игры {record['game_id']}", flush=True)
    else:
        data.append(record)
        print(f"💾 Новая запись для игры {record['game_id']}", flush=True)
    if len(data) > MAX_RECORDS:
        data = data[-MAX_RECORDS:]
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def get_active_games():
    try:
        url = f"{BASE_URL}/service-api/main-live-feed/v3/games1x2?cfView=3&count=40&fcountry=190&gr=415&grMode=4&lng=ru&ref=7&selectedMs=10.146.1643503"
        response = requests.get(url, headers=HEADERS, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict) and "Value" in data:
                games = data.get("Value", [])
            elif isinstance(data, list):
                games = data
            else:
                return []
            
            active_games = []
            for game in games:
                if game.get("liga", {}).get("id") == 1643503:
                    active_games.append(game)
            return active_games
        else:
            return []
    except Exception as e:
        print(f"❌ Ошибка: {e}", flush=True)
        return []

def get_game_data(game_id):
    url = f"{BASE_URL}/service-api/LiveFeed/GetGameZip?id={game_id}&isSubGames=true&GroupEvents=true&countevents=250&grMode=4&partner=7&topGroups=&country=190&marketType=1&isNewBuilder=true"
    try:
        start_time = time.time()
        response = requests.get(url, headers=HEADERS, timeout=5)
        end_time = time.time()
        latency = (end_time - start_time) * 1000
        if response.status_code == 200:
            return response.json(), latency, start_time, end_time
        else:
            return None, None, None, None
    except Exception as e:
        print(f"❌ Ошибка игры {game_id}: {e}", flush=True)
        return None, None, None, None

def parse_cards_from_json(cards_str):
    try:
        if isinstance(cards_str, str):
            cards = json.loads(cards_str)
        else:
            cards = cards_str
        result = []
        for card in cards:
            cs = card.get("CS", 0)
            cv = card.get("CV", 0)
            suit = SUITS_NAMES.get(cs, "?")
            rank = RANKS.get(cv, "?")
            result.append({"rank": rank, "suit": suit})
        return result
    except:
        return []

def analyze_game(game_id, data, latency, start_time, end_time):
    global last_card_data
    
    # === ЗАЩИТА ОТ NONE ===
    if data is None:
        print(f"⚠️ Данные для игры {game_id} пустые (None)", flush=True)
        return
    
    timestamp = datetime.fromtimestamp(start_time, MOSCOW_TZ) if start_time else datetime.now(MOSCOW_TZ)
    timestamp_msk_str = timestamp.strftime('%H:%M:%S.%f')[:-3]
    
    player_cards = []
    dealer_cards = []
    state = "0"
    
    # === ПАРСИНГ ДЛЯ ОБЫЧНОЙ 21 ===
    # Пробуем path: data.scores.statistic.main
    scores = data.get("scores", {})
    if scores:
        statistic = scores.get("statistic", {})
        main_stat = statistic.get("main", {})
        if main_stat:
            p1_str = main_stat.get("P1", "[]")
            p2_str = main_stat.get("P2", "[]")
            state = main_stat.get("STATE", "0")
            player_cards = parse_cards_from_json(p1_str)
            dealer_cards = parse_cards_from_json(p2_str)
    
    # Если не нашлось, пробуем через Value
    if not player_cards and not dealer_cards:
        value = data.get("Value", {})
        if value:
            scores = value.get("scores", {})
            if scores:
                statistic = scores.get("statistic", {})
                main_stat = statistic.get("main", {})
                if main_stat:
                    p1_str = main_stat.get("P1", "[]")
                    p2_str = main_stat.get("P2", "[]")
                    state = main_stat.get("STATE", "0")
                    player_cards = parse_cards_from_json(p1_str)
                    dealer_cards = parse_cards_from_json(p2_str)
    
    # Если всё ещё нет — пробуем SC
    if not player_cards and not dealer_cards:
        value = data.get("Value", {})
        if value:
            sc = value.get("SC", {})
            if sc:
                p1_str = sc.get("P1", "[]")
                p2_str = sc.get("P2", "[]")
                state = sc.get("STATE", "0")
                player_cards = parse_cards_from_json(p1_str)
                dealer_cards = parse_cards_from_json(p2_str)
    
    current_count = len(player_cards) + len(dealer_cards)
    
    print(f"🃏 Игра {game_id}: {len(player_cards)} карт игрока, {len(dealer_cards)} карт дилера, задержка={latency:.2f}мс, state={state}", flush=True)
    
    if current_count > 0:
        sequence = []
        max_len = max(len(player_cards), len(dealer_cards))
        for i in range(max_len):
            if i < len(player_cards):
                pc = player_cards[i]
                sequence.append({"position": i*2+1, "who": "P", "rank": pc["rank"], "suit": pc["suit"]})
            if i < len(dealer_cards):
                dc = dealer_cards[i]
                sequence.append({"position": i*2+2, "who": "D", "rank": dc["rank"], "suit": dc["suit"]})
        
        def calc_score(cards):
            score = 0
            for card in cards:
                cv = 0
                if card["rank"] == "A":
                    cv = 14
                elif card["rank"] == "K":
                    cv = 13
                elif card["rank"] == "Q":
                    cv = 12
                elif card["rank"] == "J":
                    cv = 11
                elif card["rank"].isdigit():
                    cv = int(card["rank"])
                if cv == 14:
                    score += 11
                elif cv == 13:
                    score += 4
                elif cv == 12:
                    score += 3
                elif cv == 11:
                    score += 2
                elif 6 <= cv <= 10:
                    score += cv
            return score
        
        player_score = calc_score(player_cards)
        dealer_score = calc_score(dealer_cards)
        
        record = {
            "game_id": game_id,
            "timestamp_msk": timestamp_msk_str,
            "latency_ms": round(latency, 2),
            "state": state,
            "player_score": player_score,
            "dealer_score": dealer_score,
            "player_cards": [{"rank": c["rank"], "suit": c["suit"]} for c in player_cards],
            "dealer_cards": [{"rank": c["rank"], "suit": c["suit"]} for c in dealer_cards],
            "sequence": sequence
        }
        
        seq_str = ', '.join([f"{c['who']}{c['position']}:{c['rank']}{c['suit']}" for c in sequence])
        print(f"   Последовательность: {seq_str}", flush=True)
        save_data(record)
    
    if state in ["4", "5"]:
        finished_games.add(str(game_id))
        print(f"🏁 Игра {game_id} завершена (state={state})", flush=True)

def main():
    print("🔄 АНАЛИЗАТОР ОБЫЧНОЙ 21 ЗАПУЩЕН", flush=True)
    print(f"📁 Данные сохраняются в {DATA_FILE}", flush=True)
    print(f"⏱️ Интервал опроса: {CHECK_INTERVAL} сек", flush=True)
    print("=" * 60, flush=True)
    
    existing_data = load_data()
    print(f"📊 Уже собрано записей: {len(existing_data)}", flush=True)
    print("=" * 60, flush=True)
    
    while True:
        try:
            active_games = get_active_games()
            
            if not active_games:
                print("💤 Нет активных игр 21 Очко, ждём...", flush=True)
                time.sleep(CHECK_INTERVAL)
                continue
            
            print(f"🎯 Найдено {len(active_games)} активных игр", flush=True)
            
            for game in active_games:
                game_id = str(game.get("id"))
                if game_id in finished_games:
                    continue
                
                print(f"📥 Запрос данных для игры {game_id}...", flush=True)
                data, latency, start_time, end_time = get_game_data(game_id)
                if not data:
                    print(f"❌ Не удалось получить данные для игры {game_id}", flush=True)
                    continue
                
                analyze_game(game_id, data, latency, start_time, end_time)
                time.sleep(0.5)
            
            if len(finished_games) > 500:
                finished_games.clear()
                print("🗑️ Кэш завершённых игр очищен", flush=True)
            
            time.sleep(CHECK_INTERVAL)
            
        except KeyboardInterrupt:
            print("🛑 Анализатор остановлен", flush=True)
            data_count = len(load_data())
            print(f"📊 Всего собрано записей: {data_count}", flush=True)
            break
        except Exception as e:
            print(f"❌ Ошибка: {e}", flush=True)
            import traceback
            traceback.print_exc()
            time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()