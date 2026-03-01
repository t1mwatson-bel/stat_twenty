import threading
import time
import re
import logging
import random
import json
import os
from datetime import datetime, timedelta
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
MAX_BROWSERS = 3
CHECK_INTERVAL = 30
DATA_FILE = "game_data.json"
MAX_DAYS = 3  # Храним данные 3 дня
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
game_data = {}  # {table_id: {'t_num': int, 'start_time': timestamp, 'last_update': timestamp}}
lock = threading.Lock()

# ===== ФУНКЦИИ ДЛЯ РАБОТЫ С ДАННЫМИ =====

def load_game_data():
    """Загрузка данных из файла"""
    global game_data
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Очищаем старые данные (старше MAX_DAYS дней)
                current_time = datetime.now()
                game_data = {}
                for table_id, info in data.items():
                    start_time = datetime.fromisoformat(info['start_time'])
                    if (current_time - start_time) < timedelta(days=MAX_DAYS):
                        game_data[table_id] = info
                logging.info(f"Загружено {len(game_data)} активных игр из файла")
    except Exception as e:
        logging.error(f"Ошибка загрузки данных: {e}")
        game_data = {}

def save_game_data():
    """Сохранение данных в файл"""
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(game_data, f, ensure_ascii=False, indent=2)
        logging.info("Данные сохранены")
    except Exception as e:
        logging.error(f"Ошибка сохранения данных: {e}")

def cleanup_old_data():
    """Очистка старых данных"""
    current_time = datetime.now()
    old_tables = []
    
    with lock:
        for table_id, info in list(game_data.items()):
            start_time = datetime.fromisoformat(info['start_time'])
            if (current_time - start_time) >= timedelta(days=MAX_DAYS):
                old_tables.append(table_id)
                del game_data[table_id]
        
        if old_tables:
            logging.info(f"Удалено {len(old_tables)} старых игр")
            save_game_data()

def get_t_number(table_id):
    """Получение номера T для стола"""
    with lock:
        if table_id in game_data:
            return game_data[table_id]['t_num']
        else:
            # Генерируем новый номер от 30 до 60
            t_num = random.randint(30, 60)
            game_data[table_id] = {
                't_num': t_num,
                'start_time': datetime.now().isoformat(),
                'last_update': datetime.now().isoformat()
            }
            save_game_data()
            return t_num

def update_game_data(table_id):
    """Обновление времени последнего изменения"""
    with lock:
        if table_id in game_data:
            game_data[table_id]['last_update'] = datetime.now().isoformat()
            save_game_data()

def remove_game_data(table_id):
    """Удаление данных игры"""
    with lock:
        if table_id in game_data:
            del game_data[table_id]
            save_game_data()

# ===== ОСНОВНЫЕ ФУНКЦИИ =====

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

def is_turn_indicator(driver, player="player"):
    """Проверка, чей сейчас ход (кто должен добирать)"""
    try:
        # Ищем индикатор хода (обычно подсветка или мигание)
        if player == "player":
            selector = '.live-twenty-one-field-player:first-child .live-twenty-one-field-score__turn, .live-twenty-one-field-player:first-child [class*="turn"]'
        else:
            selector = '.live-twenty-one-field-player:last-child .live-twenty-one-field-score__turn, .live-twenty-one-field-player:last-child [class*="turn"]'
        
        elements = driver.find_elements(By.CSS_SELECTOR, selector)
        return len(elements) > 0 and elements[0].is_displayed()
    except:
        return False

def is_game_finished(driver):
    """Проверка завершения игры по различным признакам"""
    try:
        # Проверка по таймеру/статусу
        status_element = driver.find_element(By.CSS_SELECTOR, '.ui-game-timer__label')
        status_text = status_element.text.lower()
        
        # Ключевые слова завершения
        finished_keywords = ['завершен', 'завершена', 'finished', 'ended', 'game over']
        
        if any(keyword in status_text for keyword in finished_keywords):
            logging.info(f"Игра завершена по статусу: {status_text}")
            return True
            
        # Проверка наличия кнопки "новая игра"
        try:
            new_game_btn = driver.find_element(By.CSS_SELECTOR, '.ui-game-controls__button, .new-game-button, [class*="new"]')
            if new_game_btn and new_game_btn.is_displayed():
                logging.info("Игра завершена - обнаружена кнопка новой игры")
                return True
        except NoSuchElementException:
            pass
        
        # Проверка, что карты больше не меняются (можно добавить дополнительную логику)
        
    except NoSuchElementException:
        pass
    except Exception as e:
        logging.error(f"Ошибка проверки завершения игры: {e}")
        
    return False

