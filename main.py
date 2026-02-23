import asyncio
import json
import os
from telethon import TelegramClient, events
from telethon.tl.types import Message
import logging
from datetime import datetime

# ----------------------------- НАСТРОЙКИ -----------------------------
API_ID = 27496254  # Твой API ID
API_HASH = '4042aeeec61e0b3635658747eb912a3d'  # Твой API Hash
SOURCE_CHANNEL = '-1001424761216'  # Откуда брать
YOUR_BOT_TOKEN = '8042203861:AAHDAyTa9r-w8CD3DsgzRRDmqW-NOwX5CZQ'  # Если хочешь постить ботом
YOUR_CHANNEL = '-1003477065559'  # Куда постить
SESSION_FILE = '@TimWat48'  # Файл сессии
# ---------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Храним соответствие: ID исходного сообщения -> ID сообщения в твоём канале
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
# Если хочешь постить через бота, а не через юзера
# bot = TelegramClient('bot_session', API_ID, API_HASH).start(bot_token=YOUR_BOT_TOKEN)

@client.on(events.NewMessage(chats=SOURCE_CHANNEL))
async def on_new_message(event):
    """Новое сообщение в канале статистики"""
    message_map = load_message_map()
    original_id = event.message.id
    
    try:
        # Пересылаем в твой канал
        sent = await client.send_message(YOUR_CHANNEL, event.message)
        
        # Сохраняем соответствие
        message_map[str(original_id)] = sent.id
        save_message_map(message_map)
        
        logger.info(f"✅ Новое: {original_id} -> {sent.id}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка при отправке нового: {e}")

@client.on(events.MessageEdited(chats=SOURCE_CHANNEL))
async def on_edit_message(event):
    """Сообщение отредактировали в исходном канале"""
    message_map = load_message_map()
    original_id = event.message.id
    
    if str(original_id) not in message_map:
        logger.warning(f"⚠️ Редактирование неизвестного сообщения {original_id}")
        return
    
    target_id = message_map[str(original_id)]
    
    try:
        # Редактируем сообщение в твоём канале
        await client.edit_message(YOUR_CHANNEL, target_id, event.message.text)
        logger.info(f"✏️ Отредактировано: {original_id} -> {target_id}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка при редактировании: {e}")

async def main():
    """Запуск"""
    await client.start()
    logger.info(f"✅ Юзербот запущен как {await client.get_me().username}")
    logger.info(f"👀 Слушаю канал: {SOURCE_CHANNEL}")
    logger.info(f"📤 Отправляю в: {YOUR_CHANNEL}")
    
    await client.run_until_disconnected()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Остановлено")
    except Exception as e:
        logger.critical(f"💥 Критическая ошибка: {e}")