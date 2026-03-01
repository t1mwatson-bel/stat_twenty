import threading
import time
import re
import logging
import random
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import StaleElementReferenceException, NoSuchElementException, TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import telebot
import pickle
import os
from telebot import apihelper

# ===== НАСТРОЙКИ =====
TOKEN = "8357635747:AAGAH_Rwk-vR8jGa6Q9F-AJLsMaEIj-JDBU"
CHANNEL_ID = "-1003179573402"
MAIN_URL = "https://1xlite-7636770.bar/ru/live/twentyone/1643503-twentyone-game"
MAX_BROWSERS = 2
DATA_FILE = "game_data.pkl"
DATA_RETENTION_DAYS = 3
# =====================

# Настройка обработки ошибок Telegram
apihelper.RETRY_ON_ERROR = True
apihelper.MAX_RETRIES = 5

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
active_tables = {}  # {table_id: thread}
message_ids = {}    # {table_id: message_id}
table_drivers = {}  # {table_id: driver}
last_messages = {}  # {table_id: last_message_text}
lock = threading.Lock()

class GameData:
    def __init__(self):
        self.completed_games = {}  # {table_id: {'message': str, 'timestamp': datetime, 't_num': int}}
        self.last_game_number = 0
        self.load_data()
    
    def load_data(self):
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, 'rb') as f:
                    data = pickle.load(f)
                    self.completed_games = data.get('completed_games', {})
                    self.last_game_number = data.get('last_game_number', 0)
                    self.clean_old_data()
                logging.info(f"Загружено {len(self.completed_games)} завершенных игр")
            except Exception as e:
                logging.error(f"Ошибка загрузки данных: {e}")
                self.completed_games = {}
                self.last_game_number = 0
    
    def save_data(self):
        try:
            self.clean_old_data()
            with open(DATA_FILE, 'wb') as f:
                pickle.dump({
                    'completed_games': self.completed_games,
                    'last_game_number': self.last_game_number
                }, f)
            logging.info(f"Данные сохранены. Всего игр: {len(self.completed_games)}")
        except Exception as e:
            logging.error(f"Ошибка сохранения данных: {e}")
    
    def clean_old_data(self):
        cutoff = datetime.now() - timedelta(days=DATA_RETENTION_DAYS)
        old_games = [tid for tid, data in self.completed_games.items() 
                    if data['timestamp'] < cutoff]
        for tid in old_games:
            del self.completed_games[tid]
        if old_games:
            logging.info(f"Удалено {len(old_games)} старых игр")
    
    def add_completed_game(self, table_id, message, t_num):
        self.completed_games[table_id] = {
            'message': message,
            'timestamp': datetime.now(),
            't_num': t_num
        }
        self.save_data()
    
    def is_game_completed(self, table_id):
        return table_id in self.completed_games
    
    def update_last_number(self, number):
        if number > self.last_game_number:
            self.last_game_number = number
            self.save_data()

game_data = GameData()

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
            val = re.search(r'value-(\d+)', cls)
            value = VALUE_MAP.get(f'value-{val.group(1)}', val.group(1)) if val else '?'
            cards.append(f"{value}{suit}")
        except StaleElementReferenceException:
            continue
    return cards

def format_cards(cards):
    return ''.join(cards)

def check_special_conditions(player_cards, dealer_cards, player_score, dealer_score):
    specials = []
    if player_score == "21" and len(player_cards) == 2:
        if all(card.startswith('A') for card in player_cards):
            specials.append('#G')
    if player_score == "21" or dealer_score == "21":
        specials.append('#O')
    if len(player_cards) == 2 and len(dealer_cards) == 2:
        specials.append('#R')
    return ' '.join(specials)

def determine_turn(state):
    try:
        if state.get('player_active', False):
            return 'player'
        elif state.get('dealer_active', False):
            return 'dealer'
    except:
        pass
    return None

