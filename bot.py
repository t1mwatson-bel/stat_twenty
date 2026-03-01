import asyncio
import threading
import time
import re
import logging
import random
from datetime import datetime, timedelta
import zendriver as zd
from selenium.common.exceptions import StaleElementReferenceException, NoSuchElementException, TimeoutException
import telebot
import pickle
import os
from telebot import apihelper

# ===== НАСТРОЙКИ =====
TOKEN = "8357635747:AAGAH_Rwk-vR8jGa6Q9F-AJLsMaEIj-JDBU"
CHANNEL_ID = "-1003179573402"
MAIN_URL = "https://1xlite-7636770.bar/ru/live/twentyone/1643503-twentyone-game"
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
active_tables = {}  # {table_id: thread}
message_ids = {}    # {table_id: message_id}
table_browsers = {}  # {table_id: browser}
last_messages = {}  # {table_id: last_message_text}
table_positions = {}  # {table_id: position} - 1 или 2
lock = threading.Lock()

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

async def create_browser():
    """Создание нового браузера через Zendriver"""
    try:
        browser = await zd.start(
            headless=True,
            browser_args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu']
        )
        return browser
    except Exception as e:
        logging.error(f"Ошибка создания браузера: {e}")
        return None

def parse_cards(card_elements):
    cards = []
    for el in card_elements:
        try:
            cls = ' '.join(el.attributes.get('class', []))
            suit = next((s for c, s in SUIT_MAP.items() if c in cls), '?')
            val_match = re.search(r'value-(\d+)', cls)
            if val_match:
                value = VALUE_MAP.get(f'value-{val_match.group(1)}', val_match.group(1))
            else:
                value = '?'
            cards.append(f"{value}{suit}")
        except Exception:
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
    """Быстрое получение состояния через Zendriver"""
    try:
        # Ждем загрузки основных элементов
        await page.wait_for('.live-twenty-one-field-player', timeout=5)
        
        # Очки игрока
        player_score_elem = await page.select('.live-twenty-one-field-player:first-child .live-twenty-one-field-score__label')
        player_score = await player_score_elem.text_content() if player_score_elem else "0"
        
        # Карты игрока
        player_card_elems = await page.select_all('.live-twenty-one-field-player:first-child .scoreboard-card-games-card')
        player_cards = parse_cards(player_card_elems)
        
        # Очки дилера
        dealer_score_elem = await page.select('.live-twenty-one-field-player:last-child .live-twenty-one-field-score__label')
        dealer_score = await dealer_score_elem.text_content() if dealer_score_elem else "0"
        
        # Карты дилера
        dealer_card_elems = await page.select_all('.live-twenty-one-field-player:last-child .scoreboard-card-games-card')
        dealer_cards = parse_cards(dealer_card_elems)
        
        # Определяем активность
        player_active = False
        dealer_active = False
        try:
            player_area = await page.select('.live-twenty-one-field-player:first-child')
            if player_area:
                class_attr = player_area.attributes.get('class', [])
                if any('active' in c.lower() for c in class_attr):
                    player_active = True
            
            dealer_area = await page.select('.live-twenty-one-field-player:last-child')
            if dealer_area:
                class_attr = dealer_area.attributes.get('class', [])
                if any('active' in c.lower() for c in class_attr):
                    dealer_active = True
        except:
            pass
            
        return {
            'p_score': player_score.strip(),
            'p_cards': player_cards,
            'd_score': dealer_score.strip(),
            'd_cards': dealer_cards,
            'player_active': player_active,
            'dealer_active': dealer_active
        }
    except Exception as e:
        logging.error(f"Ошибка получения состояния: {e}")
        return None

async def is_game_truly_finished(page):
    """Проверка завершения игры"""
    try:
        finished_elem = await page.select('span.ui-caption--size-xl.ui-caption--weight-700.ui-caption--color-clr-strong.ui-caption')
        if finished_elem:
            text = await finished_elem.text_content()
            if text and 'Игра завершена' in text:
                return True
    except:
        pass
    return False

