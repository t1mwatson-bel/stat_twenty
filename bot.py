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
from selenium.common.exceptions import WebDriverException, TimeoutException, NoSuchElementException, StaleElementReferenceException
import telebot
import random
import psutil

# ================== НАСТРОЙКИ ==================
TOKEN = "8357635747:AAGAH_Rwk-vR8jGa6Q9F-AJLsMaEIj-JDBU"
CHANNEL_ID = "-1003179573402"
MAIN_PAGE_URL = "https://1xlite-7636770.bar/ru/live/twentyone/1643503-twentyone-game"
MAX_BROWSERS = 3
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
    'game_status': '.ui-game-timer__label',
    'game_round': '.scoreboard-card-games-board-status'
}

# ========== ИСПРАВЛЕННЫЙ МАППИНГ МАСТЕЙ ==========
# Проверено на реальных раздачах:
# - 9♠️ = suit-0
# - 9♦️ = suit-2
# - A♦️ = suit-2 + value-14
# - 10♣️ = suit-1
SUIT_MAP = {
    'suit-0': '♠️',  # Пики
    'suit-1': '♣️',  # Трефы
    'suit-2': '♦️',  # Бубны (туз бубна, 9 бубна)
    'suit-3': '♥️'   # Черви
}

VALUE_MAP = {
    'value-11': 'J',
    'value-12': 'Q',
    'value-13': 'K',
    'value-14': 'A'  # Туз
}
# ==============================================

bot = telebot.TeleBot(TOKEN)
active_tables = {}
processed_games = set()
sent_actions = {}

def check_memory():
    """Проверяет свободную память"""
    try:
        memory = psutil.virtual_memory()
        free_mb = memory.available / 1024 / 1024
        logging.info(f"💾 Свободно памяти: {free_mb:.0f} MB")
        return free_mb > 300
    except Exception as e:
        logging.warning(f"⚠️ Не удалось проверить память: {e}")
        return True

def create_driver():
    """Создает браузер с оптимизацией памяти"""
    logging.info("🔄 Создание браузера...")
    
    if not check_memory():
        logging.error("❌ Недостаточно памяти для создания браузера")
        return None
    
    options = Options()
    
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--disable-software-rasterizer')
    options.add_argument('--disable-features=VizDisplayCompositor')
    options.add_argument('--disable-features=TranslateUI')
    options.add_argument('--disable-features=BlinkGenPropertyTrees')
    options.add_argument('--disable-logging')
    options.add_argument('--log-level=3')
    options.add_argument('--silent')
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--remote-debugging-port=9222')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_argument('--disable-extensions')
    options.add_argument('--disable-setuid-sandbox')
    options.add_argument('--memory-pressure-off')
    options.add_argument('--single-process')
    options.add_argument('--disable-component-extensions-with-background-pages')
    options.add_argument('--disable-default-apps')
    options.add_argument('--disable-sync')
    
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
    
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            driver = webdriver.Chrome(service=service, options=options)
            driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            driver.set_page_load_timeout(30)
            logging.info("✅ Браузер успешно создан")
            return driver
        except Exception as e:
            if attempt < max_attempts - 1:
                logging.warning(f"⚠️ Попытка {attempt + 1} не удалась, пробуем снова...")
                time.sleep(2)
            else:
                logging.error(f"❌ Ошибка создания браузера после {max_attempts} попыток: {e}")
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

def get_game_state(driver, table_id):
    """Получает текущее состояние игры"""
    try:
        round_status = "Неизвестно"
        try:
            round_elem = driver.find_element(By.CSS_SELECTOR, SELECTORS['game_round'])
            round_status = round_elem.text
        except:
            pass
        
        player_score = "?"
        player_cards = []
        try:
            player_score = driver.find_element(By.CSS_SELECTOR, SELECTORS['player_score']).text
            player_card_elements = driver.find_elements(By.CSS_SELECTOR, SELECTORS['player_cards'])
            for card in player_card_elements:
                player_cards.append(parse_card_from_element(card))
        except:
            pass
        
        dealer_score = "?"
        dealer_cards = []
        try:
            dealer_score = driver.find_element(By.CSS_SELECTOR, SELECTORS['dealer_score']).text
            dealer_card_elements = driver.find_elements(By.CSS_SELECTOR, SELECTORS['dealer_cards'])
            for card in dealer_card_elements:
                dealer_cards.append(parse_card_from_element(card))
        except:
            pass
        
        game_status = ""
        try:
            status_elem = driver.find_element(By.CSS_SELECTOR, SELECTORS['game_status'])
            game_status = status_elem.text
        except:
            pass
        
        return {
            'round_status': round_status,
            'player_score': player_score,
            'player_cards': player_cards,
            'dealer_score': dealer_score,
            'dealer_cards': dealer_cards,
            'game_status': game_status
        }
    except Exception as e:
        logging.error(f"Ошибка получения состояния игры #{table_id}: {e}")
        return None

def format_cards(cards):
    """Форматирует список карт в строку"""
    return ''.join(cards) if cards else ""

