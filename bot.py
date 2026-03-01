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
from selenium.common.exceptions import WebDriverException, TimeoutException, NoSuchElementException
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

# Проверяем доступ к Telegram при старте
try:
    bot.send_message(CHANNEL_ID, "🤖 Бот запускается... Проверка связи")
    logging.info("✅ Telegram работает")
except Exception as e:
    logging.error(f"❌ Telegram НЕ работает: {e}")

def create_driver():
    """Создает браузер"""
    logging.info("🔄 Создание браузера...")
    options = Options()
    
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--remote-debugging-port=9222')
    options.add_argument('--disable-blink-features=AutomationControlled')
    
    # Путь к Chromium
    chrome_paths = ['/usr/bin/chromium', '/usr/bin/google-chrome', '/usr/bin/google-chrome-stable']
    chrome_found = False
    for path in chrome_paths:
        if os.path.exists(path):
            options.binary_location = path
            logging.info(f"✅ Chrome найден: {path}")
            chrome_found = True
            break
    
    if not chrome_found:
        logging.error("❌ Chrome не найден")
        return None
    
    # Путь к chromedriver
    driver_paths = ['/usr/bin/chromedriver', '/usr/local/bin/chromedriver']
    driver_found = False
    service = None
    for path in driver_paths:
        if os.path.exists(path):
            service = Service(path)
            logging.info(f"✅ Chromedriver найден: {path}")
            driver_found = True
            break
    
    if not driver_found:
        logging.error("❌ Chromedriver не найден")
        return None
    
    try:
        driver = webdriver.Chrome(service=service, options=options)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        logging.info("✅ Браузер успешно создан")
        return driver
    except Exception as e:
        logging.error(f"❌ Ошибка создания браузера: {e}")
        return None

def parse_card_from_element(card_element):
    """Из элемента карты достает масть и значение"""
    try:
        class_str = card_element.get_attribute('class')
        logging.debug(f"Парсим карту с классом: {class_str}")
        
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
        
        result = f"{value}{suit}"
        logging.debug(f"Карта распознана: {result}")
        return result
    except Exception as e:
        logging.error(f"Ошибка парсинга карты: {e}")
        return '??'

