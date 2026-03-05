import threading
import time
import re
import logging
import asyncio
import gc
from datetime import datetime, timedelta
from playwright.async_api import async_playwright
import telebot
from telebot import apihelper

# ===== НАСТРОЙКИ =====
TOKEN = "8357635747:AAGAH_Rwk-vR8jGa6Q9F-AJLsMaEIj-JDBU"
CHANNEL_ID = "-1003179573402"
MAIN_URL = "https://1xlite-9048339.bar/ru/live/twentyone/1643503-twentyone-game?platform_type=desktop"
MAX_TABLES = 3  # Максимум столов (раньше было MAX_BROWSERS)
# =====================

apihelper.RETRY_ON_ERROR = True
apihelper.MAX_RETRIES = 5

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# Соответствие мастей (из классов)
SUIT_MAP = {
    'suit-0': '♠️',
    'suit-1': '♣️',
    'suit-2': '♦️',
    'suit-3': '♥️'
}

# Соответствие значений (из классов)
VALUE_MAP = {
    '2': '2', '3': '3', '4': '4', '5': '5', '6': '6', '7': '7', '8': '8', '9': '9', '10': '10',
    '11': 'J',
    '12': 'Q',
    '13': 'K',
    '14': 'A'
}

bot = telebot.TeleBot(TOKEN)
active_tables = {}  # game_number -> thread
monitoring_games = set()
lock = threading.Lock()
bot_running = True

