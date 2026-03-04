import asyncio
import nest_asyncio
import nodriver as uc
import logging
import time
import re
from datetime import datetime
import telebot

# ===== НАСТРОЙКИ =====
TOKEN = "8357635747:AAGAH_Rwk-vR8jGa6Q9F-AJLsMaEIj-JDBU"
CHANNEL_ID = "-1003179573402"
MAIN_URL = "https://1xlite-9048339.bar/ru/live/twentyone/2092323-21-classics?platform_type=desktop"
# =====================

# Для работы в асинхронной среде
nest_asyncio.apply()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
bot = telebot.TeleBot(TOKEN)

# Соответствие мастей
SUIT_MAP = {
    'suit-0': '♠️',
    'suit-1': '♣️',
    'suit-2': '♦️',
    'suit-3': '♥️'
}

# Соответствие значений
VALUE_MAP = {
    '11': 'J',
    '12': 'Q',
    '13': 'K',
    '14': 'A'
}

async def extract_cards_from_element(card_element):
    """Извлекает масть и значение из элемента карты"""
    try:
        # Получаем класс элемента
        class_name = await card_element.get_attribute('class')
        if not class_name:
            return None
        
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
            value = VALUE_MAP.get(val, val)
        else:
            value = '?'
        
        return f"{value}{suit}"
    except Exception as e:
        logging.error(f"Ошибка при парсинге карты: {e}")
        return None

async def get_game_state(page):
    """Получает текущее состояние игры"""
    try:
        # Ждем появления карт
        await page.wait_for('.live-twenty-one-cards', timeout=5)
        
        # Находим всех игроков (игрок и дилер)
        players = await page.find_all('.live-twenty-one-field-player')
        
        if len(players) < 2:
            return None
        
        player_cards = []
        dealer_cards = []
        
        # Парсим карты игрока (первый игрок)
        player_cards_container = await players[0].find('.live-twenty-one-cards')
        if player_cards_container:
            card_elements = await player_cards_container.find_all('.scoreboard-card-games-card')
            for card in card_elements:
                card_str = await extract_cards_from_element(card)
                if card_str:
                    player_cards.append(card_str)
        
        # Парсим карты дилера (второй игрок)
        dealer_cards_container = await players[1].find('.live-twenty-one-cards')
        if dealer_cards_container:
            card_elements = await dealer_cards_container.find_all('.scoreboard-card-games-card')
            for card in card_elements:
                card_str = await extract_cards_from_element(card)
                if card_str:
                    dealer_cards.append(card_str)
        
        # Получаем очки
        player_score_elem = await players[0].find('.live-twenty-one-field-score__label')
        player_score = await player_score_elem.text if player_score_elem else '0'
        
        dealer_score_elem = await players[1].find('.live-twenty-one-field-score__label')
        dealer_score = await dealer_score_elem.text if dealer_score_elem else '0'
        
        return {
            'player_cards': player_cards,
            'dealer_cards': dealer_cards,
            'player_score': player_score.strip() if player_score else '0',
            'dealer_score': dealer_score.strip() if dealer_score else '0'
        }
    except Exception as e:
        logging.error(f"Ошибка в get_game_state: {e}")
        return None

def send_telegram(message):
    """Отправляет сообщение в Telegram"""
    try:
        bot.send_message(CHANNEL_ID, message)
        logging.info(f"Отправлено: {message[:50]}...")
    except Exception as e:
        logging.error(f"Ошибка отправки в Telegram: {e}")

async def monitor_table():
    """Основная функция мониторинга стола"""
    logging.info("🚀 Запуск мониторинга 21 Classic на Nodriver")
    
    last_state = None
    
    try:
        # Запускаем браузер
        browser = await uc.start(
            headless=True,
            no_sandbox=True,
            browser_args=[
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--blink-settings=imagesEnabled=false',  # отключаем картинки
                '--disable-remote-fonts'                  # отключаем шрифты
            ]
        )
        
        # Открываем страницу
        page = await browser.get(MAIN_URL)
        logging.info("Страница загружена")
        
        # Ждем загрузки контента
        await asyncio.sleep(5)
        
        while True:
            try:
                # Получаем состояние игры
                state = await get_game_state(page)
                
                if state and state['player_cards']:
                    # Формируем сообщение
                    player_cards_str = ' '.join(state['player_cards'])
                    dealer_cards_str = ' '.join(state['dealer_cards'])
                    
                    message = (f"🎮 Игрок: {state['player_score']} ({player_cards_str})\n"
                              f"🏦 Дилер: {state['dealer_score']} ({dealer_cards_str})\n"
                              f"⏱ {datetime.now().strftime('%H:%M:%S')}")
                    
                    # Отправляем, если изменилось
                    if message != last_state:
                        send_telegram(message)
                        last_state = message
                
                # Ждем перед следующим обновлением
                await asyncio.sleep(2)
                
            except Exception as e:
                logging.error(f"Ошибка в цикле: {e}")
                await asyncio.sleep(5)
                
    except Exception as e:
        logging.error(f"Критическая ошибка: {e}")
    finally:
        if 'browser' in locals():
            browser.stop()

def main():
    """Точка входа"""
    try:
        asyncio.run(monitor_table())
    except KeyboardInterrupt:
        logging.info("Бот остановлен")

if __name__ == "__main__":
    main()