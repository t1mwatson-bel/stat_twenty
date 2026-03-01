import threading
import time
import re
import logging
import random
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import StaleElementReferenceException, NoSuchElementException, TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
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
lock = threading.Lock()

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

def calculate_total_bochkov(p_cards, d_cards):
    return len(p_cards) + len(d_cards)

def has_21(p_score, d_score):
    return p_score == 21 or d_score == 21

def is_instant_finish(p_cards, d_cards):
    return len(p_cards) == 2 and len(d_cards) == 2

def is_golden_point(p_cards, d_cards):
    if len(p_cards) == 2 and p_cards[0][0] == 'A' and p_cards[1][0] == 'A':
        return True
    if len(d_cards) == 2 and d_cards[0][0] == 'A' and d_cards[1][0] == 'A':
        return True
    return False

def is_game_finished(driver):
    try:
        status_element = driver.find_element(By.CSS_SELECTOR, '.ui-game-timer__label')
        status_text = status_element.text.lower()
        
        finished_keywords = ['завершен', 'завершена', 'finished', 'ended', 'game over']
        
        if any(keyword in status_text for keyword in finished_keywords):
            logging.info(f"Игра завершена по статусу: {status_text}")
            return True
            
        try:
            new_game_btn = driver.find_element(By.CSS_SELECTOR, '.ui-game-controls__button, .new-game-button, [class*="new"]')
            if new_game_btn and new_game_btn.is_displayed():
                logging.info("Игра завершена - обнаружена кнопка новой игры")
                return True
        except NoSuchElementException:
            pass
        
    except NoSuchElementException:
        logging.warning("Элемент статуса не найден")
        return True
    except Exception as e:
        logging.error(f"Ошибка проверки завершения игры: {e}")
        
    return False

def is_player_turn(driver):
    try:
        player_element = driver.find_element(By.CSS_SELECTOR, '.live-twenty-one-field-player:first-child')
        dealer_element = driver.find_element(By.CSS_SELECTOR, '.live-twenty-one-field-player:last-child')
        
        player_class = player_element.get_attribute('class')
        dealer_class = dealer_element.get_attribute('class')
        
        if 'active' in player_class or 'highlight' in player_class:
            return 'player'
        elif 'active' in dealer_class or 'highlight' in dealer_class:
            return 'dealer'
    except:
        pass
    
    return None

def safe_quit_driver(driver, table_id):
    try:
        if driver:
            logging.info(f"Закрытие драйвера для стола {table_id}")
            driver.quit()
    except Exception as e:
        logging.error(f"Ошибка при закрытии драйвера стола {table_id}: {e}")

def get_state(driver):
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '.live-twenty-one-field-player'))
        )
        
        player_score = driver.find_element(By.CSS_SELECTOR, '.live-twenty-one-field-player:first-child .live-twenty-one-field-score__label').text
        player_cards = parse_cards(driver.find_elements(By.CSS_SELECTOR, '.live-twenty-one-field-player:first-child .scoreboard-card-games-card'))
        dealer_score = driver.find_element(By.CSS_SELECTOR, '.live-twenty-one-field-player:last-child .live-twenty-one-field-score__label').text
        dealer_cards = parse_cards(driver.find_elements(By.CSS_SELECTOR, '.live-twenty-one-field-player:last-child .scoreboard-card-games-card'))
        
        try:
            status = driver.find_element(By.CSS_SELECTOR, '.ui-game-timer__label').text
        except:
            status = "Идет игра"
            
        turn = is_player_turn(driver)
            
        return {
            'p_score': player_score,
            'p_cards': player_cards,
            'd_score': dealer_score,
            'd_cards': dealer_cards,
            'status': status,
            'turn': turn
        }
    except TimeoutException:
        logging.error(f"Таймаут загрузки страницы")
        return None
    except Exception as e:
        logging.error(f"Ошибка получения состояния: {e}")
        return None

def format_message(table_id, state, is_final=False, t_num=None):
    p_cards = format_cards(state['p_cards'])
    d_cards = format_cards(state['d_cards'])
    
    player_part = f"{state['p_score']}({p_cards})"
    dealer_part = f"{state['d_score']}({d_cards})"
    
    if not is_final:
        if state['turn'] == 'player':
            player_part = f"▶ {player_part}"
        elif state['turn'] == 'dealer':
            dealer_part = f"▶ {dealer_part}"
        
        return f"⏰#{table_id}. {player_part} - {dealer_part}"
    else:
        tags = []
        
        total_bochkov = calculate_total_bochkov(state['p_cards'], state['d_cards'])
        tags.append(f"#T{total_bochkov}")
        
        if is_instant_finish(state['p_cards'], state['d_cards']):
            tags.append("#R")
        
        if has_21(int(state['p_score']), int(state['d_score'])):
            tags.append("#O")
        
        if is_golden_point(state['p_cards'], state['d_cards']):
            tags.append("#G")
        
        base_msg = f"#{table_id}. {player_part} - {dealer_part}"
        return f"{base_msg} {' '.join(tags)}"

def monitor_table(table_url, table_id):
    driver = None
    last_state = None
    msg_id = None
    t_num = random.randint(30, 60)
    game_active = True
    no_response_count = 0
    max_no_response = 5

    try:
        driver = create_driver()
        if not driver:
            logging.error(f"Не удалось создать драйвер для стола {table_id}.")
            return

        logging.info(f"Начало мониторинга стола {table_id}")
        driver.get(table_url)
        time.sleep(5)

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
                
                no_response_count = 0
                
                if is_game_finished(driver):
                    final_state = get_state(driver) or state
                    final_msg = format_message(table_id, final_state, is_final=True, t_num=t_num)
                    
                    try:
                        if msg_id:
                            bot.edit_message_text(final_msg, CHANNEL_ID, msg_id)
                        else:
                            bot.send_message(CHANNEL_ID, final_msg)
                        logging.info(f"Стол {table_id} завершен")
                    except Exception as e:
                        logging.error(f"Ошибка отправки финального сообщения для стола {table_id}: {e}")
                    
                    game_active = False
                    break

                if state != last_state:
                    msg = format_message(table_id, state, is_final=False, t_num=t_num)
                    try:
                        if msg_id:
                            bot.edit_message_text(msg, CHANNEL_ID, msg_id)
                        else:
                            sent = bot.send_message(CHANNEL_ID, msg)
                            msg_id = sent.message_id
                        last_state = state
                        logging.info(f"Стол {table_id} обновлен")
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
        safe_quit_driver(driver, table_id)
        
        with lock:
            if table_id in active_tables:
                del active_tables[table_id]
            if table_id in message_ids:
                del message_ids[table_id]
        
        logging.info(f"Мониторинг стола {table_id} завершен")

def scan_tables():
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
    with lock:
        dead = [tid for tid, t in active_tables.items() if not t.is_alive()]
        for tid in dead:
            del active_tables[tid]
            if tid in message_ids:
                del message_ids[tid]
            logging.info(f"Поток стола {tid} очищен")

def main():
    logging.info("Чистый бот запущен")
    
    try:
        while True:
            try:
                clean_threads()
                scan_tables()
                
                with lock:
                    logging.info(f"Активных столов: {len(active_tables)}")
                
                time.sleep(CHECK_INTERVAL)
                
            except KeyboardInterrupt:
                logging.info("Получен сигнал завершения")
                break
            except Exception as e:
                logging.error(f"Ошибка в главном цикле: {e}")
                time.sleep(60)
    finally:
        logging.info("Завершение работы бота...")

if __name__ == "__main__":
    main()