async def safe_close_browser(table_id):
    """Безопасное закрытие браузера"""
    try:
        with lock:
            if table_id in table_browsers:
                browser = table_browsers[table_id]
                if browser:
                    logging.info(f"Закрытие браузера для стола {table_id}")
                    await browser.stop()
                    del table_browsers[table_id]
    except Exception as e:
        logging.error(f"Ошибка при закрытии браузера стола {table_id}: {e}")

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

async def get_table_by_position(page, position):
    """Получить стол по позиции"""
    try:
        await page.wait_for('.dashboard-game-block', timeout=10)
        await asyncio.sleep(2)
        
        tables = await page.select_all('.dashboard-game-block')
        logging.info(f"Найдено столов: {len(tables)}")
        
        if len(tables) >= position:
            table = tables[position - 1]
            
            try:
                id_elem = await table.select('.dashboard-game-info__additional-info')
                table_id = await id_elem.text_content() if id_elem else ""
                table_id = table_id.strip() if table_id else ""
                
                link_elem = await table.select('.dashboard-game-block__link')
                if link_elem:
                    href = link_elem.attributes.get('href', '')
                    
                    match = re.search(r'(\d+)$', table_id)
                    numeric_id = match.group(1) if match else table_id
                    
                    logging.info(f"Стол по позиции {position}: ID {table_id}")
                    return href, numeric_id
            except Exception as e:
                logging.error(f"Ошибка при получении данных стола: {e}")
                return None, None
        else:
            logging.warning(f"Нет стола на позиции {position}")
            return None, None
    except Exception as e:
        logging.error(f"Ошибка при поиске стола: {e}")
        return None, None

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

