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
MAIN_URL = "https://1xlite-7636770.bar/ru/live/twentyone/1643503-twentyone-game"
MAX_BROWSERS = 4
DATA_FILE = "game_data.pkl"
DATA_RETENTION_DAYS = 3
# =====================

apihelper.RETRY_ON_ERROR = True
apihelper.MAX_RETRIES = 5

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
message_ids = {}
last_messages = {}
last_table_id = 0
lock = threading.Lock()
tasks = {}

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

async def parse_cards(elements):
    cards = []
    for el in elements:
        try:
            class_name = await el.get_attribute('class') or ''
            suit = next((s for c, s in SUIT_MAP.items() if c in class_name), '?')
            val_match = re.search(r'value-(\d+)', class_name)
            if val_match:
                value = VALUE_MAP.get(f'value-{val_match.group(1)}', val_match.group(1))
            else:
                value = '?'
            cards.append(f"{value}{suit}")
        except:
            continue
    return cards

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

def determine_turn(state):
    if state.get('player_active', False):
        return 'player'
    elif state.get('dealer_active', False):
        return 'dealer'
    return None

async def get_state_fast(page):
    try:
        player_score_el = await page.query_selector('.live-twenty-one-field-player:first-child .live-twenty-one-field-score__label')
        player_score = await player_score_el.text_content() if player_score_el else '0'
        
        player_cards_els = await page.query_selector_all('.live-twenty-one-field-player:first-child .scoreboard-card-games-card')
        player_cards = await parse_cards(player_cards_els)
        
        dealer_score_el = await page.query_selector('.live-twenty-one-field-player:last-child .live-twenty-one-field-score__label')
        dealer_score = await dealer_score_el.text_content() if dealer_score_el else '0'
        
        dealer_cards_els = await page.query_selector_all('.live-twenty-one-field-player:last-child .scoreboard-card-games-card')
        dealer_cards = await parse_cards(dealer_cards_els)
        
        player_active = False
        dealer_active = False
        
        player_area = await page.query_selector('.live-twenty-one-field-player:first-child')
        if player_area:
            class_name = await player_area.get_attribute('class') or ''
            if 'active' in class_name.lower():
                player_active = True
        
        dealer_area = await page.query_selector('.live-twenty-one-field-player:last-child')
        if dealer_area:
            class_name = await dealer_area.get_attribute('class') or ''
            if 'active' in class_name.lower():
                dealer_active = True
        
        return {
            'p_score': player_score.strip(),
            'p_cards': player_cards,
            'd_score': dealer_score.strip(),
            'd_cards': dealer_cards,
            'player_active': player_active,
            'dealer_active': dealer_active
        }
    except Exception as e:
        return None