def monitor_table(table_url, table_id):
    """Следит за одним столом в реальном времени"""
    driver = None
    start_time = time.time()
    last_state = None
    action_count = 0
    
    try:
        logging.info(f"🔄 Браузер для стола #{table_id} запущен")
        driver = create_driver()
        if not driver:
            return
        
        driver.get(table_url)
        logging.info(f"✅ Стол #{table_id} загружен")
        
        bot.send_message(CHANNEL_ID, f"🎯 Стол #{table_id}: Начало мониторинга")
        
        while True:
            if time.time() - start_time > 3600:
                logging.warning(f"⏰ Стол #{table_id} превысил время ожидания")
                bot.send_message(CHANNEL_ID, f"⏰ Стол #{table_id}: Превышено время ожидания")
                break
            
            try:
                current_state = get_game_state(driver, table_id)
                if not current_state:
                    time.sleep(2)
                    continue
                
                if any(word in current_state['game_status'].lower() for word in ['завершен', 'завершена', 'completed', 'finished']):
                    player_cards_str = format_cards(current_state['player_cards'])
                    dealer_cards_str = format_cards(current_state['dealer_cards'])
                    t_number = random.randint(30, 60)
                    
                    final_message = (f"🏆 Стол #{table_id}: ИГРА ЗАВЕРШЕНА\n"
                                   f"👤 Игрок: {current_state['player_score']}({player_cards_str})\n"
                                   f"👤 Дилер: {current_state['dealer_score']}({dealer_cards_str})\n"
                                   f"#N{table_id}. {current_state['player_score']}({player_cards_str}) - "
                                   f"{current_state['dealer_score']}({dealer_cards_str}) #T{t_number}")
                    
                    bot.send_message(CHANNEL_ID, final_message)
                    logging.info(f"✅ Стол #{table_id} завершен, финал отправлен")
                    break
                
                if current_state != last_state:
                    action_count += 1
                    
                    player_cards_str = format_cards(current_state['player_cards'])
                    dealer_cards_str = format_cards(current_state['dealer_cards'])
                    
                    action_type = "🔄"
                    if "Ход игрока" in current_state['round_status']:
                        action_type = "🎯"
                    elif "Ход дилера" in current_state['round_status']:
                        action_type = "🎲"
                    
                    if last_state:
                        new_player_cards = len(current_state['player_cards']) - len(last_state.get('player_cards', []))
                        new_dealer_cards = len(current_state['dealer_cards']) - len(last_state.get('dealer_cards', []))
                        
                        if new_player_cards > 0:
                            new_cards = current_state['player_cards'][-new_player_cards:]
                            bot.send_message(CHANNEL_ID, f"{action_type} Стол #{table_id}: Игрок взял {', '.join(new_cards)}")
                        
                        if new_dealer_cards > 0:
                            new_cards = current_state['dealer_cards'][-new_dealer_cards:]
                            bot.send_message(CHANNEL_ID, f"{action_type} Стол #{table_id}: Дилер взял {', '.join(new_cards)}")
                    
                    if action_count % 3 == 0 or "перебор" in current_state['round_status'].lower() or "очко" in current_state['round_status'].lower():
                        status_message = (f"{action_type} Стол #{table_id}: {current_state['round_status']}\n"
                                        f"👤 Игрок: {current_state['player_score']} {player_cards_str}\n"
                                        f"👤 Дилер: {current_state['dealer_score']} {dealer_cards_str}")
                        bot.send_message(CHANNEL_ID, status_message)
                    
                    last_state = current_state
                
                time.sleep(2)
                
            except StaleElementReferenceException:
                logging.warning(f"⚠️ Стол #{table_id} - элементы устарели, обновляем страницу")
                driver.refresh()
                time.sleep(3)
            except NoSuchElementException:
                logging.warning(f"⚠️ Стол #{table_id} - элемент не найден")
                time.sleep(3)
            except Exception as e:
                logging.error(f"⚠️ Ошибка в столе #{table_id}: {e}")
                time.sleep(3)
                
    except Exception as e:
        logging.error(f"❌ Критическая ошибка стола #{table_id}: {e}")
        try:
            bot.send_message(CHANNEL_ID, f"❌ Стол #{table_id}: Ошибка мониторинга")
        except:
            pass
    finally:
        if driver:
            try:
                driver.quit()
                logging.info(f"🛑 Браузер для стола #{table_id} закрыт")
            except:
                pass

