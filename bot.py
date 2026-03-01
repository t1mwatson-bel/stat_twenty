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
CHECK_INTERVAL = 30
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
last_messages = {}  # {table_id: last_message_text} для проверки изменений
lock = threading.Lock()

class GameData:
    def __init__(self):
        self.completed_games = {}  # {table_id: {'message': str, 'timestamp': datetime, 't_num': int}}
        self.last_game_number = 0
        self.load_data()
    
    def load_data(self):
        """Загрузка данных из файла"""
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, 'rb') as f:
                    data = pickle.load(f)
                    self.completed_games = data.get('completed_games', {})
                    self.last_game_number = data.get('last_game_number', 0)
                    
                    # Очистка старых данных
                    self.clean_old_data()
                    
                logging.info(f"Загружено {len(self.completed_games)} завершенных игр")
            except Exception as e:
                logging.error(f"Ошибка загрузки данных: {e}")
                self.completed_games = {}
                self.last_game_number = 0
    
    def save_data(self):
        """Сохранение данных в файл"""
        try:
            # Очистка старых данных перед сохранением
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
        """Удаление данных старше 3 дней"""
        cutoff = datetime.now() - timedelta(days=DATA_RETENTION_DAYS)
        old_games = [tid for tid, data in self.completed_games.items() 
                    if data['timestamp'] < cutoff]
        
        for tid in old_games:
            del self.completed_games[tid]
        
        if old_games:
            logging.info(f"Удалено {len(old_games)} старых игр")
    
    def add_completed_game(self, table_id, message, t_num):
        """Добавление завершенной игры"""
        self.completed_games[table_id] = {
            'message': message,
            'timestamp': datetime.now(),
            't_num': t_num
        }
        self.save_data()
    
    def is_game_completed(self, table_id):
        """Проверка, была ли игра уже завершена"""
        return table_id in self.completed_games
    
    def update_last_number(self, number):
        """Обновление последнего номера игры"""
        if number > self.last_game_number:
            self.last_game_number = number
            self.save_data()

game_data = GameData()

def create_driver():
    """Создание нового драйвера для каждого стола"""
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
    """Проверка специальных условий игры"""
    specials = []
    
    # Проверка на золотое очко (два туза у игрока)
    if player_score == "21" and len(player_cards) == 2:
        if all(card.startswith('A') for card in player_cards):
            specials.append('#G')
    
    # Проверка на 21 очко у игрока или дилера
    if player_score == "21" or dealer_score == "21":
        specials.append('#O')
    
    # Проверка на завершение игры с 2 картами
    if len(player_cards) == 2 and len(dealer_cards) == 2:
        specials.append('#R')
    
    return ' '.join(specials)

def determine_turn(state):
    """Определение, чей сейчас ход"""
    try:
        if state.get('player_active', False):
            return 'player'
        elif state.get('dealer_active', False):
            return 'dealer'
    except:
        pass
    return None

def get_state_fast(driver):
    """Быстрое получение состояния без лишних проверок"""
    try:
        player_score = driver.find_element(By.CSS_SELECTOR, '.live-twenty-one-field-player:first-child .live-twenty-one-field-score__label').text
        player_cards = parse_cards(driver.find_elements(By.CSS_SELECTOR, '.live-twenty-one-field-player:first-child .scoreboard-card-games-card'))
        dealer_score = driver.find_element(By.CSS_SELECTOR, '.live-twenty-one-field-player:last-child .live-twenty-one-field-score__label').text
        dealer_cards = parse_cards(driver.find_elements(By.CSS_SELECTOR, '.live-twenty-one-field-player:last-child .scoreboard-card-games-card'))
        
        # Определяем, чей ход
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
    """Тщательная проверка завершения игры"""
    try:
        # Проверка по селектору завершения
        finished_element = driver.find_element(By.CSS_SELECTOR, 
            'span.ui-caption--size-xl.ui-caption--weight-700.ui-caption--color-clr-strong.ui-caption')
        
        if finished_element and 'Игра завершена' in finished_element.text:
            logging.info("Игра завершена (обнаружен селектор завершения)")
            return True
    except NoSuchElementException:
        pass
    
    # Проверка по наличию кнопки новой игры или других признаков
    try:
        new_game_btn = driver.find_elements(By.CSS_SELECTOR, '.ui-game-controls__button, .new-game-button, [class*="new"]')
        if new_game_btn and any(btn.is_displayed() for btn in new_game_btn):
            logging.info("Игра завершена (обнаружена кнопка новой игры)")
            return True
    except:
        pass
    
    return False

