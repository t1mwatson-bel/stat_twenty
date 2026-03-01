FROM python:3.9-slim

# Европейские зеркала для скорости
RUN sed -i 's/deb.debian.org/ftp.nl.debian.org/g' /etc/apt/sources.list

RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    chromium \
    chromium-driver \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "bot.py"]