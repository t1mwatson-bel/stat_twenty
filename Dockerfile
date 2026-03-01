FROM python:3.9-slim

# Установка Firefox и необходимых зависимостей
RUN apt-get update && apt-get install -y \
    firefox-esr \
    wget \
    bzip2 \
    && rm -rf /var/lib/apt/lists/*

# Скачиваем geckodriver
RUN wget https://github.com/mozilla/geckodriver/releases/download/v0.35.0/geckodriver-v0.35.0-linux64.tar.gz \
    && tar -xvzf geckodriver-v0.35.0-linux64.tar.gz \
    && chmod +x geckodriver \
    && mv geckodriver /usr/local/bin/ \
    && rm geckodriver-v0.35.0-linux64.tar.gz

# Установка Python зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код бота
COPY . .

CMD ["python", "bot.py"]