def safe_quit_driver(table_id):
    """Безопасное закрытие драйвера для конкретного стола"""
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
    """Форматирование сообщения с учетом специальных условий и префикса N"""
    p_cards = format_cards(state['p_cards'])
    d_cards = format_cards(state['d_cards'])
    
    # Определяем номер игры (циклически от 1 до 1440)
    if table_number is None:
        table_number = int(table_id) % 1440
        if table_number == 0:
            table_number = 1440
    
    # Считаем общую сумму очков
    try:
        total_score = int(state['p_score']) + int(state['d_score'])
    except:
        total_score = 0
    
    if is_final:
        # Определяем победителя
        try:
            p_score_int = int(state['p_score'])
            d_score_int = int(state['d_score'])
            
            # Проверка на перебор
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
        
        # Формируем сообщение с победителем
        if winner == 'player':
            score_part = f"✅{state['p_score']}({p_cards}) - {state['d_score']}({d_cards})"
        elif winner == 'dealer':
            score_part = f"{state['p_score']}({p_cards}) - ✅{state['d_score']}({d_cards})"
        else:
            score_part = f"{state['p_score']}({p_cards}) - {state['d_score']}({d_cards})"
        
        specials = check_special_conditions(state['p_cards'], state['d_cards'], 
                                           state['p_score'], state['d_score'])
        
        base_msg = f"#N{table_number}. {score_part} #T{total_score}"
        
        if specials:
            return f"{base_msg} {specials}"
        else:
            return base_msg
    else:
        # Определяем, кто сейчас ходит
        turn = determine_turn(state)
        
        if turn == 'player':
            # Игрок добирает - стрелка перед дилером
            return f"⏰#N{table_number}. {state['p_score']}({p_cards}) 👈 {state['d_score']}({d_cards}) #T{total_score}"
        elif turn == 'dealer':
            # Дилер добирает - стрелка перед игроком
            return f"⏰#N{table_number}. {state['p_score']}({p_cards}) 👉 {state['d_score']}({d_cards}) #T{total_score}"
        else:
            # Никто не добирает - без стрелки
            return f"⏰#N{table_number}. {state['p_score']}({p_cards}) - {state['d_score']}({d_cards}) #T{total_score}"

def send_telegram_message_with_retry(chat_id, text, reply_to_message_id=None, parse_mode=None):
    """Отправка сообщения с повторными попытками при ошибке 429"""
    max_retries = 5
    for attempt in range(max_retries):
        try:
            return bot.send_message(chat_id, text, reply_to_message_id=reply_to_message_id, parse_mode=parse_mode)
        except Exception as e:
            if "429" in str(e):
                retry_after = 15
                match = re.search(r'retry after (\d+)', str(e))
                if match:
                    retry_after = int(match.group(1))
                
                logging.warning(f"Ошибка 429 при отправке, ожидание {retry_after} сек (попытка {attempt + 1}/{max_retries})")
                time.sleep(retry_after)
            else:
                raise e
    
    raise Exception(f"Не удалось отправить сообщение после {max_retries} попыток")

def edit_telegram_message_with_retry(chat_id, message_id, text, parse_mode=None):
    """Редактирование сообщения с повторными попытками при ошибке 429 и игнорированием ошибки 400"""
    max_retries = 5
    for attempt in range(max_retries):
        try:
            return bot.edit_message_text(text, chat_id, message_id, parse_mode=parse_mode)
        except Exception as e:
            if "429" in str(e):
                retry_after = 15
                match = re.search(r'retry after (\d+)', str(e))
                if match:
                    retry_after = int(match.group(1))
                
                logging.warning(f"Ошибка 429 при редактировании, ожидание {retry_after} сек (попытка {attempt + 1}/{max_retries})")
                time.sleep(retry_after)
            elif "400" in str(e) and "message is not modified" in str(e):
                # Игнорируем ошибку "message is not modified"
                logging.debug(f"Сообщение не изменилось")
                return None
            else:
                logging.error(f"Ошибка при редактировании сообщения: {e}")
                return None
    
    logging.error(f"Не удалось отредактировать сообщение после {max_retries} попыток")
    return None

