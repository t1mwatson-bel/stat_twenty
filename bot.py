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
MAX_MONITORS = 3
CHECK_INTERVAL = 10
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
monitors = {}  # {table_id: thread}
message_ids = {}
game_data = {}
scanner_thread = None
scanner_running = False
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
    options.add_argument('--disable-background-networking')
    options.add_argument('--disable-client-side-phishing-detection')
    options.add_argument('--disable-default-apps')
    options.add_argument('--disable-hang-monitor')
    options.add_argument('--disable-sync')
    options.add_argument('--disable-web-resources')
    options.add_argument('--no-first-run')
    options.add_argument('--password-store=basic')
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

def is_game_finished(driver):
    try:
        status_elements = driver.find_elements(By.CSS_SELECTOR, '.scoreboard-card-games-board-status')
        if status_elements:
            status_text = status_elements[0].text.strip()
            if "Победа" in status_text or "Ничья" in status_text:
                logging.info(f"Игра завершена: {status_text}")
                return True
        
        timer_elements = driver.find_elements(By.CSS_SELECTOR, '.ui-game-timer__label')
        if timer_elements:
            timer_text = timer_elements[0].text.strip()
            if timer_text == "Игра завершена":
                logging.info("Игра завершена по таймеру")
                return True
    except:
        pass
    return False

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
    """Монитор для конкретного стола"""
    driver = None
    last_state = None
    msg_id = None
    t_num = get_t_number(table_id)
    cards_appeared = False
    start_time = time.time()
    max_lifetime = 300  # 5 минут
    crash_count = 0
    max_crashes = 2

    with lock:
        if table_id in game_data and 'msg_id' in game_data[table_id]:
            saved_msg_id = game_data[table_id]['msg_id']
            if saved_msg_id:
                msg_id = saved_msg_id
                message_ids[table_id] = saved_msg_id
                logging.info(f"Монитор {table_id}: загружен msg_id {saved_msg_id}")

    try:
        driver = create_driver()
        if not driver:
            logging.error(f"Монитор {table_id}: не удалось создать драйвер")
            return

        logging.info(f"Монитор {table_id} запущен (T{t_num})")
        driver.get(table_url)
        time.sleep(3)

        while crash_count < max_crashes and (time.time() - start_time) < max_lifetime:
            try:
                state = get_state(driver)
                
                if not state:
                    time.sleep(1)
                    continue
                
                crash_count = 0
                
                if not cards_appeared:
                    if state['p_cards'] or state['d_cards']:
                        cards_appeared = True
                        msg = format_message(table_id, state)
                        try:
                            if msg_id:
                                bot.edit_message_text(msg, CHANNEL_ID, msg_id)
                                logging.info(f"Монитор {table_id}: сообщение отредактировано")
                            else:
                                sent = bot.send_message(CHANNEL_ID, msg)
                                msg_id = sent.message_id
                                get_t_number(table_id, msg_id)
                                with lock:
                                    message_ids[table_id] = msg_id
                                logging.info(f"Монитор {table_id}: первое сообщение")
                            last_state = state
                        except Exception as e:
                            logging.error(f"Монитор {table_id}: ошибка отправки: {e}")
                    continue

                if is_game_finished(driver):
                    final_msg = format_message(table_id, state, is_final=True, t_num=t_num)
                    try:
                        if msg_id:
                            bot.edit_message_text(final_msg, CHANNEL_ID, msg_id)
                        else:
                            bot.send_message(CHANNEL_ID, final_msg)
                        logging.info(f"Монитор {table_id}: игра завершена")
                    except Exception as e:
                        logging.error(f"Монитор {table_id}: ошибка финала: {e}")
                    break

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
                        logging.info(f"Монитор {table_id}: обновлен")
                    except Exception as e:
                        logging.error(f"Монитор {table_id}: ошибка обновления: {e}")

                time.sleep(2)

            except WebDriverException as e:
                crash_count += 1
                logging.error(f"Монитор {table_id}: краш {crash_count}/{max_crashes}")
                time.sleep(3)
                continue
            except Exception as e:
                logging.error(f"Монитор {table_id}: ошибка: {e}")
                time.sleep(2)

        logging.info(f"Монитор {table_id}: завершение работы")

    except Exception as e:
        logging.error(f"Монитор {table_id}: критическая ошибка: {e}")
    finally:
        if driver:
            try:
                driver.quit()
                logging.info(f"Монитор {table_id}: браузер закрыт")
            except:
                pass
        with lock:
            if table_id in monitors:
                del monitors[table_id]

def scanner_worker():
    """Сканер - живет вечно, ищет новые столы"""
    global scanner_running
    logging.info("Сканер запущен")
    
    while scanner_running:
        driver = None
        try:
            driver = create_driver()
            if not driver:
                time.sleep(5)
                continue

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
                # Берем последний (нижний) стол
                last_table = tables[-1]
                table_id, href = last_table
                
                with lock:
                    if len(monitors) < MAX_MONITORS and table_id not in monitors:
                        thread = threading.Thread(target=monitor_table, args=(href, table_id))
                        thread.daemon = True
                        thread.start()
                        monitors[table_id] = thread
                        logging.info(f"Сканер: запущен монитор для стола {table_id}")

            logging.info(f"Сканер: найдено столов {len(tables)}, мониторов {len(monitors)}/{MAX_MONITORS}")

        except Exception as e:
            logging.error(f"Сканер: ошибка: {e}")
        finally:
            if driver:
                try:
                    driver.quit()
                except:
                    pass
        
        time.sleep(CHECK_INTERVAL)

def start_scanner():
    """Запуск сканера в отдельном потоке"""
    global scanner_thread, scanner_running
    if scanner_thread and scanner_thread.is_alive():
        return
    scanner_running = True
    scanner_thread = threading.Thread(target=scanner_worker)
    scanner_thread.daemon = True
    scanner_thread.start()

def stop_scanner():
    """Остановка сканера"""
    global scanner_running
    scanner_running = False

def clean_monitors():
    """Очистка завершенных мониторов"""
    with lock:
        dead = [tid for tid, t in monitors.items() if not t.is_alive()]
        for tid in dead:
            del monitors[tid]
            if tid in message_ids:
                del message_ids[tid]
            logging.info(f"Монитор {tid} очищен")

def main():
    load_game_data()
    logging.info("Бот запущен")
    
    start_scanner()
    
    try:
        while True:
            time.sleep(30)
            clean_monitors()
            with lock:
                logging.info(f"Активных мониторов: {len(monitors)}/{MAX_MONITORS}")
    except KeyboardInterrupt:
        logging.info("Остановка...")
        stop_scanner()
    finally:
        save_game_data()
        logging.info("Бот остановлен")

if __name__ == "__main__":
    main()