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
MAIN_URL = "https://1xlite-9048339.bar/ru/live/twentyone/2092323-21-classics?platform_type=desktop"
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
active_tables = {}          # game_number -> thread
monitoring_games = set()    # множество игр, которые сейчас в мониторинге
message_ids = {}
last_messages = {}
lock = threading.Lock()
bot_running = True
searcher_busy = False

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
    """Расчет номера игры по времени (игры стартуют в ЧЕТНЫЕ минуты)"""
    if dt is None:
        dt = datetime.now()
    
    start_of_day = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    minutes_passed = (dt - start_of_day).total_seconds() / 60
    
    game_number = int(minutes_passed // 2) + 1
    return game_number

def get_next_game_time():
    """Возвращает время следующей игры (ближайшая ЧЕТНАЯ минута)"""
    now = datetime.now()
    
    if now.minute % 2 == 0:
        if now.second < 30:
            next_game_minute = now.minute
        else:
            next_game_minute = now.minute + 2
    else:
        next_game_minute = now.minute + 1
    
    next_game_hour = now.hour
    next_game_day = now.day
    
    if next_game_minute >= 60:
        next_game_minute -= 60
        next_game_hour += 1
        if next_game_hour >= 24:
            next_game_hour -= 24
            next_game_day += 1
    
    try:
        next_game_time = now.replace(
            day=next_game_day,
            hour=next_game_hour,
            minute=next_game_minute,
            second=0,
            microsecond=0
        )
    except ValueError:
        next_game_time = now + timedelta(minutes=(next_game_minute - now.minute) % 60)
        next_game_time = next_game_time.replace(second=0, microsecond=0)
    
    seconds_to_start = (next_game_time - now).total_seconds()
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

async def search_next_table():
    """Отдельный поисковик следующего стола (работает в фоне)"""
    global searcher_busy
    
    if searcher_busy:
        return None, None, None
    
    searcher_busy = True
    try:
        next_game_time, _ = get_next_game_time()
        game_number = get_game_number_by_time(next_game_time)
        
        # ПРОВЕРКА: не мониторим ли мы уже эту игру?
        with lock:
            if game_number in monitoring_games:
                logging.info(f"⚠️ Поисковик: игра #{game_number} уже в мониторинге, пропускаем")
                return None, None, None
        
        logging.info(f"🔍 Поисковик ищет стол #{game_number}")
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--js-flags=--max-old-space-size=256",
                    "--blink-settings=imagesEnabled=false",
                    "--disable-remote-fonts"
                ]
            )
            page = await browser.new_page()
            
            async def block_resources(route):
                if route.request.resource_type in ['image', 'stylesheet', 'font', 'media']:
                    await route.abort()
                else:
                    await route.continue_()
            
            await page.route('**/*', block_resources)
            await page.goto(MAIN_URL, timeout=30000, wait_until="domcontentloaded")
            
            # Быстрый поиск с несколькими попытками
            for attempt in range(5):
                tables = await page.query_selector_all('.dashboard-game-block')
                
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
                                            href = f"https://1xlite-9048339.bar{href}"
                                        
                                        # ФИНАЛЬНАЯ ПРОВЕРКА перед отправкой
                                        with lock:
                                            if game_number in monitoring_games:
                                                logging.info(f"⚠️ Поисковик: игра #{game_number} уже занята, отказываемся")
                                                return None, None, None
                                        
                                        logging.info(f"✅ Поисковик нашёл стол #{game_number}")
                                        return href, game_number, next_game_time
                    except:
                        continue
                
                await asyncio.sleep(2)
            
            logging.warning(f"❌ Поисковик не нашёл стол #{game_number}")
            return None, None, None
            
    except Exception as e:
        logging.error(f"Ошибка в поисковике: {e}")
        return None, None, None
    finally:
        searcher_busy = False

