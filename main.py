import os
import json
import asyncio
import logging
from telethon import TelegramClient, events
from datetime import datetime

# ----------------------------- НАСТРОЙКИ -----------------------------
API_ID = 27496254  # Твой
API_HASH = '4042aeeec61e0b3635658747eb912a3d'
SOURCE_CHANNEL = -100123456789  # ID канала статистики (с минусом)
TARGET_CHANNEL = '-1003477065559'  # Куда постить
SESSION_FILE = '@TimWat48'
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

@client.on(events.NewMessage(chats=SOURCE_CHANNEL))
async def on_new_message(event):
    """Новое сообщение — отправляем в свой канал"""
    message_map = load_message_map()
    original_id = event.message.id
    
    try:
        # Пересылаем
        sent = await client.send_message(TARGET_CHANNEL, event.message)
        
        # Сохраняем связь
        message_map[str(original_id)] = sent.id
        save_message_map(message_map)
        
        logger.info(f"✅ Новое #{original_id} -> {sent.id}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")

@client.on(events.MessageEdited(chats=SOURCE_CHANNEL))
async def on_edit_message(event):
    """Сообщение изменили (добавили карту, флаги) — обновляем у себя"""
    message_map = load_message_map()
    original_id = event.message.id
    
    if str(original_id) not in message_map:
        logger.warning(f"⚠️ Нет в маппинге: {original_id}")
        return
    
    target_id = message_map[str(original_id)]
    
    try:
        await client.edit_message(TARGET_CHANNEL, target_id, event.message.text)
        logger.info(f"✏️ Обновлено #{original_id} -> {target_id}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка редактирования: {e}")

async def main():
    await client.start()
    me = await client.get_me()
    logger.info(f"✅ Запущен как @{me.username}")
    logger.info(f"👀 Слушаю канал: {SOURCE_CHANNEL}")
    logger.info(f"📤 Отправляю в: {TARGET_CHANNEL}")
    logger.info(f"🔄 Редактирования: ВКЛЮЧЕНЫ")
    
    await client.run_until_disconnected()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Остановлено")