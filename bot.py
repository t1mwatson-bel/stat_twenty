import threading
import time
import re
import logging
import os
import signal
import sys
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import WebDriverException, TimeoutException
import telebot
import random

# ================== НАСТРОЙКИ ==================
TOKEN = "8357635747:AAGAH_Rwk-vR8jGa6Q9F-AJLsMaEIj-JDBU"
CHANNEL_ID = "-1003179573402"
MAIN_PAGE_URL = "https://1xlite-7636770.bar/ru/live/twentyone/1643503-twentyone-game"
MAX_BROWSERS = 10
CHECK_INTERVAL = 60

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)

# Селекторы
SELECTORS = {
    'table_link': '.dashboard-game-block__link',
    'table_id': '.dashboard-game-info__additional-info',
    'player_score': '.live-twenty-one-field-player:first-child .live-twenty-one-field-score__label',
    'player_cards': '.live-twenty-one-field-player:first-child .scoreboard-card-games-card',
    'dealer_score': '.live-twenty-one-field-player:last-child .live-twenty-one-field-score__label',
    'dealer_cards': '.live-twenty-one-field-player:last-child .scoreboard-card-games-card',
    'game_status': '.ui-game-timer__label'
}

# Масти и значения
SUIT_MAP = {
    'suit-0': '♠️',
    'suit-1': '♥️',
    'suit-2': '♣️',
    'suit-3': '♦️'
}

VALUE_MAP = {
    'value-11': 'J',
    'value-12': 'Q',
    'value-13': 'K',
    'value-14': 'A'
}
# ==============================================

bot = telebot.TeleBot(TOKEN)
active_tables = {}
processed_games = set()

def create_driver():
    """Создает браузер для Railway"""
    logging.info("🔄 Создание браузера...")
    
    options = Options()
    
    # Критические параметры для Railway
    options.add_argument('--headless=new')  # новый режим headless
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--remote-debugging-port=9222')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_argument('--disable-extensions')
    options.add_argument('--disable-setuid-sandbox')
    options.add_argument('--window-size=1920,1080')
    
    # Путь к Chromium в Railway
    chrome_paths = [
        '/usr/bin/chromium',
        '/usr/bin/chromium-browser',
        '/usr/bin/google-chrome',
        '/usr/bin/google-chrome-stable'
    ]
    
    chrome_found = False
    for path in chrome_paths:
        if os.path.exists(path):
            options.binary_location = path
            chrome_found = True
            logging.info(f"✅ Chrome найден: {path}")
            break
    
    if not chrome_found:
        logging.error("❌ Chrome не найден!")
        return None
    
    # Путь к chromedriver
    driver_paths = [
        '/usr/bin/chromedriver',
        '/usr/lib/chromium/chromedriver',
        '/usr/bin/chromium-driver'
    ]
    
    driver_path = None
    for path in driver_paths:
        if os.path.exists(path):
            driver_path = path
            logging.info(f"✅ Chromedriver найден: {path}")
            break
    
    if not driver_path:
        logging.error("❌ Chromedriver не найден!")
        return None
    
    try:
        service = Service(executable_path=driver_path)
        driver = webdriver.Chrome(service=service, options=options)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        driver.set_page_load_timeout(30)
        logging.info("✅ Браузер успешно создан")
        return driver
    except Exception as e:
        logging.error(f"❌ Ошибка создания браузера: {e}")
        return None

def parse_card_from_element(card_element):
    """Из элемента карты достает масть и значение"""
    try:
        class_str = card_element.get_attribute('class')
        
        suit = '?'
        for suit_class, suit_symbol in SUIT_MAP.items():
            if suit_class in class_str:
                suit = suit_symbol
                break
        
        value_match = re.search(r'value-(\d+)', class_str)
        if value_match:
            value_num = value_match.group(1)
            value = VALUE_MAP.get(f'value-{value_num}', value_num)
        else:
            value = '?'
        
        return f"{value}{suit}"
    except Exception as e:
        logging.error(f"Ошибка парсинга карты: {e}")
        return '??'

