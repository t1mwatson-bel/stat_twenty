import threading
import time
import re
import logging
import os
import sys
import random
import subprocess
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import (
    WebDriverException, TimeoutException, NoSuchElementException,
    StaleElementReferenceException, InvalidSessionIdException
)
import telebot
import psutil

# ================== НАСТРОЙКИ ==================
TOKEN = "8357635747:AAGAH_Rwk-vR8jGa6Q9F-AJLsMaEIj-JDBU"
CHANNEL_ID = "-1003179573402"
MAIN_PAGE_URL = "https://1xlite-7636770.bar/ru/live/twentyone/1643503-twentyone-game"
MAX_BROWSERS = 3
CHECK_INTERVAL = 60

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)

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
active_tables = {}
processed_games = set()
# ==============================================

def kill_all_chromes():
    """Беспощадно убивает всё, что связано с chromium/chrome"""
    try:
        subprocess.run(["pkill", "-9", "-f", "chromium"], capture_output=True)
        subprocess.run(["pkill", "-9", "-f", "chrome"], capture_output=True)
        subprocess.run(["pkill", "-9", "-f", "chromedriver"], capture_output=True)
        logging.info("💀 Все процессы Chromium убиты")
    except:
        pass

def check_memory():
    try:
        mem = psutil.virtual_memory()
        free_mb = mem.available / 1024 / 1024
        logging.info(f"💾 Свободно памяти: {free_mb:.0f} MB")
        return free_mb > 300
    except:
        return True

def create_driver(retries=2):
    kill_all_chromes()  # чистим перед созданием
    for attempt in range(retries):
        try:
            logging.info(f"🔄 Попытка {attempt+1} создания браузера...")
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
            options.add_argument('--disable-background-networking')
            options.add_argument('--disable-background-timer-throttling')
            options.add_argument('--disable-backgrounding-occluded-windows')
            options.add_argument('--disable-breakpad')
            options.add_argument('--disable-client-side-phishing-detection')
            options.add_argument('--disable-component-update')
            options.add_argument('--disable-hang-monitor')
            options.add_argument('--disable-ipc-flooding-protection')
            options.add_argument('--disable-popup-blocking')
            options.add_argument('--disable-prompt-on-repost')
            options.add_argument('--disable-renderer-backgrounding')
            options.add_argument('--force-max-rate-commit-spans')
            options.add_argument('--enable-automation')
            options.add_argument('--password-store=basic')
            options.add_argument('--use-mock-keychain')
            options.add_experimental_option('excludeSwitches', ['enable-logging'])

            chrome_path = '/usr/bin/chromium'
            if os.path.exists(chrome_path):
                options.binary_location = chrome_path
            else:
                logging.error("❌ Chrome не найден")
                return None

            chromedriver_path = '/usr/bin/chromedriver'
            if not os.path.exists(chromedriver_path):
                logging.error("❌ Chromedriver не найден")
                return None

            service = Service(chromedriver_path)
            driver = webdriver.Chrome(service=service, options=options)
            driver.set_page_load_timeout(30)
            logging.info("✅ Браузер успешно создан")
            return driver

        except Exception as e:
            logging.warning(f"⚠️ Попытка {attempt+1} не удалась: {e}")
            kill_all_chromes()
            if attempt < retries - 1:
                time.sleep(3)
    logging.error("❌ Не удалось создать браузер")
    return None

def parse_card_from_element(card):
    try:
        class_str = card.get_attribute('class')
        suit = next((s for c, s in SUIT_MAP.items() if c in class_str), '?')
        val = re.search(r'value-(\d+)', class_str)
        if val:
            v = val.group(1)
            value = VALUE_MAP.get(f'value-{v}', v)
        else:
            value = '?'
        return f"{value}{suit}"
    except Exception as e:
        logging.error(f"Ошибка парсинга карты: {e}")
        return '??'

def get_game_state(driver, table_id):
    try:
        state = {
            'round_status': '',
            'player_score': '?',
            'player_cards': [],
            'dealer_score': '?',
            'dealer_cards': [],
            'game_status': ''
        }
        try:
            state['round_status'] = driver.find_element(By.CSS_SELECTOR, SELECTORS['game_round']).text
        except:
            pass
        try:
            state['player_score'] = driver.find_element(By.CSS_SELECTOR, SELECTORS['player_score']).text
            cards = driver.find_elements(By.CSS_SELECTOR, SELECTORS['player_cards'])
            state['player_cards'] = [parse_card_from_element(c) for c in cards]
        except:
            pass
        try:
            state['dealer_score'] = driver.find_element(By.CSS_SELECTOR, SELECTORS['dealer_score']).text
            cards = driver.find_elements(By.CSS_SELECTOR, SELECTORS['dealer_cards'])
            state['dealer_cards'] = [parse_card_from_element(c) for c in cards]
        except:
            pass
        try:
            state['game_status'] = driver.find_element(By.CSS_SELECTOR, SELECTORS['game_status']).text
        except:
            pass
        return state
    except InvalidSessionIdException:
        logging.warning(f"⚠️ #{table_id}: сессия невалидна, выходим")
        return None
    except Exception as e:
        logging.error(f"get_game_state #{table_id}: {e}")
        return None

