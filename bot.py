import threading
import time
import re
import logging
import random
import asyncio
from datetime import datetime, timedelta
from playwright.async_api import async_playwright
import telebot
import pickle
import os
from telebot import apihelper

# ===== НАСТРОЙКИ =====
TOKEN = "8357635747:AAGAH_Rwk-vR8jGa6Q9F-AJLsMaEIj-JDBU"
CHANNEL_ID = "-1003179573402"
MAIN_URL = "https://1xlite-6997737.bar/ru/live/twentyone/2092323-21-classics"
MAX_BROWSERS = 4
DATA_FILE = "game_data.pkl"
DATA_RETENTION_DAYS = 3
BROWSER_START_OFFSET = 20  # За 20 секунд до игры
GAME_DURATION = 120  # Игра длится 2 минуты (120 секунд)
# =====================

apihelper.RETRY_ON_ERROR = True
apihelper.MAX_RETRIES = 5

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# Маппинги для классической версии
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
message_ids = {}
last_messages = {}
table_schedule = {}  # Словарь для хранения времени начала игр {table_id: start_time}
last_table_id = 0
lock = threading.Lock()
tasks = {}
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
    
    def add_completed_game(self, table_id, message, t_num):
        self.completed_games[table_id] = {
            'message': message,
            'timestamp': datetime.now(),
            't_num': t_num
        }
        self.save_data()
    
    def is_game_completed(self, table_id):
        return table_id in self.completed_games
    
    def update_last_number(self, number):
        if number > self.last_game_number:
            self.last_game_number = number
            self.save_data()

game_data = GameData()

def format_cards(cards):
    return ''.join(cards)

def check_special_conditions(player_cards, dealer_cards, player_score, dealer_score):
    specials = []
    if player_score == "21" and len(player_cards) == 2:
        if all(card.startswith('A') for card in player_cards):
            specials.append('#G')
    if player_score == "21" or dealer_score == "21":
        specials.append('#O')
    if len(player_cards) == 2 and len(dealer_cards) == 2:
        specials.append('#R')
    return ' '.join(specials)

async def extract_cards(page, player_selector):
    """Извлечение карт для классической версии"""
    cards = []
    
    try:
        # Проверяем, жива ли страница
        if page.is_closed():
            return cards
            
        cards_container = await page.query_selector(f'{player_selector} .live-twenty-one-cards')
        if not cards_container:
            return cards
        
        card_elements = await cards_container.query_selector_all('.scoreboard-card-games-card')
        
        for el in card_elements:
            try:
                # Проверяем, не является ли карта рубашкой (скрытой)
                back_element = await el.query_selector('.scoreboard-card-games-card__back')
                if back_element:
                    is_visible = await back_element.is_visible()
                    if is_visible:
                        continue
                
                class_name = await el.get_attribute('class') or ''
                
                # Определяем масть
                suit = '?'
                if 'scoreboard-card-games-card--suit-0' in class_name:
                    suit = '♠️'
                elif 'scoreboard-card-games-card--suit-1' in class_name:
                    suit = '♣️'
                elif 'scoreboard-card-games-card--suit-2' in class_name:
                    suit = '♦️'
                elif 'scoreboard-card-games-card--suit-3' in class_name:
                    suit = '♥️'
                
                # Определяем значение
                value_match = re.search(r'value-(\d+)', class_name)
                if value_match:
                    val = value_match.group(1)
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
            except Exception as e:
                logging.error(f"Ошибка при обработке карты: {e}")
                continue
        
    except Exception as e:
        if "closed" not in str(e).lower():
            logging.error(f"Ошибка в extract_cards: {e}")
    
    return cards

