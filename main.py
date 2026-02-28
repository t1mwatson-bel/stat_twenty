# -*- coding: utf-8 -*-
import logging
import os
import sys
import json
import asyncio
import requests
from datetime import datetime, time, timedelta
from collections import defaultdict, deque
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import random
import pytz
import aiohttp
from bs4 import BeautifulSoup
import re

# ======== НАСТРОЙКА ЛОГИРОВАНИЯ ========
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ======== НАСТРОЙКИ ========
TOKEN = os.environ.get('BOT_TOKEN')
OUTPUT_CHANNEL_ID = int(os.environ.get('OUTPUT_CHANNEL_ID', '0'))

if not TOKEN or not OUTPUT_CHANNEL_ID:
    logger.error("❌ Не все переменные окружения заданы!")
    sys.exit(1)

# ======== МАППИНГ МАСТЕЙ ========
SUIT_MAP = {
    '0': '♥️',  # Черви
    '1': '♦️',  # Бубны
    '2': '♠️',  # Пики
    '3': '♣️',  # Трефы
}

VALUE_MAP = {
    '1': 'A',
    '11': 'J',
    '12': 'Q',
    '13': 'K'
}

# ======== ЖЁСТКИЙ СБРОС ВЕБХУКА ========
def force_reset_webhook():
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/deleteWebhook?drop_pending_updates=true"
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            if data.get('ok'):
                logger.info("✅ Вебхук принудительно сброшен")
    except:
        pass

force_reset_webhook()