def safe_quit_driver(driver, table_id):
    """Безопасное закрытие драйвера"""
    try:
        if driver:
            logging.info(f"Закрытие драйвера для стола {table_id}")
            driver.quit()
    except Exception as e:
        logging.error(f"Ошибка при закрытии драйвера стола {table_id}: {e}")

def calculate_score(cards):
    """Подсчет очков по картам"""
    score = 0
    aces = 0
    
    for card in cards:
        value = card[:-1]  # Убираем масть
        if value == 'A':
            aces += 1
            score += 11
        elif value in ['J', 'Q', 'K']:
            score += 10
        else:
            score += int(value)
    
    # Корректировка для тузов
    while score > 21 and aces > 0:
        score -= 10
        aces -= 1
    
    return score

def check_special_conditions(state):
    """Проверка особых условий: #O (21), #R (2 карты), #G (золотое очко)"""
    tags = []
    
    # Подсчет очков
    p_score = calculate_score(state['p_cards'])
    d_score = calculate_score(state['d_cards'])
    
    # #O - у кого-то 21 очко
    if p_score == 21:
        tags.append('#O')
    elif d_score == 21:
        tags.append('#O')
    
    # #R - у игрока или дилера по 2 карты
    if len(state['p_cards']) == 2 or len(state['d_cards']) == 2:
        tags.append('#R')
    
    # #G - золотое очко (два туза у игрока)
    if len(state['p_cards']) == 2 and all('A' in card for card in state['p_cards']):
        if state['p_cards'][0][:-1] == 'A' and state['p_cards'][1][:-1] == 'A':
            tags.append('#G')
    
    # Убираем дубликаты и сортируем
    tags = list(dict.fromkeys(tags))
    return ' '.join(tags)

def get_state(driver):
    try:
        # Ждем загрузки основных элементов
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '.live-twenty-one-field-player'))
        )
        
        player_score_elem = driver.find_element(By.CSS_SELECTOR, '.live-twenty-one-field-player:first-child .live-twenty-one-field-score__label')
        player_score = player_score_elem.text
        
        player_cards = parse_cards(driver.find_elements(By.CSS_SELECTOR, '.live-twenty-one-field-player:first-child .scoreboard-card-games-card'))
        
        dealer_score_elem = driver.find_element(By.CSS_SELECTOR, '.live-twenty-one-field-player:last-child .live-twenty-one-field-score__label')
        dealer_score = dealer_score_elem.text
        
        dealer_cards = parse_cards(driver.find_elements(By.CSS_SELECTOR, '.live-twenty-one-field-player:last-child .scoreboard-card-games-card'))
        
        # Пробуем получить статус
        try:
            status = driver.find_element(By.CSS_SELECTOR, '.ui-game-timer__label').text
        except:
            status = "Идет игра"
        
        # Проверяем, чей ход
        player_turn = is_turn_indicator(driver, "player")
        dealer_turn = is_turn_indicator(driver, "dealer")
        
        return {
            'p_score': player_score,
            'p_cards': player_cards,
            'd_score': dealer_score,
            'd_cards': dealer_cards,
            'status': status,
            'player_turn': player_turn,
            'dealer_turn': dealer_turn
        }
    except TimeoutException:
        logging.error(f"Таймаут загрузки страницы")
        return None
    except Exception as e:
        logging.error(f"Ошибка получения состояния: {e}")
        return None

def format_message(table_id, state, is_final=False, t_num=None):
    """Форматирование сообщения с учетом всех тегов"""
    p_cards = format_cards(state['p_cards'])
    d_cards = format_cards(state['d_cards'])
    
    # Определяем, нужно ли добавить стрелку хода
    turn_arrow = ""
    if not is_final:
        if state.get('player_turn', False):
            turn_arrow = "▶ "
        elif state.get('dealer_turn', False):
            turn_arrow = "▶ "
            # Стрелка перед дилером - меняем формат
    
    # Формируем базовое сообщение
    if turn_arrow and state.get('dealer_turn', False):
        # Стрелка перед дилером
        base_msg = f"#{table_id}. {state['p_score']}({p_cards}) - {turn_arrow}{state['d_score']}({d_cards})"
    else:
        # Стрелка перед игроком или без стрелки
        base_msg = f"#{table_id}. {turn_arrow}{state['p_score']}({p_cards}) - {state['d_score']}({d_cards})"
    
    # Добавляем теги
    if is_final:
        special_tags = check_special_conditions(state)
        if special_tags:
            return f"#{table_id}. {state['p_score']}({p_cards}) - {state['d_score']}({d_cards}) #T{t_num} {special_tags}"
        else:
            return f"#{table_id}. {state['p_score']}({p_cards}) - {state['d_score']}({d_cards}) #T{t_num}"
    else:
        return base_msg