async def get_state_fast(page):
    """Получение состояния игры"""
    try:
        if page.is_closed():
            return None
            
        # Получаем статус игры из заголовка
        status_el = await page.query_selector('.live-twenty-one-table-head__status')
        game_status = await status_el.text_content() if status_el else ''
        
        # Получаем счет игрока
        player_score_el = await page.query_selector('.live-twenty-one-field__player:first-child .live-twenty-one-field-score__label')
        player_score = await player_score_el.text_content() if player_score_el else '0'
        
        # Получаем карты игрока
        player_cards = await extract_cards(page, '.live-twenty-one-field__player:first-child')
        
        # Получаем счет дилера
        dealer_score_el = await page.query_selector('.live-twenty-one-field__player:last-child .live-twenty-one-field-score__label')
        dealer_score = await dealer_score_el.text_content() if dealer_score_el else '0'
        
        # Получаем карты дилера
        dealer_cards = await extract_cards(page, '.live-twenty-one-field__player:last-child')
        
        # Проверяем таймер завершения
        timer_el = await page.query_selector('.ui-game-timer__label')
        timer_text = await timer_el.text_content() if timer_el else ''
        
        # Определяем, завершена ли игра
        is_finished = False
        winner = None
        
        if status_el:
            status_text = await status_el.text_content()
            if 'Победа игрока' in status_text:
                is_finished = True
                winner = 'player'
            elif 'Победа дилера' in status_text:
                is_finished = True
                winner = 'dealer'
            elif 'Ничья' in status_text:
                is_finished = True
                winner = 'tie'
        
        if timer_text and 'завершена' in timer_text.lower():
            is_finished = True
        
        return {
            'p_score': player_score.strip(),
            'p_cards': player_cards,
            'd_score': dealer_score.strip(),
            'd_cards': dealer_cards,
            'game_status': game_status.strip(),
            'timer_text': timer_text.strip(),
            'is_finished': is_finished,
            'winner': winner
        }
    except Exception as e:
        if "closed" not in str(e).lower():
            logging.error(f"Ошибка в get_state_fast: {e}")
        return None

async def is_game_truly_finished(page):
    """Проверка завершения игры по статусу"""
    try:
        if page.is_closed():
            return False, None
            
        # Проверяем статус в заголовке
        status_el = await page.query_selector('.live-twenty-one-table-head__status')
        if status_el:
            status_text = await status_el.text_content()
            if 'Победа игрока' in status_text:
                return True, 'player'
            elif 'Победа дилера' in status_text:
                return True, 'dealer'
            elif 'Ничья' in status_text:
                return True, 'tie'
        
        # Проверяем таймер завершения
        timer_el = await page.query_selector('.ui-game-timer__label')
        if timer_el:
            timer_text = await timer_el.text_content()
            if timer_text and 'завершена' in timer_text.lower():
                # Пробуем определить победителя по счету
                return True, 'unknown'
        
        return False, None
        
    except Exception as e:
        if "closed" not in str(e).lower():
            logging.error(f"Ошибка в is_game_truly_finished: {e}")
        return False, None

def format_message(table_id, state, is_final=False, t_num=None, table_number=None):
    p_cards = format_cards(state['p_cards'])
    d_cards = format_cards(state['d_cards'])
    
    if table_number is None:
        table_number = int(table_id) % 1440
        if table_number == 0:
            table_number = 1440
    
    try:
        total_score = int(state['p_score']) + int(state['d_score'])
    except:
        total_score = 0
    
    if is_final:
        winner = state.get('winner', 'unknown')
        
        if winner == 'player':
            score_part = f"✅{state['p_score']}({p_cards}) - {state['d_score']}({d_cards})"
        elif winner == 'dealer':
            score_part = f"{state['p_score']}({p_cards}) - ✅{state['d_score']}({d_cards})"
        else:
            score_part = f"{state['p_score']}({p_cards}) - {state['d_score']}({d_cards})"
        
        specials = check_special_conditions(state['p_cards'], state['d_cards'], 
                                           state['p_score'], state['d_score'])
        
        base_msg = f"#N{table_number}. {score_part} #T{total_score}"
        return f"{base_msg} {specials}" if specials else base_msg
    else:
        return f"⏰#N{table_number}. {state['p_score']}({p_cards}) - {state['d_score']}({d_cards}) #T{total_score}"

