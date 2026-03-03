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

# ===== ПУЛ БРАУЗЕРОВ =====
class BrowserPool:
    def __init__(self, size=3):
        self.size = size
        self.browsers = []
        self.available = []
        self.busy = {}
        self.lock = threading.Lock()
        self.playwright = None
        self.running = True
    
    async def start(self):
        """Запускает пул и создает браузеры"""
        try:
            self.playwright = await async_playwright().start()
            for i in range(self.size):
                try:
                    browser = await self.playwright.chromium.launch(
                        headless=True,
                        args=["--no-sandbox"]
                    )
                    self.browsers.append(browser)
                    self.available.append(browser)
                    logging.info(f"✅ Браузер {i+1}/{self.size} запущен")
                except Exception as e:
                    logging.error(f"Ошибка запуска браузера {i+1}: {e}")
            
            logging.info(f"✅ Всего запущено: {len(self.browsers)}/{self.size} браузеров")
            return len(self.browsers) > 0
        except Exception as e:
            logging.error(f"Ошибка запуска пула: {e}")
            return False
    
    async def get_browser(self, game_number):
        with self.lock:
            if not self.available:
                return None
            browser = self.available.pop()
            self.busy[game_number] = browser
            return browser
    
    async def release_browser(self, game_number):
        with self.lock:
            if game_number in self.busy:
                browser = self.busy.pop(game_number)
                self.available.append(browser)
                logging.info(f"🔄 Браузер для игры #{game_number} освобожден")
    
    async def stop_all(self):
        self.running = False
        for browser in self.browsers:
            try:
                await browser.close()
            except:
                pass
        if self.playwright:
            await self.playwright.stop()
        logging.info("🛑 Все браузеры закрыты")
# =========================

# ===== НАСТРОЙКИ =====
TOKEN = "8357635747:AAGAH_Rwk-vR8jGa6Q9F-AJLsMaEIj-JDBU"
CHANNEL_ID = "-1003179573402"
MAIN_URL = "https://1xlite-7636770.bar/ru/live/twentyone/2092323-21-classics"
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
active_games = {}
message_ids = {}
last_messages = {}
lock = threading.Lock()
bot_running = True
pool = None

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

async def get_active_tables(page):
    tables = []
    
    try:
        await page.wait_for_selector('.dashboard-game-block', timeout=30000)
        await page.wait_for_timeout(2000)
        
        blocks = await page.query_selector_all('.dashboard-game-block')
        
        for block in blocks:
            try:
                number = None
                number_elem = await block.query_selector('.dashboard-game-info__additional-info')
                if number_elem:
                    text = await number_elem.text_content()
                    match = re.search(r'(\d+)', text)
                    if match:
                        number = int(match.group(1))
                
                if not number:
                    continue
                
                timer = await block.query_selector('.dashboard-game-info__time')
                timer_text = await timer.text_content() if timer else "00:00"
                
                scores = await block.query_selector_all('.ui-game-scores__num')
                p_score = await scores[0].text_content() if len(scores) > 0 else "0"
                d_score = await scores[1].text_content() if len(scores) > 1 else "0"
                
                completed = await block.query_selector('.dashboard-game-info__period:has-text("Игра завершена")')
                
                link = await block.query_selector('.dashboard-game-block__link')
                href = await link.get_attribute('href') if link else None
                if href and not href.startswith('http'):
                    href = f"https://1xlite-7636770.bar{href}"
                
                tables.append({
                    'number': number,
                    'timer': timer_text,
                    'p_score': p_score,
                    'd_score': d_score,
                    'completed': bool(completed),
                    'url': href
                })
                
            except Exception as e:
                continue
                
    except Exception as e:
        logging.error(f"Ошибка при парсинге столов: {e}")
    
    return tables

async def monitor_game(browser, game_number, game_url, start_time):
    msg_id = None
    last_state = None
    game_finished = False
    first_message_sent = False
    start_real = time.time()
    ignore_finish_until = start_time + 15
    
    page = await browser.new_page()
    
    try:
        await page.goto(game_url, timeout=60000, wait_until="domcontentloaded")
        logging.info(f"Игра #{game_number}: страница загружена")
        
        # Ждем появления карт (до 10 секунд)
        for i in range(10):
            state = await get_state_fast(page)
            if state and (len(state['p_cards']) > 0 or len(state['d_cards']) > 0):
                logging.info(f"Игра #{game_number}: карты появились через {i+1} сек")
                break
            await asyncio.sleep(1)
        
        while not game_finished and (time.time() - start_real) < 240:
            try:
                current_time = time.time()
                
                if current_time < ignore_finish_until:
                    is_finished = False
                else:
                    is_finished = await is_game_truly_finished(page)
                
                if is_finished and not game_finished:
                    game_finished = True
                    logging.info(f"Игра #{game_number}: завершена")
                    
                    await asyncio.sleep(2)
                    
                    final_state = None
                    for attempt in range(15):
                        try:
                            final_state = await get_state_fast(page)
                            if final_state and (len(final_state['p_cards']) > 0 or len(final_state['d_cards']) > 0):
                                break
                        except:
                            pass
                        await asyncio.sleep(0.3)
                    
                    if final_state:
                        final_msg = format_message(game_number, final_state, is_final=True)
                        
                        if msg_id:
                            edit_telegram_message_with_retry(CHANNEL_ID, msg_id, final_msg)
                        else:
                            sent = send_telegram_message_with_retry(CHANNEL_ID, final_msg)
                            msg_id = sent.message_id
                        
                        game_data.add_completed_game(game_number, final_msg)
                        logging.info(f"Игра #{game_number}: финал отправлен")
                        break
                
                elif not game_finished:
                    state = await get_state_fast(page)
                    turn = await determine_turn(page)
                    
                    if state and (len(state['p_cards']) > 0 or len(state['d_cards']) > 0):
                        if state != last_state:
                            msg = format_message(game_number, state, turn=turn)
                            
                            if msg_id and first_message_sent:
                                edit_telegram_message_with_retry(CHANNEL_ID, msg_id, msg)
                            elif not first_message_sent:
                                sent = send_telegram_message_with_retry(CHANNEL_ID, msg)
                                msg_id = sent.message_id
                                first_message_sent = True
                                logging.info(f"Игра #{game_number}: первое сообщение")
                            
                            last_state = state
                
                await asyncio.sleep(0.3)
                
            except Exception as e:
                if "closed" in str(e).lower():
                    break
                logging.error(f"Ошибка в игре #{game_number}: {e}")
                await asyncio.sleep(1)
    
    except Exception as e:
        logging.error(f"Критическая ошибка в игре #{game_number}: {e}")
    finally:
        try:
            await page.close()
        except:
            pass
        await pool.release_browser(game_number)
        logging.info(f"Игра #{game_number}: завершена, браузер освобожден")