async def monitor_table_async(table_url, table_id, position):
    browser = None
    last_state = None
    msg_id = None
    t_num = random.randint(30, 60)
    game_active = True
    no_response_count = 0
    max_no_response = 10
    table_number = int(table_id) % 1440
    if table_number == 0:
        table_number = 1440
    last_send_time = 0
    min_send_interval = 2
    initial_load = True
    last_activity_time = time.time()
    max_idle_time = 60
    verification_pending = False
    verification_start = 0
    last_turn = None
    transition_start = 0
    in_transition = False
    transition_timeout = 3

    logging.info(f"Браузер для позиции {position} начал мониторинг стола {table_id}")

    try:
        browser = await create_browser()
        if not browser:
            logging.error(f"Не удалось создать браузер для стола {table_id}.")
            return

        with lock:
            table_browsers[table_id] = browser
            table_positions[table_id] = position

        page = browser
        await page.get(table_url)
        
        # Ждем загрузки карт
        cards_loaded = False
        wait_start = time.time()
        max_wait = 30
        
        while not cards_loaded and (time.time() - wait_start) < max_wait:
            try:
                player_cards = await page.select_all('.live-twenty-one-field-player:first-child .scoreboard-card-games-card')
                if player_cards and len(player_cards) > 0:
                    cards_loaded = True
                    logging.info(f"Карты загружены для стола {table_id}")
                    break
                
                if await is_game_truly_finished(page):
                    logging.info(f"Игра на столе {table_id} уже завершена")
                    game_active = False
                    break
                    
                await asyncio.sleep(0.5)
            except Exception as e:
                await asyncio.sleep(0.5)
        
        if cards_loaded:
            await asyncio.sleep(1)
        
        logging.info(f"Старт мониторинга стола {table_id}")

        while game_active:
            try:
                current_time = time.time()
                
                if current_time - last_activity_time > max_idle_time:
                    if not await is_game_truly_finished(page):
                        logging.warning(f"Стол {table_id} бездействует, обновляем")
                        await page.reload()
                        await asyncio.sleep(3)
                        last_activity_time = current_time
                        continue
                
                state = await get_state_fast(page)
                
                if not state:
                    no_response_count += 1
                    if no_response_count >= max_no_response:
                        if await is_game_truly_finished(page):
                            logging.info(f"Стол {table_id} завершен")
                            game_active = False
                            break
                        else:
                            no_response_count = max_no_response - 3
                    await asyncio.sleep(2)
                    continue
                
                last_activity_time = current_time
                no_response_count = 0
                
                current_turn = determine_turn(state)
                
                if current_turn is None and last_turn is not None:
                    if not in_transition:
                        in_transition = True
                        transition_start = current_time
                        logging.info(f"Стол {table_id}: переход хода от {last_turn}")
                    
                    elif current_time - transition_start > transition_timeout:
                        logging.warning(f"Стол {table_id}: переход затянулся, обновляем")
                        await page.reload()
                        await asyncio.sleep(2)
                        in_transition = False
                        transition_start = 0
                        last_turn = None
                        continue

                elif current_turn is not None:
                    if in_transition:
                        logging.info(f"Стол {table_id}: переход завершен, ходит {current_turn}")
                        in_transition = False
                        transition_start = 0
                    
                    last_turn = current_turn
                
                if await is_game_truly_finished(page):
                    if not verification_pending:
                        verification_pending = True
                        verification_start = current_time
                        logging.info(f"Стол {table_id}: возможное завершение")
                        await asyncio.sleep(3)
                        continue
                    elif current_time - verification_start >= 3:
                        if await is_game_truly_finished(page):
                            logging.info(f"Стол {table_id}: завершение подтверждено")
                            final_state = await get_state_fast(page) or state
                            
                            if len(final_state['p_cards']) > 0 or len(final_state['d_cards']) > 0:
                                final_msg = format_message(table_id, final_state, is_final=True, 
                                                          t_num=t_num, table_number=table_number)
                                
                                try:
                                    with lock:
                                        if msg_id:
                                            edit_telegram_message_with_retry(CHANNEL_ID, msg_id, final_msg)
                                        else:
                                            sent = send_telegram_message_with_retry(CHANNEL_ID, final_msg)
                                            msg_id = sent.message_id
                                            message_ids[table_id] = msg_id
                                    
                                    game_data.add_completed_game(table_id, final_msg, t_num)
                                    game_data.update_last_number(table_number)
                                    logging.info(f"Стол {table_id} завершен")
                                except Exception as e:
                                    logging.error(f"Ошибка отправки финала: {e}")
                            
                            game_active = False
                            break
                        else:
                            logging.info(f"Стол {table_id}: ложное срабатывание")
                            verification_pending = False
                else:
                    verification_pending = False

                if state != last_state or initial_load:
                    cards_changed = False
                    if last_state:
                        if len(state['p_cards']) != len(last_state['p_cards']) or \
                           len(state['d_cards']) != len(last_state['d_cards']):
                            cards_changed = True
                    
                    if cards_changed or initial_load or (current_time - last_send_time) >= min_send_interval:
                        msg = format_message(table_id, state, table_number=table_number)
                        
                        with lock:
                            last_msg = last_messages.get(table_id)
                            if last_msg == msg and not initial_load and not cards_changed:
                                await asyncio.sleep(1)
                                continue
                        
                        try:
                            with lock:
                                if msg_id:
                                    result = edit_telegram_message_with_retry(CHANNEL_ID, msg_id, msg)
                                    if result is not None:
                                        last_messages[table_id] = msg
                                else:
                                    if len(state['p_cards']) > 0 or len(state['d_cards']) > 0:
                                        sent = send_telegram_message_with_retry(CHANNEL_ID, msg)
                                        msg_id = sent.message_id
                                        message_ids[table_id] = msg_id
                                        last_messages[table_id] = msg
                                        logging.info(f"Стол {table_id}: первое сообщение")
                            
                            if msg_id:
                                last_state = state
                                last_send_time = current_time
                                initial_load = False
                                
                        except Exception as e:
                            logging.error(f"Ошибка отправки: {e}")
                            await asyncio.sleep(2)

                await asyncio.sleep(1)

            except Exception as e:
                logging.error(f"Ошибка в цикле: {e}")
                await asyncio.sleep(2)

    except Exception as e:
        logging.error(f"Критическая ошибка: {e}")
    finally:
        if browser and game_active:
            try:
                await asyncio.sleep(3)
                if not await is_game_truly_finished(page):
                    state = await get_state_fast(page)
                    if state and (len(state['p_cards']) > 0 or len(state['d_cards']) > 0):
                        logging.warning(f"Стол {table_id}: принудительное завершение")
                        final_msg = format_message(table_id, state, is_final=True, 
                                                  t_num=t_num, table_number=table_number)
                        try:
                            with lock:
                                if msg_id:
                                    edit_telegram_message_with_retry(CHANNEL_ID, msg_id, final_msg)
                                else:
                                    send_telegram_message_with_retry(CHANNEL_ID, final_msg)
                        except:
                            pass
            except:
                pass
        
        await safe_close_browser(table_id)
        with lock:
            if table_id in active_tables:
                del active_tables[table_id]
            if table_id in message_ids:
                del message_ids[table_id]
            if table_id in last_messages:
                del last_messages[table_id]
            if table_id in table_positions:
                logging.info(f"Освободилась позиция {table_positions[table_id]} для нового браузера")
                del table_positions[table_id]
        
        logging.info(f"Мониторинг стола {table_id} завершен")