async def monitor_table(table_url, game_number, game_start_time):
    """Мониторинг конкретного стола"""
    
    # Отмечаем, что игра пошла в мониторинг
    with lock:
        monitoring_games.add(game_number)
    
    msg_id = None
    last_state = None
    browser = None
    page = None
    game_finished = False
    first_message_sent = False
    start_time = time.time()
    max_duration = 240
    
    game_real_start = game_start_time.timestamp()
    ignore_finish_until = game_real_start + 15
    
    logging.info(f"🎮 Стол #{game_number}: начало мониторинга")
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--js-flags=--max-old-space-size=256",
                    "--blink-settings=imagesEnabled=false",
                    "--disable-remote-fonts",
                    "--disable-default-apps",
                    "--disable-translate",
                    "--disable-sync",
                    "--disable-extensions"
                ]
            )
            
            page = await browser.new_page()
            
            async def block_resources(route):
                if route.request.resource_type in ['image', 'stylesheet', 'font', 'media']:
                    await route.abort()
                else:
                    await route.continue_()
            
            await page.route('**/*', block_resources)
            await page.goto(table_url, timeout=30000, wait_until="domcontentloaded")
            logging.info(f"🎮 Стол #{game_number}: страница загружена")
            
            try:
                await page.wait_for_selector('.live-twenty-one-cards', timeout=5000)
            except:
                pass
            
            for i in range(5):
                state = await get_state_fast(page)
                if state and (len(state['p_cards']) > 0 or len(state['d_cards']) > 0):
                    logging.info(f"🎮 Стол #{game_number}: карты появились")
                    break
                await asyncio.sleep(0.5)
            
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
                        logging.info(f"🎮 Стол #{game_number}: игра завершена")
                        
                        await asyncio.sleep(1)
                        
                        final_state = None
                        for attempt in range(10):
                            final_state = await get_state_fast(page)
                            if final_state and (len(final_state['p_cards']) > 0 or len(final_state['d_cards']) > 0):
                                break
                            await asyncio.sleep(0.2)
                        
                        if final_state:
                            final_msg = format_message(game_number, final_state, is_final=True)
                            
                            if msg_id:
                                edit_telegram_message_with_retry(CHANNEL_ID, msg_id, final_msg)
                            else:
                                sent = send_telegram_message_with_retry(CHANNEL_ID, final_msg)
                                msg_id = sent.message_id
                            
                            game_data.add_completed_game(game_number, final_msg)
                            game_data.update_last_number(game_number)
                            
                            logging.info(f"🎮 Стол #{game_number}: ФИНАЛ ОТПРАВЛЕН")
                            break
                    
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
                                
                                last_state = state
                    
                    await asyncio.sleep(0.3)
                    
                except Exception as e:
                    if "closed" in str(e).lower():
                        break
                    else:
                        logging.error(f"Ошибка в цикле стола #{game_number}: {e}")
                        await asyncio.sleep(1)
            
    except Exception as e:
        logging.error(f"Критическая ошибка стола #{game_number}: {e}")
    finally:
        if browser:
            await browser.close()
        
        with lock:
            if game_number in active_tables:
                del active_tables[game_number]
            monitoring_games.discard(game_number)
            if game_number in message_ids:
                del message_ids[game_number]
            if game_number in last_messages:
                del last_messages[game_number]
        
        logging.info(f"🎮 Стол #{game_number}: мониторинг завершён")

def run_async_monitor(table_url, game_number, game_start_time):
    try:
        asyncio.run(monitor_table(table_url, game_number, game_start_time))
    except Exception as e:
        logging.error(f"Ошибка в потоке мониторинга стола #{game_number}: {e}")

def launch_monitor(table_url, game_number, game_start_time):
    """Запускает мониторинг в отдельном потоке с проверкой на дубли"""
    with lock:
        # ТРОЙНАЯ ПРОВЕРКА
        if game_number in monitoring_games:
            logging.info(f"⚠️ Игра #{game_number} уже в monitoring_games, пропускаем")
            return
        if game_number in active_tables:
            logging.info(f"⚠️ Игра #{game_number} уже в active_tables, пропускаем")
            return
        if game_data.is_game_completed(game_number):
            logging.info(f"Игра #{game_number} уже завершена, пропускаем")
            return
        
        # Резервируем игру
        monitoring_games.add(game_number)
    
    thread = threading.Thread(
        target=run_async_monitor, 
        args=(table_url, game_number, game_start_time)
    )
    thread.daemon = True
    thread.start()
    
    with lock:
        active_tables[game_number] = thread
    
    logging.info(f"✅ Игра #{game_number}: мониторинг запущен (активных: {len(active_tables)}/{MAX_BROWSERS})")

def clean_threads():
    with lock:
        dead = [gid for gid, t in active_tables.items() if not t.is_alive()]
        for gid in dead:
            del active_tables[gid]
            monitoring_games.discard(gid)
            if gid in message_ids:
                del message_ids[gid]
            if gid in last_messages:
                del last_messages[gid]
            logging.info(f"🧹 Поток игры #{gid} очищен")

def monitor_loop():
    global bot_running
    logging.info("🚀 Бот 21 Classic запущен (СТРОГОЕ РАСПРЕДЕЛЕНИЕ СТОЛОВ)")
    logging.info(f"Максимум браузеров: {MAX_BROWSERS}")
    
    last_search_time = 0
    
    while bot_running:
        try:
            clean_threads()
            
            # Если есть свободные браузеры
            if len(active_tables) < MAX_BROWSERS and (time.time() - last_search_time) > 30:
                # Запускаем поисковик
                threading.Thread(target=lambda: asyncio.run(search_and_launch()), daemon=True).start()
                last_search_time = time.time()
            
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

async def search_and_launch():
    """Ищет и запускает следующий стол"""
    url, game_number, game_time = await search_next_table()
    if url and game_number:
        launch_monitor(url, game_number, game_time)

def main():
    monitor_loop()

if __name__ == "__main__":
    main()