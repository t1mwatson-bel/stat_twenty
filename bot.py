import asyncio
import logging
import re
import time
from datetime import datetime
from playwright.async_api import async_playwright
import telebot
from telebot import apihelper

# ===== НАСТРОЙКИ =====
TOKEN = "8357635747:AAGAH_Rwk-vR8jGa6Q9F-AJLsMaEIj-JDBU"
CHANNEL_ID = "-1003179573402"
MAIN_URL = "https://1xlite-9048339.bar/ru/live/twentyone/1643503-twentyone-game?platform_type=desktop"
# =====================

apihelper.RETRY_ON_ERROR = True
apihelper.MAX_RETRIES = 5

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

bot = telebot.TeleBot(TOKEN)

class TableMonitor:
    def __init__(self):
        self.current_table_index = 0
        
    def send_telegram(self, message):
        """Отправляет сообщение в Telegram"""
        try:
            bot.send_message(CHANNEL_ID, message)
            logging.info(f"✅ Отправлено: {message[:50]}...")
        except Exception as e:
            logging.error(f"Ошибка отправки в Telegram: {e}")
        
    async def get_tables_from_lobby(self, page):
        """Получает список всех доступных столов из лобби"""
        try:
            # Ждем загрузки списка столов
            await page.wait_for_selector('.dashboard-game-block', timeout=10000)
            
            # Находим все столы
            tables = await page.query_selector_all('.dashboard-game-block')
            
            table_urls = []
            for table in tables:
                try:
                    link = await table.query_selector('.dashboard-game-block__link')
                    if link:
                        href = await link.get_attribute('href')
                        if href:
                            if not href.startswith('http'):
                                href = f"https://1xlite-9048339.bar{href}"
                            table_urls.append(href)
                except:
                    continue
            
            logging.info(f"Найдено столов: {len(table_urls)}")
            return table_urls
            
        except Exception as e:
            logging.error(f"Ошибка при получении списка столов: {e}")
            return []

    async def monitor_single_table(self, table_url, table_index):
        """Мониторит один стол до конца игры"""
        logging.info(f"🎮 Стол #{table_index + 1}: начало мониторинга")
        
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
            
            # Загружаем страницу стола
            await page.goto(table_url, timeout=30000, wait_until="domcontentloaded")
            logging.info(f"Стол #{table_index + 1}: страница загружена")
            
            # Ждем появления карт
            try:
                await page.wait_for_selector('.scoreboard-card-games-card', timeout=15000)
                logging.info(f"Стол #{table_index + 1}: карты появились")
            except:
                logging.warning(f"Стол #{table_index + 1}: карты не найдены, но продолжаем")
            
            game_active = True
            last_state = None
            
            while game_active:
                try:
                    # Проверяем, не завершилась ли игра
                    timer_div = await page.query_selector('.ui-game-timer__label')
                    if timer_div:
                        timer_text = await timer_div.text_content()
                        if "Игра завершена" in timer_text:
                            logging.info(f"Стол #{table_index + 1}: игра завершена")
                            game_active = False
                            break
                    
                    # Получаем состояние игры
                    state = await self.get_game_state(page)
                    
                    if state and state != last_state:
                        self.send_telegram(state)
                        last_state = state
                    
                    await asyncio.sleep(1)
                    
                except Exception as e:
                    logging.error(f"Ошибка при мониторинге стола #{table_index + 1}: {e}")
                    await asyncio.sleep(2)
            
            logging.info(f"Стол #{table_index + 1}: мониторинг завершён")

    async def get_game_state(self, page):
        """Получает текущее состояние игры с определением хода"""
        try:
            # Определяем, чей ход
            turn_symbol = ""
            player_area = await page.query_selector('.live-twenty-one-field-player:first-child')
            if player_area:
                class_name = await player_area.get_attribute('class') or ''
                if 'active' in class_name.lower():
                    turn_symbol = " 👈"
            
            dealer_area = await page.query_selector('.live-twenty-one-field-player:last-child')
            if dealer_area:
                class_name = await dealer_area.get_attribute('class') or ''
                if 'active' in class_name.lower():
                    turn_symbol = " 👉"
            
            # Карты игрока
            player_container = await page.query_selector('.live-twenty-one-field-player:first-child .live-twenty-one-cards')
            player_cards = await self.extract_cards(player_container)
            
            # Карты дилера
            dealer_container = await page.query_selector('.live-twenty-one-field-player:last-child .live-twenty-one-cards')
            dealer_cards = await self.extract_cards(dealer_container)
            
            # Счет игрока
            player_score_el = await page.query_selector('.live-twenty-one-field-player:first-child .live-twenty-one-field-score__label')
            player_score = await player_score_el.text_content() if player_score_el else '0'
            
            # Счет дилера
            dealer_score_el = await page.query_selector('.live-twenty-one-field-player:last-child .live-twenty-one-field-score__label')
            dealer_score = await dealer_score_el.text_content() if dealer_score_el else '0'
            
            # Формируем строку состояния
            return f"#{self.current_table_index} {player_score}({player_cards}){turn_symbol} - {dealer_score}({dealer_cards})"
            
        except Exception as e:
            logging.error(f"Ошибка в get_game_state: {e}")
            return None

    async def extract_cards(self, container):
        """Извлекает карты из контейнера"""
        if not container:
            return "нет карт"
        
        cards = []
        card_elements = await container.query_selector_all('.scoreboard-card-games-card')
        
        for card in card_elements:
            try:
                class_name = await card.get_attribute('class') or ''
                
                # Пропускаем скрытые карты
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
                    value_map = {'11': 'J', '12': 'Q', '13': 'K', '14': 'A'}
                    value = value_map.get(val, val)
                else:
                    value = '?'
                
                cards.append(f"{value}{suit}")
            except:
                continue
        
        return ' '.join(cards) if cards else "нет карт"

    async def run(self):
        """Основной цикл мониторинга"""
        logging.info("🚀 Запуск мониторинга столов по очереди")
        self.send_telegram("🚀 Бот запущен, начинаю мониторинг столов")
        
        while True:
            try:
                async with async_playwright() as p:
                    browser = await p.chromium.launch(
                        headless=True,
                        args=["--no-sandbox", "--disable-dev-shm-usage"]
                    )
                    
                    page = await browser.new_page()
                    
                    # Заходим в лобби
                    await page.goto(MAIN_URL, timeout=30000, wait_until="domcontentloaded")
                    
                    # Получаем список столов
                    tables = await self.get_tables_from_lobby(page)
                    
                    if not tables:
                        logging.warning("Нет доступных столов, жду 30 секунд...")
                        await asyncio.sleep(30)
                        continue
                    
                    # Определяем текущий стол по индексу
                    if self.current_table_index >= len(tables):
                        self.current_table_index = 0
                    
                    table_url = tables[self.current_table_index]
                    logging.info(f"Выбран стол #{self.current_table_index + 1} из {len(tables)}")
                    
                    # Закрываем браузер лобби
                    await browser.close()
                    
                    # Мониторим выбранный стол
                    await self.monitor_single_table(table_url, self.current_table_index)
                    
                    # Переходим к следующему столу
                    self.current_table_index += 1
                    
                    # Небольшая пауза перед следующим циклом
                    await asyncio.sleep(5)
                    
            except Exception as e:
                logging.error(f"Критическая ошибка: {e}")
                self.send_telegram(f"❌ Ошибка: {str(e)[:100]}")
                await asyncio.sleep(10)

if __name__ == "__main__":
    monitor = TableMonitor()
    asyncio.run(monitor.run())