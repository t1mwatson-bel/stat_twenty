import threading
import time
import re
import logging
import random
import json
import os
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import StaleElementReferenceException, NoSuchElementException, TimeoutException, WebDriverException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import telebot

# ===== НАСТРОЙКИ =====
TOKEN = "8357635747:AAGAH_Rwk-vR8jGa6Q9F-AJLsMaEIj-JDBU"
CHANNEL_ID = "-1003179573402"
MAIN_URL = "https://1xlite-7636770.bar/ru/live/twentyone/1643503-twentyone-game"
MAX_BROWSERS = 1
CHECK_INTERVAL = 30
DATA_FILE = "game_data.json"
MAX_DAYS = 3
# =====================

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

SUIT_MAP = {
    'suit-0': '♠️',
    'suit-1': '♣️',
    'suit-2': '♦️',
    'suit-3': '♥️'
}

VALUE_MAP = {
    'value-11': 'J',
    'value-12': 'Q',
    'value-13': 'K',
    'value-14': 'A'
}

bot = telebot.TeleBot(TOKEN)
active_tables = {}
message_ids = {}
game_data = {}
lock = threading.Lock()

# ===== ФУНКЦИИ ДЛЯ РАБОТЫ С ДАННЫМИ =====

def load_game_data():
    global game_data
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                current_time = datetime.now()
                game_data = {}
                for table_id, info in data.items():
                    try:
                        start_time = datetime.fromisoformat(info['start_time'])
                        if (current_time - start_time) < timedelta(days=MAX_DAYS):
                            game_data[table_id] = info
                    except:
                        continue
                logging.info(f"Загружено {len(game_data)} активных игр")
    except Exception as e:
        logging.error(f"Ошибка загрузки данных: {e}")
        game_data = {}

