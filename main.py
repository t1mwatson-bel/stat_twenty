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

# Проверка наличия обязательных переменных
if not all([API_ID, API_HASH, PHONE_NUMBER, SOURCE_CHANNEL, TARGET_CHANNEL]):
    print("❌ Ошибка: Не все переменные окружения заданы!")
    print("   API_ID, API_HASH, PHONE_NUMBER, SOURCE_CHANNEL, TARGET_CHANNEL")
    exit(1)

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

# Создаем клиент с файлом сессии
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
        logger.error(f"❌ Ошибка при отправке нового: {e}")

@client.on(events.MessageEdited(chats=SOURCE_CHANNEL))
async def on_edit_message(event):
    """Сообщение отредактировали (добавили карту или флаги)"""
    message_map = load_message_map()
    original_id = event.message.id
    
    if str(original_id) not in message_map:
        logger.warning(f"⚠️ Редактирование неизвестного сообщения {original_id}")
        return
    
    target_id = message_map[str(original_id)]
    
    try:
        # Редактируем сообщение в твоём канале
        await client.edit_message(TARGET_CHANNEL, target_id, event.message.text)
        logger.info(f"✏️ Отредактировано: {original_id} -> {target_id}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка при редактировании: {e}")

async def main():
    """Запуск клиента"""
    logger.info("🔄 Запуск клиента...")
    
    # Проверяем наличие файла сессии
    if os.path.exists(f'{SESSION_FILE}.session'):
        logger.info("✅ Файл сессии найден, авторизация не требуется")
    else:
        logger.warning("⚠️ Файл сессии не найден, потребуется авторизация")
    
    # Запускаем с номером телефона (если сессия есть - проигнорируется)
    await client.start(phone=PHONE_NUMBER)
    
    # Получаем информацию о себе
    me = await client.get_me()
    logger.info(f"✅ Запущен как: @{me.username or 'нет юзернейма'} (ID: {me.id})")
    logger.info(f"👀 Слушаю канал: {SOURCE_CHANNEL}")
    logger.info(f"📤 Отправляю в: {TARGET_CHANNEL}")
    logger.info(f"🔄 Редактирования: ВКЛЮЧЕНЫ")
    
    # Бесконечное ожидание сообщений
    await client.run_until_disconnected()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен вручную")
    except Exception as e:
        logger.critical(f"💥 Критическая ошибка: {e}")        logger.info("🛑 Остановлено")