def send_telegram_message_with_retry(chat_id, text):
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
                raise e
    raise Exception(f"Не удалось отправить сообщение")

def edit_telegram_message_with_retry(chat_id, message_id, text):
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
                logging.error(f"Ошибка при редактировании: {e}")
                return None
    return None

async def get_next_table(page):
    global last_table_id
    
    try:
        await page.wait_for_selector('.dashboard-game-block', timeout=30000)
        await page.wait_for_timeout(3000)
        
        tables = await page.query_selector_all('.dashboard-game-block')
        logging.info(f"Найдено столов: {len(tables)}")
        
        if not tables:
            return None, None, None
        
        valid_tables = []
        current_time = datetime.now()
        
        for table in tables:
            try:
                # Получаем ID стола
                id_element = await table.query_selector('.dashboard-game-info__additional-info')
                if not id_element:
                    continue
                    
                table_id_text = await id_element.text_content()
                if not table_id_text:
                    continue
                    
                match = re.search(r'(\d+)$', table_id_text.strip())
                if not match:
                    continue
                    
                table_num = int(match.group(1))
                
                # Получаем время начала игры (если есть)
                time_element = await table.query_selector('.dashboard-game-info__time')
                start_time = current_time
                
                if time_element:
                    time_text = await time_element.text_content()
                    # Парсим время в формате "MM:SS"
                    time_parts = time_text.strip().split(':')
                    if len(time_parts) == 2:
                        minutes = int(time_parts[0])
                        seconds = int(time_parts[1])
                        # Вычисляем время начала игры
                        # Если время меньше 2 минут, значит игра уже идет
                        if minutes == 0 and seconds < GAME_DURATION:
                            # Игра началась, вычисляем время окончания
                            elapsed = minutes * 60 + seconds
                            start_time = current_time - timedelta(seconds=elapsed)
                
                # Проверяем, что это игра 21 Classic
                link_element = await table.query_selector('.dashboard-game-block__link')
                if link_element:
                    href = await link_element.get_attribute('href')
                    if href and '21-classics' in href:
                        valid_tables.append((table_num, table, start_time))
                        logging.info(f"Найден стол 21 Classic: {table_num}, время: {start_time.strftime('%H:%M:%S')}")
            except Exception as e:
                logging.error(f"Ошибка при парсинге стола: {e}")
                continue
        
        if not valid_tables:
            return None, None, None
        
        # Сортируем по ID
        valid_tables.sort(key=lambda x: x[0])
        
        # Ищем новые столы
        new_tables = [t for t in valid_tables if t[0] > last_table_id]
        
        if new_tables:
            selected_table = new_tables[0][1]
            selected_id = new_tables[0][0]
            selected_start_time = new_tables[0][2]
            logging.info(f"Найден новый стол: {selected_id}")
            last_table_id = selected_id
        else:
            selected_table = valid_tables[0][1]
            selected_id = valid_tables[0][0]
            selected_start_time = valid_tables[0][2]
            logging.info(f"Новых столов нет, беру первый: {selected_id}")
        
        # Получаем ссылку на стол
        link_element = await selected_table.query_selector('.dashboard-game-block__link')
        href = await link_element.get_attribute('href')
        
        if href and not href.startswith('http'):
            href = f"https://1xlite-6997737.bar{href}"
        
        return href, str(selected_id), selected_start_time
        
    except Exception as e:
        logging.error(f"Ошибка при поиске стола: {e}")
        return None, None, None