def get_game_number_by_time(dt=None):
    """Расчет номера игры по времени (игры каждые 2 минуты)"""
    if dt is None:
        dt = datetime.now()
    
    start_of_day = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    minutes_passed = (dt - start_of_day).total_seconds() / 60
    game_number = int(minutes_passed // 2) + 1
    
    return game_number

def get_next_game_time():
    """Возвращает время следующей игры (каждые 2 минуты)"""
    now = datetime.now()
    
    if now.minute % 2 == 0:
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

async def extract_cards_from_container(container):
    """Извлекает карты из контейнера"""
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
                value = VALUE_MAP.get(val, val)
            else:
                value = '?'
            
            cards.append(f"{value}{suit}")
        except:
            continue
    
    return cards

async def get_table_url(page, game_number):
    """Получение URL стола по номеру игры"""
    try:
        logging.info(f"🔍 Ищем стол №{game_number}...")
        
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
                                    href = f"https://1xlite-9048339.bar{href}"
                                
                                logging.info(f"✅ Найден нужный стол #{current_number}")
                                return href
                        else:
                            logging.info(f"Стол #{current_number} не подходит, ищем #{game_number}")
                    
            except Exception as e:
                continue
        
        logging.warning(f"❌ Стол #{game_number} не найден")
        return None
        
    except Exception as e:
        logging.error(f"Ошибка в get_table_url: {e}")
        return None

async def monitor_table(page, game_number):
    """Мониторинг конкретного стола (работает с готовой страницей)"""
    
    with lock:
        if game_number in monitoring_games:
            logging.info(f"⚠️ Игра #{game_number} уже мониторится, пропускаем")
            return
        monitoring_games.add(game_number)
    
    last_state = None
    start_time = time.time()
    max_duration = 240
    
    logging.info(f"🎮 Стол #{game_number}: начало мониторинга")
    
    try:
        # Ждём появления карт до 90 секунд
        try:
            await page.wait_for_selector('.scoreboard-card-games-card', timeout=90000)
            logging.info(f"Стол #{game_number}: карты появились")
        except:
            logging.warning(f"Стол #{game_number}: карты не найдены за 90 секунд")
        
        while time.time() - start_time < max_duration:
            try:
                if page.is_closed():
                    break
                
                state = await get_game_state(page, game_number)
                
                if state and state != last_state:
                    message = format_game_message(game_number, state)
                    send_telegram(message)
                    last_state = state
                
                await asyncio.sleep(0.5)
                
            except Exception as e:
                if "EPIPE" in str(e) or "pipe" in str(e).lower():
                    logging.error(f"Стол #{game_number}: ошибка соединения с браузером, пробуем пересоздать")
                    # Пересоздаем страницу не получится, просто выходим
                    break
                else:
                    logging.error(f"Ошибка в цикле стола #{game_number}: {e}")
                    await asyncio.sleep(1)
            
    except Exception as e:
        logging.error(f"Критическая ошибка стола #{game_number}: {e}")
    finally:
        with lock:
            monitoring_games.discard(game_number)
            if game_number in active_tables:
                del active_tables[game_number]
        
        gc.collect()
        logging.info(f"Стол #{game_number}: мониторинг завершён")

async def get_game_state(page, game_number):
    """Получает текущее состояние игры"""
    try:
        player_cards_container = await page.query_selector('.live-twenty-one-field-player:first-child .live-twenty-one-cards')
        player_cards = await extract_cards_from_container(player_cards_container)
        
        dealer_cards_container = await page.query_selector('.live-twenty-one-field-player:last-child .live-twenty-one-cards')
        dealer_cards = await extract_cards_from_container(dealer_cards_container)
        
        player_score_el = await page.query_selector('.live-twenty-one-field-player:first-child .live-twenty-one-field-score__label')
        player_score = await player_score_el.text_content() if player_score_el else '0'
        
        dealer_score_el = await page.query_selector('.live-twenty-one-field-player:last-child .live-twenty-one-field-score__label')
        dealer_score = await dealer_score_el.text_content() if dealer_score_el else '0'
        
        status_el = await page.query_selector('.live-twenty-one-table-head__status')
        status = await status_el.text_content() if status_el else ''
        
        return {
            'player_cards': player_cards,
            'dealer_cards': dealer_cards,
            'player_score': player_score.strip(),
            'dealer_score': dealer_score.strip(),
            'status': status.strip()
        }
    except Exception as e:
        logging.error(f"Ошибка в get_game_state: {e}")
        return None

def format_game_message(game_number, state):
    """Форматирует сообщение для отправки"""
    player_cards_str = ' '.join(state['player_cards']) if state['player_cards'] else 'нет карт'
    dealer_cards_str = ' '.join(state['dealer_cards']) if state['dealer_cards'] else 'нет карт'
    
    message = (
        f"🎮 Игра #{game_number}\n"
        f"👤 Игрок: {state['player_score']} ({player_cards_str})\n"
        f"🏦 Дилер: {state['dealer_score']} ({dealer_cards_str})\n"
        f"📊 Статус: {state['status']}\n"
        f"⏱ {datetime.now().strftime('%H:%M:%S')}"
    )
    return message

def send_telegram(message):
    """Отправляет сообщение в Telegram"""
    try:
        bot.send_message(CHANNEL_ID, message)
        logging.info(f"✅ Отправлено: {message[:50]}...")
    except Exception as e:
        logging.error(f"Ошибка отправки в Telegram: {e}")

def run_async_monitor(page, game_number):
    """Запускает асинхронный мониторинг в потоке"""
    try:
        asyncio.run(monitor_table(page, game_number))
    except Exception as e:
        logging.error(f"Ошибка в потоке мониторинга стола #{game_number}: {e}")

async def browser_manager():
    """Управляет ОДНИМ браузером и несколькими страницами"""
    async with async_playwright() as p:
        # Запускаем ОДИН браузер на всё
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
        
        # Словарь для хранения страниц и их статусов
        pages = {}  # table_index -> {'page': page, 'game_number': game_number, 'active': bool}
        
        while bot_running:
            try:
                # Определяем следующую игру
                next_game_time, seconds_to_next = get_next_game_time()
                game_number = get_game_number_by_time(next_game_time)
                
                # Если пора запускать новый стол
                if seconds_to_next <= 40 and len(pages) < MAX_TABLES:
                    # Создаем новый контекст и страницу
                    context = await browser.new_context()
                    page = await context.new_page()
                    
                    # Блокируем лишние ресурсы
                    await page.route('**/*', block_resources)
                    
                    # Загружаем лобби
                    await page.goto(MAIN_URL, timeout=30000, wait_until="domcontentloaded")
                    
                    # Ищем URL стола
                    table_url = await get_table_url(page, game_number)
                    
                    if table_url:
                        # Переходим на страницу стола
                        await page.goto(table_url, timeout=30000, wait_until="domcontentloaded")
                        
                        # Запускаем мониторинг в отдельной задаче
                        asyncio.create_task(monitor_table(page, game_number))
                        
                        pages[game_number] = {'page': page, 'context': context, 'active': True}
                        logging.info(f"✅ Запущен мониторинг стола #{game_number}")
                    else:
                        # Если не нашли стол, закрываем страницу
                        await page.close()
                        await context.close()
                
                # Чистим завершённые страницы
                for game_num in list(pages.keys()):
                    if game_num not in monitoring_games:
                        # Страница больше не нужна
                        await pages[game_num]['page'].close()
                        await pages[game_num]['context'].close()
                        del pages[game_num]
                        logging.info(f"🧹 Закрыта страница для игры #{game_num}")
                
                await asyncio.sleep(5)
                
            except Exception as e:
                logging.error(f"Ошибка в browser_manager: {e}")
                await asyncio.sleep(10)
        
        # Закрываем браузер при выходе
        await browser.close()

async def block_resources(route):
    """Блокирует ненужные ресурсы"""
    if route.request.resource_type in ['image', 'stylesheet', 'font', 'media']:
        await route.abort()
    else:
        await route.continue_()

def main():
    """Точка входа"""
    logging.info("🚀 Бот для 21 Classic запущен (ОПТИМИЗИРОВАННАЯ ВЕРСИЯ)")
    logging.info(f"Максимум столов: {MAX_TABLES}")
    logging.info("Архитектура: 1 браузер + несколько страниц")
    
    try:
        asyncio.run(browser_manager())
    except KeyboardInterrupt:
        logging.info("Бот остановлен")

if __name__ == "__main__":
    main()