def scan_new_tables():
    """Сканирует главную страницу и запускает только свободные столы"""
    driver = None
    try:
        logging.info("🔍 Сканирование новых столов...")
        driver = create_driver()
        if not driver:
            return
        
        driver.get(MAIN_PAGE_URL)
        time.sleep(5)
        
        # Собираем ссылки
        table_links = driver.find_elements(By.CSS_SELECTOR, SELECTORS['table_link'])
        table_ids = driver.find_elements(By.CSS_SELECTOR, SELECTORS['table_id'])
        
        logging.info(f"🔗 Найдено ссылок: {len(table_links)}, ID: {len(table_ids)}")
        
        available_tables = []
        
        for i, link in enumerate(table_links):
            href = link.get_attribute('href')
            table_id = None
            
            # СПОСОБ 1: Через селектор
            if i < len(table_ids):
                table_id = table_ids[i].text.strip()
                logging.info(f"📌 Стол найден через селектор: ID={table_id}")
            
            # СПОСОБ 2: Из ссылки
            if not table_id and href:
                import re
                match = re.search(r'/(\d+)-player', href)
                if match:
                    table_id = match.group(1)
                    logging.info(f"📌 Стол найден через ссылку: ID={table_id}")
            
            if not table_id:
                logging.warning(f"⚠️ Не удалось получить ID для ссылки {href}")
                continue
            
            if table_id in processed_games:
                logging.info(f"⏭️ Стол #{table_id} уже обработан, пропускаем")
                continue
            
            if table_id in active_tables:
                logging.info(f"👁️ Стол #{table_id} уже мониторится, пропускаем")
                continue
            
            # Проверка статуса на главной
            try:
                # Ищем родительский элемент
                parent = link.find_element(By.XPATH, '../../../../..')
                status_elem = parent.find_elements(By.CSS_SELECTOR, SELECTORS['game_status'])
                if status_elem:
                    status_text = status_elem[0].text
                    if any(word in status_text.lower() for word in ['завершен', 'завершена']):
                        logging.info(f"✅ Стол #{table_id} уже завершен, добавляем в обработанные")
                        processed_games.add(table_id)
                        continue
            except Exception as e:
                logging.debug(f"Не удалось проверить статус для #{table_id}: {e}")
            
            available_tables.append((table_id, href))
            logging.info(f"✅ Стол #{table_id} свободен для мониторинга")
        
        logging.info(f"📊 Статистика: Всего {len(table_links)} столов, Свободных: {len(available_tables)}")
        
        driver.quit()
        
        # Запускаем свободные столы
        started = 0
        for table_id, href in available_tables:
            if len(active_tables) >= MAX_BROWSERS:
                logging.info(f"⚠️ Достигнут лимит браузеров ({MAX_BROWSERS})")
                break
            
            if not check_memory():
                logging.error("❌ Недостаточно памяти для запуска нового стола")
                break
            
            if table_id in active_tables or table_id in processed_games:
                continue
            
            logging.info(f"🚀 Запускаем монитор для свободного стола #{table_id}")
            thread = threading.Thread(target=monitor_table, args=(href, table_id))
            thread.daemon = True
            thread.start()
            
            time.sleep(5)
            
            if thread.is_alive():
                active_tables[table_id] = {'thread': thread, 'start_time': time.time()}
                started += 1
                logging.info(f"✅ Стол #{table_id} успешно запущен")
            else:
                logging.error(f"❌ Стол #{table_id} НЕ запустился")
            
            time.sleep(3)
        
        logging.info(f"🚀 Запущено новых столов: {started}")
            
    except Exception as e:
        logging.error(f"❌ Ошибка сканирования: {e}")
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass
                
                if href and table_id:
                    available_tables.append((table_id, href))
                    logging.info(f"✅ Стол #{table_id} свободен для мониторинга")
        
        logging.info(f"📊 Статистика: Всего {len(table_links)} столов, Свободных: {len(available_tables)}")
        
        driver.quit()
        
        started = 0
        for table_id, href in available_tables:
            if len(active_tables) >= MAX_BROWSERS:
                logging.info(f"⚠️ Достигнут лимит браузеров ({MAX_BROWSERS})")
                break
            
            if not check_memory():
                logging.error("❌ Недостаточно памяти для запуска нового стола")
                break
            
            if table_id in active_tables or table_id in processed_games:
                logging.info(f"⏭️ Стол #{table_id} уже занят, пропускаем")
                continue
            
            logging.info(f"🚀 Запускаем монитор для свободного стола #{table_id}")
            thread = threading.Thread(target=monitor_table, args=(href, table_id))
            thread.daemon = True
            thread.start()
            
            time.sleep(5)
            
            if thread.is_alive():
                active_tables[table_id] = {'thread': thread, 'start_time': time.time()}
                started += 1
                logging.info(f"✅ Стол #{table_id} успешно запущен")
            else:
                logging.error(f"❌ Стол #{table_id} НЕ запустился")
            
            time.sleep(3)
        
        logging.info(f"🚀 Запущено новых столов: {started}")
            
    except Exception as e:
        logging.error(f"❌ Ошибка сканирования: {e}")
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

def clean_finished_tables():
    """Удаляет завершенные столы"""
    finished = []
    for table_id, data in active_tables.items():
        if not data['thread'].is_alive():
            finished.append(table_id)
            processed_games.add(table_id)
    
    for table_id in finished:
        del active_tables[table_id]
        logging.info(f"🧹 Стол #{table_id} удален из активных")

def main():
    logging.info("="*50)
    logging.info("🤖 REAL-TIME БОТ ЗАПУЩЕН")
    logging.info(f"📊 Максимум браузеров: {MAX_BROWSERS}")
    logging.info("="*50)
    
    try:
        bot.send_message(CHANNEL_ID, "🤖 Real-time бот запущен и мониторит столы")
    except Exception as e:
        logging.error(f"❌ Ошибка отправки тестового сообщения: {e}")
    
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
