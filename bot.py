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
MAX_CONCURRENT_GAMES = 5
DATA_FILE = "game_data.pkl"
DATA_RETENTION_DAYS = 3
POLL_INTERVAL = 2
MAX_POLL_ATTEMPTS = 30
# =====================

apihelper.RETRY_ON_ERROR = True
apihelper.MAX_RETRIES = 5

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

bot = telebot.TeleBot(TOKEN)
active_games = {}
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
    """Расчет номера игры по времени (игры в четные минуты)"""
    if dt is None:
        dt = datetime.now()
    
    start_of_day = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    minutes_passed = (dt - start_of_day).total_seconds() / 60
    
    if dt.minute % 2 == 0:
        game_number = int(minutes_passed // 2) + 1
    else:
        game_number = int((minutes_passed - 1) // 2) + 1
    
    return game_number

def get_next_game_time():
    """Возвращает время следующей игры (в четную минуту)"""
    now = datetime.now()
    
    if now.minute % 2 == 0:
        next_game_minute = now.minute + 2
    else:
        next_game_minute = now.minute + 1
    
    next_game_hour = now.hour
    if next_game_minute >= 60:
        next_game_minute = next_game_minute % 60
        next_game_hour = (next_game_hour + 1) % 24
    
    next_game_time = now.replace(
        hour=next_game_hour,
        minute=next_game_minute,
        second=0,
        microsecond=0
    )
    
    seconds_to_start = (next_game_time - now).total_seconds()
    
    return next_game_time, max(0, seconds_to_start)

def format_cards(cards):
    """Форматирование списка карт в строку"""
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
    """Форматирование сообщения для Telegram (БЕЗ СТРЕЛОЧЕК)"""
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
        # ПРОСТОЙ ФОРМАТ БЕЗ СТРЕЛОЧЕК
        return f"#N{game_number} {state['p_score']}({p_cards})-{state['d_score']}({d_cards}) #T{total_score}"

async def extract_cards_from_container(container):
    """Извлечение карт из контейнера"""
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

async def get_state_from_page(page):
    """Получение состояния игры со страницы"""
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
        logging.error(f"Ошибка в get_state_from_page: {e}")
        return None

async def is_game_finished(page):
    """Проверка, завершена ли игра"""
    try:
        timer_div = await page.query_selector('.ui-game-timer__label')
        if timer_div:
            text = await timer_div.text_content()
            if text and "Игра завершена" in text:
                return True
        
        status = await page.query_selector('.live-twenty-one-table-head__status')
        if status:
            text = await status.text_content()
            if 'Победа' in text or 'победил' in text.lower():
                return True
        
        replay_btn = await page.query_selector('.ui-game-replay__btn')
        if replay_btn:
            return True
        
        return False
    except Exception as e:
        logging.error(f"Ошибка в is_game_finished: {e}")
        return False

def send_telegram_message(chat_id, text):
    """Отправка сообщения с повторными попытками"""
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
                if attempt == max_retries - 1:
                    raise
                time.sleep(2)
    raise Exception(f"Не удалось отправить сообщение")

def edit_telegram_message(chat_id, message_id, text):
    """Редактирование сообщения с повторными попытками"""
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
                if attempt == max_retries - 1:
                    logging.error(f"Ошибка при редактировании: {e}")
                    return None
                time.sleep(2)
    return None

async def get_table_url(page, target_game_number):
    """Получение URL стола по номеру игры"""
    try:
        logging.info(f"🔍 Ищем стол №{target_game_number}...")
        
        await page.wait_for_selector('.dashboard-game-block', timeout=30000)
        await page.wait_for_timeout(2000)
        
        tables = await page.query_selector_all('.dashboard-game-block')
        logging.info(f"📊 Всего столов на странице: {len(tables)}")
        
        for table in tables:
            try:
                info_elem = await table.query_selector('.dashboard-game-info__additional-info')
                if info_elem:
                    text = await info_elem.text_content()
                    match = re.search(r'(\d+)', text)
                    if match:
                        current_number = int(match.group(1))
                        
                        if current_number == target_game_number:
                            link_element = await table.query_selector('.dashboard-game-block__link')
                            if link_element:
                                href = await link_element.get_attribute('href')
                                if href and not href.startswith('http'):
                                    href = f"https://1xlite-7636770.bar{href}"
                                
                                logging.info(f"✅ Найден стол #{current_number}")
                                return href
            except:
                continue
        
        logging.warning(f"❌ Стол #{target_game_number} не найден")
        return None
        
    except Exception as e:
        logging.error(f"Ошибка в get_table_url: {e}")
        return None

async def poll_game(table_url, game_number, game_start_time):
    """Опрос игры с заданным интервалом"""
    
    msg_id = None
    last_state = None
    first_message_sent = False
    game_finished = False
    game_started = False
    start_time = time.time()
    max_wait_for_start = 30
    
    logging.info(f"🎮 Игра #{game_number}: начало опроса (старт в {game_start_time.strftime('%H:%M:%S')})")
    
    for attempt in range(MAX_POLL_ATTEMPTS):
        if not bot_running:
            break
            
        browser = None
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage"]
                )
                page = await browser.new_page()
                page.set_default_timeout(30000)
                
                await page.goto(table_url, timeout=30000, wait_until="domcontentloaded")
                
                state = await get_state_from_page(page)
                
                if not state:
                    logging.warning(f"Игра #{game_number}: не удалось получить состояние")
                    continue
                
                has_cards = len(state['p_cards']) > 0 or len(state['d_cards']) > 0
                
                if has_cards and not game_started:
                    game_started = True
                    logging.info(f"Игра #{game_number}: КАРТЫ ПОЯВИЛИСЬ! П:{len(state['p_cards'])} Д:{len(state['d_cards'])}")
                
                is_finished = await is_game_finished(page)
                
                if game_started:
                    if state != last_state:
                        if not first_message_sent:
                            msg_text = format_message(game_number, state, is_final=False)
                            sent = send_telegram_message(CHANNEL_ID, msg_text)
                            msg_id = sent.message_id
                            first_message_sent = True
                            logging.info(f"Игра #{game_number}: ПЕРВОЕ СООБЩЕНИЕ: {msg_text}")
                        else:
                            msg_text = format_message(game_number, state, is_final=False)
                            edit_telegram_message(CHANNEL_ID, msg_id, msg_text)
                            logging.info(f"Игра #{game_number}: ОБНОВЛЕНО: {msg_text}")
                        
                        last_state = state
                    
                    if is_finished:
                        logging.info(f"Игра #{game_number}: обнаружено завершение")
                        await asyncio.sleep(1)
                        
                        final_state = await get_state_from_page(page)
                        if final_state:
                            final_msg = format_message(game_number, final_state, is_final=True)
                            
                            if msg_id:
                                edit_telegram_message(CHANNEL_ID, msg_id, final_msg)
                            else:
                                sent = send_telegram_message(CHANNEL_ID, final_msg)
                            
                            game_data.add_completed_game(game_number, final_msg)
                            game_data.update_last_number(game_number)
                            
                            logging.info(f"✅ Игра #{game_number}: ФИНАЛ: {final_msg}")
                            game_finished = True
                            break
                else:
                    logging.info(f"Игра #{game_number}: ожидание начала... ({attempt + 1}/{MAX_POLL_ATTEMPTS})")
                    
                    if time.time() - game_start_time.timestamp() > max_wait_for_start:
                        logging.warning(f"Игра #{game_number}: игра не началась через {max_wait_for_start} сек")
                        # Пробуем обновить URL
                        new_url = await get_table_url(page, game_number)
                        if new_url and new_url != table_url:
                            logging.info(f"Игра #{game_number}: получен новый URL, продолжаем")
                            table_url = new_url
                        else:
                            break
                
                if time.time() - start_time > 120:
                    logging.warning(f"Игра #{game_number}: превышено общее время")
                    break
                
        except Exception as e:
            logging.error(f"Игра #{game_number}: ошибка при опросе: {e}")
        finally:
            if browser:
                await browser.close()
        
        await asyncio.sleep(POLL_INTERVAL)
    
    if not game_started and not first_message_sent:
        logging.warning(f"Игра #{game_number}: игра не началась, сообщение не отправлено")
    elif not game_finished and first_message_sent and last_state:
        logging.warning(f"Игра #{game_number}: принудительный финал")
        final_msg = format_message(game_number, last_state, is_final=True)
        edit_telegram_message(CHANNEL_ID, msg_id, final_msg)
        game_data.add_completed_game(game_number, final_msg)
    
    with lock:
        if game_number in active_games:
            del active_games[game_number]
    
    logging.info(f"Игра #{game_number}: опрос завершен")

def run_polling(table_url, game_number, game_start_time):
    """Запуск опроса в отдельном потоке"""
    try:
        asyncio.run(poll_game(table_url, game_number, game_start_time))
    except Exception as e:
        logging.error(f"Критическая ошибка в игре #{game_number}: {e}")

def launch_next_game():
    """Запуск мониторинга для следующей игры"""
    async def get_game_info():
        async with async_playwright() as p:
            browser = None
            try:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage"]
                )
                page = await browser.new_page()
                
                await page.goto(MAIN_URL, timeout=30000, wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)
                
                next_game_time, _ = get_next_game_time()
                game_number = get_game_number_by_time(next_game_time)
                
                url = await get_table_url(page, game_number)
                return url, game_number, next_game_time
                
            except Exception as e:
                logging.error(f"Ошибка при получении информации об игре: {e}")
                return None, None, None
            finally:
                if browser:
                    await browser.close()
    
    try:
        table_url, game_number, game_time = asyncio.run(get_game_info())
        
        if not table_url or not game_number:
            logging.warning("⚠️ Не удалось получить URL стола")
            return
        
        with lock:
            if game_number in active_games:
                logging.info(f"Игра #{game_number} уже мониторится")
                return
            if game_data.is_game_completed(game_number):
                logging.info(f"Игра #{game_number} уже завершена")
                return
        
        logging.info(f"🚀 Игра #{game_number}: запуск опроса (старт в {game_time.strftime('%H:%M:%S')})")
        
        thread = threading.Thread(
            target=run_polling, 
            args=(table_url, game_number, game_time)
        )
        thread.daemon = True
        thread.start()
        
        with lock:
            active_games[game_number] = {
                'thread': thread,
                'start_time': game_time
            }
        
        logging.info(f"✅ Игра #{game_number}: опрос запущен (активных: {len(active_games)}/{MAX_CONCURRENT_GAMES})")
            
    except Exception as e:
        logging.error(f"Ошибка при запуске опроса: {e}")