async def watcher():
    global pool, bot_running
    logging.info("👀 Наблюдатель запускается...")
    
    retry_count = 0
    max_retries = 10
    
    while bot_running and retry_count < max_retries:
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
                page = await browser.new_page()
                logging.info("👀 Наблюдатель: браузер запущен")
                
                # Сбрасываем счетчик ошибок при успешном запуске
                retry_count = 0
                
                while bot_running:
                    try:
                        await page.goto(MAIN_URL, timeout=60000, wait_until="domcontentloaded")
                        tables = await get_active_tables(page)
                        
                        for table in tables:
                            game_num = table['number']
                            
                            if table['completed']:
                                continue
                            
                            with lock:
                                if game_num in active_games:
                                    continue
                                if game_data.is_game_completed(game_num):
                                    continue
                            
                            timer = table['timer']
                            if timer and timer != "00:00" and ':' in timer:
                                try:
                                    minutes, seconds = map(int, timer.split(':'))
                                    total_seconds = minutes * 60 + seconds
                                    
                                    if total_seconds <= 40 and total_seconds > 0:
                                        browser_worker = await pool.get_browser(game_num)
                                        
                                        if browser_worker:
                                            logging.info(f"🎯 Игра #{game_num}: старт через {total_seconds} сек")
                                            
                                            with lock:
                                                active_games[game_num] = True
                                            
                                            start_time = time.time() + total_seconds
                                            asyncio.create_task(
                                                monitor_game(browser_worker, game_num, table['url'], start_time)
                                            )
                                        else:
                                            logging.warning(f"⚠️ Нет свободных браузеров для игры #{game_num}")
                                except:
                                    pass
                        
                        await asyncio.sleep(5)
                        
                    except Exception as e:
                        logging.error(f"Ошибка наблюдателя: {e}")
                        await asyncio.sleep(10)
                        break  # перезапустим браузер
                
                await browser.close()
                logging.info("👀 Наблюдатель: браузер закрыт, перезапуск...")
                
        except Exception as e:
            logging.error(f"Критическая ошибка наблюдателя: {e}")
            retry_count += 1
            logging.info(f"Попытка перезапуска {retry_count}/{max_retries}")
            await asyncio.sleep(30)
    
    logging.error("👀 Наблюдатель остановлен после 10 попыток")

def run_watcher():
    asyncio.run(watcher())

def main():
    global pool, bot_running
    
    logging.info("🚀 Запуск бота с пулом браузеров")
    
    # Сначала создаем пул
    pool = BrowserPool(size=3)
    try:
        # Запускаем пул в отдельном цикле событий
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        success = loop.run_until_complete(pool.start())
        loop.close()
        
        if not success:
            logging.error("❌ Не удалось запустить пул браузеров")
            return
        logging.info("✅ Пул браузеров запущен")
    except Exception as e:
        logging.error(f"❌ Ошибка запуска пула: {e}")
        return
    
    # Потом запускаем наблюдателя
    watcher_thread = threading.Thread(target=run_watcher)
    watcher_thread.daemon = True
    watcher_thread.start()
    logging.info("👀 Наблюдатель запущен в отдельном потоке")
    
    # Основной цикл
    try:
        while bot_running:
            time.sleep(10)
            # Проверяем, жив ли наблюдатель
            if not watcher_thread.is_alive():
                logging.error("👀 Наблюдатель умер, перезапускаем...")
                watcher_thread = threading.Thread(target=run_watcher)
                watcher_thread.daemon = True
                watcher_thread.start()
    except KeyboardInterrupt:
        logging.info("Получен сигнал завершения")
        bot_running = False
    
    # Останавливаем пул
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(pool.stop_all())
        loop.close()
    except:
        pass
    
    game_data.save_data()
    logging.info("Бот остановлен")

if __name__ == "__main__":
    main()