def get_state_fast(driver):
    try:
        player_score = driver.find_element(By.CSS_SELECTOR, '.live-twenty-one-field-player:first-child .live-twenty-one-field-score__label').text
        player_cards = parse_cards(driver.find_elements(By.CSS_SELECTOR, '.live-twenty-one-field-player:first-child .scoreboard-card-games-card'))
        dealer_score = driver.find_element(By.CSS_SELECTOR, '.live-twenty-one-field-player:last-child .live-twenty-one-field-score__label').text
        dealer_cards = parse_cards(driver.find_elements(By.CSS_SELECTOR, '.live-twenty-one-field-player:last-child .scoreboard-card-games-card'))
        
        player_active = False
        dealer_active = False
        try:
            player_area = driver.find_element(By.CSS_SELECTOR, '.live-twenty-one-field-player:first-child')
            if 'active' in player_area.get_attribute('class').lower():
                player_active = True
            dealer_area = driver.find_element(By.CSS_SELECTOR, '.live-twenty-one-field-player:last-child')
            if 'active' in dealer_area.get_attribute('class').lower():
                dealer_active = True
        except:
            pass
            
        return {
            'p_score': player_score,
            'p_cards': player_cards,
            'd_score': dealer_score,
            'd_cards': dealer_cards,
            'player_active': player_active,
            'dealer_active': dealer_active
        }
    except Exception as e:
        return None

def is_game_truly_finished(driver):
    try:
        finished_element = driver.find_element(By.CSS_SELECTOR, 
            'span.ui-caption--size-xl.ui-caption--weight-700.ui-caption--color-clr-strong.ui-caption')
        if finished_element and 'Игра завершена' in finished_element.text:
            logging.info("Игра завершена (обнаружен селектор завершения)")
            return True
    except NoSuchElementException:
        pass
    
    try:
        new_game_btn = driver.find_elements(By.CSS_SELECTOR, '.ui-game-controls__button, .new-game-button, [class*="new"]')
        if new_game_btn and any(btn.is_displayed() for btn in new_game_btn):
            logging.info("Игра завершена (обнаружена кнопка новой игры)")
            return True
    except:
        pass
    
    return False

def safe_quit_driver(table_id):
    try:
        with lock:
            if table_id in table_drivers:
                driver = table_drivers[table_id]
                if driver:
                    logging.info(f"Закрытие драйвера для стола {table_id}")
                    driver.quit()
                    del table_drivers[table_id]
    except Exception as e:
        logging.error(f"Ошибка при закрытии драйвера стола {table_id}: {e}")

def format_message(table_id, state, is_final=False, t_num=None, table_number=None):
    p_cards = format_cards(state['p_cards'])
    d_cards = format_cards(state['d_cards'])
    
    if table_number is None:
        table_number = int(table_id) % 1440
        if table_number == 0:
            table_number = 1440
    
    try:
        total_score = int(state['p_score']) + int(state['d_score'])
    except:
        total_score = 0
    
    if is_final:
        try:
            p_score_int = int(state['p_score'])
            d_score_int = int(state['d_score'])
            
            if p_score_int > 21 and d_score_int <= 21:
                winner = 'dealer'
            elif d_score_int > 21 and p_score_int <= 21:
                winner = 'player'
            elif p_score_int > 21 and d_score_int > 21:
                winner = 'dealer' if d_score_int < p_score_int else 'player'
            else:
                winner = 'player' if p_score_int > d_score_int else 'dealer' if d_score_int > p_score_int else 'tie'
        except:
            winner = 'unknown'
        
        if winner == 'player':
            score_part = f"✅{state['p_score']}({p_cards}) - {state['d_score']}({d_cards})"
        elif winner == 'dealer':
            score_part = f"{state['p_score']}({p_cards}) - ✅{state['d_score']}({d_cards})"
        else:
            score_part = f"{state['p_score']}({p_cards}) - {state['d_score']}({d_cards})"
        
        specials = check_special_conditions(state['p_cards'], state['d_cards'], 
                                           state['p_score'], state['d_score'])
        
        base_msg = f"#N{table_number}. {score_part} #T{total_score}"
        return f"{base_msg} {specials}" if specials else base_msg
    else:
        turn = determine_turn(state)
        if turn == 'player':
            return f"⏰#N{table_number}. {state['p_score']}({p_cards}) 👈 {state['d_score']}({d_cards}) #T{total_score}"
        elif turn == 'dealer':
            return f"⏰#N{table_number}. {state['p_score']}({p_cards}) 👉 {state['d_score']}({d_cards}) #T{total_score}"
        else:
            return f"⏰#N{table_number}. {state['p_score']}({p_cards}) - {state['d_score']}({d_cards}) #T{total_score}"

