FROM python:3.10-slim

# Устанавливаем зависимости для Playwright
RUN apt-get update && apt-get install -y \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Копируем requirements
COPY requirements.txt .

# Устанавливаем Python пакеты
RUN pip install --no-cache-dir -r requirements.txt

# Устанавливаем Playwright браузеры
RUN playwright install chromium
RUN playwright install-deps

# Копируем код
COPY . .

# Запускаем бота
CMD ["python", "bot.py"]