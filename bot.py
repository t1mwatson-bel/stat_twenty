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

# ===== НАСТРОЙКИ =====
TOKEN = "8357635747:AAGAH_Rwk-vR8jGa6Q9F-AJLsMaEIj-JDBU"
CHANNEL_ID = "-1003179573402"
MAIN_URL = "https://1xlite-7636770.bar/ru/live/twentyone/1643503-twentyone-game"
MAX_BROWSERS = 2
CHECK_INTERVAL = 30
DATA_FILE = "game_data.pkl"
DATA_RETENTION_DAYS = 3
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
active_tables = {}  # {table_id: thread}
message_ids = {}    # {table_id: message_id}
table_drivers = {}  # {table_id: driver}
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

def get_state(driver):
    try:
        # Ждем загрузки основных элементов
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '.live-twenty-one-field-player'))
        )
        
        player_score = driver.find_element(By.CSS_SELECTOR, '.live-twenty-one-field-player:first-child .live-twenty-one-field-score__label').text
        player_cards = parse_cards(driver.find_elements(By.CSS_SELECTOR, '.live-twenty-one-field-player:first-child .scoreboard-card-games-card'))
        dealer_score = driver.find_element(By.CSS_SELECTOR, '.live-twenty-one-field-player:last-child .live-twenty-one-field-score__label').text
        dealer_cards = parse_cards(driver.find_elements(By.CSS_SELECTOR, '.live-twenty-one-field-player:last-child .scoreboard-card-games-card'))
        
        # Определяем, чей ход
        player_active = False
        dealer_active = False
        try:
            # Проверяем наличие активных кнопок или индикаторов
            player_area = driver.find_element(By.CSS_SELECTOR, '.live-twenty-one-field-player:first-child')
            if 'active' in player_area.get_attribute('class').lower():
                player_active = True
            
            dealer_area = driver.find_element(By.CSS_SELECTOR, '.live-twenty-one-field-player:last-child')
            if 'active' in dealer_area.get_attribute('class').lower():
                dealer_active = True
        except:
            pass
        
        # Пробуем получить статус
        try:
            status = driver.find_element(By.CSS_SELECTOR, '.ui-game-timer__label').text
        except:
            status = "Идет игра"
            
        return {
            'p_score': player_score,
            'p_cards': player_cards,
            'd_score': dealer_score,
            'd_cards': dealer_cards,
            'status': status,
            'player_active': player_active,
            'dealer_active': dealer_active
        }
    except TimeoutException:
        logging.error(f"Таймаут загрузки страницы")
        return None
    except Exception as e:
        logging.error(f"Ошибка получения состояния: {e}")
        return None

def format_message(table_id, state, is_final=False, t_num=None, table_number=None):
    """Форматирование сообщения с учетом специальных условий"""
    p_cards = format_cards(state['p_cards'])
    d_cards = format_cards(state['d_cards'])
    
    # Определяем номер игры (циклически от 1 до 1440)
    if table_number is None:
        table_number = int(table_id) % 1440
        if table_number == 0:
            table_number = 1440
    
    # Формируем основную часть сообщения
    if is_final:
        specials = check_special_conditions(state['p_cards'], state['d_cards'], 
                                           state['p_score'], state['d_score'])
        
        if specials:
            return f"#{table_number}. {state['p_score']}({p_cards}) - {state['d_score']}({d_cards}) #T{t_num} {specials}"
        else:
            return f"#{table_number}. {state['p_score']}({p_cards}) - {state['d_score']}({d_cards}) #T{t_num}"
    else:
        # Определяем, кто сейчас ходит
        turn = determine_turn(state)
        if turn == 'player':
            return f"⏰#{table_number}. ▶ {state['p_score']}({p_cards}) - {state['d_score']}({d_cards})"
        elif turn == 'dealer':
            return f"⏰#{table_number}. {state['p_score']}({p_cards}) - ▶ {state['d_score']}({d_cards})"
        else:
            return f"⏰#{table_number}. {state['p_score']}({p_cards}) - {state['d_score']}({d_cards})"

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

    # Проверяем, не была ли уже завершена эта игра
    if game_data.is_game_completed(table_id):
        logging.info(f"Стол {table_id} уже был завершен, пропускаем")
        return

    try:
        # Создаем отдельный драйвер для этого стола
        driver = create_driver()
        if not driver:
            logging.error(f"Не удалось создать драйвер для стола {table_id}.")
            return

        # Сохраняем драйвер в общем словаре
        with lock:
            table_drivers[table_id] = driver

        logging.info(f"Начало мониторинга стола {table_id} в отдельном браузере")
        driver.get(table_url)
        time.sleep(5)  # Даем время на загрузку

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
                
                # Сброс счетчика при успешном получении состояния
                no_response_count = 0
                
                # Проверка завершения игры по специальному селектору
                if is_game_finished(driver):
                    final_state = get_state(driver) or state
                    final_msg = format_message(table_id, final_state, is_final=True, 
                                              t_num=t_num, table_number=table_number)
                    
                    try:
                        # Редактируем существующее сообщение или отправляем новое
                        with lock:
                            if msg_id:
                                bot.edit_message_text(final_msg, CHANNEL_ID, msg_id)
                            else:
                                sent = bot.send_message(CHANNEL_ID, final_msg)
                                msg_id = sent.message_id
                                message_ids[table_id] = msg_id
                        
                        # Сохраняем завершенную игру
                        game_data.add_completed_game(table_id, final_msg, t_num)
                        game_data.update_last_number(table_number)
                        
                        logging.info(f"Стол {table_id} завершен, финальное сообщение отправлено")
                    except Exception as e:
                        logging.error(f"Ошибка отправки финального сообщения для стола {table_id}: {e}")
                    
                    game_active = False
                    break

                # Отправка/редактирование промежуточных результатов
                if state != last_state:
                    msg = format_message(table_id, state, table_number=table_number)
                    try:
                        with lock:
                            if msg_id:
                                # Редактируем существующее сообщение
                                bot.edit_message_text(msg, CHANNEL_ID, msg_id)
                            else:
                                # Отправляем новое сообщение
                                sent = bot.send_message(CHANNEL_ID, msg)
                                msg_id = sent.message_id
                                message_ids[table_id] = msg_id
                        
                        last_state = state
                        
                        # Определяем, кто ходит для лога
                        turn = determine_turn(state)
                        turn_symbol = "▶" if turn else ""
                        logging.info(f"Стол {table_id} обновлен: {turn_symbol} {state['p_score']} - {state['d_score']}")
                    except Exception as e:
                        logging.error(f"Ошибка отправки сообщения для стола {table_id}: {e}")

                time.sleep(2)

            except StaleElementReferenceException:
                logging.warning(f"StaleElementReferenceException для стола {table_id}, обновляем страницу")
                driver.refresh()
                time.sleep(3)
            except Exception as e:
                logging.error(f"Ошибка в цикле мониторинга стола {table_id}: {e}")
                time.sleep(2)

    except Exception as e:
        logging.error(f"Критическая ошибка мониторинга стола {table_id}: {e}")
    finally:
        # Гарантированное закрытие драйвера и очистка данных
        safe_quit_driver(table_id)
        
        with lock:
            if table_id in active_tables:
                del active_tables[table_id]
            if table_id in message_ids:
                del message_ids[table_id]
        
        logging.info(f"Мониторинг стола {table_id} завершен, браузер закрыт")

