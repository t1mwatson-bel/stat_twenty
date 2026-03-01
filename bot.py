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
from selenium.common.exceptions import StaleElementReferenceException, NoSuchElementException, TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import telebot

# ===== НАСТРОЙКИ =====
TOKEN = "8357635747:AAGAH_Rwk-vR8jGa6Q9F-AJLsMaEIj-JDBU"
CHANNEL_ID = "-1003179573402"
MAIN_URL = "https://1xlite-7636770.bar/ru/live/twentyone/1643503-twentyone-game"
MAX_BROWSERS = 3
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
message_ids = {}  # {table_id: message_id}
game_data = {}
lock = threading.Lock()

# ===== ФУНКЦИИ ДЛЯ РАБОТЫ С ДАННЫМИ =====

def load_game_data():
    """Загрузка данных из файла"""
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
                    except (ValueError, KeyError):
                        continue
                logging.info(f"Загружено {len(game_data)} активных игр из файла")
    except Exception as e:
        logging.error(f"Ошибка загрузки данных: {e}")
        game_data = {}

def save_game_data():
    """Сохранение данных в файл"""
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(game_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Ошибка сохранения данных: {e}")

def cleanup_old_data():
    """Очистка старых данных"""
    current_time = datetime.now()
    old_tables = []
    
    with lock:
        for table_id, info in list(game_data.items()):
            try:
                start_time = datetime.fromisoformat(info['start_time'])
                if (current_time - start_time) >= timedelta(days=MAX_DAYS):
                    old_tables.append(table_id)
                    del game_data[table_id]
            except (ValueError, KeyError):
                old_tables.append(table_id)
                del game_data[table_id]
        
        if old_tables:
            logging.info(f"Удалено {len(old_tables)} старых игр")
            save_game_data()

def get_t_number(table_id):
    """Получение номера T для стола"""
    with lock:
        if table_id in game_data:
            return game_data[table_id]['t_num']
        else:
            t_num = random.randint(30, 60)
            game_data[table_id] = {
                't_num': t_num,
                'start_time': datetime.now().isoformat(),
                'last_update': datetime.now().isoformat()
            }
            save_game_data()
            return t_num

def update_game_data(table_id):
    """Обновление времени последнего изменения"""
    with lock:
        if table_id in game_data:
            game_data[table_id]['last_update'] = datetime.now().isoformat()
            save_game_data()

# ===== ОСНОВНЫЕ ФУНКЦИИ =====

def create_driver():
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--disable-extensions')
    options.add_argument('--disable-infobars')
    options.add_argument('--disable-plugins')
    options.add_argument('--disable-software-rasterizer')
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
        except StaleElementReferenceException:
            continue
    return cards

def format_cards(cards):
    return ''.join(cards)

def calculate_score(cards):
    """Подсчет очков по картам"""
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
            except ValueError:
                continue
    
    while score > 21 and aces > 0:
        score -= 10
        aces -= 1
    
    return score

def check_special_conditions(state):
    """Проверка особых условий: #O (21), #R (2 карты), #G (золотое очко)"""
    tags = []
    
    if not state or not state.get('p_cards') or not state.get('d_cards'):
        return ''
    
    p_score = calculate_score(state['p_cards'])
    d_score = calculate_score(state['d_cards'])
    
    if p_score == 21 or d_score == 21:
        tags.append('#O')
    
    if len(state['p_cards']) == 2 or len(state['d_cards']) == 2:
        tags.append('#R')
    
    if len(state['p_cards']) == 2 and len(state['d_cards']) >= 1:
        if state['p_cards'][0][:-1] == 'A' and state['p_cards'][1][:-1] == 'A':
            tags.append('#G')
    
    tags = list(dict.fromkeys(tags))
    return ' '.join(tags)

def is_turn_indicator(driver, player="player"):
    """Проверка, чей сейчас ход"""
    try:
        if player == "player":
            selectors = [
                '.live-twenty-one-field-player:first-child .live-twenty-one-field-score__turn',
                '.live-twenty-one-field-player:first-child [class*="turn"]',
                '.live-twenty-one-field-player:first-child .ui-game-turn-indicator',
                '.live-twenty-one-field-player:first-child [class*="active"]'
            ]
        else:
            selectors = [
                '.live-twenty-one-field-player:last-child .live-twenty-one-field-score__turn',
                '.live-twenty-one-field-player:last-child [class*="turn"]',
                '.live-twenty-one-field-player:last-child .ui-game-turn-indicator',
                '.live-twenty-one-field-player:last-child [class*="active"]'
            ]
        
        for selector in selectors:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            if elements and elements[0].is_displayed():
                return True
    except:
        pass
    return False

def is_game_finished(driver):
    """Проверка завершения игры"""
    try:
        status_elements = driver.find_elements(By.CSS_SELECTOR, '.ui-game-timer__label')
        if status_elements:
            status_text = status_elements[0].text.lower()
            finished_keywords = ['завершен', 'завершена', 'finished', 'ended', 'game over']
            if any(keyword in status_text for keyword in finished_keywords):
                return True
        
        new_game_selectors = [
            '.ui-game-controls__button',
            '.new-game-button',
            '[class*="new"]',
            '[class*="restart"]',
            '[class*="again"]'
        ]
        
        for selector in new_game_selectors:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            if elements and elements[0].is_displayed():
                return True
        
    except Exception:
        pass
    
    return False

def safe_quit_driver(driver, table_id):
    """Безопасное закрытие драйвера"""
    try:
        if driver:
            logging.info(f"Закрытие драйвера для стола {table_id}")
            driver.quit()
    except Exception as e:
        logging.error(f"Ошибка при закрытии драйвера стола {table_id}: {e}")

def get_state(driver):
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '.live-twenty-one-field-player'))
        )
        
        player_score_elem = driver.find_elements(By.CSS_SELECTOR, '.live-twenty-one-field-player:first-child .live-twenty-one-field-score__label')
        player_score = player_score_elem[0].text if player_score_elem else "0"
        
        player_cards_elem = driver.find_elements(By.CSS_SELECTOR, '.live-twenty-one-field-player:first-child .scoreboard-card-games-card')
        player_cards = parse_cards(player_cards_elem)
        
        dealer_score_elem = driver.find_elements(By.CSS_SELECTOR, '.live-twenty-one-field-player:last-child .live-twenty-one-field-score__label')
        dealer_score = dealer_score_elem[0].text if dealer_score_elem else "0"
        
        dealer_cards_elem = driver.find_elements(By.CSS_SELECTOR, '.live-twenty-one-field-player:last-child .scoreboard-card-games-card')
        dealer_cards = parse_cards(dealer_cards_elem)
        
        try:
            status_elem = driver.find_elements(By.CSS_SELECTOR, '.ui-game-timer__label')
            status = status_elem[0].text if status_elem else "Идет игра"
        except:
            status = "Идет игра"
        
        player_turn = is_turn_indicator(driver, "player")
        dealer_turn = is_turn_indicator(driver, "dealer")
        
        return {
            'p_score': player_score,
            'p_cards': player_cards,
            'd_score': dealer_score,
            'd_cards': dealer_cards,
            'status': status,
            'player_turn': player_turn,
            'dealer_turn': dealer_turn
        }
    except TimeoutException:
        return None
    except Exception as e:
        logging.error(f"Ошибка получения состояния: {e}")
        return None

