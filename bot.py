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
MAX_BROWSERS = 2
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
active_tables = {}
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

async def monitor_table(table_url, game_number):
    """Мониторинг конкретного стола"""
    
    with lock:
        if game_number in monitoring_games:
            logging.info(f"⚠️ Игра #{game_number} уже мониторится, пропускаем")
            return
        monitoring_games.add(game_number)
    
    browser = None
    page = None
    last_state = None
    start_time = time.time()
    max_duration = 240
    browser_start_time = time.time()
    max_browser_lifetime = 600
    
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
                    "--single-process",
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
            logging.info(f"Стол #{game_number}: страница загружена")
            
            # Ждём появления карт до 90 секунд
            try:
                await page.wait_for_selector('.scoreboard-card-games-card', timeout=90000)
                logging.info(f"Стол #{game_number}: карты появились")
            except:
                logging.warning(f"Стол #{game_number}: карты не найдены за 90 секунд")
            
            while time.time() - start_time < max_duration:
                if time.time() - browser_start_time > max_browser_lifetime:
                    logging.warning(f"Стол #{game_number}: браузер работает слишком долго")
                    break
                
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
                    logging.error(f"Ошибка в цикле стола #{game_number}: {e}")
                    await asyncio.sleep(2)
            
    except Exception as e:
        logging.error(f"Критическая ошибка стола #{game_number}: {e}")
    finally:
        if browser:
            await browser.close()
        
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

def run_async_monitor(table_url, game_number):
    """Запускает асинхронный мониторинг в потоке"""
    try:
        asyncio.run(monitor_table(table_url, game_number))
    except Exception as e:
        logging.error(f"Ошибка в потоке мониторинга стола #{game_number}: {e}")

def launch_next_game_monitor():
    """Запускает монитор для следующей игры"""
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
                
                async def block_resources(route):
                    if route.request.resource_type in ['image', 'stylesheet', 'font', 'media']:
                        await route.abort()
                    else:
                        await route.continue_()
                
                await page.route('**/*', block_resources)
                await page.goto(MAIN_URL, timeout=30000, wait_until="domcontentloaded")
                
                next_game_time, _ = get_next_game_time()
                game_number = get_game_number_by_time(next_game_time)
                
                url = await get_table_url(page, game_number)
                return url, game_number
                
            except Exception as e:
                logging.error(f"Ошибка при загрузке MAIN_URL: {e}")
                return None, None
            finally:
                if browser:
                    await browser.close()
                gc.collect()
    
    try:
        table_url, game_number = asyncio.run(get_table())
        
        if not table_url or not game_number:
            logging.warning("Не удалось получить URL стола")
            return
        
        with lock:
            if game_number in active_tables:
                logging.info(f"Игра #{game_number} уже мониторится, пропускаем")
                return
        
        logging.info(f"🎯 Игра #{game_number}: запуск мониторинга")
        
        thread = threading.Thread(
            target=run_async_monitor, 
            args=(table_url, game_number)
        )
        thread.daemon = True
        thread.start()
        
        with lock:
            active_tables[game_number] = thread
        
        logging.info(f"✅ Игра #{game_number}: мониторинг запущен (активных: {len(active_tables)}/{MAX_BROWSERS})")
            
    except Exception as e:
        logging.error(f"Ошибка при запуске монитора: {e}")

def clean_threads():
    """Очищает завершённые потоки"""
    with lock:
        dead = [gid for gid, t in active_tables.items() if not t.is_alive()]
        for gid in dead:
            del active_tables[gid]
            monitoring_games.discard(gid)
            logging.info(f"🧹 Поток игры #{gid} очищен")

def monitor_loop():
    """Основной цикл мониторинга"""
    global bot_running
    last_launch_time = 0
    
    logging.info("🚀 Бот для 21 Classic запущен")
    logging.info(f"Максимум браузеров: {MAX_BROWSERS}")
    
    while bot_running:
        try:
            clean_threads()
            
            next_game_time, seconds_to_next = get_next_game_time()
            current_time = time.time()
            
            if seconds_to_next <= 40 and (current_time - last_launch_time) > 30:
                game_number = get_game_number_by_time(next_game_time)
                logging.info(f"🎯 До игры #{game_number} осталось {seconds_to_next:.0f} сек")
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
            gc.collect()
    
    logging.info("Бот остановлен")

def main():
    """Точка входа"""
    monitor_loop()

if __name__ == "__main__":
    main()