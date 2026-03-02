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
MAX_BROWSERS = 3
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
    """Расчет номера игры по времени (МСК)"""
    if dt is None:
        dt = datetime.now()
    
    # Начало игрового дня в 03:00
    start_of_day = dt.replace(hour=3, minute=0, second=0, microsecond=0)
    
    if dt < start_of_day:
        start_of_day = start_of_day - timedelta(days=1)
    
    minutes_passed = (dt - start_of_day).total_seconds() / 60
    game_number = int(minutes_passed // 2) + 1
    
    if game_number > 720:
        game_number = game_number % 720
        if game_number == 0:
            game_number = 720
    
    return game_number

def get_next_game_time():
    now = datetime.now()
    
    # Округляем до следующей чётной минуты
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
    
    seconds_to_start = (next_game_time - now).total_seconds() - 60
    
    return next_game_time, max(0, seconds_to_start)

def format_cards(cards):
    return ''.join(cards)

def determine_winner(p_score, d_score):
    """Определение победителя"""
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

def format_message(game_number, state, is_final=False):
    """Форматирование сообщения"""
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
        
        ik = random.randint(1, 5)
        return f"#N{game_number} {score_part}  #{winner} #T{total_score} #ИК{ik}"
    else:
        return f"#N{game_number} {state['p_score']}({p_cards})-{state['d_score']}({d_cards}) #T{total_score}"

async def extract_cards(page, selector_prefix):
    """Извлечение карт из DOM"""
    cards = []
    selectors = [
        f'{selector_prefix} .scoreboard-card-games-card',
        f'{selector_prefix} .card-item',
        f'{selector_prefix} .game-card',
        f'{selector_prefix} [class*="card"]'
    ]
    
    for selector in selectors:
        try:
            card_elements = await page.query_selector_all(selector)
            if card_elements:
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
                            text = await el.text_content()
                            if text and text.strip():
                                value = text.strip()
                            else:
                                value = '?'
                        
                        cards.append(f"{value}{suit}")
                    except:
                        continue
                
                if cards:
                    break
        except:
            continue
    
    return cards

async def get_state_fast(page):
    """Получение текущего состояния игры"""
    try:
        player_score_el = await page.query_selector('.live-twenty-one-field-player:first-child .live-twenty-one-field-score__label')
        player_score = await player_score_el.text_content() if player_score_el else '0'
        player_cards = await extract_cards(page, '.live-twenty-one-field-player:first-child')
        
        dealer_score_el = await page.query_selector('.live-twenty-one-field-player:last-child .live-twenty-one-field-score__label')
        dealer_score = await dealer_score_el.text_content() if dealer_score_el else '0'
        dealer_cards = await extract_cards(page, '.live-twenty-one-field-player:last-child')
        
        return {
            'p_score': player_score.strip(),
            'p_cards': player_cards,
            'd_score': dealer_score.strip(),
            'd_cards': dealer_cards
        }
    except Exception as e:
        logging.error(f"Ошибка в get_state_fast: {e}")
        return None

async def is_game_truly_finished(page):
    """Проверка завершения игры"""
    try:
        finished = await page.query_selector('.ui-game-timer__label:has-text("Игра завершена")')
        if finished:
            return True
        
        status = await page.query_selector('.live-twenty-one-table-head__status')
        if status:
            text = await status.text_content()
            if 'Победа' in text or 'победа' in text:
                return True
        
        new_game = await page.query_selector('button:has-text("Новая игра")')
        if new_game and await new_game.is_visible():
            return True
        
        return False
    except:
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

async def get_table_url(page):
    """Получение URL первого активного стола"""
    try:
        logging.info("Поиск активного стола 21 Classic...")
        await page.wait_for_selector('.dashboard-game-block', timeout=30000)
        await page.wait_for_timeout(2000)
        
        tables = await page.query_selector_all('.dashboard-game-block')
        logging.info(f"Найдено столов: {len(tables)}")
        
        if not tables:
            return None
        
        first_table = tables[0]
        link_element = await first_table.query_selector('.dashboard-game-block__link')
        if not link_element:
            return None
        
        href = await link_element.get_attribute('href')
        if href and not href.startswith('http'):
            href = f"https://1xlite-7636770.bar{href}"
        
        return href
        
    except Exception as e:
        logging.error(f"Ошибка при поиске стола: {e}")
        return None

async def monitor_table(table_url, game_number, game_start_time):
    """Мониторинг конкретного стола"""
    msg_id = None
    last_state = None
    browser = None
    page = None
    session_start = time.time()
    game_finished = False
    first_message_sent = False
    
    SESSION_DURATION = 180
    
    logging.info(f"Стол #{game_number}: начало мониторинга (игра в {game_start_time.strftime('%H:%M:%S')})")
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox"]
            )
            page = await browser.new_page()
            await page.goto(table_url, timeout=60000, wait_until="domcontentloaded")
            
            while time.time() - session_start < SESSION_DURATION and bot_running:
                try:
                    if page.is_closed():
                        break
                    
                    state = await get_state_fast(page)
                    
                    if not state:
                        await asyncio.sleep(0.5)
                        continue
                    
                    is_finished = await is_game_truly_finished(page)
                    
                    if is_finished and not game_finished:
                        game_finished = True
                        logging.info(f"Стол #{game_number}: игра завершена")
                        
                        await asyncio.sleep(1)
                        final_state = await get_state_fast(page)
                        
                        if final_state and (len(final_state['p_cards']) > 0 or len(final_state['d_cards']) > 0):
                            final_msg = format_message(game_number, final_state, is_final=True)
                            
                            if msg_id:
                                edit_telegram_message_with_retry(CHANNEL_ID, msg_id, final_msg)
                            else:
                                sent = send_telegram_message_with_retry(CHANNEL_ID, final_msg)
                                msg_id = sent.message_id
                            
                            game_data.add_completed_game(game_number, final_msg)
                            game_data.update_last_number(game_number)
                            
                            logging.info(f"Стол #{game_number}: финал: {final_msg}")
                    
                    elif not game_finished and len(state['p_cards']) + len(state['d_cards']) > 0:
                        if state != last_state:
                            msg = format_message(game_number, state, is_final=False)
                            
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
            
            logging.info(f"Стол #{game_number}: сессия завершена")
            
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
    async def get_table():
        async with async_playwright() as p:
            browser = None
            try:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox"]
                )
                page = await browser.new_page()
                await page.goto(MAIN_URL, timeout=60000, wait_until="domcontentloaded")
                await page.wait_for_timeout(5000)
                
                url = await get_table_url(page)
                return url
            except Exception as e:
                logging.error(f"Ошибка при загрузке MAIN_URL: {e}")
                return None
            finally:
                if browser:
                    await browser.close()
    
    try:
        next_game_time, seconds_to_wait = get_next_game_time()
        
        if seconds_to_wait > 0:
            logging.info(f"Следующая игра в {next_game_time.strftime('%H:%M:%S')}, жду {seconds_to_wait:.0f} сек")
            time.sleep(seconds_to_wait)
        
        game_number = get_game_number_by_time(next_game_time)
        
        if game_data.is_game_completed(game_number):
            logging.info(f"Игра #{game_number} уже была записана, пропускаю")
            return
        
        with lock:
            if len(active_tables) >= MAX_BROWSERS:
                logging.info(f"Нет свободных браузеров, игра #{game_number} пропущена")
                return
        
        table_url = asyncio.run(get_table())
        
        if table_url:
            logging.info(f"Игра #{game_number}: запуск мониторинга")
            thread = threading.Thread(
                target=run_async_monitor, 
                args=(table_url, game_number, next_game_time)
            )
            thread.daemon = True
            thread.start()
            
            with lock:
                active_tables[game_number] = thread
            
            logging.info(f"Игра #{game_number}: мониторинг запущен")
        else:
            logging.warning(f"Игра #{game_number}: не удалось получить URL стола")
            
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
    logging.info("🚀 Бот 21 Classic запущен на Chromium")
    logging.info(f"Максимум браузеров: {MAX_BROWSERS}")
    
    check_interval = 10
    
    while bot_running:
        try:
            clean_threads()
            
            if len(active_tables) < MAX_BROWSERS:
                next_game_time, seconds_to_next = get_next_game_time()
                
                if seconds_to_next <= 30:
                    logging.info(f"Активных потоков: {len(active_tables)}/{MAX_BROWSERS}")
                    launch_next_game_monitor()
            
            time.sleep(check_interval)
            
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