def monitor_table(table_url, table_id):
    """Следит за одним столом"""
    driver = None
    start_time = time.time()
    
    try:
        logging.info(f"🔄 Браузер для стола #{table_id} запущен, URL: {table_url}")
        driver = create_driver()
        if not driver:
            return
            
        driver.set_page_load_timeout(30)
        driver.get(table_url)
        time.sleep(5)
        
        # Проверяем что страница загрузилась
        logging.info(f"✅ Стол #{table_id} загружен, заголовок: {driver.title}")
        
        while True:
            if time.time() - start_time > 3600:
                logging.warning(f"⏰ Стол #{table_id} превысил время ожидания")
                break
            
            try:
                # Ищем статус игры
                status_elements = driver.find_elements(By.CSS_SELECTOR, SELECTORS['game_status'])
                if not status_elements:
                    logging.warning(f"⚠️ Стол #{table_id} - элемент статуса не найден")
                    time.sleep(5)
                    continue
                
                status_text = status_elements[0].text
                logging.info(f"🎯 Стол #{table_id} статус: '{status_text}'")
                
                # Проверяем разные варианты завершения
                if any(word in status_text.lower() for word in ['завершен', 'завершена', 'completed', 'finished']):
                    logging.info(f"✅ Стол #{table_id} завершен! Начинаем парсинг карт")
                    
                    # Парсим игрока
                    try:
                        player_score = driver.find_element(By.CSS_SELECTOR, SELECTORS['player_score']).text
                        logging.info(f"📊 #{table_id} Счет игрока: {player_score}")
                    except Exception as e:
                        logging.error(f"❌ #{table_id} Не могу найти счет игрока: {e}")
                        break
                    
                    try:
                        player_card_elements = driver.find_elements(By.CSS_SELECTOR, SELECTORS['player_cards'])
                        logging.info(f"🃏 #{table_id} Найдено карт игрока: {len(player_card_elements)}")
                        player_cards = []
                        for card in player_card_elements:
                            card_str = parse_card_from_element(card)
                            player_cards.append(card_str)
                        logging.info(f"🃏 #{table_id} Карты игрока: {player_cards}")
                    except Exception as e:
                        logging.error(f"❌ #{table_id} Ошибка парсинга карт игрока: {e}")
                        break
                    
                    # Парсим дилера
                    try:
                        dealer_score = driver.find_element(By.CSS_SELECTOR, SELECTORS['dealer_score']).text
                        logging.info(f"📊 #{table_id} Счет дилера: {dealer_score}")
                    except Exception as e:
                        logging.error(f"❌ #{table_id} Не могу найти счет дилера: {e}")
                        break
                    
                    try:
                        dealer_card_elements = driver.find_elements(By.CSS_SELECTOR, SELECTORS['dealer_cards'])
                        logging.info(f"🃏 #{table_id} Найдено карт дилера: {len(dealer_card_elements)}")
                        dealer_cards = []
                        for card in dealer_card_elements:
                            card_str = parse_card_from_element(card)
                            dealer_cards.append(card_str)
                        logging.info(f"🃏 #{table_id} Карты дилера: {dealer_cards}")
                    except Exception as e:
                        logging.error(f"❌ #{table_id} Ошибка парсинга карт дилера: {e}")
                        break
                    
                    # Формируем сообщение
                    player_cards_str = ''.join(player_cards)
                    dealer_cards_str = ''.join(dealer_cards)
                    
                    t_number = random.randint(30, 60)
                    message = f"#{table_id}. {player_score}({player_cards_str}) - {dealer_score}({dealer_cards_str}) #T{t_number}"
                    
                    logging.info(f"📝 #{table_id} Сообщение готово: {message}")
                    
                    # Отправляем
                    try:
                        bot.send_message(CHANNEL_ID, message)
                        logging.info(f"✅ #{table_id} Сообщение отправлено в Telegram")
                    except Exception as e:
                        logging.error(f"❌ #{table_id} Ошибка отправки в Telegram: {e}")
                    
                    processed_games.add(table_id)
                    break
                
                time.sleep(3)
                
            except Exception as e:
                logging.error(f"⚠️ Ошибка в столе #{table_id}: {e}")
                time.sleep(5)
                
    except Exception as e:
        logging.error(f"❌ Критическая ошибка стола #{table_id}: {e}")
    finally:
        if driver:
            try:
                driver.quit()
                logging.info(f"🛑 Браузер для стола #{table_id} закрыт")
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
            
        driver.set_page_load_timeout(30)
        driver.get(MAIN_PAGE_URL)
        time.sleep(5)
        
        # Проверяем что страница загрузилась
        logging.info(f"✅ Главная страница загружена, заголовок: {driver.title}")
        
        # Собираем все столы
        table_links = driver.find_elements(By.CSS_SELECTOR, SELECTORS['table_link'])
        table_ids = driver.find_elements(By.CSS_SELECTOR, SELECTORS['table_id'])
        
        logging.info(f"🔗 Найдено ссылок: {len(table_links)}, ID: {len(table_ids)}")
        
        available_slots = MAX_BROWSERS - len(active_tables)
        if available_slots <= 0:
            logging.info(f"⚠️ Достигнут лимит браузеров ({MAX_BROWSERS})")
            return
        
        new_tables = []
        for i, link in enumerate(table_links):
            if i < len(table_ids):
                table_id = table_ids[i].text.strip()
                href = link.get_attribute('href')
                logging.info(f"🔍 Стол #{table_id}: {href}")
                
                if href and table_id and table_id not in processed_games and table_id not in active_tables:
                    new_tables.append((table_id, href))
                    if len(new_tables) >= available_slots:
                        break
        
        logging.info(f"🚀 Новых столов для запуска: {len(new_tables)}")
        
        for table_id, href in new_tables:
            logging.info(f"🚀 Запускаем монитор для стола #{table_id}")
            thread = threading.Thread(target=monitor_table, args=(href, table_id))
            thread.daemon = True
            thread.start()
            active_tables[table_id] = {'thread': thread, 'start_time': time.time()}
            
    except Exception as e:
        logging.error(f"❌ Ошибка сканирования: {e}")
    finally:
        if driver:
            driver.quit()

def clean_finished_tables():
    """Удаляет завершенные столы"""
    finished = []
    for table_id, data in active_tables.items():
        if not data['thread'].is_alive():
            finished.append(table_id)
    
    for table_id in finished:
        del active_tables[table_id]
        logging.info(f"🧹 Стол #{table_id} удален из активных")

def main():
    logging.info("="*50)
    logging.info("🤖 БОТ ЗАПУЩЕН")
    logging.info(f"📊 Максимум браузеров: {MAX_BROWSERS}")
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
            logging.error(f"💥 Ошибка в главном цикле (попытка {error_count}): {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