def monitor_table(table_url, table_id):
    """Мониторинг конкретного стола в отдельном потоке"""
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
        
        # Ждем загрузки карт
        cards_loaded = False
        wait_start = time.time()
        max_wait = 15
        check_interval = 0.5
        
        # Сначала просто ждем появления любых карт
        while not cards_loaded and (time.time() - wait_start) < max_wait:
            try:
                player_cards = driver.find_elements(By.CSS_SELECTOR, '.live-twenty-one-field-player:first-child .scoreboard-card-games-card')
                dealer_cards = driver.find_elements(By.CSS_SELECTOR, '.live-twenty-one-field-player:last-child .scoreboard-card-games-card')
                
                if len(player_cards) > 0 or len(dealer_cards) > 0:
                    cards_loaded = True
                    logging.info(f"Карты загружены для стола {table_id}: игрок {len(player_cards)} карт, дилер {len(dealer_cards)} карт")
                    break
                    
                time.sleep(check_interval)
            except Exception as e:
                time.sleep(check_interval)
        
        # Дополнительное ожидание для получения корректных очков
        if cards_loaded:
            time.sleep(2)  # Даем время на обновление очков
        else:
            logging.warning(f"Карты не загрузились для стола {table_id} за {max_wait} секунд")
        
        logging.info(f"Старт мониторинга стола {table_id}")

        while game_active:
            try:
                current_time = time.time()
                
                # Проверка на долгое бездействие
                if current_time - last_activity_time > max_idle_time:
                    if not is_game_truly_finished(driver):
                        logging.warning(f"Стол {table_id} бездействует {max_idle_time} сек, обновляем страницу")
                        driver.refresh()
                        time.sleep(3)
                        last_activity_time = current_time
                        continue
                
                state = get_state_fast(driver)
                
                if not state:
                    no_response_count += 1
                    if no_response_count >= max_no_response:
                        if is_game_truly_finished(driver):
                            logging.info(f"Стол {table_id} завершен (обнаружено при проверке таймаута)")
                            game_active = False
                            break
                        else:
                            logging.warning(f"Стол {table_id} не отвечает {max_no_response} раз, но игра не завершена")
                            no_response_count = max_no_response - 3
                    time.sleep(2)
                    continue
                
                last_activity_time = current_time
                no_response_count = 0
                
                # Проверка завершения игры с верификацией
                if is_game_truly_finished(driver):
                    if not verification_pending:
                        # Первое обнаружение - запускаем верификацию
                        verification_pending = True
                        verification_start = current_time
                        logging.info(f"Стол {table_id}: обнаружено возможное завершение, ждем 3 сек для подтверждения")
                        time.sleep(3)
                        continue
                    elif current_time - verification_start >= 3:
                        # Прошло 3 секунды, проверяем снова
                        if is_game_truly_finished(driver):
                            # Игра действительно завершена
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
                                    logging.info(f"Стол {table_id} завершен, финальное сообщение отправлено")
                                except Exception as e:
                                    logging.error(f"Ошибка отправки финального сообщения: {e}")
                            
                            game_active = False
                            break
                        else:
                            # Ложное срабатывание
                            logging.info(f"Стол {table_id}: ложное срабатывание завершения, продолжаем")
                            verification_pending = False
                else:
                    verification_pending = False

                # Пропускаем пустые состояния только в начале
                if initial_load and len(state['p_cards']) == 0 and len(state['d_cards']) == 0:
                    time.sleep(1)
                    continue

                # Проверяем, что очки выглядят реалистично (не 21 если карт мало)
                if initial_load:
                    p_score_int = int(state['p_score']) if state['p_score'].isdigit() else 0
                    if p_score_int > 21 and len(state['p_cards']) < 3:
                        logging.debug(f"Стол {table_id}: подозрительные очки {state['p_score']} при {len(state['p_cards'])} картах, ждем обновления")
                        time.sleep(1)
                        continue

                # Отслеживаем историю количества карт
                cards_count = (len(state['p_cards']), len(state['d_cards']))
                cards_count_history.append((current_time, cards_count))
                cards_count_history = [(t, c) for t, c in cards_count_history if current_time - t < 30]
                
                # Если карты не менялись 20 секунд, возможно игра зависла
                if len(cards_count_history) > 5:
                    first_count = cards_count_history[0][1]
                    last_count = cards_count_history[-1][1]
                    if first_count == last_count and (current_time - cards_count_history[0][0]) > 20:
                        if not is_game_truly_finished(driver):
                            logging.warning(f"Стол {table_id}: карты не меняются 20 сек, обновляем страницу")
                            driver.refresh()
                            time.sleep(3)
                            cards_count_history = []
                            last_activity_time = current_time
                            continue

                # Отправка обновлений
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
                                        # Проверяем, что очки реалистичны перед первой отправкой
                                        p_score_int = int(state['p_score']) if state['p_score'].isdigit() else 0
                                        d_score_int = int(state['d_score']) if state['d_score'].isdigit() else 0
                                        
                                        if (p_score_int <= 21 or len(state['p_cards']) >= 3) and \
                                           (d_score_int <= 21 or len(state['d_cards']) >= 3):
                                            sent = send_telegram_message_with_retry(CHANNEL_ID, msg)
                                            msg_id = sent.message_id
                                            message_ids[table_id] = msg_id
                                            last_messages[table_id] = msg
                                            logging.info(f"Стол {table_id}: первое сообщение #N{table_number} с очками {state['p_score']}-{state['d_score']}")
                                        else:
                                            logging.debug(f"Стол {table_id}: ждем корректных очков, сейчас {state['p_score']}-{state['d_score']}")
                                            time.sleep(1)
                                            continue
                            
                            if msg_id:
                                last_state = state
                                last_send_time = current_time
                                initial_load = False
                            
                            if cards_changed and last_state:
                                if len(state['p_cards']) > len(last_state['p_cards']):
                                    logging.info(f"Стол {table_id}: игрок добрал карту -> {len(state['p_cards'])} карт")
                                if len(state['d_cards']) > len(last_state['d_cards']):
                                    logging.info(f"Стол {table_id}: дилер добрал карту -> {len(state['d_cards'])} карт")
                                
                        except Exception as e:
                            logging.error(f"Ошибка отправки: {e}")
                            time.sleep(2)

                time.sleep(1)

            except StaleElementReferenceException:
                logging.warning(f"StaleElementReferenceException для стола {table_id}, обновляем")
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
        # Финальная проверка перед закрытием
        if driver and game_active:
            try:
                logging.info(f"Стол {table_id}: финальная проверка перед закрытием")
                time.sleep(3)
                if is_game_truly_finished(driver):
                    logging.info(f"Стол {table_id}: игра завершена, закрываем")
                else:
                    # Игра не завершена, пробуем отправить текущее состояние
                    state = get_state_fast(driver)
                    if state and (len(state['p_cards']) > 0 or len(state['d_cards']) > 0):
                        logging.warning(f"Стол {table_id}: игра не завершена, но браузер закрывается. Отправляем текущее состояние")
                        final_msg = format_message(table_id, state, is_final=True, 
                                                  t_num=t_num, table_number=table_number)
                        try:
                            with lock:
                                if msg_id:
                                    edit_telegram_message_with_retry(CHANNEL_ID, msg_id, final_msg)
                                else:
                                    send_telegram_message_with_retry(CHANNEL_ID, final_msg)
                            logging.info(f"Стол {table_id}: принудительное завершение")
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

def scan_tables():
    """Сканирование новых столов"""
    driver = None
    try:
        driver = create_driver()
        if not driver:
            logging.error("Не удалось создать драйвер для сканирования столов.")
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

            if game_data.is_game_completed(table_id):
                continue

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

    except TimeoutException:
        logging.error("Таймаут при загрузке страницы со столами")
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
    logging.info("Бот запущен с исправлениями: #N префикс, стрелки 👈 👉, #T сумма очков, ✅ победитель")
    
    try:
        while True:
            try:
                clean_threads()
                scan_tables()
                
                with lock:
                    logging.info(f"Активных столов: {len(active_tables)}")
                
                game_data.save_data()
                time.sleep(CHECK_INTERVAL)
                
            except KeyboardInterrupt:
                logging.info("Получен сигнал завершения")
                break
            except Exception as e:
                logging.error(f"Ошибка в главном цикле: {e}")
                time.sleep(60)
    finally:
        logging.info("Завершение работы бота...")
        with lock:
            for driver in table_drivers.values():
                try:
                    driver.quit()
                except:
                    pass
            
        game_data.save_data()
        
        with lock:
            active_tables.clear()
            message_ids.clear()
            table_drivers.clear()
            last_messages.clear()

if __name__ == "__main__":
    main()
