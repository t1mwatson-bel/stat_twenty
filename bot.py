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

def is_game_finished(driver):
    """Проверка завершения игры по селектору завершения"""
    try:
        # Проверка по селектору завершения
        finished_element = driver.find_element(By.CSS_SELECTOR, 
            'span.ui-caption--size-xl.ui-caption--weight-700.ui-caption--color-clr-strong.ui-caption')
        
        if finished_element and 'Игра завершена' in finished_element.text:
            logging.info("Игра завершена (обнаружен селектор завершения)")
            return True
            
    except NoSuchElementException:
        pass
    except Exception as e:
        logging.error(f"Ошибка проверки завершения игры: {e}")
    
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

def is_game_finished_fast(driver):
    """Быстрая проверка завершения игры"""
    try:
        driver.find_element(By.CSS_SELECTOR, 
            'span.ui-caption--size-xl.ui-caption--weight-700.ui-caption--color-clr-strong.ui-caption')
        return True
    except NoSuchElementException:
        return False
    except:
        return False

def format_message(table_id, state, is_final=False, t_num=None, table_number=None):
    """Форматирование сообщения с учетом специальных условий и префикса N"""
    p_cards = format_cards(state['p_cards'])
    d_cards = format_cards(state['d_cards'])
    
    # Определяем номер игры (циклически от 1 до 1440)
    if table_number is None:
        table_number = int(table_id) % 1440
        if table_number == 0:
            table_number = 1440
    
    # Формируем основную часть сообщения с префиксом #N
    if is_final:
        specials = check_special_conditions(state['p_cards'], state['d_cards'], 
                                           state['p_score'], state['d_score'])
        
        # Определяем, кто набрал 21 для лучшей читаемости
        twenty_one_indicator = ""
        if state['p_score'] == "21" and state['d_score'] == "21":
            twenty_one_indicator = " 🔥ОБА 21🔥"
        elif state['p_score'] == "21":
            twenty_one_indicator = " 👆ИГРОК 21👆"
        elif state['d_score'] == "21":
            twenty_one_indicator = " 👇ДИЛЕР 21👇"
        
        base_msg = f"#N{table_number}. {state['p_score']}({p_cards}) - {state['d_score']}({d_cards}) #T{t_num}"
        
        if specials:
            return f"{base_msg} {specials}{twenty_one_indicator}"
        else:
            return f"{base_msg}{twenty_one_indicator}"
    else:
        # Определяем, кто сейчас ходит с явным указанием
        turn = determine_turn(state)
        if turn == 'player':
            return f"⏰#N{table_number}. 👆ИГРОК ДОБИРАЕТ👆 ▶ {state['p_score']}({p_cards}) - {state['d_score']}({d_cards})"
        elif turn == 'dealer':
            return f"⏰#N{table_number}. {state['p_score']}({p_cards}) - ▶ {state['d_score']}({d_cards}) 👇ДИЛЕР ДОБИРАЕТ👇"
        else:
            # Если ход не определен, показываем обычное сообщение
            return f"⏰#N{table_number}. {state['p_score']}({p_cards}) - {state['d_score']}({d_cards})"

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
    max_no_response = 5
    table_number = int(table_id) % 1440
    if table_number == 0:
        table_number = 1440
    last_send_time = 0
    min_send_interval = 2
    initial_load = True

    # Проверяем, не была ли уже завершена эта игра
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
        
        # Быстрая проверка загрузки карт
        cards_loaded = False
        wait_start = time.time()
        max_wait = 10
        check_interval = 0.5
        
        while not cards_loaded and (time.time() - wait_start) < max_wait:
            try:
                player_cards = driver.find_elements(By.CSS_SELECTOR, '.live-twenty-one-field-player:first-child .scoreboard-card-games-card')
                
                if len(player_cards) > 0:
                    cards_loaded = True
                    logging.info(f"Карты загружены для стола {table_id}")
                    break
                    
                if is_game_finished_fast(driver):
                    logging.info(f"Игра на столе {table_id} уже завершена")
                    game_active = False
                    break
                    
                time.sleep(check_interval)
            except Exception as e:
                time.sleep(check_interval)
        
        logging.info(f"Старт мониторинга стола {table_id}")

        while game_active:
            try:
                current_time = time.time()
                
                state = get_state_fast(driver)
                
                if not state:
                    no_response_count += 1
                    if no_response_count >= max_no_response:
                        logging.warning(f"Стол {table_id} не отвечает")
                        break
                    time.sleep(1)
                    continue
                
                no_response_count = 0
                
                # Проверка завершения
                if is_game_finished_fast(driver):
                    final_state = get_state_fast(driver) or state
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
                        logging.error(f"Ошибка отправки финального сообщения: {e}")
                    
                    game_active = False
                    break

                # Пропускаем пустые состояния
                if len(state['p_cards']) == 0 and len(state['d_cards']) == 0:
                    time.sleep(1)
                    continue

                # Отправка обновлений
                if (state != last_state or initial_load) and (current_time - last_send_time) >= min_send_interval:
                    msg = format_message(table_id, state, table_number=table_number)
                    
                    with lock:
                        last_msg = last_messages.get(table_id)
                        if last_msg == msg and not initial_load:
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
                                    sent = send_telegram_message_with_retry(CHANNEL_ID, msg)
                                    msg_id = sent.message_id
                                    message_ids[table_id] = msg_id
                                    last_messages[table_id] = msg
                                    logging.info(f"Стол {table_id}: первое сообщение #N{table_number}")
                        
                        if msg_id:
                            last_state = state
                            last_send_time = current_time
                            initial_load = False
                        
                        # Лог с информацией о ходе
                        turn = determine_turn(state)
                        if turn == 'player':
                            logging.info(f"Стол {table_id}: ИГРОК добирает - {state['p_score']}({len(state['p_cards'])} карт) - {state['d_score']}({len(state['d_cards'])} карт)")
                        elif turn == 'dealer':
                            logging.info(f"Стол {table_id}: ДИЛЕР добирает - {state['p_score']}({len(state['p_cards'])} карт) - {state['d_score']}({len(state['d_cards'])} карт)")
                        else:
                            logging.info(f"Стол {table_id}: {state['p_score']} - {state['d_score']}")
                            
                    except Exception as e:
                        logging.error(f"Ошибка отправки: {e}")
                        time.sleep(2)

                time.sleep(1)

            except StaleElementReferenceException:
                logging.warning(f"StaleElementReferenceException для стола {table_id}")
                driver.refresh()
                time.sleep(2)
            except Exception as e:
                logging.error(f"Ошибка в цикле: {e}")
                time.sleep(1)

    except Exception as e:
        logging.error(f"Критическая ошибка: {e}")
    finally:
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
    logging.info("Бот запущен с исправлениями: #N префикс и явная индикация хода")
    
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
