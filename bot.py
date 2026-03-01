import threading
import time
import re
import logging
import random
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import StaleElementReferenceException
import telebot

# ===== НАСТРОЙКИ =====
TOKEN = "8357635747:AAGAH_Rwk-vR8jGa6Q9F-AJLsMaEIj-JDBU"
CHANNEL_ID = "-1003179573402"
MAIN_URL = "https://1xlite-7636770.bar/ru/live/twentyone/1643503-twentyone-game"
MAX_BROWSERS = 2
CHECK_INTERVAL = 30
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
active_tables = {}
message_ids = {}  # {table_id: message_id}

def create_driver():
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--disable-extensions')
    options.add_argument('--disable-infobars')
    options.add_argument('--disable-plugins')
    options.add_argument('--disable-software-rasterizer')
    options.binary_location = '/usr/bin/chromium'
    
    service = Service('/usr/bin/chromedriver')
    
    try:
        driver = webdriver.Chrome(service=service, options=options)
        return driver
    except Exception as e:
        logging.error(f"Ошибка создания драйвера: {e}")
        return None

def parse_cards(elements):
    cards = []
    for el in elements:
        cls = el.get_attribute('class')
        suit = next((s for c, s in SUIT_MAP.items() if c in cls), '?')
        val = re.search(r'value-(\d+)', cls)
        value = VALUE_MAP.get(f'value-{val.group(1)}', val.group(1)) if val else '?'
        cards.append(f"{value}{suit}")
    return cards

def format_cards(cards):
    return ''.join(cards)

def get_state(driver):
    try:
        player_score = driver.find_element(By.CSS_SELECTOR, '.live-twenty-one-field-player:first-child .live-twenty-one-field-score__label').text
        player_cards = parse_cards(driver.find_elements(By.CSS_SELECTOR, '.live-twenty-one-field-player:first-child .scoreboard-card-games-card'))
        dealer_score = driver.find_element(By.CSS_SELECTOR, '.live-twenty-one-field-player:last-child .live-twenty-one-field-score__label').text
        dealer_cards = parse_cards(driver.find_elements(By.CSS_SELECTOR, '.live-twenty-one-field-player:last-child .scoreboard-card-games-card'))
        status = driver.find_element(By.CSS_SELECTOR, '.ui-game-timer__label').text
        return {
            'p_score': player_score,
            'p_cards': player_cards,
            'd_score': dealer_score,
            'd_cards': dealer_cards,
            'status': status
        }
    except Exception as e:
        logging.error(f"Ошибка получения состояния: {e}")
        return None

def format_message(table_id, state, is_final=False, t_num=None):
    p_cards = format_cards(state['p_cards'])
    d_cards = format_cards(state['d_cards'])
    if is_final:
        return f"#{table_id}. {state['p_score']}({p_cards}) - {state['d_score']}({d_cards}) #T{t_num}"
    return f"⏰#{table_id}. {state['p_score']}({p_cards}) - {state['d_score']}({d_cards})"

def monitor_table(table_url, table_id):
    driver = create_driver()
    last_state = None
    msg_id = None
    t_num = random.randint(30, 60)
    game_active = True

    if not driver:
        logging.error(f"Не удалось создать драйвер для стола {table_id}.")
        return

    try:
        driver.get(table_url)
        time.sleep(3)

        while game_active:
            try:
                state = get_state(driver)
                if not state:
                    time.sleep(2)
                    continue

                # Проверка завершения
                if any(w in state['status'].lower() for w in ['завершен', 'завершена']):
                    final_msg = format_message(table_id, state, is_final=True, t_num=t_num)
                    if msg_id:
                        bot.edit_message_text(final_msg, CHANNEL_ID, msg_id)
                    else:
                        bot.send_message(CHANNEL_ID, final_msg)
                    game_active = False
                    break

                # Отправка/редактирование
                if state != last_state:
                    msg = format_message(table_id, state)
                    if msg_id:
                        bot.edit_message_text(msg, CHANNEL_ID, msg_id)
                    else:
                        sent = bot.send_message(CHANNEL_ID, msg)
                        msg_id = sent.message_id
                    last_state = state

                time.sleep(2)

            except StaleElementReferenceException:
                driver.refresh()
                time.sleep(2)
            except Exception as e:
                logging.error(f"Ошибка #{table_id}: {e}")
                time.sleep(2)

    finally:
        if driver:
            driver.quit()
        message_ids.pop(table_id, None)

def scan_tables():
    driver = create_driver()
    try:
        if not driver:
            logging.error("Не удалось создать драйвер для сканирования столов.")
            return
        
        driver.get(MAIN_URL)
        time.sleep(5)

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

            if table_id in active_tables or table_id in message_ids:
                continue
            new_tables.append((table_id, href))

        for table_id, href in new_tables:
            if len(active_tables) >= MAX_BROWSERS:
                break
            thread = threading.Thread(target=monitor_table, args=(href, table_id))
            thread.start()
            active_tables[table_id] = thread
            time.sleep(3)

    except Exception as e:
        logging.error(f"Ошибка сканирования: {e}")
    finally:
        if driver:
            driver.quit()

def clean_threads():
    dead = [tid for tid, t in active_tables.items() if not t.is_alive()]
    for tid in dead:
        del active_tables[tid]
        message_ids.pop(tid, None)

def main():
    logging.info("Чистый бот запущен")
    while True:
        try:
            clean_threads()
            scan_tables()
            logging.info(f"Активных столов: {len(active_tables)}")
            time.sleep(CHECK_INTERVAL)
        except Exception as e:
            logging.error(f"Главный цикл: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()