def format_message(table_id, state, is_final=False, t_num=None):
    """Форматирование сообщения"""
    p_cards = format_cards(state['p_cards'])
    d_cards = format_cards(state['d_cards'])
    
    if is_final:
        special_tags = check_special_conditions(state)
        if special_tags:
            return f"#{table_id}. {state['p_score']}({p_cards}) - {state['d_score']}({d_cards}) #T{t_num} {special_tags}"
        else:
            return f"#{table_id}. {state['p_score']}({p_cards}) - {state['d_score']}({d_cards}) #T{t_num}"
    else:
        if state.get('dealer_turn', False):
            return f"⏰#{table_id}. {state['p_score']}({p_cards}) - ▶ {state['d_score']}({d_cards})"
        else:
            return f"⏰#{table_id}. ▶ {state['p_score']}({p_cards}) - {state['d_score']}({d_cards})"

def monitor_table(table_url, table_id):
    driver = None
    last_state = None
    msg_id = None
    t_num = get_t_number(table_id)
    game_active = True
    no_response_count = 0
    max_no_response = 5
    game_started = False  # Флаг, что игра началась (появились карты)

    try:
        driver = create_driver()
        if not driver:
            logging.error(f"Не удалось создать драйвер для стола {table_id}.")
            return

        logging.info(f"Начало мониторинга стола {table_id} (T{t_num})")
        driver.get(table_url)
        time.sleep(5)

        # Ждем появления первых карт (игровое поле загрузилось)
        wait_start = time.time()
        while not game_started and time.time() - wait_start < 30:
            try:
                state = get_state(driver)
                if state and (state['p_cards'] or state['d_cards']):
                    game_started = True
                    logging.info(f"Стол {table_id}: игра началась, карты появились")
                else:
                    time.sleep(2)
            except:
                time.sleep(2)

        if not game_started:
            logging.warning(f"Стол {table_id}: карты так и не появились, завершаем")
            return

        while game_active:
            try:
                state = get_state(driver)
                
                if not state:
                    no_response_count += 1
                    if no_response_count >= max_no_response:
                        logging.warning(f"Стол {table_id} не отвечает, завершаем мониторинг")
                        break
                    time.sleep(2)
                    continue
                
                no_response_count = 0
                
                # Проверка завершения игры
                if is_game_finished(driver):
                    final_state = get_state(driver) or state
                    final_msg = format_message(table_id, final_state, is_final=True, t_num=t_num)
                    
                    try:
                        if msg_id:
                            bot.edit_message_text(final_msg, CHANNEL_ID, msg_id)
                        else:
                            # Если по какой-то причине нет msg_id, отправляем новое
                            bot.send_message(CHANNEL_ID, final_msg)
                        logging.info(f"Стол {table_id} завершен")
                    except Exception as e:
                        logging.error(f"Ошибка отправки финального сообщения: {e}")
                    
                    game_active = False
                    break

                # Проверяем, изменилось ли состояние (карты или ход)
                state_changed = False
                if last_state:
                    # Сравниваем карты и счет
                    if (state['p_score'] != last_state['p_score'] or
                        state['d_score'] != last_state['d_score'] or
                        str(state['p_cards']) != str(last_state['p_cards']) or
                        str(state['d_cards']) != str(last_state['d_cards'])):
                        state_changed = True
                    # Также обновляем при смене хода (стрелка)
                    elif state['player_turn'] != last_state['player_turn'] or state['dealer_turn'] != last_state['dealer_turn']:
                        state_changed = True
                else:
                    # Первое состояние после появления карт
                    state_changed = True

                if state_changed:
                    msg = format_message(table_id, state)
                    try:
                        if msg_id:
                            # Редактируем существующее сообщение
                            bot.edit_message_text(msg, CHANNEL_ID, msg_id)
                        else:
                            # Отправляем только первое сообщение (когда появились карты)
                            sent = bot.send_message(CHANNEL_ID, msg)
                            msg_id = sent.message_id
                            with lock:
                                message_ids[table_id] = msg_id
                        
                        last_state = state.copy()
                        update_game_data(table_id)
                        logging.info(f"Стол {table_id} обновлен")
                    except Exception as e:
                        logging.error(f"Ошибка отправки сообщения: {e}")

                time.sleep(2)

            except StaleElementReferenceException:
                logging.warning(f"StaleElementReferenceException для стола {table_id}")
                driver.refresh()
                time.sleep(3)
            except Exception as e:
                logging.error(f"Ошибка в цикле мониторинга: {e}")
                time.sleep(2)

    except Exception as e:
        logging.error(f"Критическая ошибка мониторинга стола {table_id}: {e}")
    finally:
        safe_quit_driver(driver, table_id)
        
        with lock:
            if table_id in active_tables:
                del active_tables[table_id]
            if table_id in message_ids:
                del message_ids[table_id]
        
        logging.info(f"Мониторинг стола {table_id} завершен")

