FROM python:3.11-slim

# Устанавливаем Chrome и зависимости
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    curl \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

# Создаем симлинки для совместимости
RUN ln -s /usr/bin/chromium /usr/bin/google-chrome \
    && ln -s /usr/bin/chromedriver /usr/bin/chromedriver

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

CMD ["python", "bot.py"]
