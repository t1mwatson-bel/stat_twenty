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
        # Получаем счет игрока
        player_score_el = await page.query_selector('.live-twenty-one-field-player:first-child .live-twenty-one-field-score__label')
        player_score = await player_score_el.text_content() if player_score_el else '0'
        
        # Получаем карты игрока - улучшенный поиск
        player_cards = await extract_cards(page, '.live-twenty-one-field-player:first-child')
        
        # Получаем счет дилера
        dealer_score_el = await page.query_selector('.live-twenty-one-field-player:last-child .live-twenty-one-field-score__label')
        dealer_score = await dealer_score_el.text_content() if dealer_score_el else '0'
        
        # Получаем карты дилера - улучшенный поиск
        dealer_cards = await extract_cards(page, '.live-twenty-one-field-player:last-child')
        
        # Определяем активность
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
        logging.error(f"Ошибка в get_state_fast: {e}")
        return None

async def extract_cards(page, selector_prefix):
    """Улучшенная функция извлечения карт с поддержкой разных селекторов"""
    cards = []
    
    # Пробуем разные селекторы для поиска карт
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
                        
                        # Пропускаем скрытые или рубашки
                        if 'hidden' in class_name.lower() or 'face-down' in class_name.lower():
                            continue
                        
                        # Определяем масть
                        suit = '?'
                        if 'suit-0' in class_name:
                            suit = '♠️'
                        elif 'suit-1' in class_name:
                            suit = '♣️'
                        elif 'suit-2' in class_name:
                            suit = '♦️'
                        elif 'suit-3' in class_name:
                            suit = '♥️'
                        
                        # Определяем значение
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
                            # Пробуем найти значение в тексте
                            text = await el.text_content()
                            if text and text.strip():
                                value = text.strip()
                            else:
                                value = '?'
                        
                        cards.append(f"{value}{suit}")
                    except:
                        continue
                
                if cards:  # Если нашли карты, выходим
                    break
        except:
            continue
    
    return cards

async def has_active_player(page):
    """Проверяет, есть ли активный игрок или дилер"""
    try:
        player_area = await page.query_selector('.live-twenty-one-field-player:first-child')
        dealer_area = await page.query_selector('.live-twenty-one-field-player:last-child')
        
        if player_area:
            class_name = await player_area.get_attribute('class') or ''
            if 'active' in class_name.lower():
                return True
        
        if dealer_area:
            class_name = await dealer_area.get_attribute('class') or ''
            if 'active' in class_name.lower():
                return True
        
        return False
    except:
        return False

async def has_action_buttons(page):
    """Проверяет наличие кнопок действий"""
    try:
        # Проверяем разные варианты кнопок
        button_selectors = [
            'button:has-text("Hit")',
            'button:has-text("STAND")',
            'button:has-text("Double")',
            'button:has-text("Split")',
            'button:has-text("Insurance")',
            '.game-action-button',
            '.action-button',
            '[class*="action"] button'
        ]
        
        for selector in button_selectors:
            buttons = await page.query_selector_all(selector)
            for btn in buttons:
                if await btn.is_visible():
                    return True
        return False
    except:
        return False

async def has_dealing_animation(page):
    """Проверяет наличие анимации раздачи"""
    try:
        animation_selectors = [
            '.card-dealing',
            '.card-animation',
            '.dealing',
            '[class*="dealing"]',
            '[class*="animated"]'
        ]
        
        for selector in animation_selectors:
            elements = await page.query_selector_all(selector)
            if elements:
                return True
        return False
    except:
        return False

async def dealer_has_hidden_cards(page):
    """Проверяет, есть ли у дилера скрытые карты"""
    try:
        dealer_cards = await page.query_selector_all('.live-twenty-one-field-player:last-child .scoreboard-card-games-card, .live-twenty-one-field-player:last-child [class*="card"]')
        for card in dealer_cards:
            class_name = await card.get_attribute('class') or ''
            if 'hidden' in class_name.lower() or 'face-down' in class_name.lower() or 'back' in class_name.lower():
                return True
        return False
    except:
        return False

