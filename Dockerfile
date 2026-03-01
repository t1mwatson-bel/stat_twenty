FROM python:3.9-slim

# Установка Firefox и geckodriver
RUN apt-get update && apt-get install -y \
    firefox-esr \
    wget \
    && wget https://github.com/mozilla/geckodriver/releases/download/v0.35.0/geckodriver-v0.35.0-linux64.tar.gz \
    && tar -xvzf geckodriver-v0.35.0-linux64.tar.gz \
    && chmod +x geckodriver \
    && mv geckodriver /usr/local/bin/ \
    && rm geckodriver-v0.35.0-linux64.tar.gz

# Установка зависимостей Python
COPY requirements.txt .
RUN pip install -r requirements.txt

# Копируем код бота
COPY . .

CMD ["python", "bot.py"]