# ======== БОТ ДЛЯ 21 ========
class BlackjackBot:
    def __init__(self):
        self.games = {}
        self.history = deque(maxlen=1000)
        self.stats = {
            'total': 0,
            'player_wins': 0,
            'dealer_wins': 0,
            'ties': 0,
            'blackjacks': 0
        }
        self.last_check = datetime.now()
        self.session = None
        
    async def init_session(self):
        if not self.session:
            self.session = aiohttp.ClientSession(headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'text/html,application/xhtml+xml'
            })
    
    async def get_live_games(self):
        """Парсит live-столы 21 с сайта"""
        await self.init_session()
        games = []
        
        try:
            # URL страницы с 21 (вставь свой)
            url = "https://nb-bet.com/ru/live/twentyone"
            
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    
                    # Находим все столы
                    tables = soup.find_all('div', class_='live-twenty-one__table')
                    
                    for table in tables:
                        game = self.parse_game(table)
                        if game:
                            games.append(game)
                    
                    logger.info(f"📊 Найдено столов: {len(games)}")
                else:
                    logger.error(f"❌ Ошибка загрузки страницы: {resp.status}")
                    
        except Exception as e:
            logger.error(f"❌ Ошибка парсинга: {e}")
        
        return games
    
    def parse_game(self, table):
        """Парсит одну раздачу"""
        try:
            # Статус игры
            status_elem = table.find('span', class_='ui-game-timer__label')
            status = status_elem.text.strip() if status_elem else "Идет"
            
            # Результат (если есть)
            result_elem = table.find('span', class_='scoreboard-card-games-board-status')
            result = result_elem.text.strip() if result_elem else None
            
            # Поиск блоков игрока и дилера
            players = table.find_all('div', class_='live-twenty-one-field__player')
            if len(players) < 2:
                return None
            
            player_block = players[0]
            dealer_block = players[1]
            
            # Очки игрока
            player_score_elem = player_block.find('span', class_='live-twenty-one-field-score__label')
            player_score = int(player_score_elem.text) if player_score_elem else 0
            
            # Очки дилера
            dealer_score_elem = dealer_block.find('span', class_='live-twenty-one-field-score__label')
            dealer_score = int(dealer_score_elem.text) if dealer_score_elem else 0
            
            # Карты игрока
            player_cards = []
            player_card_elems = player_block.find_all('div', class_='scoreboard-card-games-card')
            
            for card in player_card_elems:
                card_info = self.parse_card(card)
                if card_info:
                    player_cards.append(card_info)
            
            # Карты дилера
            dealer_cards = []
            dealer_card_elems = dealer_block.find_all('div', class_='scoreboard-card-games-card')
            
            for card in dealer_card_elems:
                card_info = self.parse_card(card)
                if card_info:
                    dealer_cards.append(card_info)
            
            # Определяем победителя, если игра завершена
            winner = None
            if status == "Игра завершена" and result:
                if "Победа игрока" in result:
                    winner = "player"
                elif "Победа дилера" in result:
                    winner = "dealer"
                elif "Ничья" in result:
                    winner = "tie"
                elif "Блэкджек" in result:
                    winner = "blackjack"
            
            game = {
                'status': status,
                'result': result,
                'player_score': player_score,
                'dealer_score': dealer_score,
                'player_cards': player_cards,
                'dealer_cards': dealer_cards,
                'winner': winner,
                'timestamp': datetime.now()
            }
            
            return game
            
        except Exception as e:
            logger.error(f"Ошибка парсинга раздачи: {e}")
            return None
    
    def parse_card(self, card_element):
        """Парсит одну карту"""
        try:
            classes = card_element.get('class', [])
            
            # Ищем масть (suit-0, suit-1, suit-2, suit-3)
            suit_class = next((c for c in classes if 'suit-' in c), None)
            suit = suit_class.replace('suit-', '') if suit_class else '?'
            
            # Ищем значение (value-1 ... value-13)
            value_class = next((c for c in classes if 'value-' in c), None)
            value = value_class.replace('value-', '') if value_class else '?'
            
            # Закрытая карта (рубашка)
            if 'closed' in classes:
                return {'closed': True}
            
            # Преобразуем в читаемый вид
            suit_symbol = SUIT_MAP.get(suit, '?')
            
            if value in VALUE_MAP:
                card_name = VALUE_MAP[value]
            else:
                card_name = value
            
            return {
                'suit': suit,
                'suit_symbol': suit_symbol,
                'value': value,
                'card': f"{card_name}{suit_symbol}",
                'closed': False
            }
            
        except Exception as e:
            logger.error(f"Ошибка парсинга карты: {e}")
            return None
    
    def analyze_game(self, game):
        """Анализирует раздачу для статистики"""
        if game['winner']:
            self.stats['total'] += 1
            
            if game['winner'] == 'player':
                self.stats['player_wins'] += 1
            elif game['winner'] == 'dealer':
                self.stats['dealer_wins'] += 1
            elif game['winner'] == 'tie':
                self.stats['ties'] += 1
            elif game['winner'] == 'blackjack':
                self.stats['blackjacks'] += 1
            
            # Сохраняем в историю
            game_id = f"{game['timestamp'].timestamp()}"
            if game_id not in self.games:
                self.games[game_id] = game
                self.history.append(game)
    
    async def check_games(self, context):
        """Основной цикл проверки"""
        logger.info("🔍 Проверка столов 21...")
        
        games = await self.get_live_games()
        
        for game in games:
            self.analyze_game(game)
            
            # Если есть завершенная раздача с результатом
            if game['winner'] and game['status'] == "Игра завершена":
                await self.send_game_result(game, context)
    
    async def send_game_result(self, game, context):
        """Отправляет результат раздачи в канал"""
        
        # Формируем строку с картами игрока
        player_cards_str = []
        for card in game['player_cards']:
            if card and not card.get('closed'):
                player_cards_str.append(card['card'])
        
        # Карты дилера
        dealer_cards_str = []
        for card in game['dealer_cards']:
            if card and not card.get('closed'):
                dealer_cards_str.append(card['card'])
        
        # Определяем эмодзи результата
        if game['winner'] == 'player':
            result_emoji = "✅"
            result_text = "ПОБЕДА ИГРОКА"
        elif game['winner'] == 'dealer':
            result_emoji = "❌"
            result_text = "ПОБЕДА ДИЛЕРА"
        elif game['winner'] == 'tie':
            result_emoji = "🤝"
            result_text = "НИЧЬЯ"
        elif game['winner'] == 'blackjack':
            result_emoji = "🃏"
            result_text = "БЛЭКДЖЕК"
        else:
            return
        
        message = (
            f"🃏 *21 - РЕЗУЛЬТАТ РАЗДАЧИ*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{result_emoji} *{result_text}*\n\n"
            f"👤 *Игрок:* {game['player_score']}\n"
            f"   Карты: {' '.join(player_cards_str)}\n\n"
            f"👨‍💼 *Дилер:* {game['dealer_score']}\n"
            f"   Карты: {' '.join(dealer_cards_str)}\n\n"
            f"📊 *Статистика бота:*\n"
            f"   Всего раздач: {self.stats['total']}\n"
            f"   Игрок: {self.stats['player_wins']} | "
            f"Дилер: {self.stats['dealer_wins']} | "
            f"Ничьи: {self.stats['ties']}\n"
            f"   Блэкджеков: {self.stats['blackjacks']}\n\n"
            f"⏱ {datetime.now(pytz.timezone('Europe/Moscow')).strftime('%H:%M:%S')} МСК"
        )
        
        try:
            await context.bot.send_message(
                chat_id=OUTPUT_CHANNEL_ID,
                text=message,
                parse_mode='Markdown'
            )
            logger.info(f"📤 Отправлен результат раздачи")
            
        except Exception as e:
            logger.error(f"Ошибка отправки: {e}")
    
    async def daily_stats(self, context):
        """Ежедневная статистика"""
        message = (
            f"📊 *ИТОГИ ДНЯ (21)*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🎯 Всего раздач: {self.stats['total']}\n"
            f"✅ Побед игрока: {self.stats['player_wins']}\n"
            f"❌ Побед дилера: {self.stats['dealer_wins']}\n"
            f"🤝 Ничьих: {self.stats['ties']}\n"
            f"🃏 Блэкджеков: {self.stats['blackjacks']}\n\n"
            f"📈 Процент побед игрока: "
            f"{self.stats['player_wins']/max(1, self.stats['total'])*100:.1f}%\n"
            f"⏱ {datetime.now(pytz.timezone('Europe/Moscow')).strftime('%H:%M')} МСК"
        )
        
        await context.bot.send_message(
            chat_id=OUTPUT_CHANNEL_ID,
            text=message,
            parse_mode='Markdown'
        )