def scan_tables():
    """Сканирование новых столов"""
    driver = None
    try:
        # Создаем временный драйвер только для сканирования
        driver = create_driver()
        if not driver:
            logging.error("Не удалось создать драйвер для сканирования столов.")
            return
        
        logging.info("Сканирование новых столов...")
        driver.get(MAIN_URL)
        time.sleep(5)

        # Ждем загрузки блоков игр
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

            # Пропускаем уже завершенные игры
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
                    
            # Создаем отдельный поток для каждого стола
            thread = threading.Thread(target=monitor_table, args=(href, table_id))
            thread.daemon = True
            thread.start()
            
            with lock:
                active_tables[table_id] = thread
            
            logging.info(f"Запущен мониторинг стола {table_id} в отдельном потоке")
            time.sleep(3)

    except TimeoutException:
        logging.error("Таймаут при загрузке страницы со столами")
    except Exception as e:
        logging.error(f"Ошибка сканирования: {e}")
    finally:
        # Закрываем временный драйвер для сканирования
        if driver:
            driver.quit()

def clean_threads():
    """Очистка завершенных потоков и закрытие их браузеров"""
    with lock:
        dead = [tid for tid, t in active_tables.items() if not t.is_alive()]
        for tid in dead:
            # Закрываем браузер для завершенного потока
            if tid in table_drivers:
                try:
                    table_drivers[tid].quit()
                except:
                    pass
                del table_drivers[tid]
            
            del active_tables[tid]
            if tid in message_ids:
                del message_ids[tid]
            logging.info(f"Поток и браузер стола {tid} очищены")

def main():
    logging.info("Чистый бот запущен с отдельными браузерами для каждого стола")
    
    try:
        while True:
            try:
                clean_threads()
                scan_tables()
                
                with lock:
                    logging.info(f"Активных столов: {len(active_tables)}")
                    logging.info(f"Активных браузеров: {len(table_drivers)}")
                
                # Периодическое сохранение данных
                game_data.save_data()
                
                time.sleep(CHECK_INTERVAL)
                
            except KeyboardInterrupt:
                logging.info("Получен сигнал завершения")
                break
            except Exception as e:
                logging.error(f"Ошибка в главном цикле: {e}")
                time.sleep(60)
    finally:
        # Закрываем все браузеры при завершении
        logging.info("Завершение работы бота, закрытие всех браузеров...")
        with lock:
            for table_id, driver in table_drivers.items():
                try:
                    driver.quit()
                    logging.info(f"Браузер стола {table_id} закрыт")
                except:
                    pass
            
        game_data.save_data()
        
        with lock:
            active_tables.clear()
            message_ids.clear()
            table_drivers.clear()

if __name__ == "__main__":
    main()
