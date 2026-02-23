import os
import json
import asyncio
import logging
from telethon import TelegramClient, events
from datetime import datetime

# ----------------------------- НАСТРОЙКИ -----------------------------
API_ID = 27496254
API_HASH = '4042aeeec61e0b3635658747eb912a3d'
SOURCE_CHANNEL = -1001424761216  # Канал-источник (ID как число)
TARGET_CHANNEL = -1003477065559  # Канал-приёмник (ID как число)
SESSION_FILE = 'my_account'  # Без @ — просто имя файла
# ---------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === ПРОВЕРКА ФАЙЛА СЕССИИ ===
session_path = f'{SESSION_FILE}.session'
if not os.path.exists(session_path):
    logger.error(f"❌ Файл сессии {session_path} не найден!")
    logger.error("📁 Убедись, что файл my_account.session загружен")
    exit(1)
else:
    size = os.path.getsize(session_path)
    logger.info(f"✅ Файл сессии найден, размер: {size} байт")
    if size < 100:
        logger.error("❌ Файл сессии слишком мал — возможно, повреждён")
        exit(1)

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
    """Новое сообщение в канале статистики"""
    message_map = load_message_map()
    original_id = event.message.id
    
    try:
        # Пересылаем в твой канал
        sent = await client.send_message(TARGET_CHANNEL, event.message)
        
        # Сохраняем соответствие
        message_map[str(original_id)] = sent.id
        save_message_map(message_map)
        
        logger.info(f"✅ Новое: {original_id} -> {sent.id}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка при отправке: {e}")

@client.on(events.MessageEdited(chats=SOURCE_CHANNEL))
async def on_edit_message(event):
    """Сообщение отредактировали"""
    message_map = load_message_map()
    original_id = event.message.id
    
    if str(original_id) not in message_map:
        return
    
    target_id = message_map[str(original_id)]
    
    try:
        await client.edit_message(TARGET_CHANNEL, target_id, event.message.text)
        logger.info(f"✏️ Отредактировано: {original_id} -> {target_id}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка при редактировании: {e}")

async def main():
    """Запуск"""
    logger.info("🚀 Запуск бота...")
    
    await client.start()
    
    me = await client.get_me()
    logger.info(f"✅ Запущен как @{me.username or 'без username'}")
    logger.info(f"👀 Слушаю канал: {SOURCE_CHANNEL}")
    logger.info(f"📤 Отправляю в: {TARGET_CHANNEL}")
    
    await client.run_until_disconnected()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен")
    except Exception as e:
        logger.critical(f"💥 Критическая ошибка: {e}", exc_info=True)