def send_telegram_message_with_retry(chat_id, text):
    max_retries = 5
    for attempt in range(max_retries):
        try:
            return bot.send_message(chat_id, text)
        except Exception as e:
            if "429" in str(e):
                retry_after = 15
                match = re.search(r'retry after (\d+)', str(e))
                if match:
                    retry_after = int(match.group(1))
                logging.warning(f"Ошибка 429, ожидание {retry_after} сек")
                time.sleep(retry_after)
            else:
                raise e
    raise Exception(f"Не удалось отправить сообщение")

def edit_telegram_message_with_retry(chat_id, message_id, text):
    max_retries = 5
    for attempt in range(max_retries):
        try:
            return bot.edit_message_text(text, chat_id, message_id)
        except Exception as e:
            if "429" in str(e):
                retry_after = 15
                match = re.search(r'retry after (\d+)', str(e))
                if match:
                    retry_after = int(match.group(1))
                logging.warning(f"Ошибка 429, ожидание {retry_after} сек")
                time.sleep(retry_after)
            elif "400" in str(e) and "message is not modified" in str(e):
                return None
            else:
                logging.error(f"Ошибка при редактировании: {e}")
                return None
    return None

def find_empty_table(driver):
    """Найти самый верхний пустой стол"""
    try:
        # Ждем загрузки таблиц
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '.dashboard-game-block'))
        )
        
        tables = driver.find_elements(By.CSS_SELECTOR, '.dashboard-game-block')
        logging.info(f"Найдено столов: {len(tables)}")
        
        for i, table in enumerate(tables):
            try:
                # Получаем ID стола
                id_element = table.find_element(By.CSS_SELECTOR, '.dashboard-game-info__additional-info')
                table_id = id_element.text.strip()
                
                # Получаем ссылку
                link_element = table.find_element(By.CSS_SELECTOR, '.dashboard-game-block__link')
                href = link_element.get_attribute('href')
                
                # Проверяем, не завершена ли уже эта игра
                match = re.search(r'(\d+)$', table_id)
                numeric_id = match.group(1) if match else table_id
                
                if game_data.is_game_completed(numeric_id):
                    logging.info(f"Стол {numeric_id} уже завершен, пропускаем")
                    continue
                
                # Проверяем, запущен ли уже этот стол
                with lock:
                    if numeric_id in active_tables or numeric_id in message_ids:
                        logging.info(f"Стол {numeric_id} уже мониторится, пропускаем")
                        continue
                
                # Считаем этот стол подходящим
                logging.info(f"Выбран стол {i+1}: ID {table_id}")
                return href, numeric_id
                
            except Exception as e:
                logging.debug(f"Ошибка при обработке стола: {e}")
                continue
        
        logging.warning("Не найден подходящий пустой стол")
        return None, None
        
    except TimeoutException:
        logging.error("Таймаут при загрузке столов")
        return None, None
    except Exception as e:
        logging.error(f"Ошибка при поиске стола: {e}")
        return None, None

def get_next_game_time():
    """Получить время следующей игры (каждую минуту в 00 секунд)"""
    now = datetime.now()
    next_minute = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return next_minute

def wait_for_next_game():
    """Подождать до момента запуска нового браузера (за 10 сек до игры)"""
    next_game = get_next_game_time()
    launch_time = next_game - timedelta(seconds=10)
    
    now = datetime.now()
    if now < launch_time:
        wait_seconds = (launch_time - now).total_seconds()
        logging.info(f"Следующий запуск через {wait_seconds:.1f} сек (в {launch_time.strftime('%H:%M:%S')})")
        time.sleep(wait_seconds)
    else:
        # Если уже прошло время запуска, ждем до следующей минуты
        next_launch = launch_time + timedelta(minutes=1)
        wait_seconds = (next_launch - now).total_seconds()
        logging.info(f"Пропустили время запуска, ждем до {next_launch.strftime('%H:%M:%S')}")
        time.sleep(wait_seconds)

