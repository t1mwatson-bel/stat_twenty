FROM python:3.10-slim

# Устанавливаем все необходимые библиотеки для Firefox
RUN apt-get update && apt-get install -y \
    libnss3 \
    libatk-bridge2.0-0 \
    libdrm2 \
    libxkbcommon0 \
    libgbm1 \
    libasound2 \
    libgtk-3-0 \
    libgtk-4-1 \
    libdbus-glib-1-2 \
    libpulse0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Устанавливаем Playwright и Firefox
RUN playwright install firefox

COPY . .

CMD ["python", "bot.py"]