def monitor_table(table_url, table_id):
    driver = None
    start = time.time()
    last_state = None
    sent = set()
    t_num = random.randint(30, 60)

    try:
        logging.info(f"🚀 Старт монитора #{table_id}")
        driver = create_driver()
        if not driver:
            return

        driver.get(table_url)
        time.sleep(4)

        while time.time() - start < 3600:
            try:
                state = get_game_state(driver, table_id)
                if not state:
                    time.sleep(2)
                    continue

                if state['player_score'] in ['?', '0', ''] or not state['player_cards']:
                    time.sleep(2)
                    continue

                p_cards = ''.join(state['player_cards'])
                d_cards = ''.join(state['dealer_cards'])

                if any(w in state['game_status'].lower() for w in ['завершен', 'завершена']):
                    final = f"#N{table_id}. {state['player_score']}({p_cards}) - {state['dealer_score']}({d_cards}) #T{t_num}"
                    try:
                        bot.send_message(CHANNEL_ID, final)
                        logging.info(f"✅ #{table_id} завершён")
                    except:
                        pass
                    break

                if state != last_state:
                    if last_state:
                        if len(state['player_cards']) > len(last_state['player_cards']):
                            msg = f"⏰#N{table_id}. ▶ {last_state['player_score']}({''.join(last_state['player_cards'])}) - {last_state['dealer_score']}({''.join(last_state['dealer_cards'])})"
                            if msg not in sent:
                                bot.send_message(CHANNEL_ID, msg)
                                sent.add(msg)

                        if len(state['dealer_cards']) > len(last_state['dealer_cards']):
                            msg = f"⏰#N{table_id}. {last_state['player_score']}({''.join(last_state['player_cards'])}) - ▶ {last_state['dealer_score']}({''.join(last_state['dealer_cards'])})"
                            if msg not in sent:
                                bot.send_message(CHANNEL_ID, msg)
                                sent.add(msg)

                    last_state = state

                time.sleep(2)

            except (StaleElementReferenceException, InvalidSessionIdException):
                logging.warning(f"⚠️ Stale/Invalid сессия #{table_id}, выход")
                break
            except Exception as e:
                logging.error(f"⚠️ Ошибка в #{table_id}: {e}")
                time.sleep(3)

    except Exception as e:
        logging.error(f"❌ Критическая ошибка #{table_id}: {e}")
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass
            logging.info(f"🛑 Браузер #{table_id} закрыт")

def scan_new_tables():
    driver = None
    try:
        logging.info("🔍 Сканирование...")
        driver = create_driver()
        if not driver:
            return

        driver.get(MAIN_PAGE_URL)
        time.sleep(5)

        links = driver.find_elements(By.CSS_SELECTOR, SELECTORS['table_link'])
        ids = driver.find_elements(By.CSS_SELECTOR, SELECTORS['table_id'])

        free = []
        for i, link in enumerate(links):
            href = link.get_attribute('href')
            table_id = None

            if i < len(ids):
                table_id = ids[i].text.strip()
            if not table_id and href:
                match = re.search(r'/(\d+)-player', href)
                if match:
                    table_id = match.group(1)

            if not table_id:
                continue
            if table_id in processed_games or table_id in active_tables:
                continue
            free.append((table_id, href))

        driver.quit()
        logging.info(f"📊 Свободных: {len(free)}")

        for table_id, href in free:
            if len(active_tables) >= MAX_BROWSERS:
                logging.info("⚠️ Достигнут лимит браузеров")
                break
            if not check_memory():
                break

            logging.info(f"🚀 Запуск стола #{table_id}")
            t = threading.Thread(target=monitor_table, args=(href, table_id))
            t.daemon = True
            t.start()
            time.sleep(5)
            if t.is_alive():
                active_tables[table_id] = {'thread': t, 'start': time.time()}
                logging.info(f"✅ Стол #{table_id} запущен")
            else:
                logging.error(f"❌ Стол #{table_id} не запустился")
            time.sleep(4)

    except Exception as e:
        logging.error(f"❌ Ошибка сканирования: {e}")
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

def clean_finished():
    dead = []
    for tid, data in list(active_tables.items()):
        thread = data['thread']
        if not thread.is_alive():
            dead.append(tid)
            processed_games.add(tid)
            logging.info(f"🧹 Стол #{tid} (мёртвый) удалён")
        elif time.time() - data['start'] > 4200:
            logging.warning(f"⚠️ Стол #{tid} завис >70 мин")
            dead.append(tid)
            processed_games.add(tid)

    for tid in dead:
        del active_tables[tid]

    kill_all_chromes()
    logging.info(f"🧹 Активных после чистки: {len(active_tables)}")

def main():
    logging.info("="*50)
    logging.info("🤖 ULTIMATE STABLE BOT (ZOMBIE KILLER V2)")
    logging.info(f"📊 MAX_BROWSERS = {MAX_BROWSERS}")
    logging.info("="*50)

    kill_all_chromes()
    try:
        bot.send_message(CHANNEL_ID, "🤖 Ультимативный бот запущен")
    except:
        pass

    err = 0
    while True:
        try:
            clean_finished()
            scan_new_tables()
            logging.info(f"📊 Активных: {len(active_tables)}/{MAX_BROWSERS}")
            err = 0
            time.sleep(CHECK_INTERVAL)
        except Exception as e:
            err += 1
            logging.error(f"💥 Главный цикл, попытка {err}: {e}")
            kill_all_chromes()
            time.sleep(60)

if __name__ == "__main__":
    main()