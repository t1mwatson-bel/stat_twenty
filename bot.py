import threading
import time
import re
import logging
import subprocess
import glob
import os
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
import threading
import time
import re
import logging
import subprocess
import glob
import os
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
import telebot
import pickle
from telebot import apihelper

# ===== НАСТРОЙКИ =====
TOKEN = "8357635747:AAGAH_Rwk-vR8jGa6Q9F-AJLsMaEIj-JDBU"
CHANNEL_ID = "-1003179573402"
MAIN_URL = "https://1xlite-7636770.bar/ru/live/twentyone/2092323-21-classics"
MAX_CONCURRENT_GAMES = 3
DATA_FILE = "game_data.pkl"
DATA_RETENTION_DAYS = 3
POLL_INTERVAL = 2
MAX_POLL_ATTEMPTS = 30
# =====================

apihelper.RETRY_ON_ERROR = True
apihelper.MAX_RETRIES = 5

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

bot = telebot.TeleBot(TOKEN)
active_games = {}
lock = threading.Lock()
bot_running = True

class GameData:
    def __init__(self):
        self.completed_games = {}
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
    
    def add_completed_game(self, game_number, message):
        self.completed_games[game_number] = {
            'message': message,
            'timestamp': datetime.now()
        }
        self.save_data()
    
    def is_game_completed(self, game_number):
        return game_number in self.completed_games
    
    def update_last_number(self, number):
        if number > self.last_game_number:
            self.last_game_number = number
            self.save_data()

game_data = GameData()