async def monitor_table(table_url, table_id, scheduled_start):
    """Мониторинг стола с открытием за 20 секунд до игры"""
    msg_id = None
    t_num = random.randint(30, 60)
    game_active = True
    table_number = int(table_id) % 1440
    if table_number == 0:
        table_number = 1440
    last_state = None
    browser = None
    page = None
    game_finished = False
    monitoring_active = True
    
    logging.info(f"Стол {table_id}: запланирован на {scheduled_start.strftime('%H:%M:%S')}")
    
    # Вычисляем время открытия браузера (за 20 секунд до игры)
    browser_open_time = scheduled_start - timedelta(seconds=BROWSER_START_OFFSET)
    current_time = datetime.now()
    
    if current_time < browser_open_time:
        wait_seconds = (browser_open_time - current_time).total_seconds()
        logging.info(f"Стол {table_id}: ожидание {wait_seconds:.1f} секунд до открытия браузера")
        await asyncio.sleep(wait_seconds)
    
    logging.info(f"Стол {table_id}: открываю браузер")
    
    try:
        async with async_playwright() as p:
            browser = await p.firefox.launch(
                headless=True,
                executable_path="/root/.cache/ms-playwright/firefox-1509/firefox/firefox",
                args=["--no-sandbox"]
            )
            
            context = await browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0'
            )
            page = await context.new_page()
            page.set_default_timeout(30000)
            
            # Загружаем страницу
            await page.goto(table_url, timeout=30000, wait_until="domcontentloaded")
            
            # Ждем начала игры (появления карт или счета)
            game_started = False
            wait_start = time.time()
            max_wait = 30  # Ждем до 30 секунд после открытия
            
            while not game_started and (time.time() - wait_start) < max_wait:
                if page.is_closed():
                    return
                
                state = await get_state_fast(page)
                if state and (len(state['p_cards']) > 0 or state['p_score'] != '0'):
                    game_started = True
                    logging.info(f"Стол {table_id}: игра началась")
                    break
                
                await asyncio.sleep(1)
            
            if not game_started:
                logging.warning(f"Стол {table_id}: игра не началась за {max_wait} секунд")
                return
            
            # Отправляем первое сообщение
            first_state = await get_state_fast(page)
            if first_state:
                msg = format_message(table_id, first_state, table_number=table_number)
                sent = send_telegram_message_with_retry(CHANNEL_ID, msg)
                msg_id = sent.message_id
                with lock:
                    message_ids[table_id] = msg_id
                    last_messages[table_id] = msg
                last_state = first_state
                logging.info(f"Стол {table_id}: первое сообщение отправлено")
            
            # Мониторим игру до завершения
            while monitoring_active and bot_running:
                try:
                    if page.is_closed():
                        break
                    
                    state = await get_state_fast(page)
                    if not state:
                        await asyncio.sleep(1)
                        continue
                    
                    # Проверяем завершение игры
                    finished, winner = await is_game_truly_finished(page)
                    
                    if finished and not game_finished:
                        game_finished = True
                        logging.info(f"Стол {table_id}: игра завершена, победитель: {winner}")
                        
                        # Добавляем победителя в состояние
                        state['winner'] = winner
                        
                        # Отправляем финальное сообщение
                        final_msg = format_message(table_id, state, is_final=True, 
                                                 t_num=t_num, table_number=table_number)
                        
                        if msg_id:
                            edit_telegram_message_with_retry(CHANNEL_ID, msg_id, final_msg)
                        else:
                            sent = send_telegram_message_with_retry(CHANNEL_ID, final_msg)
                            msg_id = sent.message_id
                        
                        game_data.add_completed_game(table_id, final_msg, t_num)
                        game_data.update_last_number(table_number)
                        
                        monitoring_active = False
                        break
                    
                    # Обновляем сообщение при изменении состояния
                    elif state != last_state and not finished:
                        msg = format_message(table_id, state, table_number=table_number)
                        
                        with lock:
                            last_msg = last_messages.get(table_id)
                            if last_msg == msg:
                                await asyncio.sleep(0.5)
                                continue
                        
                        if msg_id:
                            result = edit_telegram_message_with_retry(CHANNEL_ID, msg_id, msg)
                            if result is not None:
                                with lock:
                                    last_messages[table_id] = msg
                                    last_state = state
                    
                    await asyncio.sleep(0.5)
                    
                except Exception as e:
                    if "closed" in str(e).lower():
                        break
                    logging.error(f"Стол {table_id}: ошибка в цикле: {e}")
                    await asyncio.sleep(1)
            
            # Даем время на отправку финального сообщения
            await asyncio.sleep(2)
            
    except Exception as e:
        logging.error(f"Стол {table_id}: критическая ошибка: {e}")
    finally:
        if browser:
            await browser.close()
        with lock:
            if table_id in active_tables:
                del active_tables[table_id]
            if table_id in message_ids:
                del message_ids[table_id]
            if table_id in last_messages:
                del last_messages[table_id]
        logging.info(f"Стол {table_id}: мониторинг завершен, браузер закрыт")