async def is_game_truly_finished(page):
    # Проверяем явные признаки завершения игры
    try:
        finished = await page.query_selector('span.ui-caption--size-xl.ui-caption--weight-700.ui-caption--color-clr-strong.ui-caption')
        if finished:
            text = await finished.text_content()
            if text and 'Игра завершена' in text:
                return True
    except:
        pass
    
    # Проверяем наличие кнопки новой игры (самый надежный признак)
    try:
        new_btns = await page.query_selector_all('.ui-game-controls__button, .new-game-button, [class*="new"]')
        for btn in new_btns:
            if await btn.is_visible():
                return True
    except:
        pass
    
    # Проверяем, есть ли активный игрок или дилер
    try:
        player_area = await page.query_selector('.live-twenty-one-field-player:first-child')
        dealer_area = await page.query_selector('.live-twenty-one-field-player:last-child')
        
        player_active = False
        dealer_active = False
        
        if player_area:
            class_name = await player_area.get_attribute('class') or ''
            if 'active' in class_name.lower():
                player_active = True
        
        if dealer_area:
            class_name = await dealer_area.get_attribute('class') or ''
            if 'active' in class_name.lower():
                dealer_active = True
        
        # Если есть активный игрок или дилер - игра точно не завершена
        if player_active or dealer_active:
            return False
    except:
        pass
    
    # Проверяем счета (если оба > 21, то игра завершена)
    try:
        player_score_el = await page.query_selector('.live-twenty-one-field-player:first-child .live-twenty-one-field-score__label')
        dealer_score_el = await page.query_selector('.live-twenty-one-field-player:last-child .live-twenty-one-field-score__label')
        
        if player_score_el and dealer_score_el:
            player_score = await player_score_el.text_content()
            dealer_score = await dealer_score_el.text_content()
            
            if player_score and dealer_score:
                try:
                    p_score = int(player_score.strip())
                    d_score = int(dealer_score.strip())
                    # Если оба перебрали - игра завершена
                    if p_score > 21 and d_score > 21:
                        return True
                except:
                    pass
    except:
        pass
    
    # По умолчанию считаем, что игра не завершена
    return False

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
        try:
            p_score_int = int(state['p_score'])
            d_score_int = int(state['d_score'])
            
            if p_score_int > 21 and d_score_int <= 21:
                winner = 'dealer'
            elif d_score_int > 21 and p_score_int <= 21:
                winner = 'player'
            elif p_score_int > 21 and d_score_int > 21:
                winner = 'dealer' if d_score_int < p_score_int else 'player'
            else:
                winner = 'player' if p_score_int > d_score_int else 'dealer' if d_score_int > p_score_int else 'tie'
        except:
            winner = 'unknown'
        
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
        turn = determine_turn(state)
        if turn == 'player':
            return f"⏰#N{table_number}. {state['p_score']}({p_cards}) 👈 {state['d_score']}({d_cards}) #T{total_score}"
        elif turn == 'dealer':
            return f"⏰#N{table_number}. {state['p_score']}({p_cards}) 👉 {state['d_score']}({d_cards}) #T{total_score}"
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
        logging.info("Ожидание загрузки страницы со столами...")
        await page.wait_for_selector('.dashboard-game-block', timeout=30000)
        await page.wait_for_timeout(3000)
        
        tables = await page.query_selector_all('.dashboard-game-block')
        logging.info(f"Найдено столов: {len(tables)}")
        
        if not tables:
            return None, None
        
        valid_tables = []
        for table in tables:
            try:
                id_element = await table.query_selector('.dashboard-game-info__additional-info')
                if id_element:
                    table_id_text = await id_element.text_content()
                    if table_id_text:
                        match = re.search(r'(\d+)$', table_id_text.strip())
                        if match:
                            table_num = int(match.group(1))
                            valid_tables.append((table_num, table))
                            logging.info(f"Найден стол: {table_num}")
            except:
                continue
        
        if not valid_tables:
            return None, None
        
        valid_tables.sort(key=lambda x: x[0])
        
        # Ищем столы, которые больше last_table_id
        new_tables = [t for t in valid_tables if t[0] > last_table_id]
        
        if new_tables:
            selected_table = new_tables[0][1]
            selected_id = new_tables[0][0]
            logging.info(f"Найден новый стол: {selected_id}")
            last_table_id = selected_id
        else:
            selected_table = valid_tables[0][1]
            selected_id = valid_tables[0][0]
            logging.info(f"Новых столов нет, беру первый: {selected_id}")
        
        link_element = await selected_table.query_selector('.dashboard-game-block__link')
        href = await link_element.get_attribute('href')
        
        if href and not href.startswith('http'):
            href = f"https://1xlite-7636770.bar{href}"
        
        return href, str(selected_id)
        
    except Exception as e:
        logging.error(f"Ошибка при поиске стола: {e}")
        return None, None