def scan_tables():
    driver = None
    try:
        driver = create_driver()
        if not driver:
            logging.error("Не удалось создать драйвер для сканирования")
            return
        
        logging.info("Сканирование новых столов...")
        driver.get(MAIN_URL)
        time.sleep(5)

        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '.dashboard-game-block__link'))
        )

        links = driver.find_elements(By.CSS_SELECTOR, '.dashboard-game-block__link')
        ids = driver.find_elements(By.CSS_SELECTOR, '.dashboard-game-info__additional-info')

        new_tables = []
        for i, link in enumerate(links):
            if i >= len(ids):
                continue
            raw_id = ids[i].text.strip()
            match = re.search(r'(\d+)$', raw_id)
            table_id = match.group(1) if match else raw_id
            href = link.get_attribute('href')

            with lock:
                if table_id in active_tables or table_id in message_ids:
                    continue
            new_tables.append((table_id, href))

        logging.info(f"Найдено новых столов: {len(new_tables)}")

        for table_id, href in new_tables:
            with lock:
                if len(active_tables) >= MAX_BROWSERS:
                    break
                    
            thread = threading.Thread(target=monitor_table, args=(href, table_id))
            thread.daemon = True
            thread.start()
            
            with lock:
                active_tables[table_id] = thread
            
            logging.info(f"Запущен мониторинг стола {table_id}")
            time.sleep(3)

    except Exception as e:
        logging.error(f"Ошибка сканирования: {e}")
    finally:
        if driver:
            driver.quit()

def clean_threads():
    """Очистка завершенных потоков"""
    with lock:
        dead = [tid for tid, t in active_tables.items() if not t.is_alive()]
        for tid in dead:
            del active_tables[tid]
            if tid in message_ids:
                del message_ids[tid]
            logging.info(f"Поток стола {tid} очищен")

def main():
    load_game_data()
    cleanup_old_data()
    
    logging.info("Бот запущен")
    
    try:
        last_cleanup = datetime.now()
        
        while True:
            try:
                clean_threads()
                scan_tables()
                
                if datetime.now() - last_cleanup > timedelta(hours=1):
                    cleanup_old_data()
                    last_cleanup = datetime.now()
                
                with lock:
                    logging.info(f"Активных столов: {len(active_tables)}, Сохранено игр: {len(game_data)}")
                
                time.sleep(CHECK_INTERVAL)
                
            except KeyboardInterrupt:
                logging.info("Получен сигнал завершения")
                break
            except Exception as e:
                logging.error(f"Ошибка в главном цикле: {e}")
                time.sleep(60)
    finally:
        save_game_data()
        logging.info("Завершение работы бота...")
        with lock:
            active_tables.clear()
            message_ids.clear()

if __name__ == "__main__":
    main()