def clean_finished_games():
    """Очистка завершенных игр"""
    with lock:
        finished = []
        for game_number, game_info in active_games.items():
            if not game_info['thread'].is_alive():
                finished.append(game_number)
        
        for game_number in finished:
            del active_games[game_number]
            logging.info(f"Игра #{game_number} очищена")

def check_stuck_games():
    """Проверка зависших игр"""
    with lock:
        for game_number, game_info in list(active_games.items()):
            if hasattr(game_info, 'start_time'):
                elapsed = (datetime.now() - game_info['start_time']).total_seconds()
                if elapsed > 120:
                    logging.warning(f"Игра #{game_number} возможно зависла ({elapsed:.0f} сек)")

def monitor_loop():
    """Основной цикл мониторинга"""
    global bot_running
    logging.info("🚀 Бот 21 Classic (polling mode) запущен")
    logging.info("🎯 Игры начинаются в четные минуты (0,2,4,6...)")
    logging.info(f"Максимум одновременных игр: {MAX_CONCURRENT_GAMES}")
    logging.info(f"Интервал опроса: {POLL_INTERVAL} сек")
    
    last_launch_time = 0
    last_game_launched = None
    
    while bot_running:
        try:
            clean_finished_games()
            
            now = datetime.now()
            current_minute = now.minute
            current_second = now.second
            
            if current_second == 0:
                logging.info(f"⏰ Текущее время: {now.strftime('%H:%M:%S')}, минута: {current_minute}")
            
            if (current_minute % 2 == 1) and current_second >= 55:
                next_game_time, seconds_to_next = get_next_game_time()
                current_time = time.time()
                
                if seconds_to_next <= 10 and (current_time - last_launch_time) > 55:
                    game_number = get_game_number_by_time(next_game_time)
                    
                    if last_game_launched != game_number:
                        logging.info(f"⏰ До игры #{game_number} осталось {seconds_to_next:.0f} сек")
                        
                        if len(active_games) < MAX_CONCURRENT_GAMES:
                            launch_next_game()
                            last_launch_time = current_time
                            last_game_launched = game_number
                        else:
                            logging.warning(f"⚠️ Достигнут лимит одновременных игр ({MAX_CONCURRENT_GAMES})")
                        
                        time.sleep(5)
            
            if current_second % 10 == 0:
                check_stuck_games()
            
            time.sleep(1)
            
        except KeyboardInterrupt:
            logging.info("Получен сигнал завершения")
            bot_running = False
            break
        except Exception as e:
            logging.error(f"Ошибка в основном цикле: {e}")
            time.sleep(5)
    
    game_data.save_data()
    logging.info("Бот остановлен")

def main():
    monitor_loop()

if __name__ == "__main__":
    main()