async def monitor_table(table_url, table_id):
    msg_id = None
    t_num = random.randint(30, 60)
    game_active = True
    table_number = int(table_id) % 1440
    if table_number == 0:
        table_number = 1440
    last_state = None
    last_send_time = 0
    initial_load = True
    no_response_count = 0
    max_no_response = 20
    browser = None
    page = None
    inactive_start = None  # Для отслеживания времени без активности
    
    logging.info(f"Начало мониторинга стола {table_id}")
    
    try:
        async with async_playwright() as p:
            browser = await p.firefox.launch(
                headless=True,
                executable_path="/root/.cache/ms-playwright/firefox-1509/firefox/firefox",
                args=["--no-sandbox"]
            )
            page = await browser.new_page()
            
            try:
                await page.goto(table_url, timeout=60000, wait_until="domcontentloaded")
                
                # Начинаем проверять карты сразу без задержки
                cards_loaded = False
                wait_start = time.time()
                max_wait = 15
                
                while not cards_loaded and (time.time() - wait_start) < max_wait and game_active:
                    try:
                        player_cards = await page.query_selector_all('.live-twenty-one-field-player:first-child .scoreboard-card-games-card')
                        if len(player_cards) > 0:
                            cards_loaded = True
                            logging.info(f"Карты загружены для стола {table_id}")
                            break
                        
                        if await is_game_truly_finished(page):
                            logging.info(f"Игра на столе {table_id} уже завершена")
                            return
                            
                        await asyncio.sleep(0.3)
                    except Exception as e:
                        logging.error(f"Ошибка при ожидании карт: {e}")
                        break
                
                if not cards_loaded:
                    logging.warning(f"Стол {table_id}: карты не появились")
                    return
                
                logging.info(f"Старт мониторинга стола {table_id}")
                
                while game_active:
                    try:
                        if not page or page.is_closed():
                            logging.warning(f"Стол {table_id}: страница закрыта, выходим")
                            break
                            
                        state = await get_state_fast(page)
                        
                        if not state:
                            no_response_count += 1
                            if no_response_count >= max_no_response:
                                if await is_game_truly_finished(page):
                                    logging.info(f"Стол {table_id} завершен (таймаут)")
                                    game_active = False
                                    break
                            await asyncio.sleep(0.5)
                            continue
                        
                        no_response_count = 0
                        
                        player_active = state.get('player_active', False)
                        dealer_active = state.get('dealer_active', False)
                        
                        # Логика определения завершения игры с защитой от ложных срабатываний
                        if not player_active and not dealer_active:
                            # Нет активности - возможно переход хода или завершение
                            if inactive_start is None:
                                inactive_start = time.time()
                                logging.info(f"Стол {table_id}: нет активности, начало отсчета")
                            else:
                                inactive_duration = time.time() - inactive_start
                                # Если нет активности больше 3 секунд и игра действительно завершена
                                if inactive_duration > 3 and await is_game_truly_finished(page):
                                    logging.info(f"Стол {table_id}: игра завершена (неактивно {inactive_duration:.1f} сек)")
                                    final_state = state
                                    
                                    if len(final_state['p_cards']) > 0 or len(final_state['d_cards']) > 0:
                                        final_msg = format_message(table_id, final_state, is_final=True, 
                                                                  t_num=t_num, table_number=table_number)
                                        
                                        if msg_id:
                                            edit_telegram_message_with_retry(CHANNEL_ID, msg_id, final_msg)
                                        else:
                                            sent = send_telegram_message_with_retry(CHANNEL_ID, final_msg)
                                            msg_id = sent.message_id
                                            with lock:
                                                message_ids[table_id] = msg_id
                                        
                                        game_data.add_completed_game(table_id, final_msg, t_num)
                                        game_data.update_last_number(table_number)
                                    
                                    game_active = False
                                    break
                        else:
                            # Активность есть - сбрасываем таймер
                            if inactive_start is not None:
                                logging.info(f"Стол {table_id}: активность возобновилась")
                                inactive_start = None
                        
                        has_cards = len(state['p_cards']) > 0 or len(state['d_cards']) > 0
                        
                        if has_cards and (state != last_state or initial_load):
                            msg = format_message(table_id, state, table_number=table_number)
                            
                            with lock:
                                last_msg = last_messages.get(table_id)
                                if last_msg == msg and not initial_load:
                                    await asyncio.sleep(0.5)
                                    continue
                            
                            if msg_id:
                                result = edit_telegram_message_with_retry(CHANNEL_ID, msg_id, msg)
                                if result is not None:
                                    with lock:
                                        last_messages[table_id] = msg
                            else:
                                sent = send_telegram_message_with_retry(CHANNEL_ID, msg)
                                msg_id = sent.message_id
                                with lock:
                                    message_ids[table_id] = msg_id
                                    last_messages[table_id] = msg
                                logging.info(f"Стол {table_id}: первое сообщение с картами")
                            
                            last_state = state
                            initial_load = False
                        
                        await asyncio.sleep(0.5)
                        
                    except Exception as e:
                        if "closed" in str(e).lower():
                            logging.warning(f"Стол {table_id}: браузер/страница закрыты, завершаем мониторинг")
                            break
                        else:
                            logging.error(f"Ошибка в цикле стола {table_id}: {e}")
                            await asyncio.sleep(1)
            
            except Exception as e:
                logging.error(f"Критическая ошибка стола {table_id}: {e}")
            finally:
                if browser:
                    await browser.close()
                    
    except Exception as e:
        logging.error(f"Ошибка при создании браузера для стола {table_id}: {e}")
    finally:
        with lock:
            if table_id in active_tables:
                del active_tables[table_id]
            if table_id in message_ids:
                del message_ids[table_id]
            if table_id in last_messages:
                del last_messages[table_id]
        logging.info(f"Мониторинг стола {table_id} завершен")

