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
from webdriver_manager.chrome import ChromeDriverManager

# ================== НАСТРОЙКИ ==================
TOKEN = "8357635747:AAGAH_Rwk-vR8jGa6Q9F-AJLsMaEIj-JDBU"
CHANNEL_ID = "-1003179573402"
MAIN_PAGE_URL = "https://1xlite-7636770.bar/ru/live/twentyone/1643503-twentyone-game"
MAX_BROWSERS = 10  # Максимум одновременных браузеров (под твой хостинг)
CHECK_INTERVAL = 60  # Сканирование новых столов каждые 60 секунд

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
active_tables = {}  # {table_id: {'thread': thread, 'start_time': time}}
processed_games = set()  # уже отправленные игры

def create_driver():
    """Создает браузер с оптимизациями для хостинга"""
    options = Options()
    
    # Режим без графики (обязательно для хостинга!)
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--disable-extensions')
    
    # Антидетект
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_argument(f'--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{random.randint(110, 115)}.0.0.0 Safari/537.36')
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    
    # Оптимизация памяти
    options.add_argument('--memory-pressure-off')
    options.add_argument('--single-process')  # для слабых хостингов
    options.add_argument('--disable-features=TranslateUI')
    options.add_argument('--disable-features=BlinkGenPropertyTrees')
    
    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        return driver
    except Exception as e:
        logging.error(f"Ошибка создания браузера: {e}")
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
    """Следит за одним столом в отдельном браузере"""
    driver = None
    start_time = time.time()
    
    try:
        logging.info(f"🔄 Браузер для стола #{table_id} запущен")
        driver = create_driver()
        if not driver:
            return
            
        driver.set_page_load_timeout(30)
        driver.get(table_url)
        time.sleep(random.uniform(3, 5))
        
        while True:
            # Проверка времени жизни (максимум 1 час на игру)
            if time.time() - start_time > 3600:
                logging.warning(f"⏰ Стол #{table_id} превысил время ожидания")
                break
            
            try:
                status_elem = driver.find_element(By.CSS_SELECTOR, SELECTORS['game_status'])
                status_text = status_elem.text
                
                if "Игра завершена" in status_text:
                    logging.info(f"✅ Стол #{table_id} завершен, парсим карты")
                    
                    # Парсим игрока
                    player_score = driver.find_element(By.CSS_SELECTOR, SELECTORS['player_score']).text
                    player_card_elements = driver.find_elements(By.CSS_SELECTOR, SELECTORS['player_cards'])
                    player_cards = [parse_card_from_element(card) for card in player_card_elements]
                    
                    # Парсим дилера
                    dealer_score = driver.find_element(By.CSS_SELECTOR, SELECTORS['dealer_score']).text
                    dealer_card_elements = driver.find_elements(By.CSS_SELECTOR, SELECTORS['dealer_cards'])
                    dealer_cards = [parse_card_from_element(card) for card in dealer_card_elements]
                    
                    # Формируем сообщение
                    player_cards_str = ''.join(player_cards)
                    dealer_cards_str = ''.join(dealer_cards)
                    
                    # Генерируем #Txx
                    t_number = random.randint(30, 60)
                    
                    message = f"#{table_id}. {player_score}({player_cards_str}) - {dealer_score}({dealer_cards_str}) #T{t_number}"
                    
                    # Отправляем
                    try:
                        bot.send_message(CHANNEL_ID, message)
                        logging.info(f"📤 Отправлено: {message}")
                    except Exception as e:
                        logging.error(f"Ошибка отправки в Telegram: {e}")
                    
                    processed_games.add(table_id)
                    break
                
                time.sleep(random.uniform(2, 4))
                
            except Exception as e:
                logging.error(f"⚠️ Ошибка в столе #{table_id}: {e}")
                time.sleep(5)
                
    except WebDriverException as e:
        logging.error(f"❌ WebDriver ошибка стола #{table_id}: {e}")
    except Exception as e:
        logging.error(f"❌ Критическая ошибка стола #{table_id}: {e}")
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass
        logging.info(f"🛑 Браузер для стола #{table_id} закрыт")

def scan_new_tables():
    """Сканирует главную страницу и запускает новые столы"""
    driver = None
    try:
        logging.info("🔍 Сканирование новых столов...")
        driver = create_driver()
        if not driver:
            return
            
        driver.set_page_load_timeout(30)
        driver.get(MAIN_PAGE_URL)
        time.sleep(random.uniform(5, 8))
        
        # Собираем все столы
        table_links = driver.find_elements(By.CSS_SELECTOR, SELECTORS['table_link'])
        table_ids = driver.find_elements(By.CSS_SELECTOR, SELECTORS['table_id'])
        
        # Ограничиваем количество браузеров
        available_slots = MAX_BROWSERS - len(active_tables)
        if available_slots <= 0:
            logging.info(f"⚠️ Достигнут лимит браузеров ({MAX_BROWSERS})")
            return
        
        new_tables = []
        for i, link in enumerate(table_links):
            if i < len(table_ids):
                table_id = table_ids[i].text.strip()
                href = link.get_attribute('href')
                
                if href and table_id and table_id not in processed_games and table_id not in active_tables:
                    new_tables.append((table_id, href))
                    if len(new_tables) >= available_slots:
                        break
        
        # Запускаем новые столы
        for table_id, href in new_tables:
            logging.info(f"🚀 Новый стол #{table_id}, запускаем монитор")
            thread = threading.Thread(target=monitor_table, args=(href, table_id))
            thread.daemon = True
            thread.start()
            active_tables[table_id] = {'thread': thread, 'start_time': time.time()}
            
    except WebDriverException as e:
        logging.error(f"❌ WebDriver ошибка сканирования: {e}")
    except Exception as e:
        logging.error(f"❌ Ошибка сканирования: {e}")
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

def clean_finished_tables():
    """Удаляет завершенные столы из active_tables"""
    finished = []
    for table_id, data in active_tables.items():
        if not data['thread'].is_alive():
            finished.append(table_id)
    
    for table_id in finished:
        del active_tables[table_id]
        logging.info(f"🧹 Стол #{table_id} удален из активных")

def signal_handler(sig, frame):
    """Обработка Ctrl+C для корректного завершения"""
    logging.info("🛑 Получен сигнал завершения, закрываем браузеры...")
    for table_id in list(active_tables.keys()):
        logging.info(f"Закрываем стол #{table_id}")
    sys.exit(0)

def main():
    """Главная функция"""
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    logging.info("="*50)
    logging.info("🤖 БОТ ЗАПУЩЕН НА ХОСТИНГЕ")
    logging.info(f"📊 Максимум браузеров: {MAX_BROWSERS}")
    logging.info(f"⏱️  Интервал сканирования: {CHECK_INTERVAL} сек")
    logging.info("="*50)
    
    # Отправляем стартовое сообщение
    try:
        bot.send_message(CHANNEL_ID, "🤖 Бот запущен и мониторит столы")
    except:
        pass
    
    error_count = 0
    while True:
        try:
            clean_finished_tables()
            scan_new_tables()
            logging.info(f"📊 Активных столов: {len(active_tables)}/{MAX_BROWSERS}")
            error_count = 0  # сброс счетчика ошибок
            time.sleep(CHECK_INTERVAL)
            
        except Exception as e:
            error_count += 1
            logging.error(f"💥 Ошибка в главном цикле (попытка {error_count}): {e}")
            
            if error_count > 5:
                logging.critical("⚠️ Слишком много ошибок, перезапуск через 5 минут")
                time.sleep(300)
                error_count = 0
            
            time.sleep(60)

if __name__ == "__main__":
    main()
