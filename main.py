# -*- coding: utf-8 -*-
import logging
import re
import os
import sys
import json
import asyncio
from datetime import datetime, time, timedelta
from collections import defaultdict, deque
from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    filters,
    ContextTypes
)
import random
import pytz
import aiohttp
from bs4 import BeautifulSoup
import hashlib

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

# Топ-5 лиг (ID для Sofascore)
LEAGUES = {
    'england': 17,    # Premier League
    'spain': 8,       # La Liga
    'italy': 11,      # Serie A
    'germany': 10,    # Bundesliga
    'france': 7,      # Ligue 1
}

# ======== ФУТБОЛЬНЫЙ БОТ ========
class FootballBot:
    def __init__(self):
        self.matches = {}  # текущие матчи
        self.history = deque(maxlen=2000)  # история матчей
        self.memory = self.load_memory()  # память паттернов
        self.active_predictions = []
        self.prediction_counter = 0
        self.stats = {'total': 0, 'success': 0}
        self.session = None
        self.last_update = datetime.now()
        
    def load_memory(self):
        try:
            if os.path.exists('football_memory.json'):
                with open('football_memory.json', 'r') as f:
                    return json.load(f)
        except:
            pass
        return {
            'patterns': {},  # найденные паттерны
            'situations': {},  # конкретные ситуации
            'league_stats': defaultdict(lambda: {'total':0, 'goals':0})
        }
    
    def save_memory(self):
        try:
            with open('football_memory.json', 'w') as f:
                json.dump(self.memory, f, indent=2)
        except:
            pass
    
    async def init_session(self):
        if not self.session:
            self.session = aiohttp.ClientSession(headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
    
    async def get_live_matches(self):
        """Получает список live-матчей из топ-5 лиг"""
        await self.init_session()
        matches = []
        
        for league_name, league_id in LEAGUES.items():
            try:
                url = f"https://www.sofascore.com/api/v1/event/{league_id}/live"
                async with self.session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        # Парсим матчи
                        for event in data.get('events', []):
                            match = {
                                'id': event['id'],
                                'home': event['homeTeam']['name'],
                                'away': event['awayTeam']['name'],
                                'league': league_name,
                                'minute': event.get('time', {}).get('current', 0),
                                'score_home': event['homeScore']['current'],
                                'score_away': event['awayScore']['current'],
                            }
                            matches.append(match)
            except Exception as e:
                logger.error(f"Ошибка получения матчей для {league_name}: {e}")
        
        return matches
    
    async def get_match_stats(self, match_id):
        """Получает детальную статистику матча"""
        await self.init_session()
        try:
            url = f"https://www.sofascore.com/api/v1/event/{match_id}/statistics"
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return self.parse_statistics(data)
        except:
            pass
        return None
    
    def parse_statistics(self, data):
        """Парсит статистику из ответа API"""
        stats = {
            'possession_home': 0,
            'possession_away': 0,
            'shots_total': 0,
            'shots_ontarget': 0,
            'corners': 0,
            'xg_home': 0,
            'xg_away': 0,
        }
        
        for period in data.get('statistics', []):
            for group in period.get('groups', []):
                for item in group.get('statisticsItems', []):
                    name = item.get('name', '').lower()
                    if 'possession' in name:
                        stats['possession_home'] = item.get('home', '0').replace('%', '')
                        stats['possession_away'] = item.get('away', '0').replace('%', '')
                    elif 'total shots' in name:
                        stats['shots_total'] = int(item.get('home', 0)) + int(item.get('away', 0))
                    elif 'shots on target' in name:
                        stats['shots_ontarget'] = int(item.get('home', 0)) + int(item.get('away', 0))
                    elif 'corner' in name:
                        stats['corners'] = int(item.get('home', 0)) + int(item.get('away', 0))
                    elif 'xg' in name:
                        stats['xg_home'] = float(item.get('home', 0))
                        stats['xg_away'] = float(item.get('away', 0))
        
        return stats
    
    def analyze_match(self, match, stats):
        """Анализирует матч на предмет "будет ли еще гол" """
        if not stats:
            return None
        
        minute = match['minute']
        score_home = match['score_home']
        score_away = match['score_away']
        total_score = score_home + score_away
        
        # Не анализируем первые 30 минут и последние 5
        if minute < 30 or minute > 85:
            return None
        
        # Считаем признаки
        signals = []
        
        # 1. xG (ожидаемые голы) - если сильно больше текущего счета
        xg_total = stats['xg_home'] + stats['xg_away']
        if xg_total > total_score + 1.0:
            signals.append(0.3)  # небольшой сигнал
        
        # 2. Удары в створ
        if stats['shots_ontarget'] > total_score * 3:
            signals.append(0.2)
        
        # 3. Владение одной команды > 65%
        if stats['possession_home'] > 65 or stats['possession_away'] > 65:
            signals.append(0.15)
        
        # 4. Много угловых
        if stats['corners'] > 8:
            signals.append(0.1)
        
        # 5. Недавние голы (будем отслеживать через историю)
        # TODO: добавить проверку
        
        if not signals:
            return None
        
        # Общая уверенность
        confidence = min(0.9, sum(signals))
        
        return {
            'confidence': confidence,
            'minute': minute,
            'score': f"{score_home}:{score_away}",
            'stats': stats
        }
    
    def get_situation_hash(self, match, analysis):
        """Создает хэш ситуации для обучения"""
        context = f"{match['league']}_{match['minute']//10}_{analysis['score']}"
        return hashlib.md5(context.encode()).hexdigest()
    
    async def check_matches(self, context):
        """Основной цикл проверки матчей"""
        logger.info("🔍 Проверка live-матчей...")
        
        # Получаем текущие матчи
        live_matches = await self.get_live_matches()
        
        for match in live_matches:
            match_id = match['id']
            
            # Получаем статистику
            stats = await self.get_match_stats(match_id)
            if not stats:
                continue
            
            # Анализируем
            analysis = self.analyze_match(match, stats)
            
            if analysis and analysis['confidence'] > 0.6:
                # Проверяем не было ли уже прогноза на этот матч
                if match_id in self.matches:
                    continue
                
                # Запоминаем что смотрели этот матч
                self.matches[match_id] = {
                    'minute': match['minute'],
                    'score': analysis['score']
                }
                
                # Создаем прогноз
                await self.create_prediction(match, analysis, context)
            
            # Обучаемся на завершенных матчах
            if match['minute'] > 90 and match_id in self.matches:
                # Проверяем был ли еще гол после прогноза
                # TODO: добавить проверку
                pass
        
        # Чистим старые матчи
        current_minute = datetime.now().minute
        self.matches = {k: v for k, v in self.matches.items() 
                       if current_minute - v['minute'] < 30}
    
    async def create_prediction(self, match, analysis, context):
        """Создает и отправляет прогноз"""
        self.prediction_counter += 1
        pid = self.prediction_counter
        
        confidence = int(analysis['confidence'] * 100)
        
        # Определяем эмодзи уверенности
        if confidence > 80:
            conf_emoji = "🔥"
        elif confidence > 70:
            conf_emoji = "⚡"
        elif confidence > 60:
            conf_emoji = "📊"
        else:
            conf_emoji = "🤔"
        
        message = (
            f"⚽ *ПРОГНОЗ НА ГОЛ #{pid}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🆚 *{match['home']}* {match['score_home']}:{match['score_away']} *{match['away']}*\n"
            f"⏱ {match['minute']}' минута\n"
            f"🏆 {match['league'].title()}\n\n"
            f"📊 *Статистика:*\n"
            f"• Владение: {analysis['stats']['possession_home']}% / {analysis['stats']['possession_away']}%\n"
            f"• xG: {analysis['stats']['xg_home']:.2f} / {analysis['stats']['xg_away']:.2f}\n"
            f"• Удары в створ: {analysis['stats']['shots_ontarget']}\n"
            f"• Угловые: {analysis['stats']['corners']}\n\n"
            f"📈 *Уверенность: {conf_emoji} {confidence}%*\n"
            f"💬 *Ждем еще гол в этом матче!*\n\n"
            f"⏱ {datetime.now(pytz.timezone('Europe/Moscow')).strftime('%H:%M')} МСК"
        )
        
        try:
            await context.bot.send_message(
                chat_id=OUTPUT_CHANNEL_ID,
                text=message,
                parse_mode='Markdown'
            )
            logger.info(f"📤 Прогноз #{pid} на матч {match['home']} - {match['away']}")
            
            self.active_predictions.append({
                'id': pid,
                'match_id': match['id'],
                'minute': match['minute'],
                'score': analysis['score'],
                'confidence': confidence,
                'status': 'pending'
            })
            
            self.stats['total'] += 1
            
        except Exception as e:
            logger.error(f"Ошибка отправки прогноза: {e}")

# ======== ИНИЦИАЛИЗАЦИЯ ========
bot = None

async def start_monitoring(context: ContextTypes.DEFAULT_TYPE):
    """Запускает мониторинг матчей"""
    global bot
    if not bot:
        bot = FootballBot()
    
    await bot.check_matches(context)

async def daily_stats(context: ContextTypes.DEFAULT_TYPE):
    """Ежедневная статистика"""
    if bot:
        total = bot.stats['total']
        success = bot.stats['success']
        percent = int(success / max(1, total) * 100) if total > 0 else 0
        
        await context.bot.send_message(
            chat_id=OUTPUT_CHANNEL_ID,
            text=(
                f"📊 *ФУТБОЛЬНАЯ СТАТИСТИКА*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📈 Всего прогнозов: {total}\n"
                f"✅ Зашло: {success}\n"
                f"📊 Процент: {percent}%\n"
                f"🧠 Паттернов: {len(bot.memory.get('patterns', {}))}"
            ),
            parse_mode='Markdown'
        )

# ======== MAIN ========
def main():
    print("\n" + "="*60)
    print("⚽ ФУТБОЛЬНЫЙ БОТ v1.0")
    print("="*60)
    print("✅ Топ-5 лиг: Англия, Испания, Италия, Германия, Франция")
    print("✅ Прогноз: 'будет ли еще гол'")
    print("✅ Самообучение на истории")
    print("="*60)
    
    app = Application.builder().token(TOKEN).build()
    
    job_queue = app.job_queue
    if job_queue:
        # Проверяем матчи каждую минуту
        job_queue.run_repeating(start_monitoring, interval=60, first=10)
        job_queue.run_daily(daily_stats, time=time(23, 59, 0))
    
    logger.info("🚀 Бот запущен и следит за футболом")
    
    try:
        app.run_polling()
    finally:
        if bot and bot.session:
            asyncio.run(bot.session.close())

if __name__ == "__main__":
    main()