import os
import json
import asyncio
import logging
from telethon import TelegramClient
from datetime import datetime

# ----------------------------- НАСТРОЙКИ -----------------------------
API_ID = 27496254
API_HASH = '4042aeeec61e0b3635658747eb912a3d'
SOURCE_CHANNEL = -1001424761216  # Откуда брать
TARGET_CHANNEL = -1003477065559  # Куда постить
SESSION_FILE = 'my_account'
CHECK_INTERVAL = 20  # Секунд между проверками
# ---------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

message_map_file = 'message_map.json'

def load_message_map():
    if os.path.exists(message_map_file):
        with open(message_map_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_message_map(message_map):
    with open(message_map_file, 'w', encoding='utf-8') as f:
        json.dump(message_map, f, ensure_ascii=False, indent=2)

client = TelegramClient(SESSION_FILE, API_ID, API_HASH)

async def check_new_messages():
    """Периодически проверяет канал на новые сообщения"""
    message_map = load_message_map()
    last_known_id = 0
    
    # Если есть сохранённые сообщения, находим максимальный ID
    if message_map:
        try:
            last_known_id = max(int(k) for k in message_map.keys())
            logger.info(f"🔄 Последний известный ID: {last_known_id}")
        except:
            pass
    
    while True:
        try:
            # Получаем последние 5 сообщений из канала
            messages = await client.get_messages(SOURCE_CHANNEL, limit=5)
            
            if messages:
                # Проходим от старых к новым
                for msg in reversed(messages):
                    msg_id = msg.id
                    
                    # Если это новое сообщение (больше последнего сохранённого)
                    if msg_id > last_known_id:
                        logger.info(f"🔥 Найдено новое сообщение #{msg_id}")
                        
                        # Пересылаем в целевой канал
                        sent = await client.send_message(TARGET_CHANNEL, msg)
                        
                        # Сохраняем соответствие
                        message_map[str(msg_id)] = sent.id
                        last_known_id = msg_id
                        
                        save_message_map(message_map)
                        logger.info(f"✅ Переслано: {msg_id} -> {sent.id}")
            
            await asyncio.sleep(CHECK_INTERVAL)
            
        except Exception as e:
            logger.error(f"❌ Ошибка при проверке: {e}")
            await asyncio.sleep(30)

async def main():
    """Запуск"""
    logger.info("🚀 Запуск бота (режим Polling)...")
    
    await client.start()
    
    me = await client.get_me()
    logger.info(f"✅ Запущен как @{me.username or 'без username'}")
    
    # Принудительно получаем диалоги, чтобы "активировать" канал
    await client.get_dialogs()
    logger.info(f"👀 Начинаю следить за каналом: {SOURCE_CHANNEL}")
    
    # Запускаем бесконечную проверку
    await check_new_messages()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен")
    except Exception as e:
        logger.critical(f"💥 Критическая ошибка: {e}")