def run_async_monitor(table_url, table_id, scheduled_start):
    try:
        asyncio.run(monitor_table(table_url, table_id, scheduled_start))
    except Exception as e:
        logging.error(f"Ошибка в потоке мониторинга стола {table_id}: {e}")

def launch_new_table_monitor():
    async def get_table():
        async with async_playwright() as p:
            browser = None
            try:
                browser = await p.firefox.launch(
                    headless=True,
                    executable_path="/root/.cache/ms-playwright/firefox-1509/firefox/firefox",
                    args=["--no-sandbox"]
                )
                page = await browser.new_page()
                await page.goto(MAIN_URL, timeout=30000, wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)
                url, tid, start_time = await get_next_table(page)
                return url, tid, start_time
            except Exception as e:
                logging.error(f"Ошибка при загрузке MAIN_URL: {e}")
                return None, None, None
            finally:
                if browser:
                    await browser.close()
    
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        table_url, table_id, scheduled_start = loop.run_until_complete(get_table())
        loop.close()
        
        if table_url and table_id and scheduled_start:
            with lock:
                if table_id in active_tables:
                    logging.info(f"Стол {table_id} уже мониторится, пропускаю")
                    return
                
                if len(active_tables) >= MAX_BROWSERS:
                    logging.info(f"Достигнут лимит браузеров ({MAX_BROWSERS})")
                    return
            
            logging.info(f"Найден следующий стол 21 Classic: {table_id}")
            
            thread = threading.Thread(target=run_async_monitor, 
                                    args=(table_url, table_id, scheduled_start))
            thread.daemon = True
            thread.start()
            
            with lock:
                active_tables[table_id] = thread
            
            logging.info(f"Запущен мониторинг стола {table_id} с открытием в {scheduled_start.strftime('%H:%M:%S')}")
        else:
            logging.warning("Не удалось найти следующий стол")
            
    except Exception as e:
        logging.error(f"Ошибка при запуске нового монитора: {e}")

def clean_threads():
    with lock:
        dead = [tid for tid, t in active_tables.items() if not t.is_alive()]
        for tid in dead:
            del active_tables[tid]
            if tid in message_ids:
                del message_ids[tid]
            if tid in last_messages:
                del last_messages[tid]
            logging.info(f"Поток стола {tid} очищен")

def monitor_loop():
    global bot_running, last_table_id
    last_table_id = 0
    logging.info("🚀 Бот для 21 Classic запущен с оптимизацией под 2-минутные игры")
    logging.info(f"Максимум браузеров: {MAX_BROWSERS}")
    logging.info(f"Браузеры открываются за {BROWSER_START_OFFSET} секунд до игры")
    
    check_interval = 60  # Проверяем новые столы каждую минуту
    last_check = time.time()
    
    while bot_running:
        try:
            current_time = time.time()
            clean_threads()
            
            if len(active_tables) < MAX_BROWSERS:
                if current_time - last_check >= check_interval:
                    logging.info(f"Активных столов: {len(active_tables)}/{MAX_BROWSERS}, ищу новые...")
                    launch_new_table_monitor()
                    last_check = current_time
            
            time.sleep(10)
            
        except KeyboardInterrupt:
            logging.info("Получен сигнал завершения")
            bot_running = False
            break
        except Exception as e:
            logging.error(f"Ошибка в основном цикле: {e}")
            time.sleep(10)
    
    game_data.save_data()
    logging.info("Бот остановлен")

def main():
    monitor_loop()

if __name__ == "__main__":
    main()