async def is_game_truly_finished(page):
    """Улучшенная проверка завершения игры"""
    try:
        # 1. Проверяем наличие явного сообщения о завершении
        finished = await page.query_selector('span.ui-caption--size-xl.ui-caption--weight-700.ui-caption--color-clr-strong.ui-caption')
        if finished:
            text = await finished.text_content()
            if text and ('Игра завершена' in text or 'Game over' in text):
                logging.info("Обнаружено сообщение о завершении игры")
                return True
        
        # 2. Проверяем наличие кнопки новой игры
        new_btns = await page.query_selector_all('.ui-game-controls__button, button:has-text("Новая игра"), button:has-text("New game"), [class*="new-game"]')
        for btn in new_btns:
            if await btn.is_visible():
                logging.info("Обнаружена кнопка новой игры")
                return True
        
        # 3. Если есть активные элементы - игра точно не завершена
        if await has_active_player(page):
            return False
        
        # 4. Если есть кнопки действий - игра продолжается
        if await has_action_buttons(page):
            logging.info("Обнаружены кнопки действий - игра продолжается")
            return False
        
        # 5. Если идет анимация раздачи - игра продолжается
        if await has_dealing_animation(page):
            logging.info("Обнаружена анимация раздачи")
            return False
        
        # 6. Если у дилера есть скрытые карты - игра продолжается
        if await dealer_has_hidden_cards(page):
            logging.info("У дилера есть скрытые карты")
            return False
        
        # 7. Проверяем счета, но с дополнительными условиями
        player_score_el = await page.query_selector('.live-twenty-one-field-player:first-child .live-twenty-one-field-score__label')
        dealer_score_el = await page.query_selector('.live-twenty-one-field-player:last-child .live-twenty-one-field-score__label')
        
        if player_score_el and dealer_score_el:
            player_score = await player_score_el.text_content()
            dealer_score = await dealer_score_el.text_content()
            
            if player_score and dealer_score:
                try:
                    p_score = int(player_score.strip())
                    d_score = int(dealer_score.strip())
                    
                    # Получаем количество карт
                    player_cards = await extract_cards(page, '.live-twenty-one-field-player:first-child')
                    dealer_cards = await extract_cards(page, '.live-twenty-one-field-player:last-child')
                    
                    # Если у дилера 2 карты и одна скрыта - игра продолжается
                    if len(dealer_cards) == 1 and await dealer_has_hidden_cards(page):
                        return False
                    
                    # Если дилер активен или есть анимация - игра продолжается
                    if await has_active_player(page) or await has_dealing_animation(page):
                        return False
                    
                    # Проверяем, не добирает ли дилер
                    if d_score < 17 and len(dealer_cards) < 5:
                        # Проверяем, есть ли признаки того, что дилер еще добирает
                        if not await has_action_buttons(page) and not await has_active_player(page):
                            # Если дилер должен добирать, но нет активности - возможно пауза
                            return False
                    
                except:
                    pass
        
        # 8. Проверяем количество карт
        player_cards = await extract_cards(page, '.live-twenty-one-field-player:first-child')
        dealer_cards = await extract_cards(page, '.live-twenty-one-field-player:last-child')
        
        # Если у дилера мало карт и нет активности - возможно игра еще идет
        if len(dealer_cards) <= 2 and not await has_active_player(page):
            # Проверяем, не ждет ли дилер
            if not await has_action_buttons(page):
                return False
        
        # 9. Если все проверки пройдены, но игра выглядит завершенной
        # Проверяем, прошло ли достаточно времени без изменений
        return True
        
    except Exception as e:
        logging.error(f"Ошибка в is_game_truly_finished: {e}")
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
    browser = None
    page = None
    game_finished_detected = False
    last_state_change = time.time()
    last_card_update = time.time()
    no_activity_count = 0
    
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
                
                # Улучшенное ожидание загрузки карт
                cards_loaded = False
                wait_start = time.time()
                max_wait = 20
                
                while not cards_loaded and (time.time() - wait_start) < max_wait:
                    try:
                        # Проверяем наличие карт у игрока
                        player_cards = await extract_cards(page, '.live-twenty-one-field-player:first-child')
                        
                        # Проверяем наличие счета
                        player_score = await page.query_selector('.live-twenty-one-field-player:first-child .live-twenty-one-field-score__label')
                        
                        if len(player_cards) > 0 and player_score:
                            score_text = await player_score.text_content()
                            if score_text and score_text.strip() != '0':
                                cards_loaded = True
                                logging.info(f"Карты и счет загружены для стола {table_id}: {score_text}, карты: {player_cards}")
                                break
                        
                        await asyncio.sleep(0.3)
                    except Exception as e:
                        logging.error(f"Ошибка при ожидании карт: {e}")
                        break
                
                if not cards_loaded:
                    logging.warning(f"Стол {table_id}: карты не появились вовремя")
                    return
                
                # Небольшая задержка для полной загрузки
                await asyncio.sleep(1)
                
                # Отправляем первое сообщение
                first_state = await get_state_fast(page)
                if first_state and (len(first_state['p_cards']) > 0 or len(first_state['d_cards']) > 0):
                    msg = format_message(table_id, first_state, table_number=table_number)
                    sent = send_telegram_message_with_retry(CHANNEL_ID, msg)
                    msg_id = sent.message_id
                    with lock:
                        message_ids[table_id] = msg_id
                        last_messages[table_id] = msg
                    last_state = first_state
                    last_card_update = time.time()
                    logging.info(f"Стол {table_id}: первое сообщение отправлено: {msg}")
                
                logging.info(f"Старт мониторинга стола {table_id}")
                
                while game_active:
                    try:
                        if not page or page.is_closed():
                            break
                        
                        # Получаем текущее состояние
                        state = await get_state_fast(page)
                        
                        if not state:
                            await asyncio.sleep(0.3)
                            continue
                        
                        # Обновляем время последнего изменения карт
                        if state != last_state:
                            last_state_change = time.time()
                            if (state['p_cards'] != last_state.get('p_cards', []) or 
                                state['d_cards'] != last_state.get('d_cards', [])):
                                last_card_update = time.time()
                                no_activity_count = 0
                        
                        # Проверяем активность
                        player_active = state.get('player_active', False)
                        dealer_active = state.get('dealer_active', False)
                        
                        # Проверяем завершение игры
                        is_finished = await is_game_truly_finished(page)
                        
                        # Логируем подозрительные ситуации
                        if not player_active and not dealer_active and not is_finished:
                            no_activity_count += 1
                            if no_activity_count > 10:  # ~3 секунды без активности
                                # Проверяем, не добирает ли дилер
                                dealer_cards = state.get('d_cards', [])
                                try:
                                    dealer_score = int(state.get('d_score', 0))
                                    # Если у дилера меньше 17 и мало карт - ждем
                                    if dealer_score < 17 and len(dealer_cards) < 5:
                                        logging.info(f"Стол {table_id}: дилер добирает ({dealer_score} очков, {len(dealer_cards)} карт), ждем")
                                        no_activity_count = 0
                                except:
                                    pass
                        
                        # Если игра завершена
                        if is_finished:
                            if not game_finished_detected:
                                game_finished_detected = True
                                logging.info(f"Стол {table_id}: обнаружено завершение игры")
                                
                                # Даем время для отображения финальных карт
                                await asyncio.sleep(1.5)
                                
                                # Получаем финальное состояние
                                final_state = await get_state_fast(page)
                                
                                if final_state and (len(final_state['p_cards']) > 0 or len(final_state['d_cards']) > 0):
                                    final_msg = format_message(table_id, final_state, is_final=True, 
                                                             t_num=t_num, table_number=table_number)
                                    
                                    if msg_id:
                                        edit_telegram_message_with_retry(CHANNEL_ID, msg_id, final_msg)
                                        logging.info(f"Стол {table_id}: финальное сообщение: {final_msg}")
                                    else:
                                        sent = send_telegram_message_with_retry(CHANNEL_ID, final_msg)
                                        msg_id = sent.message_id
                                    
                                    game_data.add_completed_game(table_id, final_msg, t_num)
                                    game_data.update_last_number(table_number)
                                
                                game_active = False
                                break
                        
                        # Если есть активность (ход игрока или дилера) - игра продолжается
                        elif player_active or dealer_active:
                            game_finished_detected = False
                            no_activity_count = 0
                            
                            # Отправляем обновления при изменении состояния
                            if state != last_state:
                                msg = format_message(table_id, state, table_number=table_number)
                                
                                with lock:
                                    last_msg = last_messages.get(table_id)
                                    if last_msg == msg:
                                        await asyncio.sleep(0.2)
                                        continue
                                
                                if msg_id:
                                    result = edit_telegram_message_with_retry(CHANNEL_ID, msg_id, msg)
                                    if result is not None:
                                        with lock:
                                            last_messages[table_id] = msg
                                            last_state = state
                                        logging.info(f"Стол {table_id}: обновлено: {msg}")
                                
                                await asyncio.sleep(0.2)
                        
                        # Проверяем, не застряла ли игра
                        elif time.time() - last_card_update > 20:
                            logging.info(f"Стол {table_id}: нет изменений карт 20 сек, проверяю завершение")
                            if await is_game_truly_finished(page):
                                logging.info(f"Стол {table_id}: игра завершена (таймаут)")
                                game_active = False
                                break
                            else:
                                # Возможно, просто пауза
                                last_card_update = time.time()
                        
                        await asyncio.sleep(0.3)
                        
                    except Exception as e:
                        if "closed" in str(e).lower():
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