def monitor_table(table_url, table_id):
    driver = None
    last_state = None
    msg_id = None
    t_num = get_t_number(table_id)
    game_active = True
    no_response_count = 0
    max_no_response = 5
    game_started = False

    try:
        driver = create_driver()
        if not driver:
            logging.error(f"Не удалось создать драйвер для стола {table_id}.")
            return

        logging.info(f"Начало мониторинга стола {table_id} (T{t_num})")
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
                
                # Отметка, что игра началась (есть карты)
                if not game_started and (state['p_cards'] or state['d_cards']):
                    game_started = True
                
                # Проверка завершения игры
                if is_game_finished(driver) and game_started:
                    final_state = get_state(driver) or state
                    final_msg = format_message(table_id, final_state, is_final=True, t_num=t_num)
                    
                    try:
                        if msg_id:
                            bot.edit_message_text(final_msg, CHANNEL_ID, msg_id)
                        else:
                            bot.send_message(CHANNEL_ID, final_msg)
                        logging.info(f"Стол {table_id} завершен, финальное сообщение отправлено")
                    except Exception as e:
                        logging.error(f"Ошибка отправки финального сообщения для стола {table_id}: {e}")
                    
                    game_active = False
                    break

                # Отправка/редактирование промежуточных результатов
                # Сравниваем состояние, исключая статус хода из сравнения для избежания лишних обновлений
                state_for_compare = {k: v for k, v in state.items() if k not in ['player_turn', 'dealer_turn']}
                last_state_for_compare = {k: v for k, v in last_state.items() if k not in ['player_turn', 'dealer_turn']} if last_state else None
                
                if state_for_compare != last_state_for_compare:
                    msg = format_message(table_id, state)
                    try:
                        if msg_id:
                            bot.edit_message_text(msg, CHANNEL_ID, msg_id)
                        else:
                            sent = bot.send_message(CHANNEL_ID, msg)
                            msg_id = sent.message_id
                        
                        # Сохраняем полное состояние для следующего сравнения
                        last_state = state.copy()
                        update_game_data(table_id)
                        logging.info(f"Стол {table_id} обновлен: {state['p_score']} - {state['d_score']}")
                    except Exception as e:
                        logging.error(f"Ошибка отправки сообщения для стола {table_id}: {e}")
                
                # Обновляем состояние хода даже если карты не менялись
                elif state.get('player_turn') != last_state.get('player_turn') or state.get('dealer_turn') != last_state.get('dealer_turn'):
                    msg = format_message(table_id, state)
                    try:
                        if msg_id:
                            bot.edit_message_text(msg, CHANNEL_ID, msg_id)
                        last_state = state.copy()
                        logging.info(f"Стол {table_id} обновлен (ход): {state['p_score']} - {state['d_score']}")
                    except Exception as e:
                        logging.error(f"Ошибка обновления хода для стола {table_id}: {e}")

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
        safe_quit_driver(driver, table_id)
        
        with lock:
            if table_id in active_tables:
                del active_tables[table_id]
            if table_id in message_ids:
                del message_ids[table_id]
        
        # Не удаляем данные игры сразу, чтобы сохранить историю
        logging.info(f"Мониторинг стола {table_id} завершен, ресурсы освобождены")

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
    """Очистка завершенных потоков"""
    with lock:
        dead = [tid for tid, t in active_tables.items() if not t.is_alive()]
        for tid in dead:
            del active_tables[tid]
            if tid in message_ids:
                del message_ids[tid]
            logging.info(f"Поток стола {tid} очищен")

def main():
    # Загружаем сохраненные данные
    load_game_data()
    
    # Очищаем старые данные при запуске
    cleanup_old_data()
    
    logging.info("Чистый бот запущен")
    
    try:
        last_cleanup = datetime.now()
        
        while True:
            try:
                clean_threads()
                scan_tables()
                
                # Очистка старых данных раз в час
                if datetime.now() - last_cleanup > timedelta(hours=1):
                    cleanup_old_data()
                    last_cleanup = datetime.now()
                
                with lock:
                    logging.info(f"Активных столов: {len(active_tables)}, Сохранено игр: {len(game_data)}")
                
                time.sleep(CHECK_INTERVAL)
                
            except KeyboardInterrupt:
                logging.info("Получен сигнал завершения")
                break
            except Exception as e:
                logging.error(f"Ошибка в главном цикле: {e}")
                time.sleep(60)
    finally:
        # Сохраняем данные при завершении
        save_game_data()
        logging.info("Завершение работы бота...")
        with lock:
            for table_id in list(active_tables.keys()):
                logging.info(f"Ожидание завершения потока стола {table_id}")
            active_tables.clear()
            message_ids.clear()

if __name__ == "__main__":
    main()