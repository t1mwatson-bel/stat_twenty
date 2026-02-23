FROM mcr.microsoft.com/playwright:v1.58.2-jammy

WORKDIR /app

# Копируем все файлы проекта
COPY package*.json ./
RUN npm install

# Копируем все остальные файлы, включая index.js и last_number.txt
COPY . .

CMD ["node", "index.js"]

RUN apt-get update && apt-get install -y \
    libnss3 \
    libatk-bridge2.0-0 \
    libdrm2 \
    libxkbcommon0 \
    libgbm1 \
    libasound2