def get_game_number_by_time(dt=None):
    if dt is None:
        dt = datetime.now()
    
    start_of_day = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    minutes_passed = (dt - start_of_day).total_seconds() / 60
    game_number = int(minutes_passed // 2) + 1
    
    return game_number

def get_next_game_time():
    now = datetime.now()
    
    next_game_minute = ((now.minute // 2) * 2 + 2) % 60
    next_game_hour = now.hour
    
    if next_game_minute < now.minute:
        next_game_hour = (next_game_hour + 1) % 24
    
    next_game_time = now.replace(
        hour=next_game_hour,
        minute=next_game_minute,
        second=0,
        microsecond=0
    )
    
    seconds_to_start = (next_game_time - now).total_seconds()
    
    return next_game_time, max(0, seconds_to_start)

def format_cards(cards):
    return ''.join(cards)

def determine_winner(p_score, d_score):
    try:
        p = int(p_score)
        d = int(d_score)
        
        if p > 21 and d <= 21:
            return 'П2'
        elif d > 21 and p <= 21:
            return 'П1'
        elif p > 21 and d > 21:
            return 'П2' if d < p else 'П1'
        else:
            return 'П1' if p > d else 'П2' if d > p else 'НИЧЬЯ'
    except:
        return 'UNKNOWN'

def format_message(game_number, state, is_final=False):
    p_cards = format_cards(state['p_cards'])
    d_cards = format_cards(state['d_cards'])
    
    try:
        total_score = int(state['p_score']) + int(state['d_score'])
    except:
        total_score = 0
    
    if is_final:
        winner = determine_winner(state['p_score'], state['d_score'])
        
        if winner == 'П1':
            score_part = f"☑️{state['p_score']}({p_cards})-{state['d_score']}({d_cards})"
        elif winner == 'П2':
            score_part = f"{state['p_score']}({p_cards})-☑️{state['d_score']}({d_cards})"
        else:
            score_part = f"{state['p_score']}({p_cards})-{state['d_score']}({d_cards})"
        
        return f"#N{game_number} {score_part}  #{winner} #T{total_score}"
    else:
        return f"#N{game_number} {state['p_score']}({p_cards})-{state['d_score']}({d_cards}) #T{total_score}"

def create_driver():
    """Создание драйвера с жестким путем к Chromium"""
    
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-software-rasterizer")
    chrome_options.add_argument("--remote-debugging-port=9222")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-setuid-sandbox")
    
    # Жестко прописываем путь из Dockerfile
    chromium_path = "/usr/bin/chromium"
    
    if os.path.exists(chromium_path):
        chrome_options.binary_location = chromium_path
        logging.info(f"Использую Chromium: {chromium_path}")
    else:
        logging.error(f"Chromium НЕ НАЙДЕН по пути: {chromium_path}")
        # Пробуем найти через which
        try:
            result = subprocess.run(["which", "chromium"], capture_output=True, text=True)
            if result.stdout.strip():
                chrome_options.binary_location = result.stdout.strip()
                logging.info(f"Найден через which: {result.stdout.strip()}")
        except:
            pass
    
    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.set_page_load_timeout(15)
        return driver
    except Exception as e:
        logging.error(f"Ошибка создания драйвера: {e}")
        # Печатаем содержимое /usr/bin для отладки
        try:
            print("Содержимое /usr/bin:")
            result = subprocess.run(["ls", "-la", "/usr/bin/ | grep chrom"], shell=True, capture_output=True, text=True)
            print(result.stdout)
        except:
            pass
        raise e

def extract_cards_from_container(driver, container_selector):
    cards = []
    try:
        container = driver.find_element(By.CSS_SELECTOR, container_selector)
        card_elements = container.find_elements(By.CSS_SELECTOR, '.scoreboard-card-games-card')
        
        for el in card_elements:
            try:
                class_name = el.get_attribute('class') or ''
                
                if 'hidden' in class_name.lower() or 'face-down' in class_name.lower():
                    continue
                
                suit = '?'
                if 'suit-0' in class_name:
                    suit = '♠️'
                elif 'suit-1' in class_name:
                    suit = '♣️'
                elif 'suit-2' in class_name:
                    suit = '♦️'
                elif 'suit-3' in class_name:
                    suit = '♥️'
                
                val_match = re.search(r'value-(\d+)', class_name)
                if val_match:
                    val = val_match.group(1)
                    if val == '11':
                        value = 'J'
                    elif val == '12':
                        value = 'Q'
                    elif val == '13':
                        value = 'K'
                    elif val == '14':
                        value = 'A'
                    else:
                        value = val
                else:
                    value = '?'
                
                cards.append(f"{value}{suit}")
            except:
                continue
    except:
        pass
    
    return cards

def get_state_from_driver(driver):
    try:
        player_score_el = driver.find_element(By.CSS_SELECTOR, '.live-twenty-one-field-player:first-child .live-twenty-one-field-score__label')
        player_score = player_score_el.text if player_score_el else '0'
        
        player_cards_container = '.live-twenty-one-field-player:first-child .live-twenty-one-cards'
        player_cards = extract_cards_from_container(driver, player_cards_container)
        
        dealer_score_el = driver.find_element(By.CSS_SELECTOR, '.live-twenty-one-field-player:last-child .live-twenty-one-field-score__label')
        dealer_score = dealer_score_el.text if dealer_score_el else '0'
        
        dealer_cards_container = '.live-twenty-one-field-player:last-child .live-twenty-one-cards'
        dealer_cards = extract_cards_from_container(driver, dealer_cards_container)
        
        return {
            'p_score': player_score.strip(),
            'p_cards': player_cards,
            'd_score': dealer_score.strip(),
            'd_cards': dealer_cards
        }
    except Exception as e:
        logging.error(f"Ошибка в get_state_from_driver: {e}")
        return None

def is_game_finished(driver):
    try:
        timers = driver.find_elements(By.CSS_SELECTOR, '.live-twenty-one-table-footer__timer .ui-game-timer__label')
        for timer in timers:
            if timer.text and "Игра завершена" in timer.text:
                return True
        
        statuses = driver.find_elements(By.CSS_SELECTOR, '.live-twenty-one-table-head__status')
        for status in statuses:
            if status.text and 'Победа' in status.text:
                return True
        
        return False
    except Exception as e:
        logging.error(f"Ошибка в is_game_finished: {e}")
        return False

def get_table_url(driver, target_game_number):
    try:
        logging.info(f"🔍 Ищем стол №{target_game_number}...")
        
        driver.implicitly_wait(10)
        tables = driver.find_elements(By.CSS_SELECTOR, '.dashboard-game-block')
        logging.info(f"📊 Всего столов на странице: {len(tables)}")
        
        for table in tables:
            try:
                info_elem = table.find_element(By.CSS_SELECTOR, '.dashboard-game-info__additional-info')
                if info_elem:
                    text = info_elem.text
                    match = re.search(r'(\d+)', text)
                    if match:
                        current_number = int(match.group(1))
                        
                        if current_number == target_game_number:
                            link_element = table.find_element(By.CSS_SELECTOR, '.dashboard-game-block__link')
                            if link_element:
                                href = link_element.get_attribute('href')
                                if href and not href.startswith('http'):
                                    href = f"https://1xlite-7636770.bar{href}"
                                
                                logging.info(f"✅ Найден стол #{current_number}")
                                return href
            except:
                continue
        
        logging.warning(f"❌ Стол #{target_game_number} не найден")
        return None
        
    except Exception as e:
        logging.error(f"Ошибка в get_table_url: {e}")
        return None

def send_telegram_message(chat_id, text):
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
                if attempt == max_retries - 1:
                    raise
                time.sleep(2)
    raise Exception(f"Не удалось отправить сообщение")

def edit_telegram_message(chat_id, message_id, text):
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
                if attempt == max_retries - 1:
                    logging.error(f"Ошибка при редактировании: {e}")
                    return None
                time.sleep(2)
    return None

def poll_game(table_url, game_number, game_start_time):
    msg_id = None
    last_state = None
    first_message_sent = False
    game_started = False
    start_time = time.time()
    
    logging.info(f"🎮 Игра #{game_number}: начало опроса")
    
    for attempt in range(MAX_POLL_ATTEMPTS):
        if not bot_running:
            break
        
        if time.time() - start_time > 120:
            logging.warning(f"Игра #{game_number}: превышено время опроса")
            break
        
        driver = None
        try:
            driver = create_driver()
            driver.get(table_url)
            time.sleep(2)
            
            state = get_state_from_driver(driver)
            
            if not state:
                logging.warning(f"Игра #{game_number}: не удалось получить состояние")
                driver.quit()
                time.sleep(POLL_INTERVAL)
                continue
            
            has_cards = len(state['p_cards']) > 0 or len(state['d_cards']) > 0
            is_finished = is_game_finished(driver)
            
            logging.info(f"Игра #{game_number}: попытка {attempt+1}, карты: П:{len(state['p_cards'])} Д:{len(state['d_cards'])}, финиш: {is_finished}")
            
            if has_cards and not game_started:
                game_started = True
                logging.info(f"Игра #{game_number}: КАРТЫ ПОЯВИЛИСЬ!")
            
            if game_started and state != last_state:
                if not first_message_sent:
                    msg_text = format_message(game_number, state, is_final=False)
                    sent = send_telegram_message(CHANNEL_ID, msg_text)
                    msg_id = sent.message_id
                    first_message_sent = True
                    logging.info(f"Игра #{game_number}: ПЕРВОЕ СООБЩЕНИЕ")
                else:
                    msg_text = format_message(game_number, state, is_final=False)
                    edit_telegram_message(CHANNEL_ID, msg_id, msg_text)
                
                last_state = state
            
            if is_finished and game_started:
                logging.info(f"Игра #{game_number}: ЗАВЕРШЕНА")
                
                time.sleep(1)
                
                final_state = get_state_from_driver(driver)
                if final_state:
                    final_msg = format_message(game_number, final_state, is_final=True)
                    
                    if msg_id:
                        edit_telegram_message(CHANNEL_ID, msg_id, final_msg)
                    else:
                        sent = send_telegram_message(CHANNEL_ID, final_msg)
                    
                    game_data.add_completed_game(game_number, final_msg)
                    logging.info(f"✅ Игра #{game_number}: ФИНАЛ ОТПРАВЛЕН")
                    driver.quit()
                    break
            
            driver.quit()
            
        except Exception as e:
            logging.error(f"Игра #{game_number}: ошибка: {e}")
            if driver:
                try:
                    driver.quit()
                except:
                    pass
        
        time.sleep(POLL_INTERVAL)
    
    with lock:
        if game_number in active_games:
            del active_games[game_number]
    
    logging.info(f"Игра #{game_number}: опрос завершен")

def launch_next_game():
    driver = None
    try:
        driver = create_driver()
        driver.get(MAIN_URL)
        time.sleep(3)
        
        next_game_time, _ = get_next_game_time()
        game_number = get_game_number_by_time(next_game_time)
        
        table_url = get_table_url(driver, game_number)
        
        if not table_url or not game_number:
            logging.warning("⚠️ Не удалось получить URL стола")
            return
        
        now = datetime.now()
        if now < next_game_time - timedelta(seconds=2):
            logging.info(f"Игра #{game_number}: еще рано (старт в {next_game_time.strftime('%H:%M:%S')})")
            return
        
        with lock:
            if game_number in active_games:
                logging.info(f"Игра #{game_number} уже мониторится")
                return
            if game_data.is_game_completed(game_number):
                logging.info(f"Игра #{game_number} уже завершена")
                return
        
        logging.info(f"🚀 Игра #{game_number}: запуск опроса")
        
        thread = threading.Thread(
            target=poll_game, 
            args=(table_url, game_number, next_game_time)
        )
        thread.daemon = True
        thread.start()
        
        with lock:
            active_games[game_number] = {
                'thread': thread,
                'start_time': next_game_time
            }
        
        logging.info(f"✅ Игра #{game_number}: опрос запущен (активных: {len(active_games)}/{MAX_CONCURRENT_GAMES})")
        
    except Exception as e:
        logging.error(f"Ошибка при запуске опроса: {e}")
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

def clean_finished_games():
    with lock:
        finished = [g for g, info in active_games.items() if not info['thread'].is_alive()]
        for g in finished:
            del active_games[g]
            logging.info(f"Игра #{g} очищена")

def monitor_loop():
    global bot_running
    logging.info("🚀 Бот 21 Classic ЗАПУЩЕН (Selenium)")
    logging.info(f"Максимум одновременных игр: {MAX_CONCURRENT_GAMES}")
    
    last_launch_time = 0
    last_game_launched = None
    
    while bot_running:
        try:
            clean_finished_games()
            
            now = datetime.now()
            
            if now.second >= 58 and now.minute % 2 == 1:
                next_game_time, seconds_to_next = get_next_game_time()
                
                if seconds_to_next <= 2 and (time.time() - last_launch_time) > 58:
                    game_number = get_game_number_by_time(next_game_time)
                    
                    if last_game_launched != game_number:
                        logging.info(f"⏰ Запуск игры #{game_number}")
                        
                        if len(active_games) < MAX_CONCURRENT_GAMES:
                            launch_next_game()
                            last_launch_time = time.time()
                            last_game_launched = game_number
                        
                        time.sleep(2)
            
            time.sleep(1)
            
        except KeyboardInterrupt:
            bot_running = False
            break
        except Exception as e:
            logging.error(f"Ошибка в основном цикле: {e}")
            time.sleep(5)
    
    game_data.save_data()
    logging.info("Бот остановлен")

def main():
    monitor_loop()

if __name__ == "__main__":
    main().webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
import telebot
import pickle
from telebot import apihelper

# ===== ПОИСК CHROMIUM =====
print("="*50)
print("ПОИСК CHROMIUM НА СИСТЕМЕ:")
try:
    result = subprocess.run(["find", "/nix/store", "-name", "chromium", "-type", "f"], 
                           capture_output=True, text=True, timeout=10)
    print("В /nix/store найдено:")
    print(result.stdout if result.stdout else "ничего не найдено")
except:
    print("Ошибка поиска в /nix/store")

try:
    result2 = subprocess.run(["which", "chromium"], capture_output=True, text=True, timeout=5)
    print("which chromium:", result2.stdout.strip() if result2.stdout else "не найден")
except:
    print("which chromium: ошибка")

try:
    result3 = subprocess.run(["which", "chromium-browser"], capture_output=True, text=True, timeout=5)
    print("which chromium-browser:", result3.stdout.strip() if result3.stdout else "не найден")
except:
    pass

print("="*50)
# ===========================

# ===== НАСТРОЙКИ =====
TOKEN = "8357635747:AAGAH_Rwk-vR8jGa6Q9F-AJLsMaEIj-JDBU"
CHANNEL_ID = "-1003179573402"
MAIN_URL = "https://1xlite-7636770.bar/ru/live/twentyone/2092323-21-classics"
MAX_CONCURRENT_GAMES = 3
DATA_FILE = "game_data.pkl"
DATA_RETENTION_DAYS = 3
POLL_INTERVAL = 2
MAX_POLL_ATTEMPTS = 30
# =====================

apihelper.RETRY_ON_ERROR = True
apihelper.MAX_RETRIES = 5

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

bot = telebot.TeleBot(TOKEN)
active_games = {}
lock = threading.Lock()
bot_running = True

class GameData:
    def __init__(self):
        self.completed_games = {}
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
    
    def add_completed_game(self, game_number, message):
        self.completed_games[game_number] = {
            'message': message,
            'timestamp': datetime.now()
        }
        self.save_data()
    
    def is_game_completed(self, game_number):
        return game_number in self.completed_games
    
    def update_last_number(self, number):
        if number > self.last_game_number:
            self.last_game_number = number
            self.save_data()

game_data = GameData()

def get_game_number_by_time(dt=None):
    if dt is None:
        dt = datetime.now()
    
    start_of_day = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    minutes_passed = (dt - start_of_day).total_seconds() / 60
    game_number = int(minutes_passed // 2) + 1
    
    return game_number

def get_next_game_time():
    now = datetime.now()
    
    next_game_minute = ((now.minute // 2) * 2 + 2) % 60
    next_game_hour = now.hour
    
    if next_game_minute < now.minute:
        next_game_hour = (next_game_hour + 1) % 24
    
    next_game_time = now.replace(
        hour=next_game_hour,
        minute=next_game_minute,
        second=0,
        microsecond=0
    )
    
    seconds_to_start = (next_game_time - now).total_seconds()
    
    return next_game_time, max(0, seconds_to_start)

def format_cards(cards):
    return ''.join(cards)

def determine_winner(p_score, d_score):
    try:
        p = int(p_score)
        d = int(d_score)
        
        if p > 21 and d <= 21:
            return 'П2'
        elif d > 21 and p <= 21:
            return 'П1'
        elif p > 21 and d > 21:
            return 'П2' if d < p else 'П1'
        else:
            return 'П1' if p > d else 'П2' if d > p else 'НИЧЬЯ'
    except:
        return 'UNKNOWN'

def format_message(game_number, state, is_final=False):
    p_cards = format_cards(state['p_cards'])
    d_cards = format_cards(state['d_cards'])
    
    try:
        total_score = int(state['p_score']) + int(state['d_score'])
    except:
        total_score = 0
    
    if is_final:
        winner = determine_winner(state['p_score'], state['d_score'])
        
        if winner == 'П1':
            score_part = f"☑️{state['p_score']}({p_cards})-{state['d_score']}({d_cards})"
        elif winner == 'П2':
            score_part = f"{state['p_score']}({p_cards})-☑️{state['d_score']}({d_cards})"
        else:
            score_part = f"{state['p_score']}({p_cards})-{state['d_score']}({d_cards})"
        
        return f"#N{game_number} {score_part}  #{winner} #T{total_score}"
    else:
        return f"#N{game_number} {state['p_score']}({p_cards})-{state['d_score']}({d_cards}) #T{total_score}"

def create_driver():
    """Создание драйвера Chrome/Chromium для Railway"""
    
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-software-rasterizer")
    chrome_options.add_argument("--remote-debugging-port=9222")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-setuid-sandbox")
    
    # Пробуем найти Chromium
    chromium_path = None
    
    # Сначала через which
    try:
        result = subprocess.run(["which", "chromium"], capture_output=True, text=True)
        if result.stdout.strip():
            chromium_path = result.stdout.strip()
            logging.info(f"Найден Chromium через which: {chromium_path}")
    except:
        pass
    
    # Потом через find в nix/store
    if not chromium_path:
        try:
            result = subprocess.run(["find", "/nix/store", "-name", "chromium", "-type", "f", "-print", "-quit"], 
                                   capture_output=True, text=True)
            if result.stdout.strip():
                chromium_path = result.stdout.strip()
                logging.info(f"Найден Chromium в nix/store: {chromium_path}")
        except:
            pass
    
    # Стандартные пути
    if not chromium_path:
        standard_paths = [
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/nix/store/*/bin/chromium"
        ]
        
        for path in standard_paths:
            if "*" in path:
                matches = glob.glob(path)
                if matches:
                    chromium_path = matches[0]
                    logging.info(f"Найден Chromium по шаблону: {chromium_path}")
                    break
            elif os.path.exists(path):
                chromium_path = path
                logging.info(f"Найден Chromium: {path}")
                break
    
    if chromium_path:
        chrome_options.binary_location = chromium_path
        logging.info(f"Использую Chromium: {chromium_path}")
    else:
        logging.warning("Chromium не найден, пробуем без указания пути")
    
    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.set_page_load_timeout(15)
        return driver
    except Exception as e:
        logging.error(f"Ошибка создания драйвера: {e}")
        raise e

def extract_cards_from_container(driver, container_selector):
    cards = []
    try:
        container = driver.find_element(By.CSS_SELECTOR, container_selector)
        card_elements = container.find_elements(By.CSS_SELECTOR, '.scoreboard-card-games-card')
        
        for el in card_elements:
            try:
                class_name = el.get_attribute('class') or ''
                
                if 'hidden' in class_name.lower() or 'face-down' in class_name.lower():
                    continue
                
                suit = '?'
                if 'suit-0' in class_name:
                    suit = '♠️'
                elif 'suit-1' in class_name:
                    suit = '♣️'
                elif 'suit-2' in class_name:
                    suit = '♦️'
                elif 'suit-3' in class_name:
                    suit = '♥️'
                
                val_match = re.search(r'value-(\d+)', class_name)
                if val_match:
                    val = val_match.group(1)
                    if val == '11':
                        value = 'J'
                    elif val == '12':
                        value = 'Q'
                    elif val == '13':
                        value = 'K'
                    elif val == '14':
                        value = 'A'
                    else:
                        value = val
                else:
                    value = '?'
                
                cards.append(f"{value}{suit}")
            except:
                continue
    except:
        pass
    
    return cards

def get_state_from_driver(driver):
    try:
        player_score_el = driver.find_element(By.CSS_SELECTOR, '.live-twenty-one-field-player:first-child .live-twenty-one-field-score__label')
        player_score = player_score_el.text if player_score_el else '0'
        
        player_cards_container = '.live-twenty-one-field-player:first-child .live-twenty-one-cards'
        player_cards = extract_cards_from_container(driver, player_cards_container)
        
        dealer_score_el = driver.find_element(By.CSS_SELECTOR, '.live-twenty-one-field-player:last-child .live-twenty-one-field-score__label')
        dealer_score = dealer_score_el.text if dealer_score_el else '0'
        
        dealer_cards_container = '.live-twenty-one-field-player:last-child .live-twenty-one-cards'
        dealer_cards = extract_cards_from_container(driver, dealer_cards_container)
        
        return {
            'p_score': player_score.strip(),
            'p_cards': player_cards,
            'd_score': dealer_score.strip(),
            'd_cards': dealer_cards
        }
    except Exception as e:
        logging.error(f"Ошибка в get_state_from_driver: {e}")
        return None

def is_game_finished(driver):
    try:
        timers = driver.find_elements(By.CSS_SELECTOR, '.live-twenty-one-table-footer__timer .ui-game-timer__label')
        for timer in timers:
            if timer.text and "Игра завершена" in timer.text:
                return True
        
        statuses = driver.find_elements(By.CSS_SELECTOR, '.live-twenty-one-table-head__status')
        for status in statuses:
            if status.text and 'Победа' in status.text:
                return True
        
        return False
    except Exception as e:
        logging.error(f"Ошибка в is_game_finished: {e}")
        return False

def get_table_url(driver, target_game_number):
    try:
        logging.info(f"🔍 Ищем стол №{target_game_number}...")
        
        driver.implicitly_wait(10)
        tables = driver.find_elements(By.CSS_SELECTOR, '.dashboard-game-block')
        logging.info(f"📊 Всего столов на странице: {len(tables)}")
        
        for table in tables:
            try:
                info_elem = table.find_element(By.CSS_SELECTOR, '.dashboard-game-info__additional-info')
                if info_elem:
                    text = info_elem.text
                    match = re.search(r'(\d+)', text)
                    if match:
                        current_number = int(match.group(1))
                        
                        if current_number == target_game_number:
                            link_element = table.find_element(By.CSS_SELECTOR, '.dashboard-game-block__link')
                            if link_element:
                                href = link_element.get_attribute('href')
                                if href and not href.startswith('http'):
                                    href = f"https://1xlite-7636770.bar{href}"
                                
                                logging.info(f"✅ Найден стол #{current_number}")
                                return href
            except:
                continue
        
        logging.warning(f"❌ Стол #{target_game_number} не найден")
        return None
        
    except Exception as e:
        logging.error(f"Ошибка в get_table_url: {e}")
        return None

def send_telegram_message(chat_id, text):
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
                if attempt == max_retries - 1:
                    raise
                time.sleep(2)
    raise Exception(f"Не удалось отправить сообщение")

def edit_telegram_message(chat_id, message_id, text):
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
                if attempt == max_retries - 1:
                    logging.error(f"Ошибка при редактировании: {e}")
                    return None
                time.sleep(2)
    return None

def poll_game(table_url, game_number, game_start_time):
    msg_id = None
    last_state = None
    first_message_sent = False
    game_started = False
    start_time = time.time()
    
    logging.info(f"🎮 Игра #{game_number}: начало опроса")
    
    for attempt in range(MAX_POLL_ATTEMPTS):
        if not bot_running:
            break
        
        if time.time() - start_time > 120:
            logging.warning(f"Игра #{game_number}: превышено время опроса")
            break
        
        driver = None
        try:
            driver = create_driver()
            driver.get(table_url)
            time.sleep(2)
            
            state = get_state_from_driver(driver)
            
            if not state:
                logging.warning(f"Игра #{game_number}: не удалось получить состояние")
                driver.quit()
                time.sleep(POLL_INTERVAL)
                continue
            
            has_cards = len(state['p_cards']) > 0 or len(state['d_cards']) > 0
            is_finished = is_game_finished(driver)
            
            logging.info(f"Игра #{game_number}: попытка {attempt+1}, карты: П:{len(state['p_cards'])} Д:{len(state['d_cards'])}, финиш: {is_finished}")
            
            if has_cards and not game_started:
                game_started = True
                logging.info(f"Игра #{game_number}: КАРТЫ ПОЯВИЛИСЬ!")
            
            if game_started and state != last_state:
                if not first_message_sent:
                    msg_text = format_message(game_number, state, is_final=False)
                    sent = send_telegram_message(CHANNEL_ID, msg_text)
                    msg_id = sent.message_id
                    first_message_sent = True
                    logging.info(f"Игра #{game_number}: ПЕРВОЕ СООБЩЕНИЕ")
                else:
                    msg_text = format_message(game_number, state, is_final=False)
                    edit_telegram_message(CHANNEL_ID, msg_id, msg_text)
                
                last_state = state
            
            if is_finished and game_started:
                logging.info(f"Игра #{game_number}: ЗАВЕРШЕНА")
                
                time.sleep(1)
                
                final_state = get_state_from_driver(driver)
                if final_state:
                    final_msg = format_message(game_number, final_state, is_final=True)
                    
                    if msg_id:
                        edit_telegram_message(CHANNEL_ID, msg_id, final_msg)
                    else:
                        sent = send_telegram_message(CHANNEL_ID, final_msg)
                    
                    game_data.add_completed_game(game_number, final_msg)
                    logging.info(f"✅ Игра #{game_number}: ФИНАЛ ОТПРАВЛЕН")
                    driver.quit()
                    break
            
            driver.quit()
            
        except Exception as e:
            logging.error(f"Игра #{game_number}: ошибка: {e}")
            if driver:
                try:
                    driver.quit()
                except:
                    pass
        
        time.sleep(POLL_INTERVAL)
    
    with lock:
        if game_number in active_games:
            del active_games[game_number]
    
    logging.info(f"Игра #{game_number}: опрос завершен")

def launch_next_game():
    driver = None
    try:
        driver = create_driver()
        driver.get(MAIN_URL)
        time.sleep(3)
        
        next_game_time, _ = get_next_game_time()
        game_number = get_game_number_by_time(next_game_time)
        
        table_url = get_table_url(driver, game_number)
        
        if not table_url or not game_number:
            logging.warning("⚠️ Не удалось получить URL стола")
            return
        
        now = datetime.now()
        if now < next_game_time - timedelta(seconds=2):
            logging.info(f"Игра #{game_number}: еще рано (старт в {next_game_time.strftime('%H:%M:%S')})")
            return
        
        with lock:
            if game_number in active_games:
                logging.info(f"Игра #{game_number} уже мониторится")
                return
            if game_data.is_game_completed(game_number):
                logging.info(f"Игра #{game_number} уже завершена")
                return
        
        logging.info(f"🚀 Игра #{game_number}: запуск опроса")
        
        thread = threading.Thread(
            target=poll_game, 
            args=(table_url, game_number, next_game_time)
        )
        thread.daemon = True
        thread.start()
        
        with lock:
            active_games[game_number] = {
                'thread': thread,
                'start_time': next_game_time
            }
        
        logging.info(f"✅ Игра #{game_number}: опрос запущен (активных: {len(active_games)}/{MAX_CONCURRENT_GAMES})")
        
    except Exception as e:
        logging.error(f"Ошибка при запуске опроса: {e}")
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

def clean_finished_games():
    with lock:
        finished = [g for g, info in active_games.items() if not info['thread'].is_alive()]
        for g in finished:
            del active_games[g]
            logging.info(f"Игра #{g} очищена")

def monitor_loop():
    global bot_running
    logging.info("🚀 Бот 21 Classic ЗАПУЩЕН (Selenium)")
    logging.info(f"Максимум одновременных игр: {MAX_CONCURRENT_GAMES}")
    
    last_launch_time = 0
    last_game_launched = None
    
    while bot_running:
        try:
            clean_finished_games()
            
            now = datetime.now()
            
            if now.second >= 58 and now.minute % 2 == 1:
                next_game_time, seconds_to_next = get_next_game_time()
                
                if seconds_to_next <= 2 and (time.time() - last_launch_time) > 58:
                    game_number = get_game_number_by_time(next_game_time)
                    
                    if last_game_launched != game_number:
                        logging.info(f"⏰ Запуск игры #{game_number}")
                        
                        if len(active_games) < MAX_CONCURRENT_GAMES:
                            launch_next_game()
                            last_launch_time = time.time()
                            last_game_launched = game_number
                        
                        time.sleep(2)
            
            time.sleep(1)
            
        except KeyboardInterrupt:
            bot_running = False
            break
        except Exception as e:
            logging.error(f"Ошибка в основном цикле: {e}")
            time.sleep(5)
    
    game_data.save_data()
    logging.info("Бот остановлен")

def main():
    monitor_loop()

if __name__ == "__main__":
    main()