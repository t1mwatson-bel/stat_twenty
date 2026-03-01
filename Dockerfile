FROM python:3.11-slim

# Устанавливаем Chromium и драйвер
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    curl \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

# Создаем симлинки
RUN ln -s /usr/bin/chromium /usr/bin/google-chrome || true \
    && ln -s /usr/bin/chromedriver /usr/bin/chromedriver || true

# Проверяем установку
RUN which chromium && which chromedriver || echo "Check failed"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

CMD ["python", "bot.py"]