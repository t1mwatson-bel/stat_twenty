FROM mcr.microsoft.com/playwright:v1.58.2-jammy

WORKDIR /app

# Копируем все файлы проекта
COPY package*.json ./
RUN npm install

# Копируем все остальные файлы, включая index.js и last_number.txt
COPY . .

CMD ["node", "index.js"]