def monitor_table(table_url, table_id):
    driver = None
    last_state = None
    msg_id = None
    t_num = random.randint(30, 60)
    game_active = True
    no_response_count = 0
    max_no_response = 10
    table_number = int(table_id) % 1440
    if table_number == 0:
        table_number = 1440
    last_send_time = 0
    min_send_interval = 2
    initial_load = True
    last_activity_time = time.time()
    max_idle_time = 60
    cards_count_history = []
    verification_pending = False
    verification_start = 0

    if game_data.is_game_completed(table_id):
        logging.info(f"Стол {table_id} уже был завершен, пропускаем")
        return

    try:
        driver = create_driver()
        if not driver:
            logging.error(f"Не удалось создать драйвер для стола {table_id}.")
            return

        with lock:
            table_drivers[table_id] = driver

        logging.info(f"Начало мониторинга стола {table_id}")
        driver.get(table_url)
        
        # Ждем начала игры (появления карт)
        cards_loaded = False
        wait_start = time.time()
        max_wait = 30  # Ждем до 30 секунд после начала минуты
        
        while not cards_loaded and (time.time() - wait_start) < max_wait:
            try:
                player_cards = driver.find_elements(By.CSS_SELECTOR, '.live-twenty-one-field-player:first-child .scoreboard-card-games-card')
                dealer_cards = driver.find_elements(By.CSS_SELECTOR, '.live-twenty-one-field-player:last-child .scoreboard-card-games-card')
                
                if len(player_cards) > 0 or len(dealer_cards) > 0:
                    cards_loaded = True
                    logging.info(f"Карты загружены для стола {table_id}: игрок {len(player_cards)} карт, дилер {len(dealer_cards)} карт")
                    break
                
                # Проверяем, не завершена ли игра
                if is_game_truly_finished(driver):
                    logging.info(f"Игра на столе {table_id} уже завершена")
                    game_active = False
                    break
                    
                time.sleep(0.5)
            except Exception as e:
                time.sleep(0.5)
        
        if not cards_loaded and game_active:
            logging.warning(f"Карты не загрузились для стола {table_id} за {max_wait} секунд")
        
        # Даем время на стабилизацию очков
        if cards_loaded:
            time.sleep(1)
        
        logging.info(f"Старт мониторинга стола {table_id}")

        while game_active:
            try:
                current_time = time.time()
                
                if current_time - last_activity_time > max_idle_time:
                    if not is_game_truly_finished(driver):
                        logging.warning(f"Стол {table_id} бездействует {max_idle_time} сек, обновляем")
                        driver.refresh()
                        time.sleep(3)
                        last_activity_time = current_time
                        continue
                
                state = get_state_fast(driver)
                
                if not state:
                    no_response_count += 1
                    if no_response_count >= max_no_response:
                        if is_game_truly_finished(driver):
                            logging.info(f"Стол {table_id} завершен (таймаут)")
                            game_active = False
                            break
                        else:
                            no_response_count = max_no_response - 3
                    time.sleep(2)
                    continue
                
                last_activity_time = current_time
                no_response_count = 0
                
                if is_game_truly_finished(driver):
                    if not verification_pending:
                        verification_pending = True
                        verification_start = current_time
                        logging.info(f"Стол {table_id}: возможное завершение, ждем 3 сек")
                        time.sleep(3)
                        continue
                    elif current_time - verification_start >= 3:
                        if is_game_truly_finished(driver):
                            logging.info(f"Стол {table_id}: завершение подтверждено")
                            final_state = get_state_fast(driver) or state
                            
                            if len(final_state['p_cards']) > 0 or len(final_state['d_cards']) > 0:
                                final_msg = format_message(table_id, final_state, is_final=True, 
                                                          t_num=t_num, table_number=table_number)
                                
                                try:
                                    with lock:
                                        if msg_id:
                                            edit_telegram_message_with_retry(CHANNEL_ID, msg_id, final_msg)
                                        else:
                                            sent = send_telegram_message_with_retry(CHANNEL_ID, final_msg)
                                            msg_id = sent.message_id
                                            message_ids[table_id] = msg_id
                                    
                                    game_data.add_completed_game(table_id, final_msg, t_num)
                                    game_data.update_last_number(table_number)
                                    logging.info(f"Стол {table_id} завершен")
                                except Exception as e:
                                    logging.error(f"Ошибка отправки финала: {e}")
                            
                            game_active = False
                            break
                        else:
                            logging.info(f"Стол {table_id}: ложное срабатывание")
                            verification_pending = False
                else:
                    verification_pending = False

                if initial_load and len(state['p_cards']) == 0 and len(state['d_cards']) == 0:
                    time.sleep(1)
                    continue

                if initial_load:
                    p_score_int = int(state['p_score']) if state['p_score'].isdigit() else 0
                    if p_score_int > 21 and len(state['p_cards']) < 3:
                        time.sleep(1)
                        continue

                cards_count = (len(state['p_cards']), len(state['d_cards']))
                cards_count_history.append((current_time, cards_count))
                cards_count_history = [(t, c) for t, c in cards_count_history if current_time - t < 30]
                
                if len(cards_count_history) > 5:
                    first_count = cards_count_history[0][1]
                    last_count = cards_count_history[-1][1]
                    if first_count == last_count and (current_time - cards_count_history[0][0]) > 20:
                        if not is_game_truly_finished(driver):
                            logging.warning(f"Стол {table_id}: карты не меняются 20 сек")
                            driver.refresh()
                            time.sleep(3)
                            cards_count_history = []
                            last_activity_time = current_time
                            continue

                if state != last_state or initial_load:
                    cards_changed = False
                    if last_state:
                        if len(state['p_cards']) != len(last_state['p_cards']) or \
                           len(state['d_cards']) != len(last_state['d_cards']):
                            cards_changed = True
                    
                    if cards_changed or initial_load or (current_time - last_send_time) >= min_send_interval:
                        msg = format_message(table_id, state, table_number=table_number)
                        
                        with lock:
                            last_msg = last_messages.get(table_id)
                            if last_msg == msg and not initial_load and not cards_changed:
                                time.sleep(1)
                                continue
                        
                        try:
                            with lock:
                                if msg_id:
                                    result = edit_telegram_message_with_retry(CHANNEL_ID, msg_id, msg)
                                    if result is not None:
                                        last_messages[table_id] = msg
                                else:
                                    if len(state['p_cards']) > 0 or len(state['d_cards']) > 0:
                                        p_score_int = int(state['p_score']) if state['p_score'].isdigit() else 0
                                        d_score_int = int(state['d_score']) if state['d_score'].isdigit() else 0
                                        
                                        if (p_score_int <= 21 or len(state['p_cards']) >= 3) and \
                                           (d_score_int <= 21 or len(state['d_cards']) >= 3):
                                            sent = send_telegram_message_with_retry(CHANNEL_ID, msg)
                                            msg_id = sent.message_id
                                            message_ids[table_id] = msg_id
                                            last_messages[table_id] = msg
                                            logging.info(f"Стол {table_id}: первое сообщение #N{table_number}")
                                        else:
                                            time.sleep(1)
                                            continue
                            
                            if msg_id:
                                last_state = state
                                last_send_time = current_time
                                initial_load = False
                            
                            if cards_changed and last_state:
                                if len(state['p_cards']) > len(last_state['p_cards']):
                                    logging.info(f"Стол {table_id}: игрок добрал")
                                if len(state['d_cards']) > len(last_state['d_cards']):
                                    logging.info(f"Стол {table_id}: дилер добрал")
                                
                        except Exception as e:
                            logging.error(f"Ошибка отправки: {e}")
                            time.sleep(2)

                time.sleep(1)

            except StaleElementReferenceException:
                logging.warning(f"StaleElementReferenceException, обновляем")
                driver.refresh()
                time.sleep(3)
                last_activity_time = time.time()
                cards_count_history = []
            except Exception as e:
                logging.error(f"Ошибка в цикле: {e}")
                time.sleep(2)

    except Exception as e:
        logging.error(f"Критическая ошибка: {e}")
    finally:
        if driver and game_active:
            try:
                logging.info(f"Стол {table_id}: финальная проверка")
                time.sleep(3)
                if not is_game_truly_finished(driver):
                    state = get_state_fast(driver)
                    if state and (len(state['p_cards']) > 0 or len(state['d_cards']) > 0):
                        logging.warning(f"Стол {table_id}: принудительное завершение")
                        final_msg = format_message(table_id, state, is_final=True, 
                                                  t_num=t_num, table_number=table_number)
                        try:
                            with lock:
                                if msg_id:
                                    edit_telegram_message_with_retry(CHANNEL_ID, msg_id, final_msg)
                                else:
                                    send_telegram_message_with_retry(CHANNEL_ID, final_msg)
                        except:
                            pass
            except:
                pass
        
        safe_quit_driver(table_id)
        with lock:
            if table_id in active_tables:
                del active_tables[table_id]
            if table_id in message_ids:
                del message_ids[table_id]
            if table_id in last_messages:
                del last_messages[table_id]
        logging.info(f"Мониторинг стола {table_id} завершен")