def save_game_data():
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(game_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Ошибка сохранения данных: {e}")

def get_t_number(table_id, msg_id=None):
    with lock:
        if table_id in game_data:
            if msg_id:
                game_data[table_id]['msg_id'] = msg_id
                save_game_data()
            return game_data[table_id]['t_num']
        else:
            t_num = random.randint(30, 60)
            game_data[table_id] = {
                't_num': t_num,
                'msg_id': msg_id,
                'start_time': datetime.now().isoformat(),
                'last_update': datetime.now().isoformat()
            }
            save_game_data()
            return t_num

# ===== ОСНОВНЫЕ ФУНКЦИИ =====

def create_driver():
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--disable-extensions')
    options.add_argument('--window-size=1920,1080')
    options.binary_location = '/usr/bin/chromium'
    
    service = Service('/usr/bin/chromedriver')
    
    try:
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(30)
        return driver
    except Exception as e:
        logging.error(f"Ошибка создания драйвера: {e}")
        return None

def parse_cards(elements):
    cards = []
    for el in elements:
        try:
            cls = el.get_attribute('class')
            suit = next((s for c, s in SUIT_MAP.items() if c in cls), '?')
            val_match = re.search(r'value-(\d+)', cls)
            if val_match:
                val = val_match.group(1)
                value = VALUE_MAP.get(f'value-{val}', val)
            else:
                value = '?'
            cards.append(f"{value}{suit}")
        except:
            continue
    return cards

def format_cards(cards):
    return ''.join(cards)

def calculate_score(cards):
    score = 0
    aces = 0
    
    for card in cards:
        if not card:
            continue
        value = card[:-1]
        if value == 'A':
            aces += 1
            score += 11
        elif value in ['J', 'Q', 'K']:
            score += 10
        else:
            try:
                score += int(value)
            except:
                continue
    
    while score > 21 and aces > 0:
        score -= 10
        aces -= 1
    
    return score

def check_special_conditions(state, is_final=False):
    tags = []
    
    p_score = calculate_score(state['p_cards'])
    d_score = calculate_score(state['d_cards'])
    
    if p_score == 21 or d_score == 21:
        tags.append('#O')
    
    if is_final:
        if len(state['p_cards']) == 2 and len(state['d_cards']) == 2:
            tags.append('#R')
    else:
        if len(state['p_cards']) == 2 or len(state['d_cards']) == 2:
            tags.append('#R')
    
    if len(state['p_cards']) == 2:
        if state['p_cards'][0][:-1] == 'A' and state['p_cards'][1][:-1] == 'A':
            tags.append('#G')
    
    return ' '.join(tags)

def is_turn_indicator(driver, player="player"):
    try:
        if player == "player":
            selectors = [
                '.live-twenty-one-field-player:first-child .live-twenty-one-field-score__turn',
                '.live-twenty-one-field-player:first-child [class*="turn"]',
                '.live-twenty-one-field-player:first-child [class*="active"]'
            ]
        else:
            selectors = [
                '.live-twenty-one-field-player:last-child .live-twenty-one-field-score__turn',
                '.live-twenty-one-field-player:last-child [class*="turn"]',
                '.live-twenty-one-field-player:last-child [class*="active"]'
            ]
        
        for selector in selectors:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            if elements:
                for el in elements:
                    if el.is_displayed():
                        return True
    except:
        pass
    return False

def get_state(driver):
    try:
        player_score_elements = driver.find_elements(By.CSS_SELECTOR, '.live-twenty-one-field-player:first-child .live-twenty-one-field-score__label')
        player_score = player_score_elements[0].text if player_score_elements else "0"
        
        player_cards_elements = driver.find_elements(By.CSS_SELECTOR, '.live-twenty-one-field-player:first-child .scoreboard-card-games-card')
        player_cards = parse_cards(player_cards_elements)
        
        dealer_score_elements = driver.find_elements(By.CSS_SELECTOR, '.live-twenty-one-field-player:last-child .live-twenty-one-field-score__label')
        dealer_score = dealer_score_elements[0].text if dealer_score_elements else "0"
        
        dealer_cards_elements = driver.find_elements(By.CSS_SELECTOR, '.live-twenty-one-field-player:last-child .scoreboard-card-games-card')
        dealer_cards = parse_cards(dealer_cards_elements)
        
        player_turn = is_turn_indicator(driver, "player")
        dealer_turn = is_turn_indicator(driver, "dealer")
        
        return {
            'p_score': player_score,
            'p_cards': player_cards,
            'd_score': dealer_score,
            'd_cards': dealer_cards,
            'player_turn': player_turn,
            'dealer_turn': dealer_turn
        }
    except Exception as e:
        logging.error(f"Ошибка получения состояния: {e}")
        return None

def format_message(table_id, state, is_final=False, t_num=None):
    p_cards = format_cards(state['p_cards'])
    d_cards = format_cards(state['d_cards'])
    
    if is_final:
        try:
            p_score_int = int(state['p_score']) if state['p_score'].isdigit() else 0
            d_score_int = int(state['d_score']) if state['d_score'].isdigit() else 0
            total_score = p_score_int + d_score_int
        except:
            total_score = 0
            
        special_tags = check_special_conditions(state, is_final=True)
        if special_tags:
            return f"#{table_id}. {state['p_score']}({p_cards}) - {state['d_score']}({d_cards}) #T{total_score} {special_tags}"
        else:
            return f"#{table_id}. {state['p_score']}({p_cards}) - {state['d_score']}({d_cards}) #T{total_score}"
    else:
        if state.get('dealer_turn'):
            return f"⏰#N{table_id}. {state['p_score']}({p_cards}) - ▶ {state['d_score']}({d_cards})"
        else:
            return f"⏰#N{table_id}. ▶ {state['p_score']}({p_cards}) - {state['d_score']}({d_cards})"

def monitor_table(table_url, table_id):
    driver = None
    last_state = None
    msg_id = None
    t_num = get_t_number(table_id)
    game_active = True
    cards_appeared = False
    start_time = time.time()
    max_lifetime = 300
    crash_count = 0
    max_crashes = 2

    # Получаем сохраненный msg_id если есть
    with lock:
        if table_id in game_data and 'msg_id' in game_data[table_id]:
            saved_msg_id = game_data[table_id]['msg_id']
            if saved_msg_id:
                msg_id = saved_msg_id
                message_ids[table_id] = saved_msg_id
                logging.info(f"Стол {table_id}: загружен сохраненный msg_id {saved_msg_id}")

    try:
        driver = create_driver()
        if not driver:
            logging.error(f"Не удалось создать драйвер для стола {table_id}")
            return

        logging.info(f"Мониторинг стола {table_id} (T{t_num})")
        driver.get(table_url)
        time.sleep(3)

        while game_active and crash_count < max_crashes and (time.time() - start_time) < max_lifetime:
            try:
                state = get_state(driver)
                
                if not state:
                    time.sleep(1)
                    continue
                
                # Сброс счетчика при успехе
                crash_count = 0
                
                if not cards_appeared:
                    if state['p_cards'] or state['d_cards']:
                        cards_appeared = True
                        msg = format_message(table_id, state)
                        try:
                            if msg_id:
                                bot.edit_message_text(msg, CHANNEL_ID, msg_id)
                                logging.info(f"Стол {table_id}: сообщение отредактировано")
                            else:
                                sent = bot.send_message(CHANNEL_ID, msg)
                                msg_id = sent.message_id
                                get_t_number(table_id, msg_id)
                                with lock:
                                    message_ids[table_id] = msg_id
                                logging.info(f"Стол {table_id}: первое сообщение")
                            last_state = state
                        except Exception as e:
                            logging.error(f"Ошибка отправки: {e}")
                    continue

                if state != last_state:
                    msg = format_message(table_id, state)
                    try:
                        if msg_id:
                            bot.edit_message_text(msg, CHANNEL_ID, msg_id)
                        else:
                            sent = bot.send_message(CHANNEL_ID, msg)
                            msg_id = sent.message_id
                            get_t_number(table_id, msg_id)
                            with lock:
                                message_ids[table_id] = msg_id
                        last_state = state
                        logging.info(f"Стол {table_id} обновлен")
                    except Exception as e:
                        logging.error(f"Ошибка обновления: {e}")

                time.sleep(2)

            except WebDriverException as e:
                crash_count += 1
                logging.error(f"Краш {crash_count}/{max_crashes} для стола {table_id}")
                time.sleep(3)
                continue
            except Exception as e:
                logging.error(f"Ошибка в цикле: {e}")
                time.sleep(2)

        logging.info(f"Стол {table_id}: завершение работы")

    except Exception as e:
        logging.error(f"Критическая ошибка: {e}")
    finally:
        if driver:
            try:
                driver.quit()
                logging.info(f"Браузер для стола {table_id} закрыт")
            except:
                pass
        with lock:
            if table_id in active_tables:
                del active_tables[table_id]

def scan_tables():
    driver = None
    try:
        driver = create_driver()
        if not driver:
            return
        
        logging.info("Сканирование столов...")
        driver.get(MAIN_URL)
        time.sleep(3)

        links = driver.find_elements(By.CSS_SELECTOR, '.dashboard-game-block__link')
        ids = driver.find_elements(By.CSS_SELECTOR, '.dashboard-game-info__additional-info')

        tables = []
        for i, link in enumerate(links):
            if i >= len(ids):
                continue
            raw_id = ids[i].text.strip()
            match = re.search(r'(\d+)$', raw_id)
            table_id = match.group(1) if match else raw_id
            href = link.get_attribute('href')
            tables.append((table_id, href))

        if tables:
            last_table = tables[-1]
            table_id, href = last_table
            
            with lock:
                if len(active_tables) < MAX_BROWSERS and table_id not in active_tables:
                    thread = threading.Thread(target=monitor_table, args=(href, table_id))
                    thread.daemon = True
                    thread.start()
                    active_tables[table_id] = thread
                    logging.info(f"Запущен мониторинг нижнего стола {table_id}")

        logging.info(f"Найдено столов: {len(tables)}")

    except Exception as e:
        logging.error(f"Ошибка сканирования: {e}")
    finally:
        if driver:
            driver.quit()

def clean_threads():
    with lock:
        dead = [tid for tid, t in active_tables.items() if not t.is_alive()]
        for tid in dead:
            del active_tables[tid]
            logging.info(f"Поток стола {tid} очищен")

def main():
    load_game_data()
    logging.info("Бот запущен")
    
    while True:
        try:
            clean_threads()
            scan_tables()
            
            with lock:
                logging.info(f"Активных браузеров: {len(active_tables)}")
            
            time.sleep(CHECK_INTERVAL)
            
        except KeyboardInterrupt:
            break
        except Exception as e:
            logging.error(f"Ошибка: {e}")
            time.sleep(60)
    
    save_game_data()

if __name__ == "__main__":
    main()