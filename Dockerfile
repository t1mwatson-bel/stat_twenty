FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    libnss3 \
    libatk-bridge2.0-0 \
    libdrm2 \
    libgbm1 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install playwright telebot
RUN playwright install chromium

COPY bot.py .

CMD ["python", "bot.py"]