def launch_new_table_monitor():
    """Запустить мониторинг нового стола"""
    with lock:
        if len(active_tables) >= MAX_BROWSERS:
            logging.info(f"Достигнут лимит браузеров ({MAX_BROWSERS}), ждем освобождения")
            return
    
    scan_driver = None
    try:
        scan_driver = create_driver()
        if not scan_driver:
            logging.error("Не удалось создать драйвер для поиска стола")
            return
        
        logging.info("Поиск пустого стола...")
        scan_driver.get(MAIN_URL)
        
        table_url, table_id = find_empty_table(scan_driver)
        
        if table_url and table_id:
            logging.info(f"Найден стол {table_id}, запускаем мониторинг")
            
            thread = threading.Thread(target=monitor_table, args=(table_url, table_id))
            thread.daemon = True
            thread.start()
            
            with lock:
                active_tables[table_id] = thread
            
            logging.info(f"Мониторинг стола {table_id} запущен")
        else:
            logging.warning("Не удалось найти подходящий стол")
            
    except Exception as e:
        logging.error(f"Ошибка при запуске нового монитора: {e}")
    finally:
        if scan_driver:
            scan_driver.quit()

def clean_threads():
    with lock:
        dead = [tid for tid, t in active_tables.items() if not t.is_alive()]
        for tid in dead:
            if tid in table_drivers:
                try:
                    table_drivers[tid].quit()
                except:
                    pass
                del table_drivers[tid]
            del active_tables[tid]
            if tid in message_ids:
                del message_ids[tid]
            if tid in last_messages:
                del last_messages[tid]
            logging.info(f"Поток стола {tid} очищен")

def main():
    logging.info("🚀 Бот запущен в режиме: запуск за 10 секунд до каждой минуты")
    logging.info(f"Максимум браузеров: {MAX_BROWSERS}")
    
    while True:
        try:
            # Очищаем завершенные потоки
            clean_threads()
            
            # Ждем до момента запуска (за 10 сек до следующей минуты)
            wait_for_next_game()
            
            # Запускаем мониторинг нового стола
            launch_new_table_monitor()
            
            # Небольшая пауза, чтобы не запустить дважды
            time.sleep(2)
            
        except KeyboardInterrupt:
            logging.info("Получен сигнал завершения")
            break
        except Exception as e:
            logging.error(f"Ошибка в главном цикле: {e}")
            time.sleep(5)
    
    # Завершение работы
    logging.info("Завершение работы бота...")
    with lock:
        for driver in table_drivers.values():
            try:
                driver.quit()
            except:
                pass
    game_data.save_data()

if __name__ == "__main__":
    main()
