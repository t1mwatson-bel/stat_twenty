import threading
import time
import re
import logging
import os
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
    datefmt='%Y-%m-%d %H:%M:%S',
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

# Масти и значения
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
# ==============================================

bot = telebot.TeleBot(TOKEN)
active_tables = {}
processed_games = set()

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

def has_two_aces_of_different_suits(cards):
    """Проверяет есть ли два туза разных мастей"""
    if len(cards) != 2:
        return False
    
    aces = [card for card in cards if card.startswith('A')]
    if len(aces) != 2:
        return False
    
    # Проверяем что масти разные
    suits = [card[1] for card in aces]
    return len(set(suits)) == 2

def calculate_final_hashtags(player_score, dealer_score, player_cards, dealer_cards):
    """Рассчитывает хэштеги для финального сообщения"""
    hashtags = []
    
    # Общая сумма очков #T
    total_score = int(player_score) + int(dealer_score)
    hashtags.append(f"#T{total_score}")
    
    # #G - два туза разных мастей у игрока или дилера
    if has_two_aces_of_different_suits(player_cards) or has_two_aces_of_different_suits(dealer_cards):
        hashtags.append("#G")
    
    # #O - 21 очко у кого-то
    if player_score == "21" or dealer_score == "21":
        hashtags.append("#O")
    
    # #R - перебор или ранняя раздача? 
    # По твоему условию: #R ставится когда игра завершена и у обоих по 2 карты
    if len(player_cards) == 2 and len(dealer_cards) == 2:
        hashtags.append("#R")
    
    # #X - ничья
    if player_score == dealer_score:
        hashtags.append("#X")
    
    return " ".join(hashtags)

def monitor_table(table_url, table_id):
    driver = None
    start_time = time.time()
    last_state = None
    game_actions = set()
    t_number = random.randint(30, 60)

    try:
        logging.info(f"🔄 Браузер для стола #{table_id} запущен")
        driver = create_driver()
        if not driver:
            return

        driver.get(table_url)
        logging.info(f"✅ Стол #{table_id} загружен")

        time.sleep(4)  # Ждём полную отрисовку

        while True:
            if time.time() - start_time > 3600:
                logging.warning(f"⏰ Стол #{table_id} превысил время ожидания")
                break

            try:
                current_state = get_game_state(driver, table_id)
                if not current_state:
                    time.sleep(2)
                    continue

                # ИГНОРИРУЕМ состояния, где нет счёта или карт
                if current_state['player_score'] in ['?', '0', ''] or not current_state['player_cards']:
                    time.sleep(2)
                    continue

                player_cards_str = ''.join(current_state['player_cards'])
                dealer_cards_str = ''.join(current_state['dealer_cards'])

                # Завершение игры
                if any(word in current_state['game_status'].lower() for word in ['завершен', 'завершена']):
                    final_message = f"#N{table_id}. {current_state['player_score']}({player_cards_str}) - {current_state['dealer_score']}({dealer_cards_str}) #T{t_number}"
                    try:
                        bot.send_message(CHANNEL_ID, final_message)
                        logging.info(f"✅ Стол #{table_id} завершён")
                    except:
                        pass
                    break

                # Если состояние изменилось (и оно валидно)
                if current_state != last_state and current_state['player_score'] not in ['?', '0']:
                    if last_state:
                        # Новая карта игрока
                        if len(current_state['player_cards']) > len(last_state['player_cards']):
                            msg = f"⏰#N{table_id}. ▶ {last_state['player_score']}({''.join(last_state['player_cards'])}) - {last_state['dealer_score']}({''.join(last_state['dealer_cards'])})"
                            if msg not in game_actions:
                                bot.send_message(CHANNEL_ID, msg)
                                game_actions.add(msg)

                        # Новая карта дилера
                        if len(current_state['dealer_cards']) > len(last_state['dealer_cards']):
                            msg = f"⏰#N{table_id}. {last_state['player_score']}({''.join(last_state['player_cards'])}) - ▶ {last_state['dealer_score']}({''.join(last_state['dealer_cards'])})"
                            if msg not in game_actions:
                                bot.send_message(CHANNEL_ID, msg)
                                game_actions.add(msg)

                    last_state = current_state

                time.sleep(2)

            except Exception as e:
                logging.error(f"⚠️ Ошибка в столе #{table_id}: {e}")
                time.sleep(3)

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
    """Сканирует главную страницу и запускает только свободные столы"""
    driver = None
    try:
        logging.info("🔍 Сканирование новых столов...")
        driver = create_driver()
        if not driver:
            return
        
        driver.get(MAIN_PAGE_URL)
        time.sleep(5)
        
        table_links = driver.find_elements(By.CSS_SELECTOR, SELECTORS['table_link'])
        table_ids = driver.find_elements(By.CSS_SELECTOR, SELECTORS['table_id'])
        
        logging.info(f"🔗 Найдено ссылок: {len(table_links)}, ID: {len(table_ids)}")
        
        available_tables = []
        
        for i, link in enumerate(table_links):
            href = link.get_attribute('href')
            table_id = None
            
            # Пробуем получить ID разными способами
            if i < len(table_ids):
                table_id = table_ids[i].text.strip()
                logging.info(f"📌 Способ 1: ID={table_id}")
            
            if not table_id and href:
                import re
                match = re.search(r'/(\d+)-player', href)
                if match:
                    table_id = match.group(1)
                    logging.info(f"📌 Способ 2: ID={table_id}")
            
            if not table_id:
                logging.warning(f"⚠️ Не удалось получить ID")
                continue
            
            if table_id in processed_games:
                logging.info(f"⏭️ Стол #{table_id} уже обработан")
                continue
            
            if table_id in active_tables:
                logging.info(f"👁️ Стол #{table_id} уже мониторится")
                continue
            
            available_tables.append((table_id, href))
            logging.info(f"✅ Стол #{table_id} свободен")
        
        logging.info(f"📊 Свободных столов: {len(available_tables)}")
        
        driver.quit()
        
        started = 0
        for table_id, href in available_tables:
            if len(active_tables) >= MAX_BROWSERS:
                logging.info(f"⚠️ Лимит браузеров ({MAX_BROWSERS})")
                break
            
            if not check_memory():
                logging.error("❌ Недостаточно памяти")
                break
            
            logging.info(f"🚀 Запуск стола #{table_id}")
            thread = threading.Thread(target=monitor_table, args=(href, table_id))
            thread.daemon = True
            thread.start()
            
            time.sleep(5)
            
            if thread.is_alive():
                active_tables[table_id] = {'thread': thread, 'start_time': time.time()}
                started += 1
                logging.info(f"✅ Стол #{table_id} запущен")
            else:
                logging.error(f"❌ Стол #{table_id} не запустился")
            
            time.sleep(3)
        
        logging.info(f"🚀 Запущено: {started}")
            
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
        logging.info(f"🧹 Стол #{table_id} удален")

def main():
    logging.info("="*50)
    logging.info("🤖 REAL-TIME БОТ ЗАПУЩЕН")
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
            logging.error(f"💥 Ошибка (попытка {error_count}): {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
