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

# Топ-5 лиг (только для прематча)
PREMATCH_LEAGUES = {
    'england': 17,    # Premier League
    'spain': 8,       # La Liga
    'italy': 11,      # Serie A
    'germany': 10,    # Bundesliga
    'france': 7,      # Ligue 1
}

# ======== УПРАВЛЕНИЕ БАНКОМ ========
class BankManager:
    def __init__(self, initial_balance=10000):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.bets_history = []
        self.stats = {
            'total_bets': 0,
            'wins': 0,
            'losses': 0,
            'total_profit': 0,
            'max_win_streak': 0,
            'max_loss_streak': 0,
            'current_streak': 0,
            'streak_type': None
        }
        self.load_bank()
    
    def load_bank(self):
        try:
            if os.path.exists('bank_data.json'):
                with open('bank_data.json', 'r') as f:
                    data = json.load(f)
                    self.balance = data.get('balance', self.initial_balance)
                    self.bets_history = data.get('history', [])
                    self.stats = data.get('stats', self.stats)
                    logger.info(f"💰 Банк загружен: {self.balance}")
        except:
            pass
    
    def save_bank(self):
        try:
            data = {
                'balance': self.balance,
                'history': self.bets_history[-100:],
                'stats': self.stats
            }
            with open('bank_data.json', 'w') as f:
                json.dump(data, f, indent=2)
        except:
            pass
    
    def calculate_stake(self, confidence, odds=None):
        base_stake = self.balance * 0.02
        confidence_mult = confidence / 50
        stake = base_stake * min(confidence_mult, 2.0)
        stake = min(stake, self.balance * 0.05)
        stake = max(stake, 100)
        return int(stake)
    
    def place_bet(self, prediction):
        stake = self.calculate_stake(prediction['confidence'], prediction.get('odds'))
        
        if stake > self.balance:
            stake = self.balance
            if stake < 100:
                return None, "❌ Недостаточно средств"
        
        bet = {
            'id': prediction['id'],
            'match': prediction['match'],
            'type': prediction['type'],
            'value': prediction['value'],
            'confidence': prediction['confidence'],
            'odds': prediction.get('odds', None),
            'stake': stake,
            'balance_before': self.balance,
            'status': 'pending',
            'timestamp': datetime.now().isoformat()
        }
        
        self.bets_history.append(bet)
        self.stats['total_bets'] += 1
        
        return bet, None
    
    def settle_bet(self, bet_id, won):
        for bet in self.bets_history:
            if bet['id'] == bet_id and bet['status'] == 'pending':
                if won:
                    profit = bet['stake'] * (bet['odds'] - 1) if bet['odds'] else bet['stake']
                    self.balance += profit
                    bet['profit'] = profit
                    bet['status'] = 'win'
                    
                    self.stats['wins'] += 1
                    self.stats['total_profit'] += profit
                    
                    if self.stats['streak_type'] == 'win':
                        self.stats['current_streak'] += 1
                    else:
                        self.stats['streak_type'] = 'win'
                        self.stats['current_streak'] = 1
                    
                    self.stats['max_win_streak'] = max(
                        self.stats['max_win_streak'], 
                        self.stats['current_streak']
                    )
                    
                else:
                    self.balance -= bet['stake']
                    bet['profit'] = -bet['stake']
                    bet['status'] = 'loss'
                    
                    self.stats['losses'] += 1
                    self.stats['total_profit'] -= bet['stake']
                    
                    if self.stats['streak_type'] == 'loss':
                        self.stats['current_streak'] += 1
                    else:
                        self.stats['streak_type'] = 'loss'
                        self.stats['current_streak'] = 1
                    
                    self.stats['max_loss_streak'] = max(
                        self.stats['max_loss_streak'], 
                        self.stats['current_streak']
                    )
                
                bet['balance_after'] = self.balance
                self.save_bank()
                return bet
        
        return None
    
    def can_bet(self):
        drawdown = (self.initial_balance - self.balance) / self.initial_balance
        if drawdown > 0.5:
            return False, f"❌ Стоп-лосс: просадка {drawdown:.1%}"
        
        if self.balance < 1000:
            return False, f"❌ Мало денег: {self.balance}"
        
        if self.stats['streak_type'] == 'loss' and self.stats['current_streak'] >= 3:
            return True, f"⚠️ Серия поражений {self.stats['current_streak']}, ставки 50%"
        
        return True, "✅ Можно ставить"
    
    def get_stats(self):
        roi = (self.stats['total_profit'] / self.initial_balance) * 100
        win_rate = (self.stats['wins'] / max(1, self.stats['total_bets'])) * 100
        
        return {
            'balance': self.balance,
            'profit': self.stats['total_profit'],
            'roi': roi,
            'total_bets': self.stats['total_bets'],
            'wins': self.stats['wins'],
            'losses': self.stats['losses'],
            'win_rate': win_rate,
            'current_streak': self.stats['current_streak'],
            'streak_type': self.stats['streak_type'],
            'max_win_streak': self.stats['max_win_streak'],
            'max_loss_streak': self.stats['max_loss_streak']
        }