# ======== ИНИЦИАЛИЗАЦИЯ ========
bot = None

async def check_blackjack(context: ContextTypes.DEFAULT_TYPE):
    """Проверка столов 21"""
    global bot
    if not bot:
        bot = BlackjackBot()
    
    await bot.check_games(context)

async def daily_stats_job(context: ContextTypes.DEFAULT_TYPE):
    """Ежедневная статистика"""
    if bot:
        await bot.daily_stats(context)

# ======== MAIN ========
def main():
    print("\n" + "="*60)
    print("🃏 БОТ ДЛЯ 21 (БЛЭКДЖЕК) v1.0")
    print("="*60)
    print("🎯 Парсинг live-столов")
    print("📊 Сбор статистики")
    print("✅ Автоматические отчёты")
    print("="*60)
    
    app = Application.builder().token(TOKEN).build()
    
    job_queue = app.job_queue
    if job_queue:
        # Проверяем столы каждые 30 секунд
        job_queue.run_repeating(check_blackjack, interval=30, first=10)
        # Статистика раз в день
        job_queue.run_daily(daily_stats_job, time=time(23, 59, 0))
    
    logger.info("🚀 Бот для 21 запущен")
    
    try:
        app.run_polling(
            allowed_updates=['channel_post', 'edited_channel_post'],
            drop_pending_updates=True
        )
    finally:
        if bot and bot.session:
            asyncio.run(bot.session.close())

if __name__ == "__main__":
    main()