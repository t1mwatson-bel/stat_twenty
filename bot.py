import threading
import time
import re
import logging
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
MAIN_URL = "https://1xlite-7636770.bar/ru/live/twentyone/2092323-21-classics"
MAX_BROWSERS = 2
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
lock = threading.Lock()
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
    
    def add_completed_game(self, game_number, message):
        self.completed_games[game_number] = {
            'message': message,
            'timestamp': datetime.now()
        }
        self.save_data()
    
    def is_game_completed(self, game_number):
        return game_number in self.completed_games
    
    def update_last_number(self, number):
        if number > self.last_game_number:
            self.last_game_number = number
            self.save_data()

game_data = GameData()

def get_game_number_by_time(dt=None):
    """Расчет номера игры по времени"""
    if dt is None:
        dt = datetime.now()
    
    start_of_day = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    minutes_passed = (dt - start_of_day).total_seconds() / 60
    game_number = int(minutes_passed // 2) + 1
    
    return game_number

def get_next_game_time():
    """Возвращает время следующей игры"""
    now = datetime.now()
    
    next_game_minute = ((now.minute // 2) * 2 + 2) % 60
    next_game_hour = now.hour
    
    if next_game_minute < now.minute:
        next_game_hour = (next_game_hour + 1) % 24
    
    next_game_time = now.replace(
        hour=next_game_hour,
        minute=next_game_minute,
        second=0,
        microsecond=0
    )
    
    seconds_to_start = (next_game_time - now).total_seconds() - 30
    
    return next_game_time, max(0, seconds_to_start)

def format_cards(cards):
    return ''.join(cards)

def determine_winner(p_score, d_score):
    try:
        p = int(p_score)
        d = int(d_score)
        
        if p > 21 and d <= 21:
            return 'П2'
        elif d > 21 and p <= 21:
            return 'П1'
        elif p > 21 and d > 21:
            return 'П2' if d < p else 'П1'
        else:
            return 'П1' if p > d else 'П2' if d > p else 'НИЧЬЯ'
    except:
        return 'UNKNOWN'

def format_message(game_number, state, turn=None, is_final=False):
    p_cards = format_cards(state['p_cards'])
    d_cards = format_cards(state['d_cards'])
    
    try:
        total_score = int(state['p_score']) + int(state['d_score'])
    except:
        total_score = 0
    
    if is_final:
        winner = determine_winner(state['p_score'], state['d_score'])
        
        if winner == 'П1':
            score_part = f"☑️{state['p_score']}({p_cards})-{state['d_score']}({d_cards})"
        elif winner == 'П2':
            score_part = f"{state['p_score']}({p_cards})-☑️{state['d_score']}({d_cards})"
        else:
            score_part = f"{state['p_score']}({p_cards})-{state['d_score']}({d_cards})"
        
        return f"#N{game_number} {score_part}  #{winner} #T{total_score}"
    else:
        if turn == 'player':
            return f"#N{game_number} {state['p_score']}({p_cards}) 👈 {state['d_score']}({d_cards}) #T{total_score}"
        elif turn == 'dealer':
            return f"#N{game_number} {state['p_score']}({p_cards}) 👉 {state['d_score']}({d_cards}) #T{total_score}"
        else:
            return f"#N{game_number} {state['p_score']}({p_cards})-{state['d_score']}({d_cards}) #T{total_score}"

async def extract_cards_from_container(container):
    cards = []
    if not container:
        return cards
    
    card_elements = await container.query_selector_all('.scoreboard-card-games-card')
    
    for el in card_elements:
        try:
            class_name = await el.get_attribute('class') or ''
            
            if 'hidden' in class_name.lower() or 'face-down' in class_name.lower():
                continue
            
            suit = '?'
            if 'suit-0' in class_name:
                suit = '♠️'
            elif 'suit-1' in class_name:
                suit = '♣️'
            elif 'suit-2' in class_name:
                suit = '♦️'
            elif 'suit-3' in class_name:
                suit = '♥️'
            
            val_match = re.search(r'value-(\d+)', class_name)
            if val_match:
                val = val_match.group(1)
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
        except:
            continue
    
    return cards

async def get_state_fast(page):
    try:
        player_score_el = await page.query_selector('.live-twenty-one-field-player:first-child .live-twenty-one-field-score__label')
        player_score = await player_score_el.text_content() if player_score_el else '0'
        
        player_cards_container = await page.query_selector('.live-twenty-one-field-player:first-child .live-twenty-one-cards')
        player_cards = await extract_cards_from_container(player_cards_container)
        
        dealer_score_el = await page.query_selector('.live-twenty-one-field-player:last-child .live-twenty-one-field-score__label')
        dealer_score = await dealer_score_el.text_content() if dealer_score_el else '0'
        
        dealer_cards_container = await page.query_selector('.live-twenty-one-field-player:last-child .live-twenty-one-cards')
        dealer_cards = await extract_cards_from_container(dealer_cards_container)
        
        return {
            'p_score': player_score.strip(),
            'p_cards': player_cards,
            'd_score': dealer_score.strip(),
            'd_cards': dealer_cards
        }
    except Exception as e:
        logging.error(f"Ошибка в get_state_fast: {e}")
        return None

async def determine_turn(page):
    try:
        player_area = await page.query_selector('.live-twenty-one-field-player:first-child')
        if player_area:
            class_name = await player_area.get_attribute('class') or ''
            if 'active' in class_name.lower():
                return 'player'
        
        dealer_area = await page.query_selector('.live-twenty-one-field-player:last-child')
        if dealer_area:
            class_name = await dealer_area.get_attribute('class') or ''
            if 'active' in class_name.lower():
                return 'dealer'
        
        return None
    except Exception as e:
        logging.error(f"Ошибка в determine_turn: {e}")
        return None

async def is_game_truly_finished(page):
    try:
        timer_div = await page.query_selector('.live-twenty-one-table-footer__timer .ui-game-timer__label')
        if timer_div:
            text = await timer_div.text_content()
            if text and "Игра завершена" in text:
                return True
        
        finished = await page.query_selector('span:has-text("Игра завершена")')
        if finished:
            return True
        
        status = await page.query_selector('.live-twenty-one-table-head__status')
        if status:
            text = await status.text_content()
            if 'Победа' in text:
                return True
        
        return False
    except Exception as e:
        logging.error(f"Ошибка в is_game_truly_finished: {e}")
        return False

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

async def get_table_url(page, game_number):
    """Получение URL стола по номеру игры (ускоренная версия)"""
    try:
        logging.info(f"Ищем стол №{game_number}...")
        
        tables = await page.query_selector_all('.dashboard-game-block')
        logging.info(f"Всего столов: {len(tables)}")
        
        for table in tables:
            try:
                info_elem = await table.query_selector('.dashboard-game-info__additional-info')
                if info_elem:
                    text = await info_elem.text_content()
                    match = re.search(r'(\d+)', text)
                    if match:
                        current_number = int(match.group(1))
                        
                        if current_number == game_number:
                            link_element = await table.query_selector('.dashboard-game-block__link')
                            if link_element:
                                href = await link_element.get_attribute('href')
                                if href and not href.startswith('http'):
                                    href = f"https://1xlite-7636770.bar{href}"
                                
                                logging.info(f"Найден нужный стол #{current_number}")
                                return href
                        else:
                            logging.info(f"Стол #{current_number} не подходит, ищем #{game_number}")
                    
            except Exception as e:
                continue
        
        logging.warning(f"Стол #{game_number} не найден")
        return None
        
    except Exception as e:
        logging.error(f"Ошибка в get_table_url: {e}")
        return None

async def monitor_table(table_url, game_number, game_start_time):
    """Мониторинг конкретного стола (ускоренная версия с ограничением памяти)"""
    
    msg_id = None
    last_state = None
    browser = None
    page = None
    game_finished = False
    first_message_sent = False
    start_time = time.time()
    max_duration = 240
    
    # Время реального старта игры
    game_real_start = game_start_time.timestamp()
    ignore_finish_until = game_real_start + 15
    
    logging.info(f"Стол #{game_number}: начало мониторинга (игра в {game_start_time.strftime('%H:%M:%S')})")
    
    try:
        async with async_playwright() as p:
            # ===== ОПТИМИЗАЦИЯ ПАМЯТИ =====
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-software-rasterizer",
                    "--disable-setuid-sandbox",
                    "--js-flags=--max-old-space-size=256",  # Ограничение JS памяти до 256 МБ
                    "--single-process",  # Единый процесс вместо многопроцессности
                    "--memory-pressure-off",  # Отключаем автоматическое управление памятью
                    "--max_old_space_size=256",  # Ограничение V8
                    "--disable-background-timer-throttling",
                    "--disable-backgrounding-occluded-windows",
                    "--disable-renderer-backgrounding",
                    "--disable-ipc-flooding-protection",
                    "--disable-background-networking"
                ]
            )
            # ==============================
            
            page = await browser.new_page()
            
            # Ускоренная загрузка страницы (не ждём полной загрузки)
            await page.goto(table_url, timeout=30000, wait_until="commit")
            logging.info(f"Стол #{game_number}: страница загружена (commit)")
            
            # Ждем появления карт (но недолго)
            for i in range(5):
                state = await get_state_fast(page)
                if state and (len(state['p_cards']) > 0 or len(state['d_cards']) > 0):
                    logging.info(f"Стол #{game_number}: карты появились через {i+1} сек")
                    break
                await asyncio.sleep(1)
            
            while not game_finished and (time.time() - start_time) < max_duration:
                try:
                    if page.is_closed():
                        break
                    
                    current_time = time.time()
                    
                    if current_time < ignore_finish_until:
                        is_finished = False
                    else:
                        is_finished = await is_game_truly_finished(page)
                    
                    if is_finished and not game_finished:
                        game_finished = True
                        logging.info(f"Стол #{game_number}: игра завершена")
                        
                        await asyncio.sleep(2)
                        
                        final_state = None
                        for attempt in range(10):
                            final_state = await get_state_fast(page)
                            if final_state and (len(final_state['p_cards']) > 0 or len(final_state['d_cards']) > 0):
                                logging.info(f"Стол #{game_number}: финал получен с {attempt+1} попытки")
                                break
                            await asyncio.sleep(0.3)
                        
                        if final_state:
                            final_msg = format_message(game_number, final_state, is_final=True)
                            
                            if msg_id:
                                edit_telegram_message_with_retry(CHANNEL_ID, msg_id, final_msg)
                            else:
                                sent = send_telegram_message_with_retry(CHANNEL_ID, final_msg)
                                msg_id = sent.message_id
                            
                            game_data.add_completed_game(game_number, final_msg)
                            game_data.update_last_number(game_number)
                            
                            logging.info(f"Стол #{game_number}: ФИНАЛ ОТПРАВЛЕН: {final_msg}")
                            break
                        else:
                            logging.error(f"Стол #{game_number}: НЕ УДАЛОСЬ ПОЛУЧИТЬ ФИНАЛ!!!")
                    
                    elif not game_finished:
                        state = await get_state_fast(page)
                        turn = await determine_turn(page)
                        
                        if state and (len(state['p_cards']) > 0 or len(state['d_cards']) > 0):
                            if state != last_state:
                                msg = format_message(game_number, state, turn=turn, is_final=False)
                                
                                if msg_id and first_message_sent:
                                    edit_telegram_message_with_retry(CHANNEL_ID, msg_id, msg)
                                elif not first_message_sent:
                                    if len(state['p_cards']) > 0 or len(state['d_cards']) > 0:
                                        sent = send_telegram_message_with_retry(CHANNEL_ID, msg)
                                        msg_id = sent.message_id
                                        first_message_sent = True
                                        logging.info(f"Стол #{game_number}: первое сообщение: {msg}")
                                
                                last_state = state
                    
                    await asyncio.sleep(0.3)
                    
                except Exception as e:
                    if "closed" in str(e).lower():
                        break
                    else:
                        logging.error(f"Ошибка в цикле стола #{game_number}: {e}")
                        await asyncio.sleep(1)
            
            if not game_finished:
                logging.warning(f"Стол #{game_number}: превышено время ожидания ({max_duration} сек)")
            
    except Exception as e:
        logging.error(f"Критическая ошибка стола #{game_number}: {e}")
    finally:
        if browser:
            await browser.close()
        
        with lock:
            if game_number in active_tables:
                del active_tables[game_number]
            if game_number in message_ids:
                del message_ids[game_number]
            if game_number in last_messages:
                del last_messages[game_number]
        
        logging.info(f"Стол #{game_number}: мониторинг завершен")

def run_async_monitor(table_url, game_number, game_start_time):
    try:
        asyncio.run(monitor_table(table_url, game_number, game_start_time))
    except Exception as e:
        logging.error(f"Ошибка в потоке мониторинга стола #{game_number}: {e}")

def launch_next_game_monitor():
    """Запускает монитор для следующей игры (ускоренная версия)"""
    async def get_table():
        async with async_playwright() as p:
            browser = None
            try:
                browser = await p.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--js-flags=--max-old-space-size=256"
                    ]
                )
                page = await browser.new_page()
                # Ускоренная загрузка
                await page.goto(MAIN_URL, timeout=30000, wait_until="commit")
                
                next_game_time, _ = get_next_game_time()
                game_number = get_game_number_by_time(next_game_time)
                
                url = await get_table_url(page, game_number)
                return url, game_number, next_game_time
                
            except Exception as e:
                logging.error(f"Ошибка при загрузке MAIN_URL: {e}")
                return None, None, None
            finally:
                if browser:
                    await browser.close()
    
    try:
        table_url, game_number, game_time = asyncio.run(get_table())
        
        if not table_url or not game_number:
            logging.warning("Не удалось получить URL стола")
            return
        
        with lock:
            if game_number in active_tables:
                logging.info(f"Игра #{game_number} уже мониторится, пропускаем")
                return
            if game_data.is_game_completed(game_number):
                logging.info(f"Игра #{game_number} уже завершена, пропускаем")
                return
        
        logging.info(f"Игра #{game_number}: запуск мониторинга (старт в {game_time.strftime('%H:%M:%S')})")
        
        thread = threading.Thread(
            target=run_async_monitor, 
            args=(table_url, game_number, game_time)
        )
        thread.daemon = True
        thread.start()
        
        with lock:
            active_tables[game_number] = thread
        
        logging.info(f"Игра #{game_number}: мониторинг запущен (активных: {len(active_tables)}/{MAX_BROWSERS})")
            
    except Exception as e:
        logging.error(f"Ошибка при запуске монитора: {e}")