def monitor_table(table_url, table_id):
    """Следит за одним столом"""
    driver = None
    start_time = time.time()
    
    try:
        logging.info(f"🔄 Мониторинг стола #{table_id}")
        driver = create_driver()
        if not driver:
            return
            
        driver.get(table_url)
        logging.info(f"✅ Зашел в стол #{table_id}")
        time.sleep(3)
        
        while True:
            if time.time() - start_time > 3600:
                logging.warning(f"⏰ Таймаут стола #{table_id}")
                break
            
            try:
                # Проверяем статус
                status_elem = driver.find_element(By.CSS_SELECTOR, SELECTORS['game_status'])
                status_text = status_elem.text
                logging.info(f"Стол #{table_id} статус: {status_text}")
                
                if "завершен" in status_text.lower() or "completed" in status_text.lower():
                    logging.info(f"✅ Стол #{table_id} завершен")
                    
                    # Парсим данные
                    player_score = driver.find_element(By.CSS_SELECTOR, SELECTORS['player_score']).text
                    player_cards_elem = driver.find_elements(By.CSS_SELECTOR, SELECTORS['player_cards'])
                    player_cards = [parse_card_from_element(card) for card in player_cards_elem]
                    
                    dealer_score = driver.find_element(By.CSS_SELECTOR, SELECTORS['dealer_score']).text
                    dealer_cards_elem = driver.find_elements(By.CSS_SELECTOR, SELECTORS['dealer_cards'])
                    dealer_cards = [parse_card_from_element(card) for card in dealer_cards_elem]
                    
                    # Формируем сообщение
                    player_cards_str = ''.join(player_cards)
                    dealer_cards_str = ''.join(dealer_cards)
                    t_number = random.randint(30, 60)
                    
                    message = f"#{table_id}. {player_score}({player_cards_str}) - {dealer_score}({dealer_cards_str}) #T{t_number}"
                    
                    # Отправляем
                    try:
                        bot.send_message(CHANNEL_ID, message)
                        logging.info(f"📤 Отправлено: {message}")
                    except Exception as e:
                        logging.error(f"Ошибка отправки: {e}")
                    
                    processed_games.add(table_id)
                    break
                
                time.sleep(2)
                
            except Exception as e:
                logging.error(f"Ошибка в столе #{table_id}: {e}")
                time.sleep(5)
                
    except Exception as e:
        logging.error(f"❌ Ошибка стола #{table_id}: {e}")
    finally:
        if driver:
            try:
                driver.quit()
                logging.info(f"🛑 Браузер стола #{table_id} закрыт")
            except:
                pass

def scan_new_tables():
    """Сканирует главную страницу"""
    driver = None
    try:
        logging.info("🔍 Сканирование новых столов...")
        driver = create_driver()
        if not driver:
            return
            
        driver.get(MAIN_PAGE_URL)
        time.sleep(5)
        
        # Собираем столы
        table_links = driver.find_elements(By.CSS_SELECTOR, SELECTORS['table_link'])
        table_ids = driver.find_elements(By.CSS_SELECTOR, SELECTORS['table_id'])
        
        logging.info(f"Найдено столов: {len(table_links)}")
        
        available_slots = MAX_BROWSERS - len(active_tables)
        
        for i, link in enumerate(table_links):
            if i < len(table_ids) and len(active_tables) < MAX_BROWSERS:
                table_id = table_ids[i].text.strip()
                href = link.get_attribute('href')
                
                if href and table_id and table_id not in processed_games and table_id not in active_tables:
                    logging.info(f"🚀 Новый стол #{table_id}")
                    thread = threading.Thread(target=monitor_table, args=(href, table_id))
                    thread.daemon = True
                    thread.start()
                    active_tables[table_id] = {'thread': thread, 'start_time': time.time()}
                    
    except Exception as e:
        logging.error(f"Ошибка сканирования: {e}")
    finally:
        if driver:
            driver.quit()

def clean_finished_tables():
    """Очищает завершенные столы"""
    finished = []
    for table_id, data in active_tables.items():
        if not data['thread'].is_alive():
            finished.append(table_id)
    
    for table_id in finished:
        del active_tables[table_id]
        logging.info(f"🧹 Стол #{table_id} удален")

def main():
    """Главная функция"""
    logging.info("="*50)
    logging.info("🤖 БОТ ЗАПУЩЕН")
    logging.info("="*50)
    
    error_count = 0
    while True:
        try:
            clean_finished_tables()
            scan_new_tables()
            logging.info(f"📊 Активных столов: {len(active_tables)}/{MAX_BROWSERS}")
            error_count = 0
            time.sleep(CHECK_INTERVAL)
            
        except Exception as e:
            error_count += 1
            logging.error(f"Ошибка: {e}")
            if error_count > 5:
                logging.error("Много ошибок, жду 5 минут")
                time.sleep(300)
                error_count = 0
            time.sleep(60)

if __name__ == "__main__":
    main()