# ======== ФУТБОЛЬНЫЙ БОТ ========
class FootballBot:
    def __init__(self):
        self.bank = BankManager(initial_balance=10000)
        self.matches = {}
        self.history = deque(maxlen=2000)
        self.memory = self.load_memory()
        self.active_predictions = []
        self.prediction_counter = 0
        self.session = None
        
    def load_memory(self):
        try:
            if os.path.exists('football_memory.json'):
                with open('football_memory.json', 'r') as f:
                    return json.load(f)
        except:
            pass
        return {
            'patterns': {},
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
    
    # ===== LIVE - ВСЕ МАТЧИ МИРА =====
    async def get_live_matches(self):
        """Получает ВСЕ live-матчи с Sofascore (все лиги мира)"""
        await self.init_session()
        matches = []
        
        try:
            url = "https://www.sofascore.com/api/v1/sport/football/events/live"
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for event in data.get('events', []):
                        match = {
                            'id': event['id'],
                            'home': event['homeTeam']['name'],
                            'away': event['awayTeam']['name'],
                            'league': event.get('tournament', {}).get('name', 'Unknown'),
                            'minute': event.get('time', {}).get('current', 0),
                            'score_home': event['homeScore']['current'],
                            'score_away': event['awayScore']['current'],
                            'status': 'live'
                        }
                        matches.append(match)
        except Exception as e:
            logger.error(f"Ошибка получения live-матчей: {e}")
        
        logger.info(f"📊 Найдено live-матчей: {len(matches)}")
        return matches
    
    # ===== ПРЕМАТЧ - ТОЛЬКО ТОП-5 ЛИГ =====
    async def get_upcoming_matches(self):
        """Получает предстоящие матчи топ-5 лиг"""
        await self.init_session()
        matches = []
        today = datetime.now().strftime('%Y-%m-%d')
        
        for league_name, league_id in PREMATCH_LEAGUES.items():
            try:
                url = f"https://www.sofascore.com/api/v1/event/{league_id}/date/{today}"
                async with self.session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for event in data.get('events', []):
                            if event['status']['type'] == 'notstarted':
                                match = {
                                    'id': event['id'],
                                    'home': event['homeTeam']['name'],
                                    'away': event['awayTeam']['name'],
                                    'league': league_name,
                                    'start_time': event['startTimestamp'],
                                    'status': 'upcoming'
                                }
                                matches.append(match)
            except Exception as e:
                logger.error(f"Ошибка получения прематча для {league_name}: {e}")
        
        return matches
    
    async def get_match_stats(self, match_id):
        """Получает статистику матча"""
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
    
    async def get_match_odds(self, match_id):
        """Получает коэффициенты на матч"""
        await self.init_session()
        try:
            url = f"https://www.sofascore.com/api/v1/event/{match_id}/odds"
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return self.parse_odds(data)
        except:
            pass
        return None
    
    def parse_statistics(self, data):
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
                        stats['possession_home'] = float(item.get('home', '0').replace('%', ''))
                        stats['possession_away'] = float(item.get('away', '0').replace('%', ''))
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
    
    def parse_odds(self, data):
        odds = {}
        for market in data.get('markets', []):
            if market.get('name') == 'Total Goals Over/Under':
                for choice in market.get('choices', []):
                    name = choice.get('name', '')
                    if 'Over' in name:
                        odds['total_over'] = float(choice.get('fractional', 2.0))
                    elif 'Under' in name:
                        odds['total_under'] = float(choice.get('fractional', 2.0))
        return odds
    
    def analyze_match(self, match, stats, odds, is_live):
        """Анализирует матч в зависимости от типа"""
        
        if is_live:
            # ======== LIVE ПРОГНОЗ ========
            minute = match['minute']
            total_score = match['score_home'] + match['score_away']
            
            if minute < 30 or minute > 85:
                return None
            
            signals = []
            
            # xG
            xg_total = stats['xg_home'] + stats['xg_away']
            if xg_total > total_score + 1.0:
                signals.append(0.3)
            
            # Удары в створ
            if stats['shots_ontarget'] > total_score * 3:
                signals.append(0.2)
            
            # Владение
            if stats['possession_home'] > 65 or stats['possession_away'] > 65:
                signals.append(0.15)
            
            # Угловые
            if stats['corners'] > 8:
                signals.append(0.1)
            
            if not signals:
                return None
            
            confidence = min(0.9, sum(signals))
            
            return {
                'type': 'live',
                'value': 'goal',
                'text': 'Будет еще гол',
                'confidence': int(confidence * 100)
            }
            
        else:
            # ======== ПРЕМАТЧ ПРОГНОЗ (Тотал) ========
            if not odds:
                return None
            
            signals = []
            
            # Коэффициент на тотал меньше 1.85
            if odds.get('total_over', 2) < 1.85:
                signals.append(0.25)
            
            # Статистика лиги
            league_stats = self.memory['league_stats'].get(match['league'], {})
            if league_stats.get('avg_goals', 2.5) > 2.7:
                signals.append(0.2)
            
            if not signals:
                return None
            
            confidence = min(0.85, 0.5 + sum(signals))
            
            return {
                'type': 'prematch',
                'value': 'total_over',
                'text': 'Тотал БОЛЬШЕ 2.5',
                'confidence': int(confidence * 100),
                'odds': odds.get('total_over', 2.0)
            }
    
    async def check_matches(self, context):
        """Основной цикл проверки"""
        logger.info("🔍 Проверка матчей...")
        
        # Получаем live-матчи (ВСЕ)
        live_matches = await self.get_live_matches()
        for match in live_matches:
            await self.process_match(match, context, is_live=True)
        
        # Получаем предстоящие матчи (ТОП-5)
        upcoming_matches = await self.get_upcoming_matches()
        for match in upcoming_matches:
            await self.process_match(match, context, is_live=False)
    
    async def process_match(self, match, context, is_live):
        """Обрабатывает отдельный матч"""
        match_id = match['id']
        
        # Проверяем не обрабатывали ли уже
        if match_id in self.matches:
            return
        
        # Получаем статистику и коэффициенты
        stats = await self.get_match_stats(match_id)
        odds = await self.get_match_odds(match_id)
        
        if not stats and is_live:
            return
        
        # Анализируем
        analysis = self.analyze_match(match, stats, odds, is_live)
        
        if analysis and analysis['confidence'] > (65 if is_live else 60):
            self.matches[match_id] = match
            await self.create_prediction(match, analysis, context)
    
    async def create_prediction(self, match, analysis, context):
        """Создаёт и отправляет прогноз"""
        self.prediction_counter += 1
        pid = self.prediction_counter
        
        # Проверяем можно ли ставить
        can_bet, bet_status = self.bank.can_bet()
        
        prediction = {
            'id': pid,
            'match': f"{match['home']} - {match['away']}",
            'type': analysis['type'],
            'value': analysis['value'],
            'text': analysis['text'],
            'confidence': analysis['confidence'],
            'odds': analysis.get('odds')
        }
        
        # Размещаем ставку
        bet, error = self.bank.place_bet(prediction)
        if error:
            logger.warning(f"⚠️ Ставка не размещена: {error}")
            return
        
        # Статистика банка
        bank_stats = self.bank.get_stats()
        
        # Эмодзи уверенности
        if analysis['confidence'] > 80:
            conf_emoji = "🔥"
        elif analysis['confidence'] > 70:
            conf_emoji = "⚡"
        else:
            conf_emoji = "📊"
        
        # Формируем сообщение
        if analysis['type'] == 'live':
            message = (
                f"⚽ *LIVE ПРОГНОЗ #{pid}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🆚 *{match['home']}* {match['score_home']}:{match['score_away']} *{match['away']}*\n"
                f"🏆 {match['league']}\n"
                f"⏱ {match['minute']}' минута\n\n"
                f"🎯 *{analysis['text']}*\n"
                f"📈 *Уверенность:* {conf_emoji} {analysis['confidence']}%\n"
                f"💰 *Ставка:* {bet['stake']}₽ ({bet['stake']/self.bank.balance*100:.1f}%)\n"
                f"💳 *Баланс:* {self.bank.balance}₽\n"
                f"📊 *Статистика:* {bank_stats['wins']}/{bank_stats['total_bets']} "
                f"({bank_stats['win_rate']:.1f}%) | ROI: {bank_stats['roi']:+.1f}%\n\n"
                f"⏱ {datetime.now(pytz.timezone('Europe/Moscow')).strftime('%H:%M')} МСК"
            )
        else:
            message = (
                f"⚽ *ПРЕМАТЧ ПРОГНОЗ #{pid}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🆚 *{match['home']}* – *{match['away']}*\n"
                f"🏆 {match['league'].title()}\n"
                f"⏱ Начало: {datetime.fromtimestamp(match['start_time']).strftime('%H:%M')}\n\n"
                f"🎯 *{analysis['text']}*\n"
                f"📊 *Коэффициент:* {analysis['odds']:.2f}\n"
                f"📈 *Уверенность:* {conf_emoji} {analysis['confidence']}%\n"
                f"💰 *Ставка:* {bet['stake']}₽ ({bet['stake']/self.bank.balance*100:.1f}%)\n"
                f"💳 *Баланс:* {self.bank.balance}₽\n"
                f"📊 *Статистика:* {bank_stats['wins']}/{bank_stats['total_bets']} "
                f"({bank_stats['win_rate']:.1f}%) | ROI: {bank_stats['roi']:+.1f}%\n\n"
                f"⏱ {datetime.now(pytz.timezone('Europe/Moscow')).strftime('%H:%M')} МСК"
            )
        
        try:
            sent = await context.bot.send_message(
                chat_id=OUTPUT_CHANNEL_ID,
                text=message,
                parse_mode='Markdown'
            )
            
            self.active_predictions.append({
                'id': pid,
                'match_id': match['id'],
                'bet_id': bet['id'],
                'msg_id': sent.message_id,
                'status': 'pending',
                'analysis': analysis
            })
            
            logger.info(f"📤 Прогноз #{pid} на матч {match['home']} - {match['away']}")
            
        except Exception as e:
            logger.error(f"Ошибка отправки: {e}")
    
    async def check_results(self, context):
        """Проверяет результаты ставок"""
        # TODO: добавить проверку результатов
        pass

# ======== ИНИЦИАЛИЗАЦИЯ ========
bot = None

async def start_monitoring(context: ContextTypes.DEFAULT_TYPE):
    """Запускает мониторинг"""
    global bot
    if not bot:
        bot = FootballBot()
    
    await bot.check_matches(context)

async def check_results_job(context: ContextTypes.DEFAULT_TYPE):
    """Проверяет результаты"""
    if bot:
        await bot.check_results(context)

async def daily_report(context: ContextTypes.DEFAULT_TYPE):
    """Ежедневный отчёт"""
    if bot:
        stats = bot.bank.get_stats()
        
        message = (
            f"📊 *ИТОГИ ДНЯ*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💰 Баланс: {stats['balance']}₽\n"
            f"📈 Профит: {stats['profit']:+.0f}₽ ({stats['roi']:+.1f}%)\n"
            f"🎯 Ставок: {stats['total_bets']}\n"
            f"✅ Зашло: {stats['wins']}\n"
            f"❌ Не зашло: {stats['losses']}\n"
            f"📊 Винрейт: {stats['win_rate']:.1f}%\n\n"
            f"🔥 Макс. серия побед: {stats['max_win_streak']}\n"
            f"❄️ Макс. серия поражений: {stats['max_loss_streak']}"
        )
        
        await context.bot.send_message(
            chat_id=OUTPUT_CHANNEL_ID,
            text=message,
            parse_mode='Markdown'
        )

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

# ======== MAIN ========
def main():
    print("\n" + "="*60)
    print("⚽ ФУТБОЛЬНЫЙ БОТ v3.0")
    print("="*60)
    print("🌍 LIVE: все матчи мира")
    print("🏆 ПРЕМАТЧ: топ-5 лиг (Англия, Испания, Италия, Германия, Франция)")
    print("⚡ LIVE: прогноз на +1 гол")
    print("📊 ПРЕМАТЧ: тотал БОЛЬШЕ 2.5")
    print("💰 Управление банком")
    print("="*60)
    
    app = Application.builder().token(TOKEN).build()
    
    job_queue = app.job_queue
    if job_queue:
        job_queue.run_repeating(start_monitoring, interval=60, first=10)
        job_queue.run_repeating(check_results_job, interval=300, first=30)
        job_queue.run_daily(daily_report, time=time(23, 59, 0))
    
    logger.info("🚀 Бот запущен")
    
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