def clean_threads():
    with lock:
        dead = [gid for gid, t in active_tables.items() if not t.is_alive()]
        for gid in dead:
            del active_tables[gid]
            if gid in message_ids:
                del message_ids[gid]
            if gid in last_messages:
                del last_messages[gid]
            logging.info(f"Поток игры #{gid} очищен")

def monitor_loop():
    global bot_running
    logging.info("🚀 Бот 21 Classic запущен на Chromium (УСКОРЕННАЯ ВЕРСИЯ + ОГРАНИЧЕНИЕ ПАМЯТИ)")
    logging.info(f"Максимум браузеров: {MAX_BROWSERS}")
    
    last_launch_time = 0
    
    while bot_running:
        try:
            clean_threads()
            
            # Проверяем только в нечетные минуты
            now = datetime.now()
            if now.minute % 2 == 1:  # нечетная минута
                next_game_time, seconds_to_next = get_next_game_time()
                current_time = time.time()
                
                if seconds_to_next <= 30 and (current_time - last_launch_time) > 25:
                    game_number = get_game_number_by_time(next_game_time)
                    logging.info(f"До игры #{game_number} осталось {seconds_to_next:.0f} сек")
                    launch_next_game_monitor()
                    last_launch_time = current_time
                    time.sleep(35)
            
            time.sleep(5)
            
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