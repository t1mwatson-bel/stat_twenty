import os
import json
import asyncio
import logging
from telethon import TelegramClient, events

# ----------------------------- НАСТРОЙКИ -----------------------------
# ВСЕ НАСТРОЙКИ ЧЕРЕЗ ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ (Railway -> Variables)
API_ID = int(os.environ.get('27496254', 0))
API_HASH = os.environ.get('4042aeeec61e0b3635658747eb912a3d', '')
PHONE_NUMBER = os.environ.get('+79205026567', '')  # Твой номер для входа
SOURCE_CHANNEL = int(os.environ.get('-100123456789', 0))  # ID канала-источника (с минусом)
TARGET_CHANNEL = os.environ.get('-1003477065559', '')  # @username твоего канала
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
    message_map = load_message_map()
    original_id = event.message.id
    
    try:
        sent = await client.send_message(TARGET_CHANNEL, event.message)
        message_map[str(original_id)] = sent.id
        save_message_map(message_map)
        logger.info(f"✅ Новое: {original_id} -> {sent.id}")
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")

@client.on(events.MessageEdited(chats=SOURCE_CHANNEL))
async def on_edit_message(event):
    message_map = load_message_map()
    original_id = event.message.id
    
    if str(original_id) not in message_map:
        return
    
    target_id = message_map[str(original_id)]
    
    try:
        await client.edit_message(TARGET_CHANNEL, target_id, event.message.text)
        logger.info(f"✏️ Обновлено: {original_id} -> {target_id}")
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")

async def main():
    logger.info("🔄 Запуск клиента...")
    
    if os.path.exists(f'{SESSION_FILE}.session'):
        logger.info("✅ Файл сессии найден")
    else:
        logger.warning("⚠️ Файл сессии не найден")
    
    await client.start(phone=PHONE_NUMBER)
    me = await client.get_me()
    logger.info(f"✅ Запущен как: @{me.username or 'нет юзернейма'}")
    logger.info(f"👀 Слушаю канал: {SOURCE_CHANNEL}")
    logger.info(f"📤 Отправляю в: {TARGET_CHANNEL}")
    
    await client.run_until_disconnected()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен вручную")
    except Exception as e:
        logger.critical(f"💥 Критическая ошибка: {e}")
        logger.info("🛑 Остановка")