def run_async_monitor(table_url, table_id):
    asyncio.run(monitor_table(table_url, table_id))

def launch_new_table_monitor():
    async def get_table():
        async with async_playwright() as p:
            browser = await p.firefox.launch(
                headless=True,
                executable_path="/root/.cache/ms-playwright/firefox-1509/firefox/firefox",
                args=["--no-sandbox"]
            )
            page = await browser.new_page()
            try:
                await page.goto(MAIN_URL, timeout=60000, wait_until="domcontentloaded")
                await page.wait_for_timeout(5000)
                url, tid = await get_next_table(page)
                await browser.close()
                return url, tid
            except Exception as e:
                logging.error(f"Ошибка при загрузке MAIN_URL: {e}")
                await browser.close()
                return None, None
    
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        table_url, table_id = loop.run_until_complete(get_table())
        loop.close()
        
        if table_url and table_id:
            with lock:
                if table_id in active_tables:
                    logging.info(f"Стол {table_id} уже мониторится, пропускаю")
                    return
                
                if len(active_tables) >= MAX_BROWSERS:
                    logging.info(f"Достигнут лимит браузеров ({MAX_BROWSERS}), стол {table_id} не запущен")
                    return
            
            logging.info(f"Найден следующий стол: {table_id}")
            
            thread = threading.Thread(target=run_async_monitor, args=(table_url, table_id))
            thread.daemon = True
            thread.start()
            
            with lock:
                active_tables[table_id] = thread
            
            logging.info(f"Запущен мониторинг стола {table_id}")
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

def get_next_game_time():
    now = datetime.now()
    next_minute = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return next_minute

def wait_for_next_game():
    next_game = get_next_game_time()
    launch_time = next_game - timedelta(seconds=10)
    
    now = datetime.now()
    if now < launch_time:
        wait_seconds = (launch_time - now).total_seconds()
        logging.info(f"Следующий запуск через {wait_seconds:.1f} сек (в {launch_time.strftime('%H:%M:%S')})")
        time.sleep(wait_seconds)
    else:
        next_launch = launch_time + timedelta(minutes=1)
        wait_seconds = (next_launch - now).total_seconds()
        logging.info(f"Пропустили время запуска, ждем до {next_launch.strftime('%H:%M:%S')}")
        time.sleep(wait_seconds)

def main():
    global last_table_id
    last_table_id = 0
    logging.info("🚀 Бот запущен на Playwright с Firefox")
    logging.info(f"Максимум браузеров: {MAX_BROWSERS}")
    
    while True:
        try:
            clean_threads()
            wait_for_next_game()
            launch_new_table_monitor()
            time.sleep(2)
            
        except KeyboardInterrupt:
            logging.info("Получен сигнал завершения")
            break
        except Exception as e:
            logging.error(f"Ошибка в главном цикле: {e}")
            time.sleep(5)
    
    game_data.save_data()

if __name__ == "__main__":
    main()