def monitor_table_thread(table_url, table_id, position):
    """Обертка для запуска асинхронной функции в потоке"""
    asyncio.run(monitor_table_async(table_url, table_id, position))

def launch_new_table_monitor():
    """Запустить мониторинг нового стола"""
    with lock:
        used_positions = set(table_positions.values())
        free_positions = []
        
        for pos in range(1, MAX_BROWSERS + 1):
            if pos not in used_positions:
                free_positions.append(pos)
        
        if not free_positions:
            logging.info("Нет свободных позиций для браузеров")
            return
        
        target_position = free_positions[0]
        logging.info(f"Свободна позиция {target_position}, запускаем браузер")
    
    async def find_and_launch():
        scan_browser = None
        try:
            scan_browser = await create_browser()
            if not scan_browser:
                logging.error("Не удалось создать браузер для поиска стола")
                return
            
            logging.info(f"Поиск стола для позиции {target_position}...")
            await scan_browser.get(MAIN_URL)
            
            table_url, table_id = await get_table_by_position(scan_browser, target_position)
            
            if table_url and table_id:
                logging.info(f"Найден стол {table_id} для позиции {target_position}")
                
                thread = threading.Thread(target=monitor_table_thread, args=(table_url, table_id, target_position))
                thread.daemon = True
                thread.start()
                
                with lock:
                    active_tables[table_id] = thread
                
                logging.info(f"Браузер для позиции {target_position} запущен на столе {table_id}")
            else:
                logging.warning(f"Не удалось найти стол для позиции {target_position}")
                
        except Exception as e:
            logging.error(f"Ошибка при запуске нового монитора: {e}")
        finally:
            if scan_browser:
                await scan_browser.stop()
    
    asyncio.run(find_and_launch())

def clean_threads():
    with lock:
        dead = [tid for tid, t in active_tables.items() if not t.is_alive()]
        for tid in dead:
            if tid in table_browsers:
                try:
                    # В синхронном контексте не можем вызвать async, просто удаляем ссылку
                    del table_browsers[tid]
                except:
                    pass
            del active_tables[tid]
            if tid in message_ids:
                del message_ids[tid]
            if tid in last_messages:
                del last_messages[tid]
            logging.info(f"Поток стола {tid} очищен")

def main():
    logging.info("🚀 Бот запущен с Zendriver (позиции 1 и 2)")
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
    
    logging.info("Завершение работы бота...")
    
    async def close_all():
        with lock:
            for browser in table_browsers.values():
                try:
                    await browser.stop()
                except:
                    pass
    
    asyncio.run(close_all())
    game_data.save_